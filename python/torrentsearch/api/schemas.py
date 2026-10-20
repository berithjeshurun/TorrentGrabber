"""Pydantic request/response schemas for the FastAPI service."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from ..models import Category, SortCriteria, SortOrder


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=512, examples=["ubuntu"])
    category: Category = Category.ALL
    providers: Optional[list[str]] = Field(
        default=None,
        description="Provider IDs to use. Defaults to the app's enabled-by-default set.",
    )
    all_providers: bool = False
    nsfw: bool = False
    hide_dead: bool = False
    max_results: Optional[int] = Field(default=50, ge=1, le=1000)
    sort: SortCriteria = SortCriteria.SEEDERS
    order: SortOrder = SortOrder.DESCENDING
    resolve_magnets: bool = True
    resolve_concurrency: int = Field(default=8, ge=1, le=32)
    save: bool = Field(default=True, description="Persist this search + results to SQL")


class ProviderErrorOut(BaseModel):
    provider_id: str
    provider: str
    url: Optional[str] = None
    kind: str
    message: str


class TorrentOut(BaseModel):
    id: str
    name: str
    size: Optional[str] = None
    seeders: Optional[int] = None
    peers: Optional[int] = None
    provider: str
    upload_date: Optional[datetime] = None
    category: Optional[str] = None
    details_page_url: Optional[str] = None
    file_download_link: Optional[str] = None
    magnet: Optional[str] = None
    magnet_fetch_url: Optional[str] = None
    nsfw: bool = False
    dead: bool = False
    rank: Optional[int] = None
    search_id: Optional[int] = None
    db_id: Optional[int] = None

    model_config = {"from_attributes": True}


class SearchResponse(BaseModel):
    search_id: Optional[int] = None
    query: str
    category: str
    count: int
    duration_ms: int
    saved: bool
    errors: list[ProviderErrorOut] = []
    results: list[TorrentOut] = []


class ResolveMagnetOut(BaseModel):
    db_id: int
    magnet: Optional[str] = None
    resolved: bool


class SearchSummary(BaseModel):
    id: int
    query: str
    category: str
    nsfw: bool
    result_count: int
    error_count: int
    duration_ms: Optional[int] = None
    providers: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SearchDetail(SearchSummary):
    hide_dead: bool
    sort_by: str
    sort_order: str
    max_results: Optional[int] = None
    resolve_magnets: bool
    results: list[TorrentOut] = []
    errors: list[ProviderErrorOut] = []


class PaginatedSearches(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[SearchSummary]


class PaginatedTorrents(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[TorrentOut]


class ProviderOut(BaseModel):
    id: str
    name: str
    url: str
    default: bool
    cloudflare: bool
    safety: str
    categories: list[str]


class HealthOut(BaseModel):
    status: str
    version: str
    database: str
    providers: int
    healthy: bool = True
    phrase: str = "Ready"
    searching: int = 0
    downloads_active: int = 0
    downloads_streaming: int = 0
    underruns: int = 0
    uptime_seconds: float = 0.0
    downloader_running: bool = False


class ActivityEventOut(BaseModel):
    time: datetime
    level: str
    area: str
    message: str


class ActivityFeedOut(BaseModel):
    phrase: str
    healthy: bool
    events: list[ActivityEventOut]
