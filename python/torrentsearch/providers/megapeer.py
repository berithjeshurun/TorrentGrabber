"""MegaPeer search provider — port of MegaPeer.kt."""

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


def _uint(s: Optional[str]) -> Optional[int]:
    if s is None:
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


def _find_td_contains_own(root, text: str):
    """Jsoup td:containsOwn(text) equivalent."""
    for el in root.find_all("td"):
        own = "".join(el.find_all(string=True, recursive=False))
        if text.lower() in own.lower():
            return el
    return None


def _category_from_id(cat_id: str) -> Category:
    if cat_id in ("80", "79"):
        return Category.MOVIES
    if cat_id in ("5", "6", "55", "21"):
        return Category.SERIES
    if cat_id == "94":
        return Category.MUSIC
    if cat_id == "28":
        return Category.GAMES
    if cat_id == "29":
        return Category.APPS
    if cat_id == "52":
        return Category.BOOKS
    return Category.OTHER


class MegaPeer(
    SearchProvider,
    MagnetUriProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
):
    id = "megapeer"
    name = "MegaPeer"
    url = "https://megapeer.vip"
    supported_categories = {
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

    _CATEGORY_MAP = {
        Category.ALL: 0,
        Category.APPS: 29,
        Category.BOOKS: 52,
        Category.GAMES: 28,
        Category.MOVIES: 80,
        Category.MUSIC: 94,
        Category.OTHER: 59,
        Category.SERIES: 6,
    }

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _MegaPeerResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._CATEGORY_MAP.get(category, 0)
        request_url = (
            f"{self.url}/browse.php?search={query}&age=&cat={category_id}"
            f"&stype=0&sort=0&ascdesc=0"
        )
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _MegaPeersDetailsPageParser.parse(response_html, details_page_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _MegaPeersDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        response_html = await self.network.get_text(self.url)
        return self._results_parser.parse(response_html, self.url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)


class _MegaPeerResultsPageParser:
    LIST_ITEM = "div#index > table > tbody > tr.table_fon"
    TORRENT_NAME = "td:nth-child(2) > a:nth-child(2)"
    SIZE = "td:nth-child(3)"
    SEEDERS = "td:nth-child(4) > font:nth-child(2)"
    PEERS = "td:nth-child(4) > font:nth-child(4)"
    FILE_DOWNLOAD_LINK = "td:nth-child(2) > a:nth-child(1)"
    DETAILS_PAGE_URL = TORRENT_NAME
    UPLOAD_DATE = "td:nth-child(1)"

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
        name_el = H.select_first(list_item, self.DETAILS_PAGE_URL)
        details_page_url = H.attr(name_el, "abs:href", page_url)
        if not details_page_url:
            return None
        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)
        torrent_name = H.own_text(name_el)
        if not torrent_name:
            return None

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        date_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if date_raw:
            try:
                converted = utils.convert_russian_month_to_english(
                    date_raw.replace("Мая", "Май")
                )
                upload_date = utils.parse_date(converted, "d MMM yy")
            except (KeyError, ValueError, IndexError):
                upload_date = None

        file_download_link = H.attr(
            H.select_first(list_item, self.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            file_download_link=file_download_link,
            details_page_url=details_page_url,
        )


class _MegaPeersDetailsPageParser:
    TORRENT_NAME = "h1"
    MAGNET_URI = 'a[href^="magnet:?xt="]'
    FILE_DOWNLOAD_LINK = 'a[href^="/download/"]'
    DESCRIPTION = "table#details > tbody > tr:nth-child(1) > td:nth-child(2)"
    POSTER_URL = "table#details > tbody > tr:nth-child(1) > td:nth-child(2) > img"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)

        torrent_name = H.text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None

        size = None
        size_td = _find_td_contains_own(soup, "Размер")
        if size_td is not None:
            sibling = size_td.find_next_sibling("td")
            if sibling is not None:
                size_raw = H.own_text(sibling) or ""
                size = size_raw.split("(")[0].strip() or None

        seeders = None
        seeders_td = _find_td_contains_own(soup, "Раздают")
        if seeders_td is not None:
            sibling = seeders_td.find_next_sibling("td")
            if sibling is not None:
                seeders = _uint(H.own_text(sibling))

        peers = None
        peers_td = _find_td_contains_own(soup, "Качают")
        if peers_td is not None:
            sibling = peers_td.find_next_sibling("td")
            if sibling is not None:
                peers = _uint(H.own_text(sibling))

        category = None
        category_td = _find_td_contains_own(soup, "Категория")
        if category_td is not None:
            sibling = category_td.find_next_sibling("td")
            if sibling is not None:
                cat_link = H.select_first(sibling, "a")
                href = H.attr(cat_link, "href")
                if href:
                    category = _category_from_id(href.removeprefix("/cat/"))

        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "src")

        description_element = H.select_first(soup, cls.DESCRIPTION)
        screenshot_urls = (
            cls._extract_screenshots(description_element)
            if description_element is not None
            else []
        )

        description = None
        if description_element is not None:
            # Remove poster
            img = H.select_first(description_element, "img")
            if img is not None:
                img.decompose()
            # Remove screenshots and unsupported tags
            for wrap in description_element.select("div.sp-wrap"):
                wrap.decompose()
            description = description_element.decode_contents()

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            category=category,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
            screenshot_urls=screenshot_urls or [],
        )

    @staticmethod
    def _extract_screenshots(description) -> Optional[list[str]]:
        head = None
        for el in description.select("div.sp-head"):
            own = "".join(el.find_all(string=True, recursive=False))
            if "Скриншоты".lower() in own.lower():
                head = el
                break
        if head is None:
            return None
        sibling = head.find_next_sibling()
        if sibling is None:
            return None
        urls: list[str] = []
        for img in sibling.select("img.lazy-screenshot"):
            src = img.get("data-src")
            if src and str(src).strip():
                urls.append(str(src))
        return urls

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
