"""TorrentGalaxy search provider."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

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


class TorrentGalaxy(SearchProvider, MagnetUriProvider):
    id = "torrentgalaxy"
    name = "TorrentGalaxy"
    url = "https://torrentgalaxy.one"
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
    is_cloudflare_protected = True

    # Prefer working mirrors; NetworkClient follows redirects.
    _MIRRORS = (
        "https://torrentgalaxy.one",
        "https://torrentgalaxy.info",
        "https://torrentgalaxy.to",
    )

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    def _cat_token(self, category: Category) -> Optional[str]:
        return {
            Category.ANIME: "Anime",
            Category.APPS: "Apps",
            Category.BOOKS: "Books",
            Category.GAMES: "Games",
            Category.MOVIES: "Movies",
            Category.MUSIC: "Music",
            Category.SERIES: "TV",
            Category.PORN: "XXX",
            Category.OTHER: "Other",
        }.get(category)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = quote(query, safe="")
        path = f"/get-posts/keywords:{q}"
        token = self._cat_token(category) if category is not Category.ALL else None
        if token:
            path += f":category:{token}"
        last_err: Optional[Exception] = None
        for base in self._MIRRORS:
            try:
                request_url = f"{base}{path}"
                soup = await self.network.get_soup(request_url)
                out = self._parse(soup, base, category)
                if out or True:
                    self.url = base
                    return out
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                continue
        if last_err:
            raise last_err
        return []

    def _parse(self, soup, base: str, category: Category) -> list[Torrent]:
        out: list[Torrent] = []
        rows = soup.select("div.tgxtablerow")
        if not rows:
            # classic torrents.php layout fallback
            for tr in soup.select("div.tgxtable tr, table tr"):
                a = tr.select_one("a[href*='/torrent/'], a[href*='torrentid']")
                if a is None:
                    continue
                rows.append(tr)

        for row in rows:
            title_a = row.select_one(
                "a[href^='/post-detail/'], a[href*='/torrent/'], a.txlight"
            )
            if title_a is None:
                continue
            name = title_a.get("title") or H.text(title_a)
            href = title_a.get("href") or ""
            if not name or not href:
                continue
            details = href if href.startswith("http") else f"{base}{href}"
            size = seeders = peers = None
            cells = row.select("div.tgxtablecell, td")
            for cell in cells:
                t = H.text(cell)
                if not t:
                    continue
                if any(u in t.lower() for u in ("kb", "mb", "gb", "tb")) and size is None:
                    size = t
                nums = [n for n in t.replace(",", " ").split() if n.isdigit()]
                if len(nums) >= 2 and seeders is None:
                    seeders = _uint(nums[0])
                    peers = _uint(nums[1])
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
