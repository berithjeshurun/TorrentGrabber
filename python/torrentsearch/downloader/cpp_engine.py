"""C++ libtorrent torrent-engine sidecar client."""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from ..activity import ActivityLevel, activity
from .engine import DownloadEngine, DownloadState, DownloadStatus
from .modes import DownloadConfig, DownloadMode

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_EXE_CANDIDATES = [
    _REPO_ROOT / "engine" / "build" / "Release" / "torrent-engine.exe",
    _REPO_ROOT / "engine" / "build" / "Debug" / "torrent-engine.exe",
    _REPO_ROOT / "engine" / "build" / "torrent-engine.exe",
]


class EngineHttpError(RuntimeError):
    """Upstream torrent-engine HTTP error with structured JSON body."""

    def __init__(self, status_code: int, data: dict[str, Any]):
        self.status_code = status_code
        self.data = data
        msg = data.get("error") or data.get("detail") or str(data)
        super().__init__(str(msg))


def find_torrent_engine_exe() -> Optional[Path]:
    env = os.environ.get("TORRENT_ENGINE_EXE")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for cand in _DEFAULT_EXE_CANDIDATES:
        if cand.is_file():
            return cand
    which = shutil.which("torrent-engine") or shutil.which("torrent-engine.exe")
    if which:
        return Path(which)
    return None


def cpp_engine_available() -> bool:
    return find_torrent_engine_exe() is not None


def _default_ffmpeg_dir() -> Path:
    env = os.environ.get("TORRENT_ENGINE_FFMPEG")
    if env:
        return Path(env)
    return _REPO_ROOT / "python" / "etc"


def _default_data_dir() -> Path:
    env = os.environ.get("TORRENT_ENGINE_DATA")
    if env:
        return Path(env)
    return _REPO_ROOT / "engine-data"


_STATUS_MAP = {
    "queued": DownloadStatus.QUEUED,
    "metadata": DownloadStatus.METADATA,
    "downloading": DownloadStatus.DOWNLOADING,
    "streaming": DownloadStatus.STREAMING,
    "paused": DownloadStatus.PAUSED,
    "completed": DownloadStatus.COMPLETED,
    "seeding": DownloadStatus.SEEDING,
    "error": DownloadStatus.ERROR,
    "stopped": DownloadStatus.STOPPED,
}


