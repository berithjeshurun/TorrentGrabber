"""TorrentDownloads search provider — port of TorrentDownloads.kt."""

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
from ..provider import (
    LatestTorrentsProvider,
    MagnetUriProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)


class TorrentDownloads(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "torrentdownloads"
    name = "TorrentDownloads"
    url = "https://torrentdownloads.pro"
    cloudflare_solver_url = f"{url}/search/?s_cat=0&search=ubuntu"
    safety = SearchProviderSafety.SAFE
    supported_categories = {
        Category.ANIME,
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
        Category.OTHER,
    }
    is_cloudflare_protected = True
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _TorrentDownloadsResultsPageParser(
            self.id, self.name
        )

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._get_category_id(category)
        request_url = f"{self.url}/search/?s_cat={category_id}&search={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    def _get_category_id(self, category: Category) -> int:
        if category is Category.ALL:
            return 0
        if category is Category.ANIME:
            return 1
        if category is Category.APPS:
            return 7
        if category is Category.BOOKS:
            return 2
        if category is Category.GAMES:
            return 3
        if category is Category.MOVIES:
            return 4
        if category is Category.MUSIC:
            return 5
        if category is Category.SERIES:
            return 8
        if category in {Category.PORN, Category.OTHER}:
            return 9
        return 9

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        if category is Category.ANIME:
            request_url = f"{self.url}/view/today/Anime.html"
        elif category is Category.APPS:
            request_url = f"{self.url}/view/today/Software.html"
        elif category is Category.BOOKS:
            request_url = f"{self.url}/view/today/Books.html"
        elif category is Category.GAMES:
            request_url = f"{self.url}/view/today/Games.html"
        elif category is Category.MOVIES:
            request_url = f"{self.url}/view/today/Movies.html"
        elif category is Category.MUSIC:
            request_url = f"{self.url}/view/today/Music.html"
        elif category is Category.SERIES:
            request_url = f"{self.url}/view/today/TV_Shows.html"
        elif category is Category.OTHER:
            request_url = f"{self.url}/view/today/Other.html"
        else:
            request_url = f"{self.url}/most-active"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        if category is Category.ANIME:
            request_url = f"{self.url}/view/popular/Anime.html"
        elif category is Category.APPS:
            request_url = f"{self.url}/view/popular/Software.html"
        elif category is Category.BOOKS:
            request_url = f"{self.url}/view/popular/Books.html"
        elif category is Category.GAMES:
            request_url = f"{self.url}/view/popular/Games.html"
        elif category is Category.MOVIES:
            request_url = f"{self.url}/view/popular/Movies.html"
        elif category is Category.MUSIC:
            request_url = f"{self.url}/view/popular/Music.html"
        elif category is Category.SERIES:
            request_url = f"{self.url}/view/popular/TV_Shows.html"
        elif category is Category.OTHER:
            request_url = f"{self.url}/view/popular/Other.html"
        else:
            request_url = f"{self.url}/most-seeded"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _TorrentDownloadsDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TorrentDownloadsDetailsPageParser.parse(
            html=response_html, page_url=details_page_url
        )


class _TorrentDownloadsResultsPageParser:
    LIST_ITEM_CONTAINER = "div.inner_container"
    LIST_ITEM = "div.grey_bar3"
    TORRENT_NAME = "p:nth-child(1) > a:nth-child(2)"
    SIZE = "span:nth-child(5)"
    SEEDERS = "span:nth-child(4)"
    PEERS = "span:nth-child(3)"
    CATEGORY = "p:nth-child(1) > img:nth-child(1)"
    DETAILS_PAGE_URL = TORRENT_NAME

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        soup = H.parse_html(html, page_url)
        containers = H.select(soup, self.LIST_ITEM_CONTAINER)
        if not containers:
            return []
        last = containers[-1]
        items = H.select(last, self.LIST_ITEM)[2:]
        return [
            t
            for item in items
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item: Tag, page_url: str) -> Optional[Torrent]:
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        name = H.own_text(H.select_first(list_item, self.TORRENT_NAME))
        if not name:
            return None

        torrent_id = utils.create_torrent_id(self._provider_id, details_page_url)
        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(list_item, self.PEERS)))
        category_src = H.attr(H.select_first(list_item, self.CATEGORY), "src")
        category = (
            get_category_from_category_icon_url(category_src)
            if category_src
            else None
        )

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self._provider_name,
            category=category,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _TorrentDownloadsDetailsPageParser:
    TORRENT_NAME = "div.inner_container > h1.titl_1 > span"
    SIZE = "div.inner_container > div:nth-child(13) > p"
    SEEDERS = "div.inner_container > div:nth-child(15) > p"
    PEERS = "div.inner_container > div:nth-child(16) > p"
    UPLOAD_DATE = "div.inner_container > div:nth-child(19) > p"
    CATEGORY = "div.inner_container > h1:nth-child(1) > img"
    LAST_CHECKED = "div.inner_container > div:nth-child(18) > p"
    MAGNET_URI = 'a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = "u.download > li:nth-child(2) > a"
    DATE_FORMAT = "uuuu-MM-dd HH:mm:ss"

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)
        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        size = H.own_text(H.select_first(soup, cls.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(soup, cls.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(soup, cls.PEERS)))

        upload_raw = H.own_text(H.select_first(soup, cls.UPLOAD_DATE))
        upload_date = None
        if upload_raw is not None:
            upload_date = utils.parse_date(upload_raw.strip(), cls.DATE_FORMAT)

        category_src = H.attr(H.select_first(soup, cls.CATEGORY), "src")
        category = (
            get_category_from_category_icon_url(category_src)
            if category_src
            else None
        )

        last_checked_raw = H.own_text(H.select_first(soup, cls.LAST_CHECKED))
        last_checked = None
        if last_checked_raw is not None:
            cleaned = last_checked_raw.removesuffix(" ()").strip()
            last_checked = utils.parse_date(cleaned, cls.DATE_FORMAT)

        file_download_link = H.attr(
            H.select_first(soup, cls.FILE_DOWNLOAD_LINK), "abs:href", page_url
        )

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")


def get_category_from_category_icon_url(url: str) -> Category:
    key = url.removeprefix("/templates/new/images/icons/")
    mapping = {
        "menu_icon0.png": Category.ALL,
        "menu_icon1.png": Category.ANIME,
        "menu_icon2.png": Category.BOOKS,
        "menu_icon3.png": Category.GAMES,
        "menu_icon4.png": Category.MOVIES,
        "menu_icon5.png": Category.MUSIC,
        "menu_icon7.png": Category.APPS,
        "menu_icon8.png": Category.SERIES,
        "menu_icon9.png": Category.OTHER,
    }
    return mapping.get(key, Category.OTHER)


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
