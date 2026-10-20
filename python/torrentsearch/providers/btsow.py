"""Btsow search provider — port of Btsow.kt."""

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
from ..provider import SearchProvider, TorrentDetailsProvider

API_BASE_URL = "https://btsow.live/bts/data/api"


class Btsow(SearchProvider, TorrentDetailsProvider):
    id = "btsow"
    name = "Btsow"
    url = "https://btsow.live"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_json_parser = _BtsowResultsJsonParser(self.id, self.name, self.url)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        # [{"search":"one"},30,3]
        request_payload = [{"search": unquote(query)}, 30, 1]
        request_url = f"{API_BASE_URL}/search"
        response_json = await self.network.post_json(
            url=request_url, payload=request_payload
        )
        if response_json is None:
            return []
        return self._results_json_parser.parse(response_json)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        info_hash = ""
        for ch in reversed(details_page_url):
            if ch == "/":
                break
            info_hash = ch + info_hash
        request_payload = [info_hash]
        request_url = f"{API_BASE_URL}/magnet"
        response_json = await self.network.post_json(
            url=request_url, payload=request_payload
        )
        if response_json is None:
            return None
        return _BtsowDetailsJsonParser.parse(response_json)


class _BtsowResultsJsonParser:
    def __init__(self, provider_id: str, provider_name: str, provider_url: str):
        self._provider_id = provider_id
        self._provider_name = provider_name
        self._provider_url = provider_url

    def parse(self, response_json: Any) -> list[Torrent]:
        if not isinstance(response_json, dict):
            return []
        data = response_json.get("data")
        if not isinstance(data, list):
            return []

        results: list[Torrent] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            torrent_name = item.get("name")
            if not isinstance(torrent_name, str):
                continue
            torrent_name = torrent_name.replace("<em>", "").replace("</em>", "")

            info_hash = item.get("hash")
            if not isinstance(info_hash, str):
                continue
            info_hash = info_hash.lower()

            torrent_id = utils.create_torrent_id(self._provider_id, info_hash)
            size_raw = item.get("size")
            size = (
                utils.format_bytes(float(size_raw))
                if size_raw is not None
                else None
            )
            magnet_uri = utils.create_magnet_uri(info_hash)

            results.append(
                Torrent(
                    id=torrent_id,
                    name=torrent_name,
                    size=size,
                    provider_name=self._provider_name,
                    magnet_uri=MagnetAvailable(value=magnet_uri),
                    details_page_url=f"{self._provider_url}/magnet/detail/{info_hash}",
                )
            )
        return results


class _BtsowDetailsJsonParser:
    @staticmethod
    def parse(response_json: Any) -> Optional[TorrentDetails]:
        if not isinstance(response_json, dict):
            return None
        data_object = response_json.get("data")
        if not isinstance(data_object, dict):
            return None

        info_hash = data_object.get("hash")
        if not isinstance(info_hash, str):
            return None
        info_hash = info_hash.lower()

        torrent_name = data_object.get("name")
        if not isinstance(torrent_name, str):
            return None

        size_raw = data_object.get("size")
        size = (
            utils.format_bytes(float(size_raw)) if size_raw is not None else None
        )

        upload_date = None
        date_raw = data_object.get("date")
        if date_raw is not None:
            upload_date = utils.epoch_second_to_datetime(int(date_raw))

        last_checked = None
        last_update = data_object.get("lastUpdateTime")
        if last_update is not None:
            last_checked = utils.epoch_second_to_datetime(int(last_update))

        return TorrentDetails(
            info_hash=info_hash,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            last_checked=last_checked,
            magnet_uri=utils.create_magnet_uri(info_hash),
        )
