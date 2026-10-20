"""Mikan search provider — port of Mikan.kt."""

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


class Mikan(SearchProvider, TorrentDetailsProvider):
    id = "mikanproject"
    name = "Mikan"
    url = "https://mikanani.me"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _MikanResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/Home/Search?searchstr={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _MikanDetailsPageParser.parse(response_html, details_page_url)


class _MikanResultsPageParser:
    LIST_ITEM = "tr.js-search-results-row"
    TORRENT_NAME = "td:nth-child(2) > a:nth-child(1)"
    SIZE = "td:nth-child(3)"
    UPLOAD_DATE = "td:nth-child(4)"
    MAGNET_URI = "td:nth-child(2) > a[data-clipboard-text]"
    FILE_DOWNLOAD_LINK = "td:nth-child(5) > a"
    DETAILS_PAGE_URL = TORRENT_NAME

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
        name_el = H.select_first(list_item, self.TORRENT_NAME)
        torrent_name = H.own_text(name_el)
        if not torrent_name:
            return None
        magnet_uri = H.attr(
            H.select_first(list_item, self.MAGNET_URI), "data-clipboard-text"
        )
        if not magnet_uri:
            return None
        torrent_id = utils.create_torrent_id(
            self._provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        size_raw = H.own_text(H.select_first(list_item, self.SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None

        upload_date = None
        date_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if date_raw:
            upload_date = utils.parse_date(date_raw, "yyyy/MM/dd HH:mm")

        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(name_el, "abs:href", page_url)

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.ANIME,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _MikanDetailsPageParser:
    TORRENT_NAME = "p.episode-title"
    TORRENT_INFO = "p.bangumi-info"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = 'a[href^="/Download/"]'
    DESCRIPTION = "div.episode-desc"
    SIZE_PREFIX = "文件大小："
    UPLOAD_DATE_PREFIX = "发布日期："

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None

        infos = [
            t
            for el in H.select(soup, cls.TORRENT_INFO)
            if (t := H.own_text(el)) is not None
        ]

        size = None
        for info in infos:
            if info.startswith(cls.SIZE_PREFIX):
                size = utils.normalize_size(info.removeprefix(cls.SIZE_PREFIX).strip())
                break

        upload_date = None
        for info in infos:
            if info.startswith(cls.UPLOAD_DATE_PREFIX):
                date_raw = info.removeprefix(cls.UPLOAD_DATE_PREFIX).rstrip()
                upload_date = utils.parse_date(date_raw, "yyyy/MM/dd HH:mm")
                break

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.ANIME,
            magnet_uri=magnet_uri,
            description=description,
            file_download_link=file_download_link,
        )
