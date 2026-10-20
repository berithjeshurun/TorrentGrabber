"""BlueRoms search provider — port of BlueRoms.kt."""

from __future__ import annotations

import base64
from typing import Optional

from bs4 import NavigableString, Tag

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
from ..provider import MagnetUriProvider, SearchProvider, TorrentDetailsProvider


def _select_first_contains_own(soup: Tag, tag: str, needle: str) -> Optional[Tag]:
    for el in soup.find_all(tag):
        ot = H.own_text(el)
        if ot is not None and needle in ot:
            return el
    return None


async def _get_magnet_uri(
    download_page_url: str, network: NetworkClient
) -> Optional[str]:
    download_page_html = await network.get_text(download_page_url)
    soup = H.parse_html(download_page_html)
    btn = H.select_first(soup, "button#magnet-button")
    encoded = H.attr(btn, "data-link")
    if not encoded or not encoded.strip():
        return None
    return base64.b64decode(encoded).decode("utf-8")


class BlueRoms(SearchProvider, MagnetUriProvider, TorrentDetailsProvider):
    id = "blueroms"
    name = "BlueRoms"
    url = "https://www.blueroms.ws"
    supported_categories = {Category.GAMES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_page_parser = _BlueRomsResultsPageParser(self.id, self.name)
        self._details_page_parser = _BlueRomsDetailsPageParser(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?g=0&p=0&q={query}"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_magnet_uri(self, url: str) -> str:
        magnet = await _get_magnet_uri(url, self.network)
        if not magnet:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return await self._details_page_parser.parse(
            html=response_html, page_url=details_page_url
        )


class _BlueRomsResultsPageParser:
    LIST_ITEM = "div.row > div.col-xs-12 > div.card"
    GAME_NAME = "h4.card-title > a"
    SIZE_NEEDLE = "Size:"
    PLATFORM_NEEDLE = "Platform:"
    DOWNLOAD_PAGE_URL = "div.card-footer > a"
    DETAILS_PAGE_URL = GAME_NAME

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
        download_page_link = H.attr(
            H.select_first(list_item, self.DOWNLOAD_PAGE_URL), "abs:href", page_url
        )
        if not download_page_link:
            return None

        torrent_remote_id = ""
        for ch in reversed(download_page_link):
            if ch == "/":
                break
            torrent_remote_id = ch + torrent_remote_id
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        game_name = H.own_text(H.select_first(list_item, self.GAME_NAME))
        if not game_name:
            return None

        platform_el = _select_first_contains_own(list_item, "strong", self.PLATFORM_NEEDLE)
        platform = None
        if platform_el is not None:
            sib = platform_el.find_next_sibling()
            if isinstance(sib, Tag):
                platform = H.own_text(sib)
        torrent_name = f"{game_name} - {platform}" if platform else game_name

        size = None
        size_el = _select_first_contains_own(list_item, "strong", self.SIZE_NEEDLE)
        if size_el is not None:
            nxt = size_el.next_sibling
            if isinstance(nxt, NavigableString):
                raw = str(nxt).strip()
                if raw:
                    size = utils.normalize_size(raw)

        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            category=Category.GAMES,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=download_page_link),
            details_page_url=details_page_url,
        )


class _BlueRomsDetailsPageParser:
    GAME_NAME = "h3.custom-title"
    PLATFORM_NEEDLE = "Platform:"
    SIZE_NEEDLE = "Files Size:"
    POSTER_URL = 'img[src^="/static/game/"]:not(.img-thumbnail)'
    DOWNLOAD_PAGE_LINK = 'a[href^="/download/"]'

    def __init__(self, network: NetworkClient):
        self._network = network

    async def parse(self, html: str, page_url: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html, page_url)
        download_page_url = H.attr(
            H.select_first(soup, self.DOWNLOAD_PAGE_LINK), "abs:href", page_url
        )
        if not download_page_url:
            return None
        magnet_uri = await _get_magnet_uri(download_page_url, self._network)
        if not magnet_uri:
            return None

        game_name = H.own_text(H.select_first(soup, self.GAME_NAME))
        if not game_name:
            return None

        platform_el = _select_first_contains_own(soup, "strong", self.PLATFORM_NEEDLE)
        platform = None
        if platform_el is not None and platform_el.parent is not None:
            platform = (H.own_text(platform_el.parent) or "").strip() or None
        torrent_name = f"{game_name} - {platform}" if platform else game_name

        size = None
        size_el = _select_first_contains_own(soup, "strong", self.SIZE_NEEDLE)
        if size_el is not None and size_el.parent is not None:
            raw = (H.own_text(size_el.parent) or "").strip()
            if raw:
                size = utils.normalize_size(raw)

        poster_url = H.attr(
            H.select_first(soup, self.POSTER_URL), "abs:src", page_url
        )

        # Screenshots panel: div.panel:has(div.panel-heading:has(h3:containsOwn(Screenshots))) img.img-thumbnail
        screenshot_urls: list[str] = []
        for panel in soup.select("div.panel"):
            heading = panel.select_one("div.panel-heading h3")
            if heading is None:
                continue
            ot = H.own_text(heading) or ""
            if "Screenshots" not in ot:
                continue
            for img in panel.select("img.img-thumbnail"):
                src = H.attr(img, "abs:src", page_url)
                if src and src.strip():
                    screenshot_urls.append(src)

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            magnet_uri=magnet_uri,
            name=torrent_name,
            size=size,
            poster_url=poster_url,
            screenshot_urls=screenshot_urls,
        )
