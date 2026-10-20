"""1337x search provider — port of ThirteenThirtySevenX.kt."""

from __future__ import annotations

import re
from typing import Optional

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
from ..provider import (
    MagnetUriProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

LIST_ITEM = "table.table-list > tbody > tr"
TORRENT_NAME = "td.name > a:nth-child(2)"
SIZE = "td.size"
SEEDERS = "td.seeds"
PEERS = "td.leeches"
UPLOAD_DATE = "td.coll-date"
CATEGORY = "td.name > a:nth-child(1)"
DETAILS_PAGE_URL = TORRENT_NAME

_CONTAINER = "div.box-info.torrent-detail-page"
_INFO_CONTAINER = f"{_CONTAINER} > div:nth-child(2) > div:nth-child(1)"
_INFO_COL_FIRST = f"{_INFO_CONTAINER} > ul:nth-child(2)"
_INFO_COL_SECOND = f"{_INFO_CONTAINER} > ul:nth-child(3)"
_D_TORRENT_NAME = f"{_CONTAINER} > div.box-info-heading > h1"
_D_CATEGORY = f"{_INFO_COL_FIRST} > li:nth-child(1) > span"
_D_SIZE = f"{_INFO_COL_FIRST} > li:nth-child(4) > span"
_D_UPLOADER = f"{_INFO_COL_FIRST} > li:nth-child(5) > span"
_D_LAST_CHECKED = f"{_INFO_COL_SECOND} > li:nth-child(2) > span"
_D_UPLOAD_DATE = f"{_INFO_COL_SECOND} > li:nth-child(3) > span"
_D_SEEDERS = f"{_INFO_COL_SECOND} > li:nth-child(4) > span"
_D_PEERS = f"{_INFO_COL_SECOND} > li:nth-child(5) > span"
_D_MAGNET_URI = 'a[href^="magnet:?"]'
_D_FILE_DOWNLOAD_LINK = 'a[href^="https://itorrents.org"]'
_D_DESCRIPTION = "div#description"
_D_POSTER_URL = "div.torrent-image > img"


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _get_category_from_id(cid: str) -> Category:
    if cid in {"28", "78", "79", "80", "81"}:
        return Category.ANIME
    if cid in {"22", "23", "24", "25", "26", "27", "53", "58", "59", "60", "68", "69"}:
        return Category.MUSIC
    if cid in {"1", "2", "3", "4", "42", "54", "55", "66", "70", "73", "76"}:
        return Category.MOVIES
    if cid in {"5", "6", "7", "41", "71", "74", "75", "9"}:
        return Category.SERIES
    if cid in {"18", "19", "20", "21", "56", "57"}:
        return Category.APPS
    if cid in {
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "43",
        "44",
        "45",
        "46",
        "72",
        "77",
        "82",
    }:
        return Category.GAMES
    if cid in {"48", "49", "50", "51", "67"}:
        return Category.PORN
    if cid in {"33", "34", "35", "36", "37", "38", "39", "40", "47", "52"}:
        return Category.OTHER
    return Category.OTHER


def _get_category_from_string(raw: str) -> Category:
    if raw == "Anime":
        return Category.ANIME
    if raw == "Apps":
        return Category.APPS
    if raw == "Games":
        return Category.GAMES
    if raw == "Movies":
        return Category.MOVIES
    if raw == "Music":
        return Category.MUSIC
    if raw == "Other":
        return Category.OTHER
    if raw == "XXX":
        return Category.PORN
    if raw in {"TV", "Documentaries"}:
        return Category.SERIES
    return Category.OTHER


def _parse_date(date: str):
    # Possible formats: 12:45am | 8pm Jun. 2nd | May. 27th '26
    normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date)
    normalized = re.sub(r"(?<=\d)(am|pm)\b", r" \1", normalized, flags=re.IGNORECASE)
    normalized = normalized.replace(".", "").replace("'", "")

    parsed = utils.parse_date(normalized, "MMM d yy")
    if parsed is not None:
        return parsed
    reconstructed = f"{normalized} {utils.get_current_year()}"
    parsed = utils.parse_date(reconstructed, "h a MMM d yyyy")
    if parsed is not None:
        return parsed
    return utils.try_parse_time(normalized)


class ThirteenThirtySevenX(
    SearchProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "1337x"
    name = "1337x"
    url = "https://1337x.to"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.PORN,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.ANIME: "Anime",
            Category.APPS: "Apps",
            Category.GAMES: "Games",
            Category.MOVIES: "Movies",
            Category.MUSIC: "Music",
            Category.OTHER: "Other",
            Category.PORN: "XXX",
            Category.SERIES: "TV",
        }
        self._results_parser = _ThirteenThirtySevenXResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        if category is Category.ALL:
            request_url = f"{self.url}/search/{query}/1/"
        else:
            category_string = self._category_map[category]
            request_url = f"{self.url}/category-search/{query}/{category_string}/1/"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top-100"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _ThirteenThirtySevenXDetailsPageParser.extract_magnet_uri(
            details_page_html
        )
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response = await self.network.get_text(details_page_url)
        return _ThirteenThirtySevenXDetailsPageParser.parse(response, details_page_url)


class _ThirteenThirtySevenXResultsPageParser:
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
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        torrent_id = utils.create_torrent_id(self.provider_id, details_page_url)
        torrent_name = H.own_text(H.select_first(list_item, TORRENT_NAME))
        if not torrent_name:
            return None
        size_raw = H.own_text(H.select_first(list_item, SIZE))
        size = size_raw.replace(",", "") if size_raw else None
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        upload_raw = H.own_text(H.select_first(list_item, UPLOAD_DATE))
        upload_date = _parse_date(upload_raw) if upload_raw else None
        category = None
        href = H.attr(H.select_first(list_item, CATEGORY), "href") or ""
        if href:
            cid = href.removeprefix("/sub/").split("/", 1)[0]
            category = _get_category_from_id(cid)

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            provider_name=self.provider_name,
            category=category,
            details_page_url=details_page_url,
            magnet_uri=MagnetRequiresFetch(details_page_url),
        )


class _ThirteenThirtySevenXDetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None
        size_raw = H.own_text(H.select_first(dom, _D_SIZE))
        size = size_raw.replace(",", "") if size_raw else None
        seeders = _to_int(H.own_text(H.select_first(dom, _D_SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(dom, _D_PEERS)))
        upload_raw = H.own_text(H.select_first(dom, _D_UPLOAD_DATE))
        upload_date = utils.try_parse_relative(upload_raw) if upload_raw else None
        cat_raw = H.own_text(H.select_first(dom, _D_CATEGORY))
        category = _get_category_from_string(cat_raw) if cat_raw else None
        uploader = H.text(H.select_first(dom, _D_UPLOADER))
        last_raw = H.own_text(H.select_first(dom, _D_LAST_CHECKED))
        last_checked = utils.try_parse_relative(last_raw) if last_raw else None
        file_download_link = H.attr(
            H.select_first(dom, _D_FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        description_el = H.select_first(dom, _D_DESCRIPTION)
        description = description_el.decode_contents() if description_el else None
        poster_url = H.attr(H.select_first(dom, _D_POSTER_URL), "abs:src", page_url)

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )

    @staticmethod
    def extract_magnet_uri(details_page_html: str) -> Optional[str]:
        dom = H.parse_html(details_page_html)
        return H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
