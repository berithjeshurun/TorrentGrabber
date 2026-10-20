"""TokyoToshokan search provider — port of TokyoToshokan.kt."""

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

MIME_TYPE_TORRENT = "application/x-bittorrent"

LIST_ITEM = "table.listing > tbody > tr:nth-child(n+2)"
NAME = "td.desc-top > a:nth-child(2)"
SIZE_AND_UPLOAD_DATE = "td.desc-bot"
SEEDERS = "td.stats > span:nth-child(1)"
PEERS = "td.stats > span:nth-child(2)"
CATEGORY = "td:nth-child(1) > a"
MAGNET_URI = "td.desc-top > a:nth-child(1)"
FILE_DOWNLOAD_LINK = NAME
DETAILS_PAGE_URL = "td.web > a:last-child"

_D_INFO_HASH = "#main > div.details > ul > li:nth-child(18)"
_D_NAME = "#main > div.details > ul > li:nth-child(6) > a"
_D_SIZE = "#main > div.details > ul > li:nth-child(10)"
_D_SEEDERS = "#main > div.details > ul > li:nth-child(20)"
_D_PEERS = "#main > div.details > ul > li:nth-child(22)"
_D_UPLOAD_DATE = "#main > div.details > ul > li:nth-child(8)"
_D_CATEGORY = "#main > div.details > ul > li:nth-child(2) > a"
_D_UPLOADER = "#main > div.details > ul > li:nth-child(28)"
_D_MAGNET_URI = 'a[href^="magnet:?"]'


def _to_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _category_from_id(cid: str) -> Category:
    if cid in {"1", "7", "8", "10", "11"}:
        return Category.ANIME
    if cid == "3":
        return Category.BOOKS
    if cid in {"2", "9"}:
        return Category.MUSIC
    if cid in {"4", "12", "13", "14", "15"}:
        return Category.PORN
    if cid == "5":
        return Category.OTHER
    return Category.OTHER


