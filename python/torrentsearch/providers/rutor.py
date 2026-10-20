"""Rutor search provider — port of Rutor.kt."""

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

LIST_ITEM = "div#index > table > tbody > tr"
TORRENT_NAME = "td:nth-child(2) > a:nth-child(3)"
SIZE = "td:nth-last-child(2)"
SEEDERS = "td:nth-last-child(1) > span:nth-child(1)"
PEERS = "td:nth-last-child(1) > span:nth-child(3)"
UPLOAD_DATE = "td:nth-child(1)"
MAGNET_URI = "td:nth-child(2) > a:nth-child(2)"
FILE_DOWNLOAD_LINK = "td:nth-child(2) > a:nth-child(1)"
DETAILS_PAGE_URL = TORRENT_NAME

_D_TORRENT_NAME = "div#all > h1"
_D_DESCRIPTION = "table#details > tbody > tr:nth-child(1) > td:nth-child(2)"
_D_POSTER_URL = "table#details > tbody > tr:nth-child(1) > td:nth-child(2) > img"
_D_MAGNET_URI = "div#download > a:nth-child(1)"
_D_FILE_DOWNLOAD_LINK = "div#download > a:nth-child(2)"


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _get_category_from_raw(raw: str) -> Category:
    if raw in {"kino", "nashe_kino", "multiki"}:
        return Category.MOVIES
    if raw in {"seriali", "nashi_seriali", "tv"}:
        return Category.SERIES
    if raw == "anime":
        return Category.ANIME
    if raw == "audio":
        return Category.MUSIC
    if raw == "games":
        return Category.GAMES
    if raw == "soft":
        return Category.APPS
    if raw == "knigi":
        return Category.BOOKS
    return Category.OTHER


class Rutor(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
):
    id = "rutorinfo"
    name = "Rutor"
    url = "https://rutor.info"
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.ALL: 0,
            Category.ANIME: 10,
            Category.APPS: 9,
            Category.BOOKS: 11,
            Category.GAMES: 8,
            Category.MOVIES: 1,
            Category.MUSIC: 2,
            Category.OTHER: 3,
            Category.SERIES: 4,
        }
        self._results_parser = _RutorResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._category_map.get(category, self._category_map[Category.ALL])
        request_url = f"{self.url}/search/0/{category_id}/010/2/{query}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url, category)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_id = self._category_map.get(category, self._category_map[Category.ALL])
        request_url = f"{self.url}/browse/0/{category_id}/0/0"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url, category)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url, category)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _RutorDetailsPageParser.parse(response_html, details_page_url)


class _RutorResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(
        self, html: str, page_url: str, search_category: Category
    ) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        results: list[Torrent] = []
        seen: set[str] = set()
        for item in H.select(dom, LIST_ITEM)[1:]:
            torrent = self._parse_list_item(item, page_url, search_category)
            if torrent is not None and torrent.id not in seen:
                seen.add(torrent.id)
                results.append(torrent)
        return results

    def _parse_list_item(
        self, list_item, page_url: str, search_category: Category
    ) -> Optional[Torrent]:
        torrent_name = H.own_text(H.select_first(list_item, TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(list_item, MAGNET_URI), "href")
        if not magnet_uri:
            return None
        torrent_id = utils.create_torrent_id(
            self.provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )
        size = H.own_text(H.select_first(list_item, SIZE))
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        upload_date = None
        raw_date = H.own_text(H.select_first(list_item, UPLOAD_DATE))
        if raw_date:
            try:
                english = utils.convert_russian_month_to_english(raw_date)
                upload_date = utils.parse_date(english, "dd MMM yy")
            except (KeyError, ValueError):
                pass
        file_download_link = H.attr(
            H.select_first(list_item, FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(
            H.select_first(list_item, DETAILS_PAGE_URL), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=search_category if search_category is not Category.ALL else None,
            provider_name=self.provider_name,
            magnet_uri=MagnetAvailable(magnet_uri),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _RutorDetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        torrent_name = H.text(H.select_first(dom, _D_TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None

        infos: dict = {}
        for tr in H.select(dom, "table#details > tbody > tr:nth-child(n+2)"):
            header = H.own_text(H.select_first(tr, "td.header"))
            value = H.select_first(tr, "td:nth-child(2)")
            if header and value is not None:
                infos[header] = value

        size_el = infos.get("Размер")
        size = None
        if size_el is not None:
            raw = H.own_text(size_el) or ""
            size = raw.split("(", 1)[0].strip() or None

        seeders = _to_int(H.own_text(infos.get("Раздают")))
        peers = _to_int(H.own_text(infos.get("Качают")))

        upload_date = None
        added_el = infos.get("Добавлен")
        if added_el is not None:
            raw = (H.own_text(added_el) or "").split("(", 1)[0].strip()
            if raw:
                upload_date = utils.parse_date(raw, "dd-MM-yyyy H:m:s")

        category = None
        cat_el = infos.get("Категория")
        if cat_el is not None:
            href = H.attr(H.select_first(cat_el, "a"), "href") or ""
            # Kotlin: substringAfter('/')
            raw = href.split("/", 1)[1] if "/" in href else href
            category = _get_category_from_raw(raw)

        uploader = H.text(infos.get("Залил"))

        last_checked = None
        checked_el = infos.get("Сидер замечен")
        if checked_el is not None:
            raw = (H.own_text(checked_el) or "").split("(", 1)[0].strip()
            if raw:
                last_checked = utils.parse_date(raw, "dd-MM-yyyy H:m:s")

        poster_url = H.attr(H.select_first(dom, _D_POSTER_URL), "src")
        description_element = H.select_first(dom, _D_DESCRIPTION)
        screenshot_urls = (
            _RutorDetailsPageParser._extract_screenshots(description_element)
            if description_element is not None
            else []
        )
        description = None
        if description_element is not None:
            img = H.select_first(description_element, "img")
            if img is not None:
                img.decompose()
            for wrap in H.select(description_element, "div.hidewrap"):
                wrap.decompose()
            description = description_element.decode_contents()

        file_download_link = H.attr(
            H.select_first(dom, _D_FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

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
            screenshot_urls=screenshot_urls or [],
        )

    @staticmethod
    def _extract_screenshots(description) -> Optional[list[str]]:
        for wrap in H.select(description, "div.hidewrap"):
            head = H.select_first(wrap, "div.hidehead")
            if H.own_text(head) != "Скриншоты":
                continue
            area = H.select_first(wrap, "textarea.hidearea")
            raw = H.own_text(area)
            if not raw:
                return None
            nested = H.parse_html(raw)
            return [
                src
                for img in H.select(nested, "a > img")
                if (src := H.attr(img, "src"))
            ]
        return None
