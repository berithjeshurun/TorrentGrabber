"""YourBittorrent search provider."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import MagnetUriProvider, SearchProvider


class YourBittorrent(SearchProvider, MagnetUriProvider):
    id = "yourbittorrent"
    name = "YourBittorrent"
    url = "https://yourbittorrent.com"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.PORN,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        url = f"{self.url}/?q={query}"
        soup = await self.network.get_soup(url)
        out: list[Torrent] = []
        for tr in soup.select("table.table tr"):
            a = tr.select_one("a[href*='/torrent/']")
            if a is None:
                continue
            name = H.text(a)
            href = a.get("href") or ""
            if not name or not href:
                continue
            details = href if href.startswith("http") else f"{self.url}{href}"
            tds = tr.find_all("td")
            size = seeders = peers = None
            for td in tds:
                t = H.text(td)
                if any(u in t.lower() for u in ("kb", "mb", "gb", "tb")) and size is None:
                    size = t
                elif t.replace(",", "").isdigit():
                    if seeders is None:
                        seeders = _uint(t)
                    elif peers is None:
                        peers = _uint(t)
            rid = details.rstrip("/").rsplit("/", 1)[-1] or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size,
                    seeders=seeders,
                    peers=peers,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetRequiresFetch(url=details),
                    category=Category.OTHER,
                )
            )
        return out

    async def get_magnet_uri(self, url: str) -> str:
        soup = await self.network.get_soup(url)
        a = soup.select_one("a[href^='magnet:']")
        if a is None or not a.get("href"):
            raise RuntimeError(f"No magnet on {url}")
        return a["href"]


def _uint(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        return int(raw.replace(",", "").strip())
    except ValueError:
        return None
