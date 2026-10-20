"""OxTorrent search provider — port of OxTorrent.kt."""

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
SIZE = "td:nth-child(2)"
SEEDERS = "td:nth-child(3)"
PEERS = "td:nth-child(4)"
CATEGORY = "td:nth-child(1) > i"
DETAILS_PAGE_URL = "td:nth-child(1) > a"

_D_TORRENT_NAME = "div.title > a"
_D_MAGNET_URL = "div.btn-magnet > a"
_D_DESCRIPTION = "div#torrentsdesc"
_D_POSTER_URL = "img.img-rounded"


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _find_td_contains_own(root, text: str):
    for el in root.find_all("td"):
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


class OxTorrent(
    SearchProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
    MagnetUriProvider,
    TorrentDetailsProvider,
):
    id = "oxtorrent"
    name = "OxTorrent"
    url = "https://oxtorrent.co"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
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
        self._results_parser = _OxTorrentResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/recherche"
        slug = self._category_map.get(category)
        if slug:
            request_url = f"{request_url}/{slug}"
        request_url = f"{request_url}/{query}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _OxTorrentDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        category_slug = self._category_map.get(category)
        if not category_slug:
            return []
        request_url = f"{self.url}/torrents/{category_slug}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/top"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_magnet_uri(self, url: str) -> str:
        details_page_html = await self.network.get_text(url)
        magnet = _OxTorrentDetailsPageParser.extract_magnet_uri(details_page_html)
        if magnet is None:
            raise RuntimeError(f"Failed to retrieve magnet URI from '{url}'")
        return magnet


class _OxTorrentResultsPageParser:
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
        name = H.own_text(H.select_first(list_item, TORRENT_NAME))
        if not name:
            return None
        torrent_id = utils.create_torrent_id(self.provider_id, details_page_url)
        size = H.own_text(H.select_first(list_item, SIZE))
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))
        cat_el = H.select_first(list_item, CATEGORY)
        category = None
        if cat_el is not None:
            classes = cat_el.get("class") or []
            class_name = " ".join(classes) if isinstance(classes, list) else str(classes)
            category = _get_category_from_raw(class_name)

        return Torrent(
            id=torrent_id,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.provider_name,
            category=category,
            magnet_uri=MagnetRequiresFetch(details_page_url),
            details_page_url=details_page_url,
        )


class _OxTorrentDetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        torrent_name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URL), "href")
        if not magnet_uri:
            return None

        size_el = _find_td_contains_own(dom, "Poids du fichier:")
        size = H.text(size_el.find_next_sibling()) if size_el else None

        seeders_el = _find_td_contains_own(dom, "Seeders:")
        seeders = (
            _to_int(H.text(seeders_el.find_next_sibling())) if seeders_el else None
        )

        peers_el = _find_td_contains_own(dom, "Leechers:")
        peers = _to_int(H.text(peers_el.find_next_sibling())) if peers_el else None

        upload_el = _find_td_contains_own(dom, "Date d'ajout:")
        upload_date = None
        if upload_el is not None:
            raw = H.text(upload_el.find_next_sibling())
            if raw:
                upload_date = utils.parse_date(raw, "dd/MM/yyyy")

        category = None
        cat_el = _find_td_contains_own(dom, "Catégories:")
        if cat_el is not None:
            sib = cat_el.find_next_sibling()
            if sib is not None:
                link = H.select_first(sib, "strong > a")
                href = H.attr(link, "href") or ""
                raw = href.removeprefix("/torrents/")
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
