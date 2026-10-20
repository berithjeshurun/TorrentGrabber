"""ZeroMagnet search provider — port of ZeroMagnet.kt."""

from __future__ import annotations

from typing import Optional

from bs4 import Tag

from .. import htmlutil as H
from .. import utils
from ..models import (
    Category,
    MagnetRequiresFetch,
    SearchProviderSafety,
    Torrent,
    TorrentDetails,
)
from ..network import NetworkClient
from ..provider import MagnetUriProvider, SearchProvider, TorrentDetailsProvider


class ZeroMagnet(SearchProvider, MagnetUriProvider, TorrentDetailsProvider):
    id = "0magnet"
    name = "0Magnet"
    url = "https://9mag.net"
    supported_categories = {Category.PORN}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _ZeroMagnetResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        # https://9mag.net/search?q=tight
        request_url = f"{self.url}/search?q={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _ZeroMagnetDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _ZeroMagnetDetailsPageParser.parse(response_html)


class _ZeroMagnetResultsPageParser:
    LIST_ITEM = "table.file-list > tbody > tr"
    TORRENT_NAME = "td.result-title > a"
    SIZE = "td.result-meta > div:nth-child(1)"
    UPLOAD_DATE = "td.result-meta > div.result-date"
    DETAILS_PAGE_URL = "td:nth-child(1) > a"

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(soup, self.LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        name = H.text(H.select_first(list_item, self.TORRENT_NAME))
        if not name:
            return None
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)
        size_raw = H.own_text(H.select_first(list_item, self.SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "yyyy-MM-dd") if upload_raw else None
        )

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            upload_date=upload_date,
            provider_name=self._provider_name,
            category=Category.PORN,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _ZeroMagnetDetailsPageParser:
    TORRENT_NAME = "h2.magnet-title"
    SIZE = "dl.torrent-info > dd:nth-child(4)"
    UPLOAD_DATE = "dl.torrent-info > dd:nth-child(6)"
    MAGNET_URI = "input#input-magnet"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)
        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "value")
        if not magnet_uri:
            return None
        size = H.own_text(H.select_first(soup, cls.SIZE))
        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "yyyy-MM-dd HH:mm:ss")
            if upload_raw
            else None
        )

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            magnet_uri=magnet_uri,
            category=Category.PORN,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "value")
