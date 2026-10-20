"""Torrent9 search provider — port of Torrent9.kt."""

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

LIST_ITEM = "table > tbody > tr"
TORRENT_NAME = "td:nth-child(1) > a"
SIZE = "td:nth-child(3)"
SEEDERS = "td:nth-child(4) > span.seed_ok"
PEERS = "td:nth-child(5)"
UPLOAD_DATE = "td:nth-child(2)"
CATEGORY = "td:nth-child(1) > i"
DETAILS_PAGE_URL = "td:nth-child(1) > a"

_D_TORRENT_NAME = "div.movie-section h1"
_D_SEEDERS = 'li[style="color:green"]'
_D_PEERS = 'li[style="color:red"]'
_D_MAGNET_URL = 'a[href^="magnet:?"]'
_D_DESCRIPTION = "p.description_torrent"
_D_POSTER_URL = "div.movie-img > img"


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _find_strong_contains_own(root, text: str):
    for el in root.find_all("strong"):
        own = "".join(el.find_all(string=True, recursive=False))
        if text in own:
            return el
    return None


def _get_category_from_raw(raw: str) -> Category:
    return {
        "ebook": Category.BOOKS,
        "films": Category.MOVIES,
        "jeux-consoles": Category.GAMES,
        "jeux-pc": Category.GAMES,
        "logiciels": Category.APPS,
        "musique": Category.MUSIC,
        "series": Category.SERIES,
    }.get(raw, Category.OTHER)


def _category_from_icon_name(icon_name: str) -> Category:
    return {
        "fa-desktop": Category.SERIES,
        "fa-video-camera": Category.MOVIES,
        "fa-music": Category.MUSIC,
        "fa-book": Category.BOOKS,
        "fa-laptop": Category.APPS,
        "fa-gamepad": Category.GAMES,
    }.get(icon_name, Category.OTHER)


class Torrent9(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "torrent9"
    name = "Torrent9"
    url = "https://www6.torrent9.to"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.APPS: "logiciels",
            Category.BOOKS: "ebook",
            Category.GAMES: "jeux-pc",
            Category.MOVIES: "films",
            Category.MUSIC: "musique",
            Category.SERIES: "series",
        }
        self._results_parser = _Torrent9ResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search_torrent"
        slug = self._category_map.get(category)
        if slug:
            request_url = f"{request_url}/{slug}"
        request_url = f"{request_url}/{query}.html"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _Torrent9DetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_slug = self._category_map.get(category)
        if not category_slug:
            return []
        request_url = f"{self.url}/torrents_{category_slug}.html"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top_torrent.html"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _Torrent9DetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _Torrent9ResultsPageParser:
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
        name = H.text(H.select_first(list_item, TORRENT_NAME))
        if not name:
            return None
        torrent_id = utils.create_torrent_id(self.provider_id, details_page_url)
        size = None
        size_raw = H.own_text(H.select_first(list_item, SIZE))
        if size_raw:
            size = utils.normalize_size(size_raw.replace("o", "B"))
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        upload_raw = H.own_text(H.select_first(list_item, UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "dd/MM/yyyy") if upload_raw else None
        )
        category = None
        cat_el = H.select_first(list_item, CATEGORY)
        if cat_el is not None:
            classes = list(cat_el.get("class") or [])
            if "fa" in classes:
                classes.remove("fa")
            icon_name = " ".join(classes)
            category = _category_from_icon_name(icon_name)

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            provider_name=self.provider_name,
            category=category,
            magnet_uri=MagnetRequiresFetch(details_page_url),
        )


class _Torrent9DetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URL), "href")
        if not magnet_uri:
            return None

        size = None
        size_label = _find_strong_contains_own(dom, "Poids du torrent")
        if size_label is not None and size_label.parent is not None:
            sib1 = size_label.parent.find_next_sibling()
            if sib1 is not None:
                sib2 = sib1.find_next_sibling()
                if sib2 is not None:
                    raw = H.own_text(sib2)
                    if raw:
                        # dropLast(1) then append B, normalize
                        size = utils.normalize_size(f"{raw[:-1]}B")

        seeders = _to_int(H.own_text(H.select_first(dom, _D_SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(dom, _D_PEERS)))

        upload_date = None
        upload_label = _find_strong_contains_own(dom, "Date d'ajout")
        if upload_label is not None and upload_label.parent is not None:
            sib1 = upload_label.parent.find_next_sibling()
            if sib1 is not None:
                sib2 = sib1.find_next_sibling()
                if sib2 is not None:
                    raw = H.own_text(sib2)
                    if raw:
                        upload_date = utils.parse_date(raw, "dd/MM/yyyy")

        category = None
        cat_label = _find_strong_contains_own(dom, "Catégories")
        if cat_label is not None and cat_label.parent is not None:
            sib1 = cat_label.parent.find_next_sibling()
            if sib1 is not None:
                sib2 = sib1.find_next_sibling()
                if sib2 is not None:
                    href = H.attr(H.select_first(sib2, "a"), "href") or ""
                    raw = href.removeprefix("/torrents_").removesuffix(".html")
                    category = _get_category_from_raw(raw)

        description_el = H.select_first(dom, _D_DESCRIPTION)
        description = description_el.decode_contents() if description_el else None
        poster_url = H.attr(H.select_first(dom, _D_POSTER_URL), "abs:src", page_url)

        return TorrentDetails(
            info_hash=utils.get_info_hash_from_magnet_uri(magnet_uri),
            magnet_uri=magnet_uri,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            description=description,
            poster_url=poster_url,
        )

    @staticmethod
    def extract_magnet_uri(details_page_html: str) -> Optional[str]:
        dom = H.parse_html(details_page_html)
        return H.attr(H.select_first(dom, _D_MAGNET_URL), "href")
