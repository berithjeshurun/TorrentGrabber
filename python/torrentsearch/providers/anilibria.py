"""AniLibria search provider — port of AniLibria.kt."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider

API_URL = "https://anilibria.top/api/v1"
MAX_RELEASES = 15


class AniLibria(SearchProvider):
    id = "anilibria"
    name = "AniLibria"
    url = "https://www.anilibria.top"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        search_url = f"{API_URL}/app/search/releases?query={query}"
        response_json = await self.network.get_json(search_url)
        if response_json is None:
            return []

        if not isinstance(response_json, list):
            return []

        release_ids: list[int] = []
        for item in response_json:
            if not isinstance(item, dict):
                continue
            rid = item.get("id")
            if rid is not None:
                release_ids.append(int(rid))
            if len(release_ids) >= MAX_RELEASES:
                break

        results = await asyncio.gather(
            *[self._fetch_release_torrents(rid) for rid in release_ids]
        )
        return [t for batch in results for t in batch]

    async def _fetch_release_torrents(self, release_id: int) -> list[Torrent]:
        torrents_url = f"{API_URL}/anime/torrents/release/{release_id}"
        response_json = await self.network.get_json(torrents_url)
        if response_json is None or not isinstance(response_json, list):
            return []

        torrents: list[Torrent] = []
        for item in response_json:
            if isinstance(item, dict):
                torrent = self._parse_torrent_object(item)
                if torrent is not None:
                    torrents.append(torrent)
        return torrents

    def _parse_torrent_object(self, obj: dict[str, Any]) -> Optional[Torrent]:
        info_hash = obj.get("hash")
        if not isinstance(info_hash, str):
            return None

        torrent_remote_id = obj.get("id")
        source_id = (
            str(int(torrent_remote_id))
            if torrent_remote_id is not None
            else info_hash
        )
        torrent_id = utils.create_torrent_id(self.id, source_id)

        release = obj.get("release") if isinstance(obj.get("release"), dict) else None
        name = obj.get("label")
        if not isinstance(name, str):
            name = None
            if release is not None:
                name_obj = release.get("name")
                if isinstance(name_obj, dict):
                    name = name_obj.get("english") or name_obj.get("main")
        if not isinstance(name, str):
            return None

        size_raw = obj.get("size")
        size = (
            utils.format_bytes(float(size_raw))
            if size_raw is not None
            else None
        )

        magnet = obj.get("magnet")
        magnet_uri = magnet if isinstance(magnet, str) else utils.create_magnet_uri(info_hash)

        seeders = obj.get("seeders")
        peers = obj.get("leechers")
        created_at = obj.get("created_at")
        upload_date = (
            utils.parse_iso(created_at) if isinstance(created_at, str) else None
        )

        description_page_url = None
        if release is not None:
            alias = release.get("alias")
            if isinstance(alias, str):
                description_page_url = f"{self.url}/anime/releases/release/{alias}"

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=int(seeders) if seeders is not None else None,
            peers=int(peers) if peers is not None else None,
            provider_name=self.name,
            upload_date=upload_date,
            category=Category.ANIME,
            details_page_url=description_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
        )
