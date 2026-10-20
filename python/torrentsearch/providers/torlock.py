"""Torlock search provider."""

from __future__ import annotations

from typing import Optional
from urllib.parse import unquote

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


class Torlock(SearchProvider, MagnetUriProvider):
    id = "torlock"
    name = "Torlock"
    url = "https://www.torlock.com"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    def _cat_path(self, category: Category) -> str:
        return {
            Category.ALL: "all",
            Category.ANIME: "anime",
            Category.APPS: "software",
            Category.GAMES: "game",
            Category.MOVIES: "movie",
            Category.MUSIC: "music",
            Category.SERIES: "television",
            Category.BOOKS: "ebooks",
            Category.OTHER: "all",
            Category.PORN: "all",
        }.get(category, "all")

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = unquote(query).replace(" ", "-")
        cat = self._cat_path(category)
        url = f"{self.url}/{cat}/torrents/{q}.html?sort=seeds&page=1"
        soup = await self.network.get_soup(url)
        out: list[Torrent] = []
        for a in soup.select("article a[href^='/torrent']"):
            href = a.get("href") or ""
            name = H.text(a)
            if not name or not href.startswith("/torrent"):
                continue
            details = f"{self.url}{href}"
            # /torrent/12345/name.html → id 12345
            parts = href.strip("/").split("/")
            rid = parts[1] if len(parts) > 1 else href
            tr = a.find_parent("tr")
            size = seeders = peers = None
            upload = None
            if tr is not None:
                size = H.text(tr.select_one("td.ts"))
                seeders = _uint(H.text(tr.select_one("td.tul")))
                peers = _uint(H.text(tr.select_one("td.tdl")))
                date_raw = H.text(tr.select_one("td.td"))
                if date_raw:
                    upload = utils.try_parse_special_relative(date_raw) or utils.parse_date(
                        date_raw, "%m/%d/%Y"
                    )
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size or None,
                    seeders=seeders,
                    peers=peers,
                    upload_date=upload,
                    provider_name=self.name,
                    details_page_url=details,
                    file_download_link=f"{self.url}/tor/{rid}.torrent",
                    magnet_uri=MagnetRequiresFetch(url=details),
                    category=category if category is not Category.ALL else Category.OTHER,
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
