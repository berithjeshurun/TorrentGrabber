"""SQLModel engine / session setup (sync + async)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Callable, TypeVar

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

DEFAULT_DB_PATH = Path(os.environ.get("TORRENTSEARCH_DB", "torrentsearch.db")).resolve()
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DEFAULT_DB_PATH.as_posix()}")


def _to_async_url(url: str) -> str:
    if url.startswith("sqlite+aiosqlite:"):
        return url
    if url.startswith("sqlite:"):
        return url.replace("sqlite:", "sqlite+aiosqlite:", 1)
    if url.startswith("postgresql+psycopg2:"):
        return url.replace("postgresql+psycopg2:", "postgresql+asyncpg:", 1)
    if url.startswith("postgresql:"):
        return url.replace("postgresql:", "postgresql+asyncpg:", 1)
    return url


ASYNC_DATABASE_URL = os.environ.get("ASYNC_DATABASE_URL", _to_async_url(DATABASE_URL))

T = TypeVar("T")


def _configure_sqlite(engine: Engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def _configure_sqlite_async(engine: AsyncEngine) -> None:
    if "sqlite" not in str(engine.url):
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


def create_db_engine(url: str | None = None) -> Engine:
    db_url = url or DATABASE_URL
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    eng = create_engine(db_url, connect_args=connect_args)
    _configure_sqlite(eng)
    return eng


def create_async_db_engine(url: str | None = None) -> AsyncEngine:
    db_url = url or ASYNC_DATABASE_URL
    connect_args = {"check_same_thread": False} if "sqlite" in db_url else {}
    eng = create_async_engine(db_url, connect_args=connect_args)
    _configure_sqlite_async(eng)
    return eng


engine = create_db_engine()
async_engine = create_async_db_engine()
SessionLocal = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


def init_db(eng: Engine | None = None) -> None:
    from . import models as _models  # noqa: F401

    SQLModel.metadata.create_all(bind=eng or engine)


async def init_db_async(eng: AsyncEngine | None = None) -> None:
    from . import models as _models  # noqa: F401

    target = eng or async_engine
    async with target.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[AsyncSession, None]:
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_db() -> Generator[Session, None, None]:
    """Sync FastAPI dependency (CLI helpers)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Async FastAPI dependency — preferred for API routes."""
    session = AsyncSessionLocal()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def run_in_thread(fn: Callable[..., T], /, *args, **kwargs) -> T:
    """Offload blocking CPU/IO work so the event loop stays free."""
    return await asyncio.to_thread(fn, *args, **kwargs)
