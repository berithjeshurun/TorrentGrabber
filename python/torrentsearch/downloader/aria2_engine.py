"""aria2c BitTorrent engine — lower CPU/RAM than Node WebTorrent for bulk downloads."""

from __future__ import annotations

import asyncio
import json
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from ..activity import ActivityLevel, activity
from .aria2_util import aria2_available, aria2c_path
from .engine import DownloadEngine, DownloadState, DownloadStatus
from .modes import DownloadConfig, DownloadMode

VIDEO_EXTS = {".mp4", ".webm", ".m4v", ".mov", ".mkv", ".avi", ".ts", ".m2ts", ".ogg", ".ogv"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Aria2Engine(DownloadEngine):
    """
    Spawns local aria2c with JSON-RPC.
    Best for general / limited / fastest / seed / selective — not playhead streaming.
    """

    def __init__(self):
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._port: Optional[int] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._items: dict[str, DownloadState] = {}
        self._gids: dict[str, str] = {}  # download_id → aria2 gid
        self._lock = asyncio.Lock()
        self._rpc_id = 0

    @property
    def rpc_url(self) -> Optional[str]:
        if not self._port:
            return None
        return f"http://127.0.0.1:{self._port}/jsonrpc"

    async def start(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        bin_path = aria2c_path()
        if not bin_path:
            raise RuntimeError("aria2c not found under python/etc")

        self._port = _free_port()
        download_root = Path("downloads").resolve()
        download_root.mkdir(parents=True, exist_ok=True)

        await activity.say("Starting aria2 download engine…", area="download")
        self._proc = await asyncio.create_subprocess_exec(
            str(bin_path),
            "--enable-rpc=true",
            f"--rpc-listen-port={self._port}",
            "--rpc-listen-all=false",
            "--rpc-allow-origin-all=true",
            "--quiet=true",
            "--continue=true",
            "--file-allocation=none",
            "--bt-enable-lpd=true",
            "--enable-dht=true",
            "--enable-dht6=true",
            "--bt-hash-check-seed=true",
            "--max-concurrent-downloads=5",
            "--check-certificate=false",
            f"--dir={download_root}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._client = httpx.AsyncClient(timeout=30.0)
        # Wait until RPC answers
        deadline = asyncio.get_running_loop().time() + 15
        last_err = ""
        while asyncio.get_running_loop().time() < deadline:
            if self._proc.returncode is not None:
                out = b""
                if self._proc.stdout:
                    out = await self._proc.stdout.read()
                raise RuntimeError(
                    "aria2c exited early:\n"
                    + out.decode("utf-8", errors="replace")[-600:]
                )
            try:
                await self._rpc("aria2.getVersion")
                await activity.say(
                    f"aria2 ready (RPC :{self._port})",
                    level=ActivityLevel.GOOD,
                    area="download",
                )
                return
            except Exception as exc:
                last_err = str(exc)
                await asyncio.sleep(0.2)
        raise RuntimeError(f"aria2 RPC did not start: {last_err}")

    async def stop(self) -> None:
        if self._client:
            try:
                await self._rpc("aria2.shutdown")
            except Exception:
                pass
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

    async def _rpc(self, method: str, params: Optional[list] = None) -> Any:
        if not self._client or not self._port:
            raise RuntimeError("aria2 is not running")
        self._rpc_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": str(self._rpc_id),
            "method": method,
            "params": params or [],
        }
        r = await self._client.post(self.rpc_url, json=payload)
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"aria2 {method}: {err.get('message', err)}"
            )
        return data.get("result")

    def _opts_for(self, config: DownloadConfig) -> dict[str, str]:
        opts: dict[str, str] = {
            "dir": str(Path(config.save_path).resolve()),
            "seed-time": "0",
        }
        Path(config.save_path).mkdir(parents=True, exist_ok=True)

        if config.download_rate_limit_kbps:
            opts["max-download-limit"] = f"{config.download_rate_limit_kbps}K"
        if config.upload_rate_limit_kbps:
            opts["max-upload-limit"] = f"{config.upload_rate_limit_kbps}K"
        if config.max_connections:
            opts["max-connection-per-server"] = str(config.max_connections)
            opts["bt-max-peers"] = str(config.max_connections)

        mode = config.mode
        if mode is DownloadMode.LIMITED:
            opts["max-download-limit"] = (
                opts.get("max-download-limit")
                or f"{config.limited.download_rate_limit_kbps or 256}K"
            )
            opts["max-upload-limit"] = (
                opts.get("max-upload-limit")
                or f"{config.limited.upload_rate_limit_kbps or 64}K"
            )
            opts["bt-max-peers"] = str(config.limited.max_peers or 8)
            opts["max-connection-per-server"] = "2"
        elif mode is DownloadMode.FASTEST:
            opts["bt-max-peers"] = str(max(55, config.fastest.max_workers * 4))
            opts["max-connection-per-server"] = "16"
            opts["split"] = "16"
            opts["bt-prioritize-piece"] = "head=8M,tail=1M"
        elif mode is DownloadMode.SEED:
            opts["seed-ratio"] = str(config.seed.target_ratio or 2.0)
            if config.seed.seed_time_seconds:
                opts["seed-time"] = str(max(1, config.seed.seed_time_seconds // 60))
            else:
                opts["seed-time"] = "60"
        elif mode is DownloadMode.PREVIEW:
            head_m = max(1, config.preview.head_bytes // (1024 * 1024))
            tail_m = max(1, config.preview.include_tail_bytes // (1024 * 1024))
            opts["bt-prioritize-piece"] = f"head={head_m}M,tail={tail_m}M"
            opts["bt-max-peers"] = "20"
        elif mode is DownloadMode.SELECTIVE and config.selective.file_indexes:
            # aria2 select-file is 1-based
            opts["select-file"] = ",".join(
                str(i + 1) for i in config.selective.file_indexes
            )
        elif mode is DownloadMode.MIRROR:
            opts["bt-max-peers"] = str(config.mirror.max_workers * 4)
            opts["bt-prioritize-piece"] = "head=16M,tail=1M"
        elif mode is DownloadMode.STREAM:
            # Fallback if hybrid routed here — favor sequential head
            opts["stream-piece-selector"] = "inorder"
            opts["bt-prioritize-piece"] = "head=32M,tail=2M"
            opts["bt-max-peers"] = "40"
        else:
            opts["bt-max-peers"] = "40"

        return opts

    async def add(
        self,
        magnet_or_url: str,
        config: DownloadConfig,
        *,
        download_id: Optional[str] = None,
    ) -> DownloadState:
        async with self._lock:
            did = download_id or uuid.uuid4().hex[:12]
            status = (
                DownloadStatus.STREAMING
                if config.mode is DownloadMode.STREAM
                else DownloadStatus.DOWNLOADING
            )
            state = DownloadState(
                id=did,
                magnet_or_url=magnet_or_url,
                mode=config.mode,
                status=DownloadStatus.METADATA,
                config=config,
                save_path=config.save_path,
                name=f"torrent-{did}",
            )
            self._items[did] = state
            opts = self._opts_for(config)
            try:
                gid = await self._rpc(
                    "aria2.addUri",
                    [[magnet_or_url], opts],
                )
            except Exception as exc:
                state.status = DownloadStatus.ERROR
                state.error = str(exc)
                await activity.say(
                    f"aria2 failed to add: {exc}",
                    level=ActivityLevel.BAD,
                    area="download",
                )
                return state

            self._gids[did] = gid
            await activity.say(
                f"aria2 downloading ({config.mode.value})…",
                level=ActivityLevel.GOOD,
                area="download",
            )
            # Quick status refresh
            await self._refresh_one(did, state)
            if state.status is DownloadStatus.METADATA:
                state.status = status
            return state

    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        async with self._lock:
            gid = self._gids.pop(download_id, None)
            self._items.pop(download_id, None)
            if gid:
                try:
                    await self._rpc(
                        "aria2.remove",
                        [gid],
                    )
                    if delete_files:
                        await self._rpc("aria2.removeDownloadResult", [gid])
                except Exception:
                    try:
                        await self._rpc("aria2.forceRemove", [gid])
                    except Exception:
                        pass
            await activity.say(f"Removed aria2 job {download_id}", area="download")

    async def pause(self, download_id: str) -> DownloadState:
        st = self._require(download_id)
        gid = self._gids.get(download_id)
        if gid:
            try:
                await self._rpc("aria2.pause", [gid])
            except Exception:
                await self._rpc("aria2.forcePause", [gid])
        st.status = DownloadStatus.PAUSED
        st.updated_at = datetime.now(timezone.utc)
        return st

    async def resume(self, download_id: str) -> DownloadState:
        st = self._require(download_id)
        gid = self._gids.get(download_id)
        if gid:
            await self._rpc("aria2.unpause", [gid])
        st.status = (
            DownloadStatus.STREAMING
            if st.mode is DownloadMode.STREAM
            else DownloadStatus.DOWNLOADING
        )
        st.updated_at = datetime.now(timezone.utc)
        return st

    async def get(self, download_id: str) -> Optional[DownloadState]:
        return self._items.get(download_id)

    async def list(self) -> list[DownloadState]:
        return list(self._items.values())

    async def set_playhead(self, download_id: str, offset_bytes: int) -> DownloadState:
        st = self._require(download_id)
        st.playhead = max(0, offset_bytes)
        st.updated_at = datetime.now(timezone.utc)
        # aria2 has no fine playhead — prioritize head via option if still early
        return st

    async def tick(self) -> None:
        for did, state in list(self._items.items()):
            if state.status in (
                DownloadStatus.PAUSED,
                DownloadStatus.ERROR,
                DownloadStatus.STOPPED,
            ):
                continue
            try:
                await self._refresh_one(did, state)
            except Exception:
                continue

    async def _refresh_one(self, download_id: str, state: DownloadState) -> None:
        gid = self._gids.get(download_id)
        if not gid:
            return
        keys = [
            "gid",
            "status",
            "totalLength",
            "completedLength",
            "downloadSpeed",
            "uploadSpeed",
            "connections",
            "numSeeders",
            "files",
            "bittorrent",
            "errorMessage",
            "dir",
        ]
        try:
            info = await self._rpc("aria2.tellStatus", [gid, keys])
        except Exception:
            # Maybe finished → tellStopped
            try:
                stopped = await self._rpc("aria2.tellStopped", [0, 50, keys])
                info = next((x for x in stopped if x.get("gid") == gid), None)
                if not info:
                    return
            except Exception:
                return

        state.total_bytes = int(info.get("totalLength") or 0)
        state.downloaded_bytes = int(info.get("completedLength") or 0)
        state.download_rate = float(info.get("downloadSpeed") or 0)
        state.upload_rate = float(info.get("uploadSpeed") or 0)
        state.num_peers = int(info.get("connections") or 0) + int(
            info.get("numSeeders") or 0
        )
        if state.total_bytes > 0:
            state.progress = state.downloaded_bytes / state.total_bytes

        bt = info.get("bittorrent") or {}
        info_dict = bt.get("info") or {}
        if info_dict.get("name"):
            state.name = info_dict["name"]

        files = info.get("files") or []
        media = self._pick_media_file(files)
        if media and media.get("path"):
            # Prefer concrete media filename for player
            state.name = Path(media["path"]).name

        astatus = info.get("status")
        if astatus == "complete":
            state.status = DownloadStatus.COMPLETED
            state.progress = 1.0
        elif astatus == "error":
            state.status = DownloadStatus.ERROR
            state.error = info.get("errorMessage") or "aria2 error"
        elif astatus == "paused":
            state.status = DownloadStatus.PAUSED
        elif astatus in ("active", "waiting"):
            if state.total_bytes > 0:
                state.status = (
                    DownloadStatus.STREAMING
                    if state.mode is DownloadMode.STREAM
                    else DownloadStatus.DOWNLOADING
                )
            else:
                state.status = DownloadStatus.METADATA

        state.last_schedule_notes = [
            f"aria2:{astatus}",
            f"peers≈{state.num_peers}",
        ]
        state.updated_at = datetime.now(timezone.utc)

    def _pick_media_file(self, files: list[dict]) -> Optional[dict]:
        if not files:
            return None
        videos = []
        for f in files:
            path = f.get("path") or ""
            if Path(path).suffix.lower() in VIDEO_EXTS:
                videos.append(f)
        pool = videos or files
        return max(pool, key=lambda x: int(x.get("length") or 0))

    def media_file_path(self, download_id: str) -> Optional[Path]:
        """Best local media path for this download (may be incomplete)."""
        # Use last known files from a sync-ish cache on state name + save_path
        # Callers should prefer async resolve_media_path.
        st = self._items.get(download_id)
        if not st:
            return None
        # Heuristic: largest video under save_path matching name
        root = Path(st.save_path)
        if st.name:
            candidate = root / st.name
            if candidate.is_file():
                return candidate
            # aria2 often nests as save_path/TorrentName/file.mkv
            nested = root / st.name
            if nested.is_dir():
                vids = [
                    p
                    for p in nested.rglob("*")
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS
                ]
                if vids:
                    return max(vids, key=lambda p: p.stat().st_size)
        vids = [
            p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ]
        if vids:
            return max(vids, key=lambda p: p.stat().st_size)
        return None

    async def resolve_media_path(self, download_id: str) -> Optional[Path]:
        gid = self._gids.get(download_id)
        if not gid:
            return self.media_file_path(download_id)
        try:
            info = await self._rpc("aria2.tellStatus", [gid, ["files", "dir"]])
        except Exception:
            return self.media_file_path(download_id)
        files = info.get("files") or []
        media = self._pick_media_file(files)
        if media and media.get("path"):
            p = Path(media["path"])
            if p.is_file() or p.parent.is_dir():
                return p if p.exists() else None
        return self.media_file_path(download_id)

    def _require(self, download_id: str) -> DownloadState:
        st = self._items.get(download_id)
        if not st:
            raise KeyError(download_id)
        return st


def aria2_engine_available() -> bool:
    return aria2_available()
