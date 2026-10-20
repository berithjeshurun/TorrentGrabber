"""TheRarBg search provider — port of TheRarBg.kt."""

from __future__ import annotations

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
    LatestTorrentsProvider,
    MagnetUriProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

LIST_ITEM = "table > tbody > tr.list-entry"
NAME = "td.cellName > div > a"
SIZE = "td.sizeCell"
SEEDERS = "td:nth-child(7)"
PEERS = "td:nth-child(8)"
UPLOAD_DATE = "td:nth-child(4)"
CATEGORY = "td:nth-child(3) > a"
DETAILS_PAGE_URL = NAME

_D_NAME = "div.postContL > h4:has(+ div.table-responsive)"
_D_MAGNET_URI = 'a[href^="magnet:?"]'


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _category_from_raw_string(raw: str) -> Category:
    return {
        "Anime": Category.ANIME,
        "Apps": Category.APPS,
        "Books": Category.BOOKS,
        "Games": Category.GAMES,
        "Movies": Category.MOVIES,
        "Music": Category.MUSIC,
        "XXX": Category.PORN,
        "Tv": Category.SERIES,
        "Other": Category.OTHER,
    }.get(raw, Category.OTHER)


def _normalize_upload_date(date: str) -> str:
    date = date.replace("a.m.", "AM").replace("p.m.", "PM")
    parts = date.split(" ", 4)
    if len(parts) < 5:
        return date
    month, day, year, time, am_pm = parts
    fixed_time = time if ":" in time else f"{time}:00"
    fixed_month = (
        month.replace("Jan.", "Jan")
        .replace("Feb.", "Feb")
        .replace("March", "Mar")
        .replace("April", "Apr")
        .replace("May", "May")
        .replace("June", "Jun")
        .replace("July", "Jul")
        .replace("Aug.", "Aug")
        .replace("Sept.", "Sep")
        .replace("Oct.", "Oct")
        .replace("Nov.", "Nov")
        .replace("Dec.", "Dec")
    )
    return f"{fixed_month} {day} {year} {fixed_time} {am_pm}"


class TheRarBg(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "therarbag"
    name = "RARBG"
    url = "https://therarbg.com"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
        Category.OTHER,
    }
    safety = SearchProviderSafety.UNSAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _TheRarBgResultsPageParser(self.id, self.name)

    def _category_name(self, raw: Category) -> str:
        return {
            Category.ALL: "",
            Category.ANIME: "Anime",
            Category.APPS: "Apps",
            Category.BOOKS: "Books",
            Category.GAMES: "Games",
            Category.MOVIES: "Movies",
            Category.MUSIC: "Music",
            Category.PORN: "XXX",
            Category.SERIES: "Tv",
            Category.OTHER: "Other",
        }.get(raw, "")

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/get-posts/keywords:{query}"
        if category is not Category.ALL:
            request_url = f"{request_url}:category:{self._category_name(category)}"
        result_page_html = await self.network.get_text(request_url)
        return self._results_parser.parse(result_page_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TheRarBgDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/get-posts/time:2D"
        if category is not Category.ALL:
            request_url = f"{request_url}:category:{self._category_name(category)}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/get-posts/time:2D:order:-se"
        if category is not Category.ALL:
            request_url = f"{request_url}:category:{self._category_name(category)}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _TheRarBgDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _TheRarBgResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(dom, LIST_ITEM)
            if (t := self.parse_list_item(item, page_url)) is not None
        ]

    def parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        torrent_id = utils.create_torrent_id(self.provider_id, details_page_url)
        name = H.own_text(H.select_first(list_item, NAME))
        if not name:
            return None
        size = None
        size_el = H.select_first(list_item, SIZE)
        if size_el is not None:
            order = size_el.get("data-order")
            if order is not None:
                size = utils.format_bytes(str(order))
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        upload_date = None
        upload_el = H.select_first(list_item, UPLOAD_DATE)
        if upload_el is not None:
            order = upload_el.get("data-order")
            if order is not None:
                try:
                    upload_date = utils.epoch_second_to_datetime(int(str(order)))
                except ValueError:
                    pass
        cat_raw = H.own_text(H.select_first(list_item, CATEGORY))
        category = _category_from_raw_string(cat_raw) if cat_raw else None

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.provider_name,
            upload_date=upload_date,
            category=category,
            magnet_uri=MagnetRequiresFetch(details_page_url),
            details_page_url=details_page_url,
        )


class _TheRarBgDetailsPageParser:
    @staticmethod
    def parse(html: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html)

        name = H.own_text(H.select_first(dom, _D_NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)

        detail_rows: dict = {}
        for tr in H.select(dom, "table.detailTable > tbody > tr"):
            label = H.own_text(H.select_first(tr, "th"))
            value = H.select_first(tr, "td")
            if label and value is not None:
                detail_rows[label] = value

        size = H.own_text(detail_rows.get("Size:"))
        seeders = None
        peers = None
        peers_raw = H.own_text(detail_rows.get("Peers:"))
        if peers_raw:
            seeders_peers = peers_raw.strip().split(",")
            if seeders_peers:
                seeders = _to_int(seeders_peers[0].removeprefix("Seeders: "))
            if len(seeders_peers) > 1:
                peers = _to_int(seeders_peers[-1].strip().removeprefix("Leechers: "))

        upload_date = None
        added = H.own_text(detail_rows.get("Added:"))
        if added:
            upload_date = utils.parse_date(
                _normalize_upload_date(added), "MMM d, yyyy, h:mm a"
            )

        cat_el = detail_rows.get("Category:")
        category = _category_from_raw_string(H.text(cat_el) or "") if cat_el else None
        uploader = H.text(detail_rows.get("Uploader:"))
        desc_el = detail_rows.get("Description:")
        description = desc_el.get_text() if desc_el is not None else None

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
            description=description,
        )

    @staticmethod
    def extract_magnet_uri(details_page_html: str) -> Optional[str]:
        dom = H.parse_html(details_page_html)
        return H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
