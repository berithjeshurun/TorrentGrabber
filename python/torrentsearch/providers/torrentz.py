"""Torrentz search provider — port of Torrentz.kt."""

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


class Torrentz(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "torrentz"
    name = "Torrentz2"
    url = "https://torrentz2.nz"
    supported_categories = {
        Category.OTHER,
        Category.MOVIES,
        Category.SERIES,
        Category.ANIME,
        Category.APPS,
        Category.GAMES,
        Category.MUSIC,
        Category.BOOKS,
        Category.PORN,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    _CATEGORY_MAP = {
        Category.OTHER: 1,
        Category.MOVIES: 2,
        Category.SERIES: 3,
        Category.ANIME: 4,
        Category.APPS: 5,
        Category.GAMES: 6,
        Category.MUSIC: 7,
        Category.BOOKS: 9,
        Category.PORN: 10,
    }

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _TorrentzResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?q={query}"
        if category is not Category.ALL:
            category_id = self._CATEGORY_MAP.get(category)
            if category_id is not None:
                request_url += f"&category={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/latest"
        if category is not Category.ALL:
            category_id = self._CATEGORY_MAP.get(category)
            if category_id is not None:
                request_url += f"?category={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/trending"
        if category is not Category.ALL:
            category_id = self._CATEGORY_MAP.get(category)
            if category_id is not None:
                request_url += f"?category={category_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _TorrentzDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TorrentzDetailsPageParser.parse(
            html=response_html, page_url=details_page_url
        )


class _TorrentzResultsPageParser:
    LIST_ITEM = "div.results > dl"
    TORRENT_NAME = "dt > a"
    SIZE = "dd > span.s"
    SEEDERS = "dd > span.u"
    PEERS = "dd > span.d"
    UPLOAD_DATE = "dd > span.a > span"
    CATEGORY = "dt"
    DETAILS_PAGE_URL = "dt > a"

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
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None
        name = H.own_text(H.select_first(list_item, self.TORRENT_NAME))
        if not name:
            return None

        torrent_remote_id = details_page_url.rsplit("/", 1)[-1]
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        size = H.own_text(H.select_first(list_item, self.SIZE))
        seeders = _to_uint(H.own_text(H.select_first(list_item, self.SEEDERS)))
        peers = _to_uint(H.own_text(H.select_first(list_item, self.PEERS)))

        upload_date = None
        upload_el = H.select_first(list_item, self.UPLOAD_DATE)
        raw_date = H.attr(upload_el, "title")
        if raw_date:
            try:
                upload_date = utils.parse_iso(raw_date)
            except ValueError:
                # dropLastWhile { it != '(' }.dropLast(2).dropLastWhile { !it.isWhitespace() }.trim()
                s = raw_date
                while s and s[-1] != "(":
                    s = s[:-1]
                s = s[:-2] if len(s) >= 2 else ""
                while s and not s[-1].isspace():
                    s = s[:-1]
                s = s.strip()
                if s:
                    upload_date = utils.parse_date(s, "E MMM dd yyyy HH:mm:ss")

        category_raw = H.own_text(H.select_first(list_item, self.CATEGORY))
        category = None
        if category_raw is not None:
            category = self._category_from_raw(category_raw.strip().removeprefix("» "))

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )

    @staticmethod
    def _category_from_raw(raw: str) -> Category:
        if raw.startswith("Anime"):
            return Category.ANIME
        if raw.startswith("Other"):
            return Category.OTHER
        if raw.startswith("Softwares"):
            return Category.APPS
        if raw.startswith("Music"):
            return Category.MUSIC
        if raw.startswith("Games"):
            return Category.GAMES
        if raw == "Movies":
            return Category.MOVIES
        if raw == "TV":
            return Category.SERIES
        return Category.OTHER


class _TorrentzDetailsPageParser:
    TORRENT_NAME = "div.download > h2"
    SIZE_LABEL = "Size"
    SEEDERS_LABEL = "Seeds"
    PEERS_LABEL = "Leechers"
    UPLOAD_DATE_LABEL = "Added"
    CATEGORY_ID = "div.download > p:last-child > a"
    MAGNET_URI = 'div.download a[href^="magnet:?"]'
    FILE_DOWNLOAD_LINK = 'div.download a[href^="/dowload/torrent/"]'

    @classmethod
    def parse(cls, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None

        size = H.own_text(cls._td_contains_own(soup, cls.SIZE_LABEL))
        seeders = _to_uint(
            H.own_text(cls._td_contains_own(soup, cls.SEEDERS_LABEL))
        )
        peers = _to_uint(H.own_text(cls._td_contains_own(soup, cls.PEERS_LABEL)))

        upload_raw = H.own_text(cls._td_contains_own(soup, cls.UPLOAD_DATE_LABEL))
        upload_date = (
            utils.parse_date(upload_raw, "M/d/yyyy") if upload_raw else None
        )

        category = None
        category_href = H.attr(H.select_first(soup, cls.CATEGORY_ID), "href")
        if category_href:
            category_id_raw = category_href.rsplit("=", 1)[-1]
            try:
                category = cls._category_from_id(int(category_id_raw))
            except ValueError:
                category = None

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
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )

    @staticmethod
    def _td_contains_own(soup: Tag, text: str) -> Optional[Tag]:
        """Jsoup td:containsOwn(text) → nextElementSibling() target."""
        needle = text.lower()
        for td in soup.select("td"):
            own = H.own_text(td) or ""
            if needle in own.lower():
                sibling = td.find_next_sibling()
                return sibling if isinstance(sibling, Tag) else None
        return None

    @staticmethod
    def _category_from_id(cid: int) -> Category:
        mapping = {
            1: Category.OTHER,
            2: Category.MOVIES,
            3: Category.SERIES,
            4: Category.ANIME,
            5: Category.APPS,
            6: Category.GAMES,
            7: Category.MUSIC,
            9: Category.BOOKS,
            10: Category.PORN,
        }
        return mapping.get(cid, Category.OTHER)

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")


def _to_uint(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
