"""CROTorrents search provider (PC game torrents)."""

from __future__ import annotations

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import MagnetUriProvider, SearchProvider


class CROTorrents(SearchProvider, MagnetUriProvider):
    id = "crotorrents"
    name = "CROTorrents"
    url = "https://crotorrents.com"
    supported_categories = {Category.GAMES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?s={query}"
        soup = await self.network.get_soup(request_url)
        out: list[Torrent] = []
        for article in soup.select("article.ct-game-card, article.post"):
            a = article.select_one("h2 a, h2.ct-game-card__title a, .entry-title a")
            if a is None:
                continue
            name = H.text(a)
            href = a.get("href") or ""
            if not name or not href:
                continue
            details = href if href.startswith("http") else f"{self.url}{href}"
            rid = article.get("id") or details.rstrip("/").rsplit("/", 1)[-1]
            magnet_a = article.select_one("a[href^='magnet:']")
            if magnet_a is not None and magnet_a.get("href"):
                magnet_uri = MagnetAvailable(value=magnet_a["href"])
            else:
                magnet_uri = MagnetRequiresFetch(url=details)
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    category=Category.GAMES,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=magnet_uri,
                )
            )
        return out

    async def get_magnet_uri(self, url: str) -> str:
        soup = await self.network.get_soup(url)
        a = soup.select_one("a[href^='magnet:']")
        if a is None or not a.get("href"):
            raise RuntimeError(f"No magnet on {url}")
        return a["href"]
