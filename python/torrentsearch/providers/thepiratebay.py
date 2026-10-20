"""ThePirateBay — port of ThePirateBay.kt."""

from __future__ import annotations

from typing import Any, Optional

from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent, TorrentDetails
from ..network import NetworkClient
from ..provider import (
    LatestTorrentsProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

API_URL = "https://apibay.org"


def category_from_id(cid: int) -> Category:
    if cid in range(300, 307) or cid == 399:
        return Category.APPS
    if cid == 601:
        return Category.BOOKS
    if cid in range(400, 409) or cid == 499:
        return Category.GAMES
    if cid in {201, 202, 204, 207, 209, 210, 211}:
        return Category.MOVIES
    if cid in range(100, 105) or cid == 199:
        return Category.MUSIC
    if cid in range(500, 508) or cid == 599:
        return Category.PORN
    if cid in {205, 208, 212}:
        return Category.SERIES
    return Category.OTHER


class ThePirateBay(
    SearchProvider, TorrentDetailsProvider, LatestTorrentsProvider, TopTorrentsProvider
):
    id = "thepiratebay"
    name = "ThePirateBay"
    url = "https://thepiratebay.org"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.UNSAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    def _category_id(self, category: Category) -> int:
        return {
            Category.ALL: 0,
            Category.ANIME: 0,
            Category.APPS: 300,
            Category.BOOKS: 601,
            Category.GAMES: 400,
            Category.MOVIES: 200,
            Category.SERIES: 200,
            Category.MUSIC: 101,
            Category.PORN: 500,
            Category.OTHER: 600,
        }[category]

    async def search(self, query: str, category: Category) -> list[Torrent]:
        url = f"{API_URL}/q.php?q={query}&cat={self._category_id(category)}"
        data = await self.network.get_json(url)
        return self._parse_array(data)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        tid = details_page_url.rsplit("=", 1)[-1]
        data = await self.network.get_json(f"{API_URL}/t.php?id={tid}")
        if not isinstance(data, dict):
            return None
        info_hash = (data.get("info_hash") or "").lower()
        name = data.get("name")
        if not info_hash or not name:
            return None
        size = utils.format_bytes(float(data["size"])) if data.get("size") is not None else None
        upload = (
            utils.epoch_second_to_datetime(int(data["added"]))
            if data.get("added") is not None
            else None
        )
        cat = category_from_id(int(data["category"])) if data.get("category") is not None else None
        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=_uint(data.get("seeders")),
            peers=_uint(data.get("leechers")),
            upload_date=upload,
            category=cat,
            uploader=data.get("username"),
            magnet_uri=utils.create_magnet_uri(info_hash),
            description=data.get("descr"),
        )

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        if category is Category.ALL:
            url = f"{API_URL}/precompiled/data_top100_recent.json"
        else:
            url = f"{API_URL}/q.php?q=category%3A{self._category_id(category)}"
        return self._parse_array(await self.network.get_json(url))

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        if category is Category.ALL:
            url = f"{API_URL}/precompiled/data_top100_48h.json"
        else:
            cid = self._category_id(category)
            url = f"{API_URL}/precompiled/data_top100_48h_{cid}.json"
        return self._parse_array(await self.network.get_json(url))

    def _parse_array(self, data: Any) -> list[Torrent]:
        if not isinstance(data, list):
            return []
        out: list[Torrent] = []
        for item in data:
            if isinstance(item, dict):
                t = self._parse_one(item)
                if t:
                    out.append(t)
        return out

    def _parse_one(self, obj: dict) -> Optional[Torrent]:
        name = obj.get("name")
        if not name or name == "No results returned":
            return None
        info_hash = (obj.get("info_hash") or "").lower().strip()
        remote_id = obj.get("id")
        if not info_hash or not remote_id:
            return None
        size = utils.format_bytes(obj["size"]) if obj.get("size") else None
        upload = None
        if obj.get("added"):
            try:
                upload = utils.epoch_second_to_datetime(int(obj["added"]))
            except (TypeError, ValueError):
                pass
        cat = None
        if obj.get("category") is not None:
            try:
                cat = category_from_id(int(obj["category"]))
            except (TypeError, ValueError):
                pass
        return Torrent(
            id=utils.create_torrent_id(self.id, str(remote_id)),
            name=name,
            size=size,
            seeders=_uint(obj.get("seeders")),
            peers=_uint(obj.get("leechers")),
            provider_name=self.name,
            upload_date=upload,
            category=cat,
            magnet_uri=MagnetAvailable(utils.create_magnet_uri(info_hash)),
            details_page_url=f"{self.url}/description.php?id={remote_id}",
        )


def _uint(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
