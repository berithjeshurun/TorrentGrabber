"""FileMood search provider — port of FileMood.kt."""

from __future__ import annotations

from typing import Optional

from .. import htmlutil as H
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


def _uint(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


class FileMood(SearchProvider, TorrentDetailsProvider):
    id = "filemood"
    name = "FileMood"
    url = "https://filemood.com"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _FileMoodResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/result?q={query}+in%3Atitle"
        response_html = await self.network.get_text(url=request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _FileMoodDetailsPageParser.parse(response_html)


class _FileMoodResultsPageParser:
    LIST_ITEM = "table > tbody > tr:has(a.btn-success)"
    NAME = "> td.dn-title"
    SIZE = "td.dn-size"
    SEEDERS_PEERS = "td.dn-status"
    DETAILS_PAGE_URL = "td.dn-btn > div > a"

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        results: list[Torrent] = []
        for item in H.select(soup, self.LIST_ITEM):
            torrent = self._parse_list_item(item, page_url)
            if torrent is not None:
                results.append(torrent)
        return results

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        torrent_name = H.text(H.select_first(list_item, self.NAME))
        if not torrent_name:
            return None
        description_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not description_page_url or not description_page_url.strip():
            return None

        info_hash = (
            description_page_url.removesuffix(".html").rsplit("-", 1)[-1].lower().strip()
        )
        torrent_id = utils.create_torrent_id(self._provider_id, info_hash)
        magnet_uri = utils.create_magnet_uri(info_hash)
        size = H.text(H.select_first(list_item, self.SIZE))

        seeders = peers = None
        sp_text = H.text(H.select_first(list_item, self.SEEDERS_PEERS))
        if sp_text is not None:
            parts = sp_text.split("/")
            seeders = _uint(parts[0]) if len(parts) > 0 else None
            peers = _uint(parts[1]) if len(parts) > 1 else None

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            category=Category.OTHER,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            details_page_url=description_page_url,
        )


class _FileMoodDetailsPageParser:
    TORRENT_NAME = "div.well > table:nth-child(1) > tbody > tr:nth-child(1) > td > h1 > b"
    SIZE = "div.well > table:nth-child(3) > tbody > tr:nth-child(2) > td:nth-child(2) > p > b"
    LAST_CHECKED = (
        "div.well > table:nth-child(3) > tbody > tr:nth-child(4) > td:nth-child(2) > p > b"
    )
    INFO_HASH = "div.well > table:nth-child(3) > tbody > tr:nth-child(5) > td:nth-child(2) > p"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        info_hash_raw = H.own_text(H.select_first(soup, cls.INFO_HASH))
        if not info_hash_raw:
            return None
        info_hash = info_hash_raw.lower().strip()
        magnet_uri = utils.create_magnet_uri(info_hash)
        size = H.own_text(H.select_first(soup, cls.SIZE))

        last_checked = None
        last_checked_raw = H.own_text(H.select_first(soup, cls.LAST_CHECKED))
        if last_checked_raw and last_checked_raw.strip():
            date_part = ""
            for ch in last_checked_raw:
                if ch.isspace():
                    break
                date_part += ch
            if date_part:
                last_checked = utils.parse_date(date_part, "yyyy-MM-dd")

        return TorrentDetails(
            info_hash=info_hash,
            name=torrent_name,
            size=size,
            category=Category.OTHER,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
        )
