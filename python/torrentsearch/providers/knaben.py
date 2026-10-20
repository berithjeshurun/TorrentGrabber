"""Knaben search provider — port of Knaben.kt."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

from .. import utils
from ..models import (
    Category,
    MagnetAvailable,
    SearchProviderSafety,
    Torrent,
)
from ..network import NetworkClient
from ..provider import LatestTorrentsProvider, SearchProvider, TopTorrentsProvider

API_URL = "https://api.knaben.org"


class Knaben(SearchProvider, LatestTorrentsProvider, TopTorrentsProvider):
    """Provider using the official Knaben API (https://knaben.org/api/v1)."""

    id = "knaben"
    name = "Knaben"
    url = "https://knaben.org"
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
        self._results_parser = _KnabenResultsJsonParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_body = self._build_request_json(query, category, order_by="seeders")
        response_json = await self.network.post_json(
            url=f"{API_URL}/v1",
            payload=request_body,
        )
        if response_json is None:
            return []
        return self._results_parser.parse(response_json)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_body = self._build_request_json(None, category, order_by="date")
        response_json = await self.network.post_json(
            url=f"{API_URL}/v1", payload=request_body
        )
        if response_json is None:
            return []
        return self._results_parser.parse(response_json)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_body = self._build_request_json(None, category, order_by="seeders")
        response_json = await self.network.post_json(
            url=f"{API_URL}/v1", payload=request_body
        )
        if response_json is None:
            return []
        return self._results_parser.parse(response_json)

    def _build_request_json(
        self, query: Optional[str], category: Category, order_by: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "size": 300,
            "order_by": order_by,
            "order_direction": "desc",
            "hide_unsafe": True,
            "hide_xxx": False,
        }
        if query is not None:
            # Orchestrator URL-encodes for HTML providers; JSON APIs need raw text.
            body["query"] = unquote(query)
        category_ids = self._get_knaben_category_ids(category)
        if category_ids:
            body["categories"] = category_ids
        return body

    @staticmethod
    def _get_knaben_category_ids(category: Category) -> list[int]:
        return {
            Category.ALL: [],
            Category.MUSIC: [1000000],
            Category.SERIES: [2000000],
            Category.MOVIES: [3000000],
            Category.APPS: [4000000],
            Category.PORN: [5000000],
            Category.ANIME: [6000000],
            Category.GAMES: [7000000],
            Category.BOOKS: [9000000],
            Category.OTHER: [10000000],
        }.get(category, [])


class _KnabenResultsJsonParser:
    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, json_data: Any) -> list[Torrent]:
        if not isinstance(json_data, dict):
            return []
        hits = json_data.get("hits")
        if not isinstance(hits, list):
            return []
        results: list[Torrent] = []
        for hit in hits:
            if isinstance(hit, dict):
                torrent = self._parse_torrent_object(hit)
                if torrent is not None:
                    results.append(torrent)
        return results

    def _parse_torrent_object(self, obj: dict[str, Any]) -> Optional[Torrent]:
        name = obj.get("title")
        if not isinstance(name, str):
            return None
        magnet_uri = obj.get("magnetUrl")
        if not isinstance(magnet_uri, str):
            return None

        torrent_remote_id = obj.get("id")
        if isinstance(torrent_remote_id, str):
            source_id = torrent_remote_id
        else:
            source_id = utils.get_info_hash_from_magnet_uri(magnet_uri)
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

        size = None
        bytes_val = obj.get("bytes")
        if bytes_val is not None:
            try:
                size = utils.format_bytes(float(bytes_val))
            except (TypeError, ValueError):
                size = None

        seeders = obj.get("seeders")
        peers = obj.get("peers")
        try:
            seeders_i = int(seeders) if seeders is not None else None
        except (TypeError, ValueError):
            seeders_i = None
        try:
            peers_i = int(peers) if peers is not None else None
        except (TypeError, ValueError):
            peers_i = None

        upload_date = None
        date_str = obj.get("date")
        if isinstance(date_str, str):
            upload_date = utils.parse_iso(date_str)

        description_page_url = obj.get("details")
        if not isinstance(description_page_url, str):
            description_page_url = None

        category = self._extract_category(obj)

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders_i,
            peers=peers_i,
            provider_name=self._provider_name,
            upload_date=upload_date,
            details_page_url=description_page_url,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            category=category,
        )

    @staticmethod
    def _extract_category(obj: dict[str, Any]) -> Category:
        category_ids = obj.get("categoryId")
        if not isinstance(category_ids, list):
            return Category.ALL

        ids: list[int] = []
        for cid in category_ids:
            try:
                ids.append(int(cid))
            except (TypeError, ValueError):
                continue
        if not ids:
            return Category.ALL

        first_id = min(ids)
        if 1000000 <= first_id <= 1999999:
            return Category.MUSIC
        if 2000000 <= first_id <= 2999999:
            return Category.SERIES
        if 3000000 <= first_id <= 3999999:
            return Category.MOVIES
        if 4000000 <= first_id <= 4999999:
            return Category.APPS
        if 5000000 <= first_id <= 5999999:
            return Category.PORN
        if 6000000 <= first_id <= 6999999:
            return Category.ANIME
        if 7000000 <= first_id <= 7999999:
            return Category.GAMES
        if 9000000 <= first_id <= 9999999:
            return Category.BOOKS
        if 10000000 <= first_id <= 10999999:
            return Category.OTHER
        return Category.ALL
