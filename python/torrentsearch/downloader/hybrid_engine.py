"""Route downloads: prefer C++ libtorrent engine; fallback Node stream + aria2."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..activity import ActivityLevel, activity
from .aria2_engine import Aria2Engine, aria2_engine_available
from .cpp_engine import CppEngineClient, cpp_engine_available
from .engine import DownloadEngine, DownloadState, SimulatedEngine
from .modes import DownloadConfig, DownloadMode
from .node_engine import NodeWebTorrentEngine, node_stream_available


class HybridEngine(DownloadEngine):
    """
    Prefers the C++ torrent-engine sidecar for all modes (download + stream +
    multi-audio HLS). Falls back to Node WebTorrent (stream) + aria2 (bulk).
    """

    def __init__(self) -> None:
        self._cpp: Optional[CppEngineClient] = (
            CppEngineClient() if cpp_engine_available() else None
        )
        self._aria2: Optional[Aria2Engine] = (
            Aria2Engine() if aria2_engine_available() else None
        )
        self._node: Optional[NodeWebTorrentEngine] = None
        self._node_wanted = node_stream_available() and self._cpp is None
        self._sim: Optional[SimulatedEngine] = None
        self._owner: dict[str, DownloadEngine] = {}

    @property
    def media_base(self) -> Optional[str]:
        if self._cpp and self._cpp.media_base:
            return self._cpp.media_base
        if self._node:
            return self._node.media_base
        return None

    def owner_for(self, download_id: str) -> Optional[DownloadEngine]:
        return self._owner.get(download_id)

    async def start(self) -> None:
        if self._cpp:
            await self._cpp.start()
            await activity.say(
                "Downloads + streaming use C++ libtorrent engine (multi-audio HLS)",
                level=ActivityLevel.GOOD,
                area="system",
            )
            return

        if self._aria2:
            await self._aria2.start()
            await activity.say(
                "Bulk downloads use aria2 (stream still uses WebTorrent)",
                level=ActivityLevel.GOOD,
                area="system",
            )
        elif self._node_wanted:
            await self._ensure_node()
        else:
            self._sim = SimulatedEngine()
            await activity.say(
                "No C++/aria2/Node — using simulated engine",
                level=ActivityLevel.WARN,
                area="system",
            )

    async def stop(self) -> None:
        if self._cpp:
            await self._cpp.stop()
            self._cpp = None
        if self._node:
            await self._node.stop()
            self._node = None
        if self._aria2:
            await self._aria2.stop()
            self._aria2 = None

    async def _ensure_node(self) -> NodeWebTorrentEngine:
        if self._node is None:
            if not self._node_wanted:
                raise RuntimeError("Node WebTorrent worker is not available")
            self._node = NodeWebTorrentEngine()
            await self._node.start()
        return self._node

    async def add(
        self,
        magnet_or_url: str,
        config: DownloadConfig,
        *,
        download_id: Optional[str] = None,
    ) -> DownloadState:
        if self._cpp:
            engine: DownloadEngine = self._cpp
        elif config.mode is DownloadMode.STREAM and self._node_wanted:
            engine = await self._ensure_node()
        elif self._aria2 and config.mode is not DownloadMode.STREAM:
            engine = self._aria2
        elif config.mode is DownloadMode.STREAM and self._aria2 and not self._node_wanted:
            await activity.say(
                "Streaming via aria2 (sequential) — Node/C++ not available",
                level=ActivityLevel.WARN,
                area="download",
            )
            engine = self._aria2
        elif self._aria2:
            engine = self._aria2
        elif self._node_wanted:
            engine = await self._ensure_node()
        else:
            if self._sim is None:
                self._sim = SimulatedEngine()
            engine = self._sim

        state = await engine.add(
            magnet_or_url, config, download_id=download_id
        )
        self._owner[state.id] = engine
        return state

    def _engine_for(self, download_id: str) -> DownloadEngine:
        eng = self._owner.get(download_id)
        if eng is None:
            raise KeyError(download_id)
        return eng

    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        eng = self._owner.pop(download_id, None)
        if eng:
            await eng.remove(download_id, delete_files=delete_files)

    async def pause(self, download_id: str) -> DownloadState:
        return await self._engine_for(download_id).pause(download_id)

    async def resume(self, download_id: str) -> DownloadState:
        return await self._engine_for(download_id).resume(download_id)

    async def get(self, download_id: str) -> Optional[DownloadState]:
        eng = self._owner.get(download_id)
        if not eng:
            for backend in (self._cpp, self._node, self._aria2, self._sim):
                if backend is None:
                    continue
                st = await backend.get(download_id)
                if st:
                    self._owner[download_id] = backend
                    return st
            return None
        return await eng.get(download_id)

    async def list(self) -> list[DownloadState]:
        seen: set[str] = set()
        out: list[DownloadState] = []
        for backend in (self._cpp, self._node, self._aria2, self._sim):
            if backend is None:
                continue
            for st in await backend.list():
                if st.id in seen:
                    continue
                seen.add(st.id)
                self._owner.setdefault(st.id, backend)
                out.append(st)
        return out

    async def set_playhead(
        self, download_id: str, offset_bytes: int
    ) -> DownloadState:
        return await self._engine_for(download_id).set_playhead(
            download_id, offset_bytes
        )

    async def tick(self) -> None:
        if self._cpp:
            await self._cpp.tick()
        if self._aria2:
            await self._aria2.tick()
        if self._node:
            await self._node.tick()
        if self._sim:
            await self._sim.tick()

    async def proxy_media(self, download_id: str, range_header: Optional[str]):
        eng = self._owner.get(download_id) or await self._resolve_owner(download_id)
        if isinstance(eng, (CppEngineClient, NodeWebTorrentEngine)):
            return await eng.proxy_media(download_id, range_header)
        raise RuntimeError("Use disk media path for aria2 downloads")

    async def resolve_media_source(
        self, download_id: str
    ) -> tuple[str, Optional[Path]]:
        """
        Returns (kind, path_or_none).
        kind: 'http' → use engine media URL ; 'file' → local Path
        """
        eng = self._owner.get(download_id) or await self._resolve_owner(download_id)
        if isinstance(eng, CppEngineClient):
            path = await eng.resolve_media_path(download_id)
            if path and path.is_file():
                return "file", path
            if not eng.media_base:
                raise RuntimeError("C++ engine media base missing")
            return "http", None
        if isinstance(eng, NodeWebTorrentEngine):
            if not eng.media_base:
                raise RuntimeError("Node media base missing")
            return "http", None
        if isinstance(eng, Aria2Engine):
            path = await eng.resolve_media_path(download_id)
            if not path or not path.is_file():
                raise RuntimeError("Media file not on disk yet — still downloading")
            return "file", path
        raise RuntimeError("No media source for this download")

    async def _resolve_owner(self, download_id: str) -> DownloadEngine:
        st = await self.get(download_id)
        if st is None:
            raise KeyError(download_id)
        eng = self._owner.get(download_id)
        if eng is None:
            raise KeyError(download_id)
        return eng


def build_default_engine() -> DownloadEngine:
    if cpp_engine_available() or aria2_engine_available() or node_stream_available():
        return HybridEngine()
    return SimulatedEngine()
