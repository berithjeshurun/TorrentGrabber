"""Node WebTorrent engine — real peer download with UDP trackers + HTTP media."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from ..activity import ActivityLevel, activity
from .engine import (
    DownloadEngine,
    DownloadState,
    DownloadStatus,
)
from .modes import DownloadConfig, DownloadMode

_NODE_DIR = Path(__file__).resolve().parent / "node_stream"
_SERVER_JS = _NODE_DIR / "server.js"


class NodeWebTorrentEngine(DownloadEngine):
    """
    Spawns a local Node WebTorrent worker (UDP/HTTP trackers work here).
    Browser players hit FastAPI /media which proxies Range requests to the worker.
    """

    def __init__(self):
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._port: Optional[int] = None
        self._base: Optional[str] = None
        self._items: dict[str, DownloadState] = {}
        self._lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def media_base(self) -> Optional[str]:
        return self._base

    async def start(self) -> None:
        if self._proc and self._proc.returncode is None:
            return
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is required for real streaming (node not found on PATH)")
        if not _SERVER_JS.is_file():
            raise RuntimeError(f"Missing WebTorrent worker: {_SERVER_JS}")

        modules = _NODE_DIR / "node_modules" / "webtorrent"
        if not modules.is_dir():
            await activity.say(
                "Installing stream engine packages (one-time)…",
                area="download",
            )
            npm = shutil.which("npm")
            if not npm:
                raise RuntimeError("npm not found — cannot install webtorrent worker")
            proc = await asyncio.create_subprocess_exec(
                npm,
                "install",
                "--omit=dev",
                cwd=str(_NODE_DIR),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    "npm install failed for stream engine:\n"
                    + (out.decode("utf-8", errors="replace")[-800:])
                )

        await activity.say("Starting stream engine…", area="download")
        self._proc = await asyncio.create_subprocess_exec(
            node,
            str(_SERVER_JS),
            "--port",
            "0",
            cwd=str(_NODE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        port = await self._wait_ready(timeout=30)
        self._port = port
        self._base = f"http://127.0.0.1:{port}"
        self._client = httpx.AsyncClient(base_url=self._base, timeout=60.0)
        await activity.say(
            f"Stream engine ready on port {port}",
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
                    "Stream engine exited early:\n"
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
            if line.startswith("WT_READY"):
                # WT_READY port=12345
                for part in line.split():
                    if part.startswith("port="):
                        return int(part.split("=", 1)[1])
        raise RuntimeError("Stream engine did not become ready in time")

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
            raise RuntimeError("Stream engine is not running")
        return self._client

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

            await activity.say(
                f"Fetching torrent metadata for stream…",
                area="download",
            )
            try:
                r = await client.post(
                    "/add",
                    json={"id": did, "magnet": magnet_or_url},
                    timeout=90.0,
                )
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                state.status = DownloadStatus.ERROR
                state.error = str(exc)
                await activity.say(
                    f"Stream engine failed to add torrent: {exc}",
                    level=ActivityLevel.BAD,
                    area="download",
                )
                return state

            self._apply_remote(state, data)
            state.status = status if data.get("ready") else DownloadStatus.METADATA
            if data.get("fileName"):
                state.name = data["fileName"]
            await activity.say(
                f"Streaming “{state.name}” — open the player when buffered",
                level=ActivityLevel.GOOD,
                area="download",
            )
            return state

    def _apply_remote(self, state: DownloadState, data: dict) -> None:
        state.info_hash = data.get("infoHash") or state.info_hash
        if data.get("fileName"):
            state.name = data["fileName"]
        elif data.get("name"):
            state.name = data["name"]
        state.total_bytes = int(data.get("totalBytes") or 0)
        state.downloaded_bytes = int(data.get("downloadedBytes") or 0)
        state.progress = float(data.get("progress") or 0.0)
        state.download_rate = float(data.get("downloadSpeed") or 0.0)
        state.upload_rate = float(data.get("uploadSpeed") or 0.0)
        state.num_peers = int(data.get("numPeers") or 0)
        state.updated_at = datetime.now(timezone.utc)
        if data.get("done"):
            state.status = DownloadStatus.COMPLETED
            state.progress = 1.0
        notes = []
        if data.get("ready"):
            notes.append("media ready for HTTP streaming")
        notes.append(f"peers={state.num_peers}")
        state.last_schedule_notes = notes

    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        async with self._lock:
            self._items.pop(download_id, None)
            client = self._client
            if client:
                try:
                    await client.delete(f"/remove/{download_id}")
                except Exception:
                    pass
            await activity.say(f"Removed stream {download_id}", area="download")

    async def pause(self, download_id: str) -> DownloadState:
        # WebTorrent worker has no pause — mark locally.
        st = self._require(download_id)
        st.status = DownloadStatus.PAUSED
        st.updated_at = datetime.now(timezone.utc)
        return st

    async def resume(self, download_id: str) -> DownloadState:
        st = self._require(download_id)
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
                r = await client.get(f"/status/{did}", timeout=10.0)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                self._apply_remote(state, data)
                if data.get("ready") and state.status is DownloadStatus.METADATA:
                    state.status = (
                        DownloadStatus.STREAMING
                        if state.mode is DownloadMode.STREAM
                        else DownloadStatus.DOWNLOADING
                    )
            except Exception:
                continue

    async def proxy_media(self, download_id: str, range_header: Optional[str]):
        """Return (status_code, headers, async byte iterator) for media."""
        client = self._require_client()
        headers = {}
        if range_header:
            headers["Range"] = range_header
        # Long timeout — video range streams can be slow while pieces arrive.
        req = client.build_request(
            "GET", f"/media/{download_id}", headers=headers, timeout=None
        )
        resp = await client.send(req, stream=True)
        return resp

    def _require(self, download_id: str) -> DownloadState:
        st = self._items.get(download_id)
        if not st:
            raise KeyError(download_id)
        return st


def node_stream_available() -> bool:
    return shutil.which("node") is not None and _SERVER_JS.is_file()
