"""SearchProvider base contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .models import Category, SearchProviderSafety, Torrent, TorrentDetails
from .network import NetworkClient


class SearchProvider(ABC):
    id: str
    name: str
    url: str
    supported_categories: set[Category] = set()
    safety: SearchProviderSafety = SearchProviderSafety.SAFE
    is_cloudflare_protected: bool = False
    enabled_by_default: bool = False
    cloudflare_solver_url: Optional[str] = None

    def __init__(self, network: NetworkClient):
        self.network = network

    @abstractmethod
    async def search(self, query: str, category: Category) -> list[Torrent]:
        ...

    def supports(self, category: Category) -> bool:
        if category is Category.ALL:
            return True
        return category in self.supported_categories


class MagnetUriProvider(ABC):
    """Mixin for providers that resolve magnets via a details URL."""

    @abstractmethod
    async def get_magnet_uri(self, url: str) -> str:
        ...


class TorrentDetailsProvider(ABC):
    @abstractmethod
    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        ...


class LatestTorrentsProvider(ABC):
    @abstractmethod
    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        ...


class TopTorrentsProvider(ABC):
    @abstractmethod
    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        ...
