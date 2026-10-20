"""IGGGAMES search provider."""

from __future__ import annotations

from typing import Optional

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


class IGGGames(SearchProvider, MagnetUriProvider):
    id = "igggames"
    name = "IGGGAMES"
    url = "https://igg-games.com"
    supported_categories = {Category.GAMES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True
    is_cloudflare_protected = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/?s={query}"
        soup = await self.network.get_soup(request_url)
        out: list[Torrent] = []
        for article in soup.select("article, .post, .uk-article"):
            a = article.select_one("h1 a, h2 a, .entry-title a, a[rel='bookmark']")
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
        # IGG often hosts magnet mirrors / Google Drive; prefer magnet first.
        a = soup.select_one("a[href^='magnet:']")
        if a is not None and a.get("href"):
            return a["href"]
        raise RuntimeError(f"No magnet on {url}")
