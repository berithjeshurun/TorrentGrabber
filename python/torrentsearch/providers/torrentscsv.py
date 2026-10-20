"""TorrentsCSV search provider — port of TorrentsCSV.kt."""

from __future__ import annotations

from typing import Any, Optional

from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent
from ..network import NetworkClient
from ..provider import SearchProvider


class TorrentsCSV(SearchProvider):
    id = "torrentscsv"
    name = "TorrentsCSV"
    url = "https://torrents-csv.com"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/service/search?q={query}"
        response_json = await self.network.get_json(request_url)
        if response_json is None or not isinstance(response_json, dict):
            return []

        torrents_raw = response_json.get("torrents")
        if not isinstance(torrents_raw, list):
            return []

        torrents: list[Torrent] = []
        for item in torrents_raw:
            if isinstance(item, dict):
                parsed = self._parse_torrent_object(item)
                if parsed is not None:
                    torrents.append(parsed)
        return torrents

    def _parse_torrent_object(self, torrent_object: dict[str, Any]) -> Optional[Torrent]:
        """
        Object layout (only shown necessary fields):

            name:         <string>
            infohash:     <string>
            size_bytes:   <number>
            seeders:      <number>
            leechers:     <number>
            created_unix: <number>
        """
        name = torrent_object.get("name")
        if not isinstance(name, str):
            return None
        info_hash = torrent_object.get("infohash")
        if not isinstance(info_hash, str):
            return None
        torrent_remote_id = torrent_object.get("id")
        if torrent_remote_id is None:
            return None
        torrent_id = utils.create_torrent_id(self.id, str(int(torrent_remote_id)))
        magnet_uri = utils.create_magnet_uri(info_hash)

        size_bytes = torrent_object.get("size_bytes")
        size = (
            utils.format_bytes(float(size_bytes))
            if size_bytes is not None
            else None
        )
        seeders = _to_uint(torrent_object.get("seeders"))
        peers = _to_uint(torrent_object.get("leechers"))
        created_unix = torrent_object.get("created_unix")
        upload_date = (
            utils.epoch_second_to_datetime(int(created_unix))
            if created_unix is not None
            else None
        )

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.name,
            upload_date=upload_date,
            category=Category.OTHER,
            magnet_uri=MagnetAvailable(value=magnet_uri),
        )


def _to_uint(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None
