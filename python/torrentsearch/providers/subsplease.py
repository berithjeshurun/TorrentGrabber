"""SubsPlease search provider — port of SubsPlease.kt."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import unquote

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
    TorrentDetailsProvider,
)


def _parse_size_from_magnet_uri(magnet_uri: str) -> Optional[str]:
    query = magnet_uri.split("?", 1)[-1] if "?" in magnet_uri else magnet_uri
    for part in query.split("&"):
        if part.startswith("xl="):
            raw = unquote(part[3:])
            try:
                return utils.format_bytes(float(raw))
            except ValueError:
                return None
    return None


def _drop_while_not_ws(s: str) -> str:
    i = 0
    while i < len(s) and not s[i].isspace():
        i += 1
    return s[i:]


def _drop_last_while_not_ws(s: str) -> str:
    while s and not s[-1].isspace():
        s = s[:-1]
    return s


class SubsPlease(SearchProvider, LatestTorrentsProvider, TorrentDetailsProvider):
    id = "subsplease"
    name = "SubsPlease"
    url = "https://subsplease.org"
    supported_categories = {Category.ANIME}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = False

    def __init__(self, network: NetworkClient):
        super().__init__(network)
        self._results_parser = _SubsPleaseResultsJsonParser(self.id, self.name, self.url)
        self._details_parser = _SubsPleaseDetailsPageParser(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/api?f=search&tz=$&s={query}"
        response_json = await self.network.get_json(url=request_url)
        if response_json is None:
            return []
        return self._results_parser.parse(response_json)

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        request_url = f"{self.url}/api/?f=latest&tz=$"
        response_json = await self.network.get_json(request_url)
        if response_json is None:
            return []
        return self._results_parser.parse(response_json)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        response_html = await self.network.get_text(details_page_url)
        return await self._details_parser.parse(response_html, details_page_url)


class _SubsPleaseResultsJsonParser:
    def __init__(self, provider_id: str, provider_name: str, provider_url: str):
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.provider_url = provider_url

    def parse(self, json_data: Any) -> list[Torrent]:
        # SubsPlease returns empty array "[]" for empty results and object when found.
        if not isinstance(json_data, dict):
            return []
        results: list[Torrent] = []
        for show_name, anime_object in json_data.items():
            if not isinstance(anime_object, dict):
                continue
            parsed = self._parse_anime_object(show_name, anime_object)
            if parsed:
                results.extend(parsed)
        return results

    def _parse_anime_object(
        self, show_name: str, anime_object: dict
    ) -> Optional[list[Torrent]]:
        upload_date = None
        release_date = anime_object.get("release_date")
        if isinstance(release_date, str):
            try:
                upload_date = utils.parse_rfc1123(release_date)
            except ValueError:
                upload_date = None
        page = anime_object.get("page")
        details_page_url = f"{self.provider_url}/{page}" if page else None
        episode_number = anime_object.get("episode")
        if episode_number is None:
            return None
        episode_number = str(episode_number)

        downloads = anime_object.get("downloads")
        if not isinstance(downloads, list):
            return None

        out: list[Torrent] = []
        for item in downloads:
            if not isinstance(item, dict):
                continue
            torrent = self._parse_download_object(
                item,
                show_name,
                upload_date,
                details_page_url,
                episode_number,
            )
            if torrent is not None:
                out.append(torrent)
        return out

    def _parse_download_object(
        self,
        download_object: dict,
        torrent_name: str,
        upload_date,
        details_page_url: Optional[str],
        episode_number: str,
    ) -> Optional[Torrent]:
        magnet_uri = download_object.get("magnet")
        if not isinstance(magnet_uri, str):
            return None
        torrent_id = utils.create_torrent_id(
            self.provider_id,
            utils.get_info_hash_from_magnet_uri(magnet_uri),
        )
        size = _parse_size_from_magnet_uri(magnet_uri)
        resolution = str(download_object.get("res", ""))
        final_torrent_name = f"{torrent_name} [{resolution}p]"
        final_details = None
        if details_page_url:
            final_details = f"{details_page_url}?ep={episode_number}&res={resolution}"

        return Torrent(
            id=torrent_id,
            name=final_torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.ANIME,
            details_page_url=final_details,
            provider_name=self.provider_name,
            magnet_uri=MagnetAvailable(magnet_uri),
        )


class _SubsPleaseDetailsPageParser:
    def __init__(self, network: NetworkClient):
        self.network = network

    async def parse(self, html: str, page_url: str) -> Optional[TorrentDetails]:
        dom = H.parse_html(html, page_url)

        show_table = H.select_first(dom, "table#show-release-table")
        show_id = H.attr(show_table, "sid") if show_table else None
        if not show_id:
            return None
        show_name = H.own_text(H.select_first(dom, "h1.entry-title"))
        if not show_name:
            return None
        poster_url = H.attr(
            H.select_first(dom, "img.img-responsive.img-center"), "abs:src", page_url
        )
        description = H.own_text(H.select_first(dom, "div.series-syn > p"))

        query = page_url.rsplit("?", 1)[-1] if "?" in page_url else ""
        parts = query.split("&", 1)
        if len(parts) != 2:
            return None
        episode_number = parts[0].removeprefix("ep=")
        torrent_resolution = parts[1].removeprefix("res=")

        pair = await self.get_episode_and_download_object(
            show_id, episode_number, torrent_resolution
        )
        if pair is None:
            return None
        episode_object, download_object = pair

        torrent_name = f"{show_name} - {episode_number} [{torrent_resolution}p]"
        magnet_uri = download_object.get("magnet")
        if not isinstance(magnet_uri, str):
            return None
        info_hash = utils.get_info_hash_from_magnet_uri(magnet_uri)
        size = _parse_size_from_magnet_uri(magnet_uri)

        upload_date = None
        release_date = episode_object.get("release_date")
        if isinstance(release_date, str):
            # dropWhile not ws, dropLastWhile not ws, dropLast(1), dropLastWhile not ws, trim
            cleaned = _drop_while_not_ws(release_date)
            cleaned = _drop_last_while_not_ws(cleaned)
            if cleaned:
                cleaned = cleaned[:-1]
            cleaned = _drop_last_while_not_ws(cleaned).strip()
            if cleaned:
                upload_date = utils.parse_date(cleaned, "dd MMM yyyy")

        file_download_link = download_object.get("torrent")
        if not isinstance(file_download_link, str):
            file_download_link = None

        return TorrentDetails(
            info_hash=info_hash,
            name=torrent_name,
            size=size,
            upload_date=upload_date,
            category=Category.ANIME,
            magnet_uri=magnet_uri,
            file_download_link=file_download_link,
            description=description,
            poster_url=poster_url,
        )

    async def get_episode_and_download_object(
        self,
        show_id: str,
        episode_number: str,
        torrent_resolution: str,
    ) -> Optional[tuple[dict, dict]]:
        request_url = f"https://subsplease.org/api/?f=show&tz=$&sid={show_id}"
        response_json = await self.network.get_json(request_url)
        if not isinstance(response_json, dict):
            return None

        episode_map = response_json.get("episode")
        if not isinstance(episode_map, dict):
            return None

        episode_object = None
        for value in episode_map.values():
            if isinstance(value, dict) and str(value.get("episode")) == episode_number:
                episode_object = value
                break
        if episode_object is None:
            return None

        downloads = episode_object.get("downloads")
        if not isinstance(downloads, list):
            return None
        download_object = None
        for item in downloads:
            if isinstance(item, dict) and str(item.get("res")) == torrent_resolution:
                download_object = item
                break
        if download_object is None:
            return None

        return episode_object, download_object