class TokyoToshokan(
    SearchProvider,
    TorrentDetailsProvider,
    LatestTorrentsProvider,
    TopTorrentsProvider,
):
    id = "tokyotoshokan"
    name = "TokyoToshokan"
    url = "https://tokyotosho.info"
    cloudflare_solver_url = f"{url}/search.php?terms=one"
    supported_categories = {
        Category.ANIME,
        Category.BOOKS,
        Category.MUSIC,
        Category.PORN,
        Category.OTHER,
    }
    safety = SearchProviderSafety.SAFE
    is_cloudflare_protected = True
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._category_map = {
            Category.ALL: 0,
            Category.ANIME: 1,
            Category.BOOKS: 3,
            Category.MUSIC: 2,
            Category.PORN: 15,
            Category.OTHER: 5,
        }
        self._results_parser = _TokyoToshokanResultsPageParser(self.id, self.name)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        category_id = self._category_map.get(category, self._category_map[Category.ALL])
        request_url = (
            f"{self.url}/search.php?terms={query}&type={category_id}&searchName=true"
        )
        response_html = await self.network.get_text(url=request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return _TokyoToshokanDetailsPageParser.parse(response_html, details_page_url)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/index.php"
        if category is not Category.ALL:
            cat_id = self._category_map.get(category)
            if cat_id is not None:
                request_url = f"{request_url}?cat={cat_id}"
        response_html = await self.network.get_text(request_url)
        return self._results_parser.parse(response_html, request_url)

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        return await self.get_latest_torrents(category)


class _TokyoToshokanResultsPageParser:
    def __init__(self, provider_id: str, provider_name: str):
        self.provider_id = provider_id
        self.provider_name = provider_name

    def parse(self, html: str, page_url: str) -> list[Torrent]:
        dom = H.parse_html(html, page_url)
        items = H.select(dom, LIST_ITEM)
        results: list[Torrent] = []
        for i in range(len(items) - 1):
            torrent = self._parse_list_item(items[i], items[i + 1], page_url)
            if torrent is not None:
                results.append(torrent)
        return results

    def _parse_list_item(self, tr1, tr2, page_url: str) -> Optional[Torrent]:
        torrent_name = H.own_text(H.select_first(tr1, NAME))
        if not torrent_name:
            return None
        magnet_uri = H.attr(H.select_first(tr1, MAGNET_URI), "href")
        if not magnet_uri:
            return None
        torrent_id = utils.create_torrent_id(
            self.provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )
        file_download_link = H.attr(
            H.select_first(tr1, FILE_DOWNLOAD_LINK), "abs:href", page_url
        )
        details_page_url = H.attr(
            H.select_first(tr1, DETAILS_PAGE_URL), "abs:href", page_url
        )
        category = None
        href = H.attr(H.select_first(tr1, CATEGORY), "href") or ""
        if href:
            category = _category_from_id(href.removeprefix("/?cat="))

        raw_size = None
        raw_upload_date = None
        size_date_raw = H.own_text(H.select_first(tr2, SIZE_AND_UPLOAD_DATE))
        if size_date_raw:
            parts = size_date_raw.split("|")
            # drop(1) then map: trim, dropWhile not whitespace, trim
            cleaned = []
            for part in parts[1:]:
                s = part.strip()
                i = 0
                while i < len(s) and not s[i].isspace():
                    i += 1
                cleaned.append(s[i:].strip())
            if len(cleaned) >= 1:
                raw_size = cleaned[0] or None
            if len(cleaned) >= 2:
                raw_upload_date = cleaned[1] or None

        size = utils.normalize_size(raw_size) if raw_size else None
        upload_date = None
        if raw_upload_date:
            upload_date = utils.parse_date(raw_upload_date, "yyyy-MM-dd HH:mm z")
        seeders = _to_int(H.own_text(H.select_first(tr2, SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(tr2, PEERS)))

        return Torrent(
            id=torrent_id,
            name=torrent_name,
            size=size,
            seeders=seeders,
            peers=peers,
            provider_name=self.provider_name,
            upload_date=upload_date,
            category=category,
            details_page_url=details_page_url,
            magnet_uri=MagnetAvailable(magnet_uri),
            file_download_link=file_download_link,
        )


class _TokyoToshokanDetailsPageParser:
    @staticmethod
    def parse(html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        name_anchor = H.select_first(dom, _D_NAME)
        if name_anchor is None:
            return None
        name = H.text(name_anchor)
        file_download_link = None
        if name_anchor.get("type") == MIME_TYPE_TORRENT:
            file_download_link = H.attr(name_anchor, "abs:href", page_url)

        info_hash = H.own_text(H.select_first(dom, _D_INFO_HASH))
        if not info_hash:
            return None
        magnet_uri = H.attr(H.select_first(dom, _D_MAGNET_URI), "href")
        if not magnet_uri:
            magnet_uri = utils.create_magnet_uri(info_hash)

        size_raw = H.own_text(H.select_first(dom, _D_SIZE))
        size = utils.normalize_size(size_raw) if size_raw else None
        seeders = _to_int(H.own_text(H.select_first(dom, _D_SEEDERS)))
        peers = _to_int(H.own_text(H.select_first(dom, _D_PEERS)))
        upload_raw = H.own_text(H.select_first(dom, _D_UPLOAD_DATE))
        upload_date = (
            utils.parse_date(upload_raw, "yyyy-MM-dd hh:mm z") if upload_raw else None
        )
        category = None
        href = H.attr(H.select_first(dom, _D_CATEGORY), "href") or ""
        if href:
            category = _category_from_id(href.removeprefix("index.php?cat="))
        uploader = H.own_text(H.select_first(dom, _D_UPLOADER))

        return TorrentDetails(
            info_hash=info_hash,
            name=name or "",
            size=size,
            seeders=seeders,
            peers=peers,
            upload_date=upload_date,
            category=category,
            uploader=uploader,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
        )