class CppEngineClient(DownloadEngine):
    """
    Spawns torrent-engine.exe and talks to /v1 over localhost.
    Handles download + stream + tracks/HLS/subtitle.
    """

    def __init__(
        self,
        *,
        exe: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        ffmpeg_dir: Optional[Path] = None,
    ) -> None:
        self._exe = exe or find_torrent_engine_exe()
        self._data_dir = data_dir or _default_data_dir()
        self._ffmpeg_dir = ffmpeg_dir or _default_ffmpeg_dir()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._port: Optional[int] = None
        self._base: Optional[str] = None
        self._items: dict[str, DownloadState] = {}
        self._media_paths: dict[str, Path] = {}
        self._lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def media_base(self) -> Optional[str]:
        return self._base

    def media_url(self, download_id: str) -> str:
        if not self._base:
            raise RuntimeError("C++ torrent engine is not running")
        return f"{self._base}/v1/torrents/{download_id}/media"

    async def start(self) -> None:
        if self._proc and self._proc.returncode is None and self._client:
            return
        if not self._exe or not self._exe.is_file():
            raise RuntimeError("torrent-engine.exe not found — build engine/ first")

        self._data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(self._exe),
            "--port",
            "0",
            "--data-dir",
            str(self._data_dir),
        ]
        if self._ffmpeg_dir.is_dir():
            args.extend(["--ffmpeg-dir", str(self._ffmpeg_dir)])

        await activity.say("Starting C++ torrent engine…", area="download")
        self._proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(self._exe.parent),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # Avoid inheriting a handle set that can stall on Windows.
            stdin=asyncio.subprocess.DEVNULL,
        )
        port = await self._wait_ready(timeout=45)
        self._port = port
        self._base = f"http://127.0.0.1:{port}"
        self._client = httpx.AsyncClient(base_url=self._base, timeout=60.0)
        await activity.say(
            f"C++ torrent engine ready on port {port}",
            level=ActivityLevel.GOOD,
            area="download",
        )

    async def _wait_ready(self, timeout: float) -> int:
        assert self._proc and self._proc.stdout
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        buf = b""
        while loop.time() < deadline:
            if self._proc.returncode is not None:
                rest = await self._proc.stdout.read()
                raise RuntimeError(
                    "torrent-engine exited early:\n"
                    + (buf + rest).decode("utf-8", errors="replace")[-800:]
                )
            try:
                chunk = await asyncio.wait_for(self._proc.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not chunk:
                continue
            buf += chunk
            line = chunk.decode("utf-8", errors="replace").strip()
            if "ENGINE_READY" in line:
                for part in line.split():
                    if part.startswith("port="):
                        return int(part.split("=", 1)[1])
        raise RuntimeError("torrent-engine did not become ready in time")

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None
        self._port = None
        self._base = None

    def _require_client(self) -> httpx.AsyncClient:
        if not self._client or not self._base:
            raise RuntimeError("C++ torrent engine is not running")
        return self._client

    def _apply_remote(self, state: DownloadState, data: dict[str, Any]) -> None:
        if data.get("info_hash"):
            state.info_hash = data["info_hash"]
        if data.get("name"):
            state.name = data["name"]
        elif data.get("file_name"):
            state.name = data["file_name"]
        state.total_bytes = int(data.get("total_bytes") or 0)
        state.downloaded_bytes = int(data.get("downloaded_bytes") or 0)
        state.progress = float(data.get("progress") or 0.0)
        state.download_rate = float(data.get("download_rate") or 0.0)
        state.upload_rate = float(data.get("upload_rate") or 0.0)
        state.num_peers = int(data.get("num_peers") or 0)
        state.playhead = int(data.get("playhead") or state.playhead)
        err = data.get("error")
        state.error = err if isinstance(err, str) and err else None
        remote_status = data.get("status")
        if isinstance(remote_status, str) and remote_status in _STATUS_MAP:
            # Don't clobber a local pause unless remote also says paused.
            if state.status is DownloadStatus.PAUSED and remote_status != "paused":
                pass
            else:
                state.status = _STATUS_MAP[remote_status]
        state.updated_at = datetime.now(timezone.utc)
        notes = [f"backend=cpp peers={state.num_peers}"]
        if data.get("media_file_index", -1) >= 0:
            notes.append("media selected")
        state.last_schedule_notes = notes
        files = data.get("files")
        if isinstance(files, list):
            state.files = [f for f in files if isinstance(f, dict)]
        if "media_file_index" in data:
            try:
                state.media_file_index = int(data.get("media_file_index", -1))
            except (TypeError, ValueError):
                state.media_file_index = -1
        mp = data.get("media_path")
        if isinstance(mp, str) and mp:
            self._media_paths[state.id] = Path(mp)

    async def add(
        self,
        magnet_or_url: str,
        config: DownloadConfig,
        *,
        download_id: Optional[str] = None,
    ) -> DownloadState:
        async with self._lock:
            client = self._require_client()
            did = download_id or uuid.uuid4().hex[:12]
            state = DownloadState(
                id=did,
                magnet_or_url=magnet_or_url,
                mode=config.mode,
                status=DownloadStatus.METADATA,
                config=config,
                save_path=str(self._data_dir),
                name=f"torrent-{did}",
                selected_files=list(config.selective.file_indexes),
            )
            self._items[did] = state

            body: dict[str, Any] = {
                "id": did,
                "magnet": magnet_or_url,
                "mode": config.mode.value,
            }
            if config.selective.file_indexes:
                body["file_indexes"] = list(config.selective.file_indexes)

            try:
                r = await client.post("/v1/torrents", json=body, timeout=90.0)
                data = r.json()
                if r.status_code >= 400 or (
                    isinstance(data, dict)
                    and data.get("error")
                    and not isinstance(data.get("error"), type(None))
                ):
                    raise RuntimeError(
                        data.get("error") if isinstance(data, dict) else r.text
                    )
            except Exception as exc:
                state.status = DownloadStatus.ERROR
                state.error = str(exc)
                await activity.say(
                    f"C++ engine failed to add torrent: {exc}",
                    level=ActivityLevel.BAD,
                    area="download",
                )
                return state

            self._apply_remote(state, data)
            label = "Streaming" if config.mode is DownloadMode.STREAM else "Downloading"
            await activity.say(
                f"{label} “{state.name}” via libtorrent engine",
                level=ActivityLevel.GOOD,
                area="download",
            )
            return state

    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        async with self._lock:
            self._items.pop(download_id, None)
            client = self._client
            if client:
                try:
                    q = "true" if delete_files else "false"
                    await client.delete(
                        f"/v1/torrents/{download_id}?delete_files={q}"
                    )
                except Exception:
                    pass
            await activity.say(f"Removed {download_id}", area="download")

    async def pause(self, download_id: str) -> DownloadState:
        client = self._require_client()
        r = await client.post(f"/v1/torrents/{download_id}/pause")
        r.raise_for_status()
        st = self._require(download_id)
        self._apply_remote(st, r.json())
        st.status = DownloadStatus.PAUSED
        return st

    async def resume(self, download_id: str) -> DownloadState:
        client = self._require_client()
        r = await client.post(f"/v1/torrents/{download_id}/resume")
        r.raise_for_status()
        st = self._require(download_id)
        self._apply_remote(st, r.json())
        return st

    async def get(self, download_id: str) -> Optional[DownloadState]:
        return self._items.get(download_id)

    async def list(self) -> list[DownloadState]:
        return list(self._items.values())

    async def set_playhead(
        self, download_id: str, offset_bytes: int
    ) -> DownloadState:
        client = self._require_client()
        st = self._require(download_id)
        r = await client.post(
            f"/v1/torrents/{download_id}/playhead",
            json={"offset_bytes": max(0, offset_bytes)},
        )
        r.raise_for_status()
        self._apply_remote(st, r.json())
        st.playhead = max(0, offset_bytes)
        return st

    async def tick(self) -> None:
        client = self._client
        if not client:
            return
        for did, state in list(self._items.items()):
            if state.status in (
                DownloadStatus.PAUSED,
                DownloadStatus.ERROR,
                DownloadStatus.STOPPED,
            ):
                continue
            try:
                r = await client.get(f"/v1/torrents/{did}", timeout=10.0)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                self._apply_remote(state, r.json())
            except Exception:
                continue

    async def proxy_media(self, download_id: str, range_header: Optional[str]):
        client = self._require_client()
        headers = {}
        if range_header:
            headers["Range"] = range_header
        req = client.build_request(
            "GET",
            f"/v1/torrents/{download_id}/media",
            headers=headers,
            timeout=None,
        )
        return await client.send(req, stream=True)

    async def resolve_media_path(self, download_id: str) -> Optional[Path]:
        """Best-effort local path under engine data dir (may be sparse)."""
        cached = self._media_paths.get(download_id)
        if cached and cached.is_file():
            return cached
        st = self._items.get(download_id)
        if not st or not st.name:
            return None
        candidate = self._data_dir / st.name
        if candidate.is_file():
            return candidate
        if self._data_dir.is_dir():
            for p in self._data_dir.rglob("*"):
                if p.is_file() and p.name == st.name:
                    return p
        return None

    async def get_tracks(self, download_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.get(f"/v1/torrents/{download_id}/tracks", timeout=90.0)
        try:
            data = r.json() if r.content else {}
        except Exception:
            raise RuntimeError(
                f"tracks non-json HTTP {r.status_code}: {r.text[:300]!r}"
            )
        if r.status_code >= 400 or (
            isinstance(data, dict)
            and data.get("error")
            and not data.get("video")
            and not data.get("audio")
        ):
            err = data.get("error") if isinstance(data, dict) else r.text
            raise RuntimeError(str(err or f"tracks HTTP {r.status_code}"))
        return data

    async def start_hls(
        self,
        download_id: str,
        *,
        audio: Optional[int] = None,
        start_seconds: Optional[float] = None,
        audio_only: bool = False,
        burn_subtitle: Optional[int] = None,
        audio_bitrate: Optional[str] = None,
        audio_channels: Optional[str] = None,
        loudnorm: Optional[bool] = None,
        deinterlace: Optional[str] = None,
        tonemap: Optional[str] = None,
        lut_cube: Optional[str] = None,
        abr: Optional[bool] = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        params: dict[str, Any] = {}
        if audio is not None:
            params["audio"] = audio
        if start_seconds is not None:
            params["start_seconds"] = start_seconds
        if audio_only:
            params["audio_only"] = "true"
        if burn_subtitle is not None:
            params["burn_subtitle"] = burn_subtitle
        if audio_bitrate:
            params["audio_bitrate"] = audio_bitrate
        if audio_channels:
            params["audio_channels"] = audio_channels
        if loudnorm is not None:
            params["loudnorm"] = "true" if loudnorm else "false"
        if deinterlace:
            params["deinterlace"] = deinterlace
        if tonemap:
            params["tonemap"] = tonemap
        if lut_cube:
            params["lut_cube"] = lut_cube
        if abr is not None:
            params["abr"] = "true" if abr else "false"
        r = await client.post(
            f"/v1/torrents/{download_id}/hls", params=params, timeout=180.0
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            body = data if isinstance(data, dict) else {"error": r.text}
            body.setdefault("error", r.text or f"HLS HTTP {r.status_code}")
            if r.status_code == 503:
                body.setdefault("retry", True)
            raise EngineHttpError(r.status_code, body)
        pl = "master.m3u8" if data.get("abr") else "index.m3u8"
        raw_pl = str(data.get("playlist_url") or "")
        if raw_pl.endswith("master.m3u8"):
            pl = "master.m3u8"
        data["playlist_url"] = f"/api/v1/downloads/{download_id}/hls/{pl}"
        if data.get("subtitle_url"):
            data["subtitle_url"] = (
                f"/api/v1/downloads/{download_id}/hls/subs.vtt"
            )
        if data.get("poster_url"):
            data["poster_url"] = f"/api/v1/downloads/{download_id}/poster.jpg"
        return data

    async def ensure_poster(self, download_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(f"/v1/torrents/{download_id}/poster", timeout=120.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        data["poster_url"] = f"/api/v1/downloads/{download_id}/poster.jpg"
        return data

    async def ensure_thumbs(
        self, download_id: str, *, interval: int = 10
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/thumbs",
            params={"interval": interval},
            timeout=300.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        data["sprite_url"] = f"/api/v1/downloads/{download_id}/thumbs/sprite.jpg"
        data["vtt_url"] = f"/api/v1/downloads/{download_id}/thumbs/thumbs.vtt"
        return data

    async def fetch_poster(self, download_id: str) -> httpx.Response:
        client = self._require_client()
        req = client.build_request(
            "GET", f"/v1/torrents/{download_id}/poster.jpg", timeout=60.0
        )
        return await client.send(req, stream=True)

    async def fetch_thumbs_file(
        self, download_id: str, filename: str
    ) -> httpx.Response:
        client = self._require_client()
        req = client.build_request(
            "GET",
            f"/v1/torrents/{download_id}/thumbs/{filename}",
            timeout=60.0,
        )
        return await client.send(req, stream=True)

    async def export_clip(
        self,
        download_id: str,
        *,
        start: float,
        end: float,
        container: str = "mp4",
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/clip",
            json={"start": start, "end": end, "container": container},
            timeout=600.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        if data.get("file"):
            data["url"] = f"/api/v1/downloads/{download_id}/exports/{data['file']}"
        return data

    async def remux(
        self,
        download_id: str,
        *,
        container: str = "mp4",
        video: Optional[int] = None,
        audio: Optional[int] = None,
        subtitle: Optional[int] = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        body: dict[str, Any] = {"container": container}
        if video is not None:
            body["video"] = video
        if audio is not None:
            body["audio"] = audio
        if subtitle is not None:
            body["subtitle"] = subtitle
        r = await client.post(
            f"/v1/torrents/{download_id}/remux", json=body, timeout=600.0
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        if data.get("file"):
            data["url"] = f"/api/v1/downloads/{download_id}/exports/{data['file']}"
        return data

    async def cast_export(
        self, download_id: str, *, profile: str = "chromecast"
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/cast",
            json={"profile": profile},
            timeout=900.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        if data.get("file"):
            data["url"] = f"/api/v1/downloads/{download_id}/exports/{data['file']}"
        return data

    async def waveform(self, download_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/waveform", timeout=600.0
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        if data.get("waveform_url"):
            data["waveform_url"] = (
                f"/api/v1/downloads/{download_id}/exports/waveform.json"
            )
        if data.get("waveform_png_url"):
            data["waveform_png_url"] = (
                f"/api/v1/downloads/{download_id}/exports/waveform.png"
            )
        return data

    async def chapters(
        self, download_id: str, *, threshold: float = 0.35
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/chapters",
            json={"threshold": threshold},
            timeout=600.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        data["url"] = f"/api/v1/downloads/{download_id}/exports/chapters.json"
        return data

    async def fetch_export(self, download_id: str, filename: str) -> httpx.Response:
        client = self._require_client()
        req = client.build_request(
            "GET",
            f"/v1/torrents/{download_id}/exports/{filename}",
            timeout=120.0,
        )
        return await client.send(req, stream=True)

    async def buffer_health(self, download_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.get(f"/v1/torrents/{download_id}/buffer", timeout=30.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def boost(
        self,
        download_id: str,
        *,
        duration: float = 45,
        multiplier: float = 3.0,
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/boost",
            json={"duration": duration, "multiplier": multiplier},
            timeout=30.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def set_abr(self, download_id: str, *, height: int = 0) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/abr",
            json={"height": height},
            timeout=30.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def invite(self, download_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.get(f"/v1/torrents/{download_id}/invite", timeout=30.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def cast_session(
        self,
        download_id: str,
        *,
        mode: str = "export",
        profile: str = "chromecast",
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/cast/session",
            json={"mode": mode, "profile": profile},
            timeout=900.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        # Rewrite to FastAPI paths for browser/3rd-party same-origin use
        if data.get("export_url") and data.get("file") is None:
            # parse filename from export_url
            pass
        tid = download_id
        if data.get("mode") == "hls":
            raw_pl = str(data.get("playlist_url") or "")
            pl = "master.m3u8" if raw_pl.endswith("master.m3u8") or data.get("abr") else "index.m3u8"
            data["playlist_url"] = f"/api/v1/downloads/{tid}/hls/{pl}"
            tok = data.get("token", "")
            data["cast_url"] = data["playlist_url"] + (f"?token={tok}" if tok else "")
        elif data.get("export_url"):
            raw = str(data["export_url"])
            name = raw.rsplit("/", 1)[-1].split("?")[0]
            data["export_url"] = f"/api/v1/downloads/{tid}/exports/{name}"
            tok = data.get("token", "")
            data["cast_url"] = data["export_url"] + (f"?token={tok}" if tok else "")
        return data

    async def party_create(
        self,
        *,
        magnet: str,
        host_peer: Optional[str] = None,
        torrent_id: Optional[str] = None,
        room_id: Optional[str] = None,
    ) -> dict[str, Any]:
        client = self._require_client()
        body: dict[str, Any] = {"magnet": magnet}
        if host_peer:
            body["host_peer"] = host_peer
        if torrent_id:
            body["torrent_id"] = torrent_id
        if room_id:
            body["id"] = room_id
        r = await client.post("/v1/party", json=body, timeout=30.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def party_get(self, room_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.get(f"/v1/party/{room_id}", timeout=15.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def party_sync(
        self,
        room_id: str,
        *,
        position_sec: float,
        paused: bool,
        who: str = "peer",
    ) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/party/{room_id}/sync",
            json={"position_sec": position_sec, "paused": paused, "who": who},
            timeout=15.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def party_list(self) -> list | dict:
        client = self._require_client()
        r = await client.get("/v1/party", timeout=15.0)
        data = r.json() if r.content else []
        if r.status_code >= 400:
            raise RuntimeError(
                (data.get("error") if isinstance(data, dict) else None) or r.text
            )
        return data

    async def party_delete(self, room_id: str) -> dict[str, Any]:
        client = self._require_client()
        r = await client.delete(f"/v1/party/{room_id}", timeout=15.0)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def set_media_file(self, download_id: str, index: int) -> dict[str, Any]:
        client = self._require_client()
        r = await client.post(
            f"/v1/torrents/{download_id}/media-file",
            json={"index": index},
            timeout=30.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        return data

    async def playlist_m3u(self, download_id: str) -> str:
        client = self._require_client()
        r = await client.get(
            f"/v1/torrents/{download_id}/playlist.m3u", timeout=15.0
        )
        if r.status_code >= 400:
            raise RuntimeError(r.text)
        return r.text

    async def engine_metrics(self) -> str:
        client = self._require_client()
        r = await client.get("/v1/metrics", timeout=10.0)
        return r.text

    async def set_subtitle(
        self, download_id: str, *, index: Optional[int] = None
    ) -> dict[str, Any]:
        client = self._require_client()
        params = {}
        if index is not None:
            params["index"] = index
        r = await client.post(
            f"/v1/torrents/{download_id}/subtitle",
            params=params,
            timeout=120.0,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(data.get("error") or r.text)
        if data.get("subtitle_url"):
            # Keep cache-buster if present
            suffix = ""
            raw = str(data["subtitle_url"])
            if "?t=" in raw:
                suffix = raw[raw.index("?t=") :]
            data["subtitle_url"] = (
                f"/api/v1/downloads/{download_id}/hls/subs.vtt{suffix}"
            )
        return data

    async def fetch_hls_file(self, download_id: str, filename: str) -> httpx.Response:
        client = self._require_client()
        req = client.build_request(
            "GET",
            f"/v1/torrents/{download_id}/hls/{filename}",
            timeout=60.0,
        )
        return await client.send(req, stream=True)

    def _require(self, download_id: str) -> DownloadState:
        st = self._items.get(download_id)
        if not st:
            raise KeyError(download_id)
        return st
