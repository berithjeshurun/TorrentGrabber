"""BTDigg search provider — port of BTDigg.kt."""

from __future__ import annotations

from typing import Optional

from bs4 import Tag

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


def _select_first_contains_own(soup: Tag, tag: str, needle: str) -> Optional[Tag]:
    for el in soup.find_all(tag):
        ot = H.own_text(el)
        if ot is not None and needle in ot:
            return el
    return None


class BTDigg(SearchProvider, TorrentDetailsProvider):
    id = "btdigg"
    name = "BTDigg"
    url = "https://btdig.com"
    supported_categories = {Category.OTHER}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _BTDiggResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"https://btdig.com/search?q={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _BTDiggDetailsPageParser.parse(response_html)


class _BTDiggResultsPageParser:
    LIST_ITEM = "div.one_result > div"
    TORRENT_NAME = "div.torrent_name > a"
    SIZE = "span.torrent_size"
    UPLOAD_DATE = "span.torrent_age"
    MAGNET_URI = "div.torrent_magnet > div.fa-magnet > a"
    DETAILS_PAGE_URL = TORRENT_NAME

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
        torrent_name = H.text(H.select_first(list_item, self.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, self.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        torrent_id = utils.create_torrent_id(
            self._provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        size = H.own_text(H.select_first(list_item, self.SIZE))
        upload_date = None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if upload_raw:
            prefix = "found "
            if upload_raw.startswith(prefix):
                upload_raw = upload_raw[len(prefix) :]
            upload_date = utils.try_parse_relative(upload_raw)

        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            details_page_url=details_page_url,
            provider_name=self._provider_name,
            magnet_uri=MagnetAvailable(value=magnet_uri),
        )


class _BTDiggDetailsPageParser:
    TORRENT_NAME_NEEDLE = "Name:"
    SIZE_NEEDLE = "Size:"
    UPLOAD_DATE_NEEDLE = "Age:"
    MAGNET_URI = 'a[href^="magnet:?xt="]'

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        name_td = _select_first_contains_own(soup, "td", cls.TORRENT_NAME_NEEDLE)
        if name_td is None:
            return None
        name_sib = name_td.find_next_sibling()
        torrent_name = H.own_text(name_sib) if isinstance(name_sib, Tag) else None
        if not torrent_name:
            return None

        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size = None
        size_td = _select_first_contains_own(soup, "td", cls.SIZE_NEEDLE)
        if size_td is not None:
            size_sib = size_td.find_next_sibling()
            if isinstance(size_sib, Tag):
                size = H.own_text(size_sib)

        upload_date = None
        age_td = _select_first_contains_own(soup, "td", cls.UPLOAD_DATE_NEEDLE)
        if age_td is not None:
            age_sib = age_td.find_next_sibling()
            if isinstance(age_sib, Tag):
                age_raw = H.own_text(age_sib)
                if age_raw:
                    upload_date = utils.try_parse_relative(f"{age_raw} ago")

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            magnet_uri=magnet_uri,
        )
