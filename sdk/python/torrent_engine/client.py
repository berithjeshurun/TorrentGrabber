"""Thin sync/async clients for torrent-engine OpenAPI v0.1."""

from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

import httpx

__sdk_version__ = "0.1.0"


def _cmp_version(a: str, b: str) -> int:
    def parts(s: str) -> list[int]:
        out: list[int] = []
        for p in s.replace("-", ".").split("."):
            try:
                out.append(int(p))
            except ValueError:
                out.append(0)
        while len(out) < 3:
            out.append(0)
        return out[:4]

    pa, pb = parts(a), parts(b)
    return (pa > pb) - (pa < pb)


class EngineError(RuntimeError):
    def __init__(self, status_code: int, data: Any):
        self.status_code = status_code
        self.data = data
        if isinstance(data, dict):
            msg = data.get("error") or data.get("detail") or str(data)
        else:
            msg = str(data)
        super().__init__(f"HTTP {status_code}: {msg}")


class TorrentEngine:
    """Synchronous client — works from scripts, Django, Flask, etc."""

    def __init__(self, base_url: str = "http://127.0.0.1:17890", *, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TorrentEngine":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        r = self._client.request(method, path, **kwargs)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            raise EngineError(r.status_code, data)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content

    def health(self) -> dict:
        return self._req("GET", "/v1/health")

    def version(self) -> dict:
        return self._req("GET", "/v1/version")

    def check_engine_update(self) -> dict:
        """Ask the running engine to check its update manifest."""
        return self._req("GET", "/v1/update/check")

    def check_sdk_update(
        self, *, manifest_url: Optional[str] = None
    ) -> dict[str, Any]:
        """Fetch the same update manifest and compare this SDK package version."""
        url = (
            manifest_url
            or os.environ.get("TORRENT_ENGINE_UPDATE_URL")
            or ""
        ).strip()
        out: dict[str, Any] = {
            "sdk": "python",
            "current_version": __sdk_version__,
            "ok": False,
            "update_available": False,
        }
        if not url:
            out["error"] = "Set TORRENT_ENGINE_UPDATE_URL or pass manifest_url="
            return out
        r = httpx.get(url, timeout=30.0, follow_redirects=True)
        if r.status_code >= 400:
            out["error"] = f"HTTP {r.status_code}"
            return out
        data = r.json()
        latest = (
            (data.get("sdk") or {}).get("python")
            or data.get("version")
            or ""
        )
        out["ok"] = True
        out["latest_version"] = latest
        out["manifest"] = data
        out["update_available"] = bool(latest) and _cmp_version(
            __sdk_version__, latest
        ) < 0
        out["notes"] = data.get("notes")
        return out

    def session(self) -> dict:
        return self._req("GET", "/v1/session")

    def metrics_json(self) -> dict:
        return self._req("GET", "/v1/metrics.json")

    def add(
        self,
        magnet_or_url: str,
        *,
        mode: str = "stream",
        id: Optional[str] = None,
        peers: Optional[list[dict]] = None,
        selective_files: Optional[list[int]] = None,
    ) -> dict:
        body: dict[str, Any] = {"magnet": magnet_or_url, "mode": mode}
        if id:
            body["id"] = id
        if peers:
            body["peers"] = peers
        if selective_files is not None:
            body["selective_files"] = selective_files
        return self._req("POST", "/v1/torrents", json=body)

    def list(self) -> Any:
        return self._req("GET", "/v1/torrents")

    def status(self, torrent_id: str) -> dict:
        return self._req("GET", f"/v1/torrents/{quote(torrent_id, safe='')}")

    def pause(self, torrent_id: str) -> Any:
        return self._req("POST", f"/v1/torrents/{quote(torrent_id, safe='')}/pause")

    def resume(self, torrent_id: str) -> Any:
        return self._req("POST", f"/v1/torrents/{quote(torrent_id, safe='')}/resume")

    def remove(self, torrent_id: str, *, delete_files: bool = False) -> Any:
        return self._req(
            "DELETE",
            f"/v1/torrents/{quote(torrent_id, safe='')}?delete_files={'true' if delete_files else 'false'}",
        )

    def playhead(self, torrent_id: str, offset_bytes: int) -> Any:
        return self._req(
            "POST",
            f"/v1/torrents/{quote(torrent_id, safe='')}/playhead",
            json={"offset_bytes": offset_bytes},
        )

    def buffer(self, torrent_id: str) -> dict:
        return self._req("GET", f"/v1/torrents/{quote(torrent_id, safe='')}/buffer")

    def boost(self, torrent_id: str, duration: float = 25.0, multiplier: float = 2.5) -> Any:
        return self._req(
            "POST",
            f"/v1/torrents/{quote(torrent_id, safe='')}/boost",
            json={"duration": duration, "multiplier": multiplier},
        )

    def tracks(self, torrent_id: str) -> dict:
        return self._req("GET", f"/v1/torrents/{quote(torrent_id, safe='')}/tracks")

    def start_hls(self, torrent_id: str, **params: Any) -> dict:
        """May raise EngineError 503 with retry=true until sequential gate passes."""
        return self._req(
            "POST",
            f"/v1/torrents/{quote(torrent_id, safe='')}/hls",
            params=params or None,
            json=params or None,
        )

    def set_media_file(self, torrent_id: str, index: int) -> Any:
        return self._req(
            "POST",
            f"/v1/torrents/{quote(torrent_id, safe='')}/media-file",
            json={"index": index},
        )

    def media_url(self, torrent_id: str) -> str:
        return f"{self.base_url}/v1/torrents/{quote(torrent_id, safe='')}/media"

    def hls_playlist_url(self, torrent_id: str, *, master: bool = False) -> str:
        name = "master.m3u8" if master else "index.m3u8"
        return f"{self.base_url}/v1/torrents/{quote(torrent_id, safe='')}/hls/{name}"


class AsyncTorrentEngine:
    """Async twin for FastAPI / aiohttp / asyncio apps."""

    def __init__(self, base_url: str = "http://127.0.0.1:17890", *, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncTorrentEngine":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def _req(self, method: str, path: str, **kwargs: Any) -> Any:
        r = await self._client.request(method, path, **kwargs)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = {"error": r.text}
            raise EngineError(r.status_code, data)
        if r.headers.get("content-type", "").startswith("application/json"):
            return r.json()
        return r.content

    async def health(self) -> dict:
        return await self._req("GET", "/v1/health")

    async def add(self, magnet_or_url: str, *, mode: str = "stream", **extra: Any) -> dict:
        body = {"magnet": magnet_or_url, "mode": mode, **extra}
        return await self._req("POST", "/v1/torrents", json=body)

    async def status(self, torrent_id: str) -> dict:
        return await self._req("GET", f"/v1/torrents/{quote(torrent_id, safe='')}")

    async def buffer(self, torrent_id: str) -> dict:
        return await self._req("GET", f"/v1/torrents/{quote(torrent_id, safe='')}/buffer")

    async def start_hls(self, torrent_id: str, **params: Any) -> dict:
        return await self._req(
            "POST",
            f"/v1/torrents/{quote(torrent_id, safe='')}/hls",
            params=params or None,
            json=params or None,
        )

    def media_url(self, torrent_id: str) -> str:
        return f"{self.base_url}/v1/torrents/{quote(torrent_id, safe='')}/media"

    def hls_playlist_url(self, torrent_id: str, *, master: bool = False) -> str:
        name = "master.m3u8" if master else "index.m3u8"
        return f"{self.base_url}/v1/torrents/{quote(torrent_id, safe='')}/hls/{name}"
