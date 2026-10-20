"""Domain models mirroring the Android app."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Union


class Category(str, Enum):
    ALL = "All"
    ANIME = "Anime"
    APPS = "Apps"
    BOOKS = "Books"
    GAMES = "Games"
    MOVIES = "Movies"
    MUSIC = "Music"
    PORN = "Porn"
    SERIES = "Series"
    OTHER = "Other"

    @property
    def is_nsfw(self) -> bool:
        return self is Category.PORN


class SortCriteria(str, Enum):
    NAME = "name"
    SEEDERS = "seeders"
    PEERS = "peers"
    FILE_SIZE = "size"
    DATE = "date"


class SortOrder(str, Enum):
    ASCENDING = "asc"
    DESCENDING = "desc"


class SearchProviderSafety(str, Enum):
    SAFE = "safe"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class MagnetAvailable:
    value: str


@dataclass(frozen=True)
class MagnetRequiresFetch:
    url: str


MagnetUri = Union[MagnetAvailable, MagnetRequiresFetch]


@dataclass
class Torrent:
    id: str
    name: str
    provider_name: str
    magnet_uri: MagnetUri
    size: Optional[str] = None
    seeders: Optional[int] = None
    peers: Optional[int] = None
    upload_date: Optional[datetime] = None
    category: Optional[Category] = None
    details_page_url: Optional[str] = None
    file_download_link: Optional[str] = None
    info_hash: Optional[str] = None

    @property
    def is_nsfw(self) -> bool:
        return bool(self.category and self.category.is_nsfw)

    @property
    def is_dead(self) -> bool:
        return self.seeders == 0 and self.peers == 0

    def magnet(self) -> Optional[str]:
        if isinstance(self.magnet_uri, MagnetAvailable):
            return self.magnet_uri.value
        return None


@dataclass
class TorrentDetails:
    info_hash: str
    name: str
    magnet_uri: str
    size: Optional[str] = None
    seeders: Optional[int] = None
    peers: Optional[int] = None
    upload_date: Optional[datetime] = None
    category: Optional[Category] = None
    uploader: Optional[str] = None
    last_checked: Optional[datetime] = None
    file_download_link: Optional[str] = None
    description: Optional[str] = None
    poster_url: Optional[str] = None
    screenshot_urls: list[str] = field(default_factory=list)


@dataclass
class SearchProviderError:
    provider_id: str
    provider_name: str
    provider_url: str
    kind: str  # "crash" | "cloudflare"
    cause: BaseException


@dataclass
class SearchResults:
    torrents: list[Torrent] = field(default_factory=list)
    errors: list[SearchProviderError] = field(default_factory=list)

    def add_success(self, torrents: list[Torrent]) -> SearchResults:
        return SearchResults(
            torrents=self.torrents + torrents,
            errors=list(self.errors),
        )

    def add_error(self, error: SearchProviderError) -> SearchResults:
        return SearchResults(
            torrents=list(self.torrents),
            errors=self.errors + [error],
        )


@dataclass
class TorznabConfig:
    id: str
    search_provider_name: str
    url: str
    api_key: str
    supported_categories: set[Category] = field(default_factory=set)
