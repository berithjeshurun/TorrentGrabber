"""SolidTorrents — JSON API with HTML fallback."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from .. import htmlutil as H
from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider

_ALL = {
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


class SolidTorrents(SearchProvider):
    id = "solidtorrents"
    name = "SolidTorrents"
    url = "https://solidtorrents.to"
    supported_categories = _ALL
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    def _category(self, category: Category) -> str:
        return {
            Category.ALL: "all",
            Category.MUSIC: "Audio",
            Category.BOOKS: "eBook",
            Category.MOVIES: "Video",
            Category.SERIES: "Video",
            Category.ANIME: "Video",
            Category.APPS: "Software",
            Category.GAMES: "Software",
            Category.PORN: "Adult",
            Category.OTHER: "all",
        }.get(category, "all")

    async def search(self, query: str, category: Category) -> list[Torrent]:
        q = unquote(query)
        cat = self._category(category)
        api = (
            f"{self.url}/api/v1/search?q={query}&sort=seeders"
            f"&category={cat}&skip=0"
        )
        try:
            data = await self.network.get_json(api)
            parsed = self._parse_json(data)
            if parsed:
                return parsed
        except Exception:  # noqa: BLE001
            pass

        # HTML fallback
        html_url = (
            f"{self.url}/search?q={query}&category={cat}"
            f"&sort=seeders&sort=desc&page=1"
        )
        soup = await self.network.get_soup(html_url)
        return self._parse_html(soup, q)

    def _parse_json(self, data: Any) -> list[Torrent]:
        if not isinstance(data, dict):
            return []
        results = data.get("results")
        if not isinstance(results, list):
            return []
        out: list[Torrent] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            name = item.get("title")
            if not isinstance(name, str):
                continue
            magnet = item.get("magnet")
            infohash = item.get("infohash") or item.get("infoHash")
            if not isinstance(magnet, str):
                if isinstance(infohash, str):
                    magnet = utils.create_magnet_uri(infohash)
                else:
                    continue
            rid = str(item.get("_id") or infohash or name)
            size = None
            if item.get("size") is not None:
                try:
                    size = utils.format_bytes(float(item["size"]))
                except (TypeError, ValueError):
                    size = str(item.get("size"))
            swarm = item.get("swarm") if isinstance(item.get("swarm"), dict) else {}
            seeders = _uint(swarm.get("seeders"))
            peers = _uint(swarm.get("leechers"))
            details = None
            if item.get("_id"):
                details = f"{self.url}/view/{item['_id']}"
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size,
                    seeders=seeders,
                    peers=peers,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetAvailable(value=magnet),
                    category=Category.OTHER,
                )
            )
        return out

    def _parse_html(self, soup, query: str) -> list[Torrent]:
        out: list[Torrent] = []
        for row in soup.select("li.search-result"):
            title_a = row.select_one("h5.title a")
            if title_a is None:
                continue
            name = H.text(title_a)
            if not name:
                continue
            href = title_a.get("href") or ""
            details = href if href.startswith("http") else f"{self.url}{href}"
            magnet_a = row.select_one("a.dl-magnet")
            magnet = magnet_a.get("href") if magnet_a is not None else None
            if not magnet:
                continue
            stats = row.select("div.stats > div")
            size = H.text(stats[1]) if len(stats) > 1 else None
            peers = _uint(H.text(stats[2])) if len(stats) > 2 else None
            seeders = _uint(H.text(stats[3])) if len(stats) > 3 else None
            rid = details.rsplit("/", 1)[-1] or name
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, rid),
                    name=name,
                    size=size,
                    seeders=seeders,
                    peers=peers,
                    provider_name=self.name,
                    details_page_url=details,
                    magnet_uri=MagnetAvailable(value=magnet),
                    category=Category.OTHER,
                )
            )
        return out


def _uint(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        s = str(raw).replace(",", "").strip()
        return int(float(s)) if s else None
    except (TypeError, ValueError):
        return None
