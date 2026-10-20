"""EpubLibre search provider — port of EpubLibre.kt."""

from __future__ import annotations

from typing import Any, Optional

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


class EpubLibre(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "epublibre"
    name = "EpubLibre"
    url = "https://epublibre.org"
    supported_categories = {Category.BOOKS}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _EpubLibreResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/catalogo/index/0/nuevo/todos/sin/todos/{query}/ajax"
        response_json = await self.network.post_json(
            url=request_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if response_json is None:
            return []
        return self._results_page_parser.parse_json(response_json)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/catalogo/index/0/nuevo/novedades/sin/todos/--/ajax"
        response_json = await self.network.post_json(
            url=request_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if response_json is None:
            return []
        return self._results_page_parser.parse_json(response_json)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/catalogo/index/0/valorado/novedades/sin/todos/--/ajax"
        response_json = await self.network.post_json(
            url=request_url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        if response_json is None:
            return []
        return self._results_page_parser.parse_json(response_json)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _EpubLibreDetailsPageParser.extract_magnet_uri(details_page_html)
        if not magnet:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        details_page_html = await self.network.get_text(details_page_url)
        return _EpubLibreDetailsPageParser.parse(details_page_html)


class _EpubLibreResultsPageParser:
    LIST_ITEM = "div.span2"
    BOOK_NAME = "a#stk > div.texto-portada > h1"
    BOOK_AUTHOR = "a#stk > div.texto-portada > h2"
    DETAILS_PAGE_URL = "a#stk"

    def __init__(self, provider_id: str, provider_name: str):
        self._provider_id = provider_id
        self._provider_name = provider_name

    def parse_json(self, json_data: Any) -> list[Torrent]:
        if not isinstance(json_data, dict):
            return []
        html = json_data.get("contenido")
        if not isinstance(html, str):
            return []
        soup = H.parse_html(html)
        return [
            t
            for item in H.select(soup, self.LIST_ITEM)
            if (t := self._parse_list_item(item)) is not None
        ]

    def _parse_list_item(self, list_item: Tag) -> Optional[Torrent]:
        book_name = H.own_text(H.select_first(list_item, self.BOOK_NAME))
        if not book_name:
            return None
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "href"
        )
        if not details_page_url:
            return None

        book_remote_id = ""
        for ch in reversed(details_page_url):
            if ch == "/":
                break
            book_remote_id = ch + book_remote_id
        source_id = book_remote_id if book_remote_id.strip() else details_page_url
        torrent_id = utils.create_torrent_id(self._provider_id, source_id)

        torrent_name = book_name
        author_name = H.own_text(H.select_first(list_item, self.BOOK_AUTHOR))
        if author_name:
            torrent_name = f"{book_name} - {author_name}"

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            provider_name=self._provider_name,
            category=Category.BOOKS,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _EpubLibreDetailsPageParser:
    BOOK_NAME = "div#titulo_libro"
    POSTER_URL = "img#portada"
    DESCRIPTION = "div.detalle"
    MAGNET_URI = "a#en_desc"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)
        book_name = H.text(H.select_first(soup, cls.BOOK_NAME))
        if not book_name:
            return None
        magnet_uri = H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
        if not magnet_uri:
            return None
        poster_url = H.attr(H.select_first(soup, cls.POSTER_URL), "src")
        desc_el = H.select_first(soup, cls.DESCRIPTION)
        description = desc_el.decode_contents() if desc_el is not None else None

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            name=book_name,
            category=Category.BOOKS,
            magnet_uri=magnet_uri,
            poster_url=poster_url,
            description=description,
        )

    @classmethod
    def extract_magnet_uri(cls, details_page_html: str) -> Optional[str]:
        soup = H.parse_html(details_page_html)
        return H.attr(H.select_first(soup, cls.MAGNET_URI), "href")
