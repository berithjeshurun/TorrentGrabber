"""BangumiMoe search provider — port of BangumiMoe.kt."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    SearchProviderSafety,
    Torrent,
    TorrentDetails,
)
from ..network import NetworkClient
from ..provider import LatestTorrentsProvider, SearchProvider, TorrentDetailsProvider


class BangumiMoe(SearchProvider, LatestTorrentsProvider, TorrentDetailsProvider):
    id = "bangumimoe"
    name = "BangumiMoe"
    url = "https://bangumi.moe"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_json_parser = _BangumiMoeResultsJsonParser(
            self.id, self.name, self.url
        )

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/api/v2/torrent/search"
        response_json = await self.network.post_json(
            request_url, payload={"query": unquote(query)}
        )
        if response_json is None:
            return []
        return self._results_json_parser.parse(response_json)

    def _parse_details_json(self, json_data: Any) -> Optional[TorrentDetails]:
        if not isinstance(json_data, dict):
            return None
        torrent_name = json_data.get("title")
        magnet_uri = json_data.get("magnet")
        if not isinstance(torrent_name, str) or not isinstance(magnet_uri, str):
            return None
        info_hash = json_data.get("infohash")
        if not isinstance(info_hash, str):
            info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)
        size = json_data.get("size") if isinstance(json_data.get("size"), str) else None
        seeders = json_data.get("seeders")
        peers = json_data.get("leechers")
        publish_time = json_data.get("publish_time")
        upload_date = (
            utils.parse_iso(publish_time) if isinstance(publish_time, str) else None
        )
        return TorrentDetails(
            info_hash=info_hash,
            magnet_uri=magnet_uri,
            name=torrent_name,
            size=size,
            seeders=int(seeders) if seeders is not None else None,
            peers=int(peers) if peers is not None else None,
            upload_date=upload_date,
            category=Category.ANIME,
        )

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        torrent_id = ""
        for ch in reversed(details_page_url):
            if ch == "/":
                break
            torrent_id = ch + torrent_id
        request_url = "https://bangumi.moe/api/torrent/fetch"
        response_json = await self.network.post_json(
            request_url, payload={"_id": torrent_id}
        )
        if response_json is None:
            return None
        return self._parse_details_json(response_json)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/api/torrent/latest"
        response_json = await self.network.get_json(request_url)
        if response_json is None:
            return []
        return self._results_json_parser.parse(response_json)


class _BangumiMoeResultsJsonParser:
    def __init__(self, provider_id: str, provider_name: str, provider_url: str):
        self._provider_id = provider_id
        self._provider_name = provider_name
        self._provider_url = provider_url

    def parse(self, json_data: Any) -> list[Torrent]:
        if not isinstance(json_data, dict):
            return []
        torrents_arr = json_data.get("torrents")
        if not isinstance(torrents_arr, list):
            return []
        results: list[Torrent] = []
        for item in torrents_arr:
            if isinstance(item, dict):
                torrent = self._parse_torrent_object(item)
                if torrent is not None:
                    results.append(torrent)
        return results

    def _parse_torrent_object(self, obj: dict[str, Any]) -> Optional[Torrent]:
        torrent_name = obj.get("title")
        magnet_uri = obj.get("magnet")
        if not isinstance(torrent_name, str) or not isinstance(magnet_uri, str):
            return None

        torrent_remote_id = obj.get("_id")
        if isinstance(torrent_remote_id, str):
            source_id = torrent_remote_id
        else:
            source_id = utils.get_info_hash_from_magnet_uri(magnet_uri)
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

        size = obj.get("size") if isinstance(obj.get("size"), str) else None
        seeders = obj.get("seeders")
        peers = obj.get("leechers")
        publish_time = obj.get("publish_time")
        upload_date = (
            utils.parse_iso(publish_time) if isinstance(publish_time, str) else None
        )
        details_page_url = (
            f"{self._provider_url}/torrent/{torrent_remote_id}"
            if isinstance(torrent_remote_id, str)
            else None
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=int(seeders) if seeders is not None else None,
            peers=int(peers) if peers is not None else None,
            upload_date=upload_date,
            provider_name=self._provider_name,
            category=Category.ANIME,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            details_page_url=details_page_url,
        )
