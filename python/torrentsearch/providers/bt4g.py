"""Bt4g search provider — port of Bt4g.kt."""

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


def _get_category_from_raw(raw: str) -> Category:
    if raw in {"Video", "Movie"}:
        return Category.MOVIES
    if raw == "Audio":
        return Category.MUSIC
    if raw == "Doc":
        return Category.BOOKS
    if raw in {"App", "Application"}:
        return Category.APPS
    if raw == "Other":
        return Category.OTHER
    return Category.OTHER


def _select_first_contains_own(soup: Tag, tag: str, needle: str) -> Optional[Tag]:
    for el in soup.find_all(tag):
        ot = H.own_text(el)
        if ot is not None and needle in ot:
            return el
    return None


class Bt4g(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "bt4g"
    name = "BT4G"
    url = "https://bt4gprx.com"
    cloudflare_solver_url = f"{url}/search?q=ubuntu"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.MOVIES,
        Category.MUSIC,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.ALL: "all",
            Category.APPS: "app",
            Category.BOOKS: "doc",
            Category.MOVIES: "movie",
            Category.MUSIC: "audio",
            Category.OTHER: "other",
        }
        self._results_page_parser = _Bt4gResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_name = self._category_map.get(
            category, self._category_map[Category.ALL]
        )
        request_url = (
            f"{self.url}/search?q={query}&category={category_name}&orderby=seeders&p=1"
        )
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _Bt4gDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/new"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/week"
        response_html = await self.network.get_text(request_url)
        return self._results_page_parser.parse(html=response_html, page_url=request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        info_hash = _Bt4gDetailsPageParser.extract_info_hash(details_page_html)
        if not info_hash:
            raise RuntimeError(f"Failed to retrieve info hash from '{url}'")
        return utils.create_magnet_uri(info_hash)


class _Bt4gResultsPageParser:
    LIST_ITEM = "div.notion-list-item"
    TORRENT_NAME = "div.notion-list-item-title > a"
    SIZE = "div.notion-list-item-meta > span:nth-child(5) > b"
    SEEDERS = "div.notion-list-item-meta span#seeders"
    PEERS = "div.notion-list-item-meta span#leechers"
    UPLOAD_DATE = "div.notion-list-item-meta > span:nth-child(2)"
    CATEGORY = "div.notion-list-item-meta span.notion-tag"
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
        details_page_url = H.attr(
            H.select_first(list_item, self.DETAILS_PAGE_URL), "abs:href", page_url
        )
        if not details_page_url:
            return None

        torrent_remote_id = ""
        for ch in reversed(details_page_url):
            if ch == "/":
                break
            torrent_remote_id = ch + torrent_remote_id
        torrent_id = utils.create_torrent_id(self._provider_id, torrent_remote_id)

        torrent_name = H.text(H.select_first(list_item, self.TORRENT_NAME))
        if not torrent_name:
            return None

        size_raw = H.own_text(H.select_first(list_item, self.SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None
        seeders_raw = H.own_text(H.select_first(list_item, self.SEEDERS))
        peers_raw = H.own_text(H.select_first(list_item, self.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_date = None
        upload_raw = H.own_text(H.select_first(list_item, self.UPLOAD_DATE))
        if upload_raw:
            prefix = "Creation Time: "
            if upload_raw.startswith(prefix):
                upload_raw = upload_raw[len(prefix) :]
            upload_raw = upload_raw.strip()
            upload_date = utils.parse_date(upload_raw, "yyyy-MM-dd")
            if upload_date is None:
                upload_date = utils.try_parse_relative(upload_raw)

        cat_raw = H.own_text(H.select_first(list_item, self.CATEGORY))
        category = _get_category_from_raw(cat_raw) if cat_raw else None

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self._provider_name,
            magnet_uri=MagnetRequiresFetch(url=details_page_url),
            details_page_url=details_page_url,
        )


class _Bt4gDetailsPageParser:
    TORRENT_NAME = "h1.notion-detail-title"
    SIZE_NEEDLE = "File Size"
    SEEDERS = "span#seeders"
    PEERS = "span#leechers"
    UPLOAD_DATE_NEEDLE = "Creation Time"
    CATEGORY_NEEDLE = "File Type"
    LAST_CHECKED_NEEDLE = "Updated"
    MAGNET_LINK_BTN = 'a[href^="//downloadtorrentfile.com/hash/"]'
    HASH_PREFIX = "//downloadtorrentfile.com/hash/"

    @classmethod
    def parse(cls, html: str) -> Optional[TorrentDetails]:
        soup = H.parse_html(html)

        info_hash = cls.extract_info_hash(html)
        if not info_hash:
            # re-parse from already-built soup
            btn = H.select_first(soup, cls.MAGNET_LINK_BTN)
            href = H.attr(btn, "href")
            if not href or not href.startswith(cls.HASH_PREFIX):
                return None
            rest = href[len(cls.HASH_PREFIX) :]
            info_hash = ""
            for ch in rest:
                if ch == "?":
                    break
                info_hash += ch
            if not info_hash:
                return None

        torrent_name = H.own_text(H.select_first(soup, cls.TORRENT_NAME))
        if not torrent_name:
            return None

        size = None
        size_el = _select_first_contains_own(soup, "span", cls.SIZE_NEEDLE)
        if size_el is not None:
            # also match class notion-property-label
            if "notion-property-label" not in (size_el.get("class") or []):
                size_el = None
                for el in soup.find_all("span", class_="notion-property-label"):
                    ot = H.own_text(el)
                    if ot and cls.SIZE_NEEDLE in ot:
                        size_el = el
                        break
        if size_el is None:
            for el in soup.find_all("span", class_="notion-property-label"):
                ot = H.own_text(el)
                if ot and cls.SIZE_NEEDLE in ot:
                    size_el = el
                    break
        if size_el is not None:
            sib = size_el.find_next_sibling()
            if isinstance(sib, Tag):
                raw = H.own_text(sib)
                if raw:
                    size = utils.normalize_size(raw)

        seeders_raw = H.own_text(H.select_first(soup, cls.SEEDERS))
        peers_raw = H.own_text(H.select_first(soup, cls.PEERS))
        seeders = int(seeders_raw) if seeders_raw and seeders_raw.isdigit() else None
        peers = int(peers_raw) if peers_raw and peers_raw.isdigit() else None

        upload_date = None
        upload_el = None
        for el in soup.find_all("span", class_="notion-property-label"):
            ot = H.own_text(el)
            if ot and cls.UPLOAD_DATE_NEEDLE in ot:
                upload_el = el
                break
        if upload_el is not None:
            sib = upload_el.find_next_sibling()
            if isinstance(sib, Tag):
                raw = H.own_text(sib)
                if raw:
                    upload_date = utils.parse_date(raw, "yyyy-MM-dd")

        category = None
        cat_el = None
        for el in soup.find_all("span", class_="notion-property-label"):
            ot = H.own_text(el)
            if ot and cls.CATEGORY_NEEDLE in ot:
                cat_el = el
                break
        if cat_el is not None:
            sib = cat_el.find_next_sibling()
            if isinstance(sib, Tag):
                raw = H.own_text(sib)
                if raw:
                    category = _get_category_from_raw(raw)

        last_checked = None
        checked_el = None
        for el in soup.find_all("span", class_="notion-property-label"):
            ot = H.own_text(el)
            if ot and cls.LAST_CHECKED_NEEDLE in ot:
                checked_el = el
                break
        if checked_el is not None:
            sib = checked_el.find_next_sibling()
            if isinstance(sib, Tag):
                raw = H.own_text(sib)
                if raw:
                    last_checked = utils.try_parse_relative(raw)

        return TorrentDetails(
            info_hash=info_hash,
            magnet_uri=utils.create_magnet_uri(info_hash),
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            last_checked=last_checked,
        )

    @classmethod
    def extract_info_hash(cls, html: str) -> Optional[str]:
        soup = H.parse_html(html)
        btn = H.select_first(soup, cls.MAGNET_LINK_BTN)
        href = H.attr(btn, "href")
        if not href or not href.startswith(cls.HASH_PREFIX):
            return None
        rest = href[len(cls.HASH_PREFIX) :]
        info_hash = ""
        for ch in rest:
            if ch == "?":
                break
            info_hash += ch
        return info_hash or None
