"""Nyaa search provider — port of Nyaa.kt."""

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
from ..provider import (
    LatestTorrentsProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

LIST_ITEM = "table.torrent-list > tbody > tr"
TORRENT_NAME = "td:nth-child(2) > a:not(.comments)"
SIZE = "td:nth-child(4)"
SEEDERS = "td:nth-child(6)"
PEERS = "td:nth-child(7)"
UPLOAD_DATE = "td:nth-child(5)"
CATEGORY = "td:nth-child(1) > a"
MAGNET_URI = "td:nth-child(3) > a:nth-child(2)"
FILE_DOWNLOAD_LINK = "td:nth-child(3) > a:nth-child(1)"
DETAILS_PAGE_URL = TORRENT_NAME

_TORRENT_INFO_CARD = ".container > div:nth-child(1)"
_TORRENT_INFO_CARD_BODY = f"{_TORRENT_INFO_CARD} > div.panel-body"
_D_TORRENT_NAME = f"{_TORRENT_INFO_CARD} > div.panel-heading > h3"
_D_SIZE = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(4) > div:nth-child(2)"
_D_SEEDERS = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(2) > div:nth-child(4)"
_D_PEERS = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(3) > div:nth-child(4)"
_D_UPLOAD_DATE = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(1) > div:nth-child(4)"
_D_CATEGORY = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(1) > div:nth-child(2)"
_D_UPLOADER = f"{_TORRENT_INFO_CARD_BODY} > div:nth-child(2) > div:nth-child(2)"
_D_DESCRIPTION = "#torrent-description"
_D_MAGNET_URI = 'a[href^="magnet:"]'
_D_FILE_DOWNLOAD_LINK = 'a[href^="/download"]'


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _category_from_id(cid: str) -> Category:
    if cid in {"1_0", "1_1", "1_2", "1_3", "1_4"}:
        return Category.ANIME
    if cid in {"2_0", "2_1", "2_2"}:
        return Category.MUSIC
    if cid in {"3_0", "3_1", "3_2", "3_3"}:
        return Category.BOOKS
    if cid in {"4_0", "4_1", "4_2", "4_3", "4_4"}:
        return Category.SERIES
    if cid in {"5_0", "5_1", "5_2"}:
        return Category.OTHER
    if cid in {"6_0", "6_1"}:
        return Category.APPS
    if cid == "6_2":
        return Category.GAMES
    return Category.OTHER


class _NyaaResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(dom, LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        torrent_name = H.own_text(H.select_first(list_item, TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, MAGNET_URI), "href")
        if not magnet_uri:
            return None
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )
        file_download_link = H.attr(
            H.select_first(list_item, FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        torrent_remote_id = (
            details_page_url.rsplit("/", 1)[-1] if details_page_url else None
        )
        torrent_id = utils.create_torrent_id(
            self.provider_id,
            torrent_remote_id or utils.get_info_hash_from_magnet_uri(magnet_uri),
        )

        size = H.own_text(H.select_first(list_item, SIZE))
        upload_date_el = H.select_first(list_item, UPLOAD_DATE)
        upload_date = None
        if upload_date_el is not None:
            ts = upload_date_el.get("data-timestamp")
            if ts is not None:
                try:
                    upload_date = utils.epoch_second_to_datetime(int(str(ts)))
                except ValueError:
                    pass
        category = None
        cat_href = H.attr(H.select_first(list_item, CATEGORY), "href")
        if cat_href is not None:
            category = _category_from_id(cat_href.removeprefix("/?c="))
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.provider_name,
            upload_date=upload_date,
            category=category,
            magnet_uri=MagnetAvailable(magnet_uri),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


def _parse_details(html: str, page_url: str) -> Optional[TorrentDetails]:
    dom = H.parse_html(html, page_url)
    name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
    if not name:
        return None
    magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
    if not magnet_uri:
        return None
    info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)

    size = H.own_text(H.select_first(dom, _D_SIZE))
    seeders = _to_int(H.text(H.select_first(dom, _D_SEEDERS)))
    peers = _to_int(H.text(H.select_first(dom, _D_PEERS)))
    upload_date = None
    upload_el = H.select_first(dom, _D_UPLOAD_DATE)
    if upload_el is not None:
        ts = upload_el.get("data-timestamp")
        if ts is not None:
            try:
                upload_date = utils.epoch_second_to_datetime(int(str(ts)))
            except ValueError:
                pass

    category = None
    cat_el = H.select_first(dom, _D_CATEGORY)
    if cat_el is not None:
        category_id = H.attr(H.select_first(cat_el, "a:nth-child(1)"), "href")
        if category_id is not None:
            category_id = category_id.removeprefix("/?c=")
        sub_category_id = H.attr(H.select_first(cat_el, "a:nth-child(2)"), "href")
        if sub_category_id is not None:
            sub_category_id = sub_category_id.removeprefix("/?c=")
        if sub_category_id == "6_2":
            category = Category.GAMES
        elif category_id is not None:
            category = _category_from_id(category_id)

    uploader = H.text(H.select_first(dom, _D_UPLOADER))
    description_el = H.select_first(dom, _D_DESCRIPTION)
    description = description_el.decode_contents() if description_el else None
    file_download_link = H.attr(
        H.select_first(dom, _D_FILE_DOWNLOAD_LINK), "abs:href", page_url
    )

    return TorrentDetails(
        info_hash=info_hash,
        name=name,
        size=size,
        seeders=seeders,
        peers=peers,
        upload_date=upload_date,
        category=category,
        uploader=uploader,
        magnet_uri=magnet_uri,
        file_download_link=file_download_link,
        description=description,
    )


class Nyaa(SearchProvider, TorrentDetailsProvider, LatestTorrentsProvider, TopTorrentsProvider):
    id = "nyaasi"
    name = "Nyaa.si"
    url = "https://nyaa.si"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MUSIC,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True
    alternate_url_domains = ["https://nyaa.iss.ink"]

    _CATEGORY_MAP = {
        Category.ALL: "0_0",
        Category.ANIME: "1_0",
        Category.APPS: "6_1",
        Category.BOOKS: "3_0",
        Category.GAMES: "6_2",
        Category.MUSIC: "2_0",
        Category.SERIES: "4_0",
    }

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._parser = _NyaaResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(category, self._CATEGORY_MAP[Category.ALL])
        request_url = f"{self.url}/?f=0&c={category_id}&q={query}"
        response_html = await self.network.get_text(request_url)
        return self._parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _parse_details(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(category, self._CATEGORY_MAP[Category.ALL])
        request_url = f"{self.url}?c={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(category, self._CATEGORY_MAP[Category.ALL])
        request_url = f"{self.url}?s=seeders&o=desc&c={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._parser.parse(response_html, request_url)
