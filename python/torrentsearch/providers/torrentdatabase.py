"""TorrentDatabase search provider — port of TorrentDatabase.kt."""

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

TORRENT_DATABASE_TRACKER_URL = "https%3A%2F%2Fdevelopify.ca%2Fannounce"
RESULT_LIST_ITEM = "table.torrent-table > tbody > tr"
TORRENT_NAME = "td:nth-child(1) > a:nth-child(2)"
SIZE = "td.size-cell"
SEEDERS = "td:nth-child(5) > div > span:nth-child(1)"
PEERS = "td:nth-child(5) > div > span:nth-child(3)"
UPLOAD_DATE = "td.date-cell"
CATEGORY = "td:nth-child(2) > span.category-bubble"
DESCRIPTION_PAGE_URL = "td:nth-child(1) > a:nth-child(1)"

_D_TORRENT_NAME = "article.torrent-detail-card > header > h1"
_D_SIZE = "div.detail-stat-grid > div:nth-child(1) > strong"
_D_SEEDERS = "div.detail-stat-grid > div:nth-child(2) > strong"
_D_PEERS = "div.detail-stat-grid > div:nth-child(3) > strong"
_D_UPLOAD_DATE = "dl.detail-metadata > div:nth-child(1) > dd"
_D_CATEGORY = "span.category-bubble"
_D_UPLOADER = "div.detail-uploader > a"
_D_LAST_CHECKED = "dl.detail-metadata > div:nth-child(2) > dd"
_D_DESCRIPTION = "div.torrent-info-content"
_D_MAGNET_URI = "#downloadMagnetBtn"


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _category_from_raw_string(raw: str) -> Category:
    key = raw.lower()
    if key == "software":
        return Category.APPS
    if key in {"e-books", "ebooks", "audiobooks"}:
        return Category.BOOKS
    if key == "games":
        return Category.GAMES
    if key == "movies":
        return Category.MOVIES
    if key == "music":
        return Category.MUSIC
    if key in {"porn", "xxx"}:
        return Category.PORN
    if key == "tv":
        return Category.SERIES
    if raw.startswith("games"):
        return Category.GAMES
    if raw.startswith("movies"):
        return Category.MOVIES
    if raw.startswith("music"):
        return Category.MUSIC
    if raw.startswith("software"):
        return Category.APPS
    if raw.startswith("tv"):
        return Category.SERIES
    return Category.OTHER


class TorrentDatabase(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "torrentdatabase"
    name = "TorrentDatabase"
    url = "https://developify.ca"
    cloudflare_solver_url = f"{url}/search?q=ubuntu"
    supported_categories = {
        Category.APPS,
        Category.BOOKS,
        Category.GAMES,
        Category.MOVIES,
        Category.MUSIC,
        Category.PORN,
        Category.SERIES,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.APPS: "software",
            Category.BOOKS: "e-books",
            Category.GAMES: "games",
            Category.MOVIES: "movies",
            Category.MUSIC: "music",
            Category.PORN: "porn",
            Category.SERIES: "tv",
        }
        self._results_parser = _TdResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/search?q={query}"
        slug = self._category_map.get(category)
        if slug:
            request_url = f"{request_url}&category={slug}"
        response_html = await self.network.get_text(url=request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TdDetailsPageParser.parse(response_html)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/newest"
        if category is not Category.ALL:
            slug = self._category_map.get(category)
            if slug is not None:
                if category is Category.BOOKS:
                    slug = slug.replace("-", "")
                request_url = f"{request_url}_{slug}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        if category is Category.ALL:
            return await self.get_latest_torrents(Category.ALL)

        slug = self._category_map.get(category)
        if slug is None:
            return []
        if category is Category.BOOKS:
            slug = slug.replace("-", "")
        request_url = f"{self.url}/top_seeded_{slug}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)


class _TdResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        return [
            t
            for item in H.select(dom, RESULT_LIST_ITEM)
            if (t := self._parse_list_item(item, page_url)) is not None
        ]

    def _parse_list_item(self, list_item, page_url: str) -> Optional[Torrent]:
        torrent_name_el = H.select_first(list_item, TORRENT_NAME)
        if torrent_name_el is None:
            return None
        torrent_name = H.own_text(torrent_name_el)
        href = H.attr(torrent_name_el, "href") or ""
        info_hash = href.removeprefix("/track/magnet/").split("?", 1)[0]
        magnet_uri = (
            f"{utils.create_magnet_uri(info_hash)}&tr={TORRENT_DATABASE_TRACKER_URL}"
        )
        description_page_url = H.attr(
            H.select_first(list_item, DESCRIPTION_PAGE_URL), "abs:href", page_url
        )
        if description_page_url is not None and not description_page_url.strip():
            description_page_url = None
        cat_raw = H.own_text(H.select_first(list_item, CATEGORY))
        category = _category_from_raw_string(cat_raw) if cat_raw else None
        size = H.own_text(H.select_first(list_item, SIZE))
        upload_raw = H.own_text(H.select_first(list_item, UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "yyyy-MM-dd HH:mm:ss") if upload_raw else None
        )
        seeders = _to_int(H.own_text(H.select_first(list_item, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(list_item, PEERS)))

        torrent_remote_id = (
            description_page_url.rsplit("/", 1)[-1] if description_page_url else None
        )
        torrent_id = utils.create_torrent_id(
            self.provider_id, torrent_remote_id or info_hash
        )

        return Torrent(
            id=torrent_id,
            name=torrent_name or "",
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            provider_name=self.provider_name,
            magnet_uri=MagnetAvailable(magnet_uri),
            details_page_url=description_page_url,
        )


class _TdDetailsPageParser:
    @staticmethod
    def parse(html: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html)

        name = H.own_text(H.select_first(dom, _D_TORRENT_NAME))
        if not name:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)

        size_raw = H.own_text(H.select_first(dom, _D_SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None
        seeders = _to_int(H.own_text(H.select_first(dom, _D_SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(dom, _D_PEERS)))

        upload_date = None
        upload_raw = H.own_text(H.select_first(dom, _D_UPLOAD_DATE))
        if upload_raw:
            upload_date = utils.parse_date(upload_raw, "yyyy-MM-dd HH:mm:ssxxx")
            if upload_date is None:
                upload_date = utils.parse_date(upload_raw, "yyyy-MM-dd HH:mm:ss")

        category = None
        cat_el = H.select_first(dom, _D_CATEGORY)
        if cat_el is not None:
            data_cat = cat_el.get("data-category")
            if data_cat:
                category = _category_from_raw_string(str(data_cat))

        uploader = H.own_text(H.select_first(dom, _D_UPLOADER))
        last_checked = None
        last_raw = H.own_text(H.select_first(dom, _D_LAST_CHECKED))
        if last_raw:
            last_checked = utils.parse_date(last_raw, "yyyy-MM-dd HH:mm:ss")

        description_el = H.select_first(dom, _D_DESCRIPTION)
        description = description_el.decode_contents() if description_el else None

        return TorrentDetails(
            info_hash=info_hash,
            name=name,
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            last_checked=last_checked,
            magnet_uri=magnet_uri,
            description=description,
        )
