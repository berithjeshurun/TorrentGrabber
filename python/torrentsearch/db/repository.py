"""Persistence helpers — auto-save search runs (SQLModel + async)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import selectinload
from sqlmodel import col, desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..export import fetch_url_of, magnet_of
from ..models import SearchResults, Torrent
from .models import SearchError, SearchQuery, TorrentResult


async def save_search(
    session: AsyncSession,
    *,
    query: str,
    category: str,
    results: SearchResults,
    nsfw: bool = False,
    hide_dead: bool = False,
    sort_by: str = "seeders",
    sort_order: str = "desc",
    max_results: Optional[int] = None,
    providers: Optional[list[str]] = None,
    resolve_magnets: bool = True,
    duration_ms: Optional[int] = None,
) -> SearchQuery:
    """Persist a search run and its torrents/errors. Returns the SearchQuery row."""
    record = SearchQuery(
        query=query,
        category=category,
        nsfw=nsfw,
        hide_dead=hide_dead,
        sort_by=sort_by,
        sort_order=sort_order,
        max_results=max_results,
        providers=",".join(providers) if providers else None,
        resolve_magnets=resolve_magnets,
        result_count=len(results.torrents),
        error_count=len(results.errors),
        duration_ms=duration_ms,
    )
    session.add(record)
    await session.flush()

    for rank, torrent in enumerate(results.torrents, start=1):
        session.add(_torrent_row(record.id, torrent, rank))  # type: ignore[arg-type]

    for err in results.errors:
        session.add(
            SearchError(
                search_id=record.id,  # type: ignore[arg-type]
                provider_id=err.provider_id,
                provider_name=err.provider_name,
                provider_url=err.provider_url,
                kind=err.kind,
                message=str(err.cause),
            )
        )

    await session.flush()
    await session.refresh(record)
    return record


async def list_results_for_search(
    session: AsyncSession, search_id: int
) -> list[TorrentResult]:
    stmt = (
        select(TorrentResult)
        .where(TorrentResult.search_id == search_id)
        .order_by(TorrentResult.rank)
    )
    return list((await session.exec(stmt)).all())


def _torrent_row(search_id: int, torrent: Torrent, rank: int) -> TorrentResult:
    return TorrentResult(
        search_id=search_id,
        torrent_id=torrent.id,
        name=torrent.name,
        size=torrent.size,
        seeders=torrent.seeders,
        peers=torrent.peers,
        provider=torrent.provider_name,
        upload_date=torrent.upload_date,
        category=torrent.category.value if torrent.category else None,
        details_page_url=torrent.details_page_url,
        file_download_link=torrent.file_download_link,
        magnet=magnet_of(torrent),
        magnet_fetch_url=fetch_url_of(torrent),
        nsfw=torrent.is_nsfw,
        dead=torrent.is_dead,
        rank=rank,
    )


async def get_search(session: AsyncSession, search_id: int) -> Optional[SearchQuery]:
    stmt = (
        select(SearchQuery)
        .where(SearchQuery.id == search_id)
        .options(
            selectinload(SearchQuery.results),  # type: ignore[arg-type]
            selectinload(SearchQuery.errors),  # type: ignore[arg-type]
        )
    )
    result = await session.exec(stmt)
    return result.first()


async def list_searches(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
) -> tuple[list[SearchQuery], int]:
    filters = []
    if q:
        filters.append(col(SearchQuery.query).ilike(f"%{q}%"))

    count_stmt = select(func.count()).select_from(SearchQuery)
    list_stmt = select(SearchQuery).order_by(desc(SearchQuery.created_at))
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    total = (await session.exec(count_stmt)).one()
    rows = list((await session.exec(list_stmt.offset(offset).limit(limit))).all())
    return rows, int(total or 0)


async def list_torrents(
    session: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    provider: Optional[str] = None,
    search_id: Optional[int] = None,
    min_seeders: Optional[int] = None,
) -> tuple[list[TorrentResult], int]:
    filters = []
    if q:
        filters.append(col(TorrentResult.name).ilike(f"%{q}%"))
    if provider:
        filters.append(col(TorrentResult.provider).ilike(provider))
    if search_id is not None:
        filters.append(TorrentResult.search_id == search_id)
    if min_seeders is not None:
        filters.append(col(TorrentResult.seeders) >= min_seeders)

    count_stmt = select(func.count()).select_from(TorrentResult)
    list_stmt = select(TorrentResult).order_by(
        desc(func.coalesce(TorrentResult.seeders, -1)),
        desc(TorrentResult.created_at),
    )
    if filters:
        count_stmt = count_stmt.where(*filters)
        list_stmt = list_stmt.where(*filters)

    total = (await session.exec(count_stmt)).one()
    rows = list((await session.exec(list_stmt.offset(offset).limit(limit))).all())
    return rows, int(total or 0)


async def delete_search(session: AsyncSession, search_id: int) -> bool:
    record = await session.get(SearchQuery, search_id)
    if record is None:
        return False
    await session.delete(record)
    return True
