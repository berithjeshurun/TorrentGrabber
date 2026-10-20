"""Yts — port of Yts.kt."""

from __future__ import annotations

from typing import Any, Optional

from .. import utils
from ..models import Category, MagnetAvailable, SearchProviderSafety, Torrent, TorrentDetails
from ..network import NetworkClient
from ..provider import (
    LatestTorrentsProvider,
    SearchProvider,
    TopTorrentsProvider,
    TorrentDetailsProvider,
)

API_BASE = "https://movies-api.accel.li/api/v2"


class Yts(SearchProvider, LatestTorrentsProvider, TopTorrentsProvider, TorrentDetailsProvider):
    id = "ytsmx"
    name = "Yts"
    url = "https://yts.bz"
    supported_categories = {Category.MOVIES}
    safety = SearchProviderSafety.SAFE
    enabled_by_default = True

    def __init__(self, network: NetworkClient):
        super().__init__(network)

    async def search(self, query: str, category: Category) -> list[Torrent]:
        url = f"{API_BASE}/list_movies.json?query_term={query}&limit=50"
        data = await self.network.get_json(url)
        return self._parse_results(data) if data else []

    async def get_latest_torrents(self, category: Category) -> list[Torrent]:
        data = await self.network.get_json(f"{API_BASE}/list_movies.json")
        return self._parse_results(data) if data else []

    async def get_top_torrents(self, category: Category) -> list[Torrent]:
        return await self.get_latest_torrents(category)

    async def get_details(self, details_page_url: str) -> Optional[TorrentDetails]:
        tail = details_page_url.rsplit("?", 1)[-1]
        parts = tail.split("&", 1)
        if len(parts) != 2:
            return None
        movie_id = parts[0].removeprefix("movieid=")
        info_hash = parts[1].removeprefix("infohash=")
        url = f"{API_BASE}/movie_details.json?movie_id={movie_id}&with_images=true"
        data = await self.network.get_json(url)
        if not isinstance(data, dict):
            return None
        movie = (data.get("data") or {}).get("movie")
        if not isinstance(movie, dict):
            return None
        torrent_obj = None
        for t in movie.get("torrents") or []:
            if isinstance(t, dict) and (t.get("hash") or "").lower() == info_hash.lower():
                torrent_obj = t
                break
        if not torrent_obj:
            return None
        title = movie.get("title_long")
        if not title:
            return None
        quality = torrent_obj.get("quality") or "-"
        typ = torrent_obj.get("type") or "-"
        codec = torrent_obj.get("video_codec") or "-"
        size = None
        if torrent_obj.get("size_bytes") is not None:
            size = utils.format_bytes(float(torrent_obj["size_bytes"]))
        upload = None
        if torrent_obj.get("date_uploaded_unix") is not None:
            upload = utils.epoch_second_to_datetime(int(torrent_obj["date_uploaded_unix"]))
        shots = [
            u
            for u in (
                movie.get("medium_screenshot_image1"),
                movie.get("medium_screenshot_image2"),
                movie.get("medium_screenshot_image3"),
            )
            if u
        ]
        return TorrentDetails(
            info_hash=info_hash.lower(),
            name=f"{title} [{quality}] [{typ}] [{codec}]",
            size=size,
            seeders=_uint(torrent_obj.get("seeds")),
            peers=_uint(torrent_obj.get("peers")),
            upload_date=upload,
            category=Category.MOVIES,
            magnet_uri=utils.create_magnet_uri(info_hash.lower()),
            file_download_link=torrent_obj.get("url"),
            description=movie.get("description_full"),
            poster_url=movie.get("medium_cover_image"),
            screenshot_urls=shots,
        )

    def _parse_results(self, data: Any) -> list[Torrent]:
        if not isinstance(data, dict):
            return []
        movies = ((data.get("data") or {}).get("movies")) or []
        out: list[Torrent] = []
        for movie in movies:
            if isinstance(movie, dict):
                out.extend(self._parse_movie(movie))
        return out

    def _parse_movie(self, movie: dict) -> list[Torrent]:
        movie_id = movie.get("id")
        title = movie.get("title_long")
        if movie_id is None or not title:
            return []
        details = movie.get("url")
        out: list[Torrent] = []
        for t in movie.get("torrents") or []:
            if not isinstance(t, dict):
                continue
            info_hash = (t.get("hash") or "").lower()
            if not info_hash:
                continue
            quality = t.get("quality") or "-"
            typ = t.get("type") or "-"
            codec = t.get("video_codec") or "-"
            upload = None
            if t.get("date_uploaded_unix") is not None:
                upload = utils.epoch_second_to_datetime(int(t["date_uploaded_unix"]))
            details_url = (
                f"{details}?movieid={movie_id}&infohash={info_hash}" if details else None
            )
            out.append(
                Torrent(
                    id=utils.create_torrent_id(self.id, info_hash),
                    name=f"{title} [{quality}] [{typ}] [{codec}]",
                    size=t.get("size"),
                    seeders=_uint(t.get("seeds")),
                    peers=_uint(t.get("peers")),
                    provider_name=self.name,
                    upload_date=upload,
                    category=Category.MOVIES,
                    magnet_uri=MagnetAvailable(utils.create_magnet_uri(info_hash)),
                    details_page_url=details_url,
                )
            )
        return out


def _uint(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
