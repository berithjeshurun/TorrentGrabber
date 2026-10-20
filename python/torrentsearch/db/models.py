"""SQLModel tables for persisted searches, torrents, and downloads."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped
from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SearchQuery(SQLModel, table=True):
    __tablename__ = "search_queries"

    id: Optional[int] = Field(default=None, primary_key=True)
    query: str = Field(max_length=512, index=True)
    category: str = Field(default="All", max_length=32)
    nsfw: bool = Field(default=False)
    hide_dead: bool = Field(default=False)
    sort_by: str = Field(default="seeders", max_length=32)
    sort_order: str = Field(default="desc", max_length=8)
    max_results: Optional[int] = Field(default=None)
    providers: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    resolve_magnets: bool = Field(default=True)
    result_count: int = Field(default=0)
    error_count: int = Field(default=0)
    duration_ms: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    results: Mapped[List["TorrentResult"]] = Relationship(
        back_populates="search",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "order_by": "TorrentResult.rank",
        },
    )
    errors: Mapped[List["SearchError"]] = Relationship(
        back_populates="search",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class TorrentResult(SQLModel, table=True):
    __tablename__ = "torrent_results"
    __table_args__ = (
        UniqueConstraint("search_id", "torrent_id", name="uq_search_torrent"),
        Index("ix_torrent_results_name", "name"),
        Index("ix_torrent_results_provider", "provider"),
        Index("ix_torrent_results_seeders", "seeders"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("search_queries.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    torrent_id: str = Field(max_length=128, index=True)
    name: str = Field(max_length=1024)
    size: Optional[str] = Field(default=None, max_length=64)
    seeders: Optional[int] = Field(default=None)
    peers: Optional[int] = Field(default=None)
    provider: str = Field(max_length=128)
    upload_date: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    category: Optional[str] = Field(default=None, max_length=32)
    details_page_url: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    file_download_link: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    magnet: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    magnet_fetch_url: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    nsfw: bool = Field(default=False)
    dead: bool = Field(default=False)
    rank: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    search: Mapped[Optional["SearchQuery"]] = Relationship(back_populates="results")


class SearchError(SQLModel, table=True):
    __tablename__ = "search_errors"

    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("search_queries.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider_id: str = Field(max_length=64)
    provider_name: str = Field(max_length=128)
    provider_url: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    kind: str = Field(max_length=32)
    message: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )

    search: Mapped[Optional["SearchQuery"]] = Relationship(back_populates="errors")


class DownloadRecord(SQLModel, table=True):
    __tablename__ = "downloads"

    id: str = Field(max_length=64, primary_key=True)
    magnet_or_url: str = Field(sa_column=Column(Text, nullable=False))
    mode: str = Field(max_length=32, index=True)
    status: str = Field(max_length=32, index=True)
    config_json: str = Field(sa_column=Column(Text, nullable=False))
    save_path: str = Field(sa_column=Column(Text, nullable=False))
    name: Optional[str] = Field(default=None, max_length=1024)
    info_hash: Optional[str] = Field(default=None, max_length=64, index=True)
    total_bytes: int = Field(default=0)
    downloaded_bytes: int = Field(default=0)
    progress: float = Field(default=0.0)
    download_rate: float = Field(default=0.0)
    upload_rate: float = Field(default=0.0)
    num_peers: int = Field(default=0)
    playhead: int = Field(default=0)
    buffered_bytes_ahead: int = Field(default=0)
    buffered_seconds_ahead: Optional[float] = Field(default=None)
    underrun: bool = Field(default=False)
    error: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    search_result_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class WatchProgress(SQLModel, table=True):
    """Continue-watching positions (product layer; not in C++ engine)."""

    __tablename__ = "watch_progress"
    __table_args__ = (
        UniqueConstraint("download_id", "file_index", name="uq_watch_dl_file"),
        Index("ix_watch_progress_updated", "updated_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    download_id: str = Field(max_length=64, index=True)
    file_index: int = Field(default=0)
    position_sec: float = Field(default=0.0)
    duration_sec: Optional[float] = Field(default=None)
    title: Optional[str] = Field(default=None, max_length=1024)
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
