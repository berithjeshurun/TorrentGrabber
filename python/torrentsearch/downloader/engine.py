"""Downloader engine interface + in-process simulated engine for policy testing."""

from __future__ import annotations

import abc
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from ..activity import ActivityLevel, activity
from .modes import DownloadConfig, DownloadMode
from .scheduler import (
    PieceBand,
    PieceInfo,
    schedule_for_mode,
    should_reprioritize_seek,
)


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    METADATA = "metadata"
    DOWNLOADING = "downloading"
    STREAMING = "streaming"
    PAUSED = "paused"
    COMPLETED = "completed"
    SEEDING = "seeding"
    ERROR = "error"
    STOPPED = "stopped"


@dataclass
class BufferedRange:
    start: int
    end: int  # exclusive


@dataclass
class DownloadState:
    id: str
    magnet_or_url: str
    mode: DownloadMode
    status: DownloadStatus
    config: DownloadConfig
    save_path: str
    name: Optional[str] = None
    info_hash: Optional[str] = None
    total_bytes: int = 0
    downloaded_bytes: int = 0
    progress: float = 0.0
    download_rate: float = 0.0
    upload_rate: float = 0.0
    num_peers: int = 0
    playhead: int = 0
    buffered_bytes_ahead: int = 0
    buffered_seconds_ahead: Optional[float] = None
    underrun: bool = False
    selected_files: list[int] = field(default_factory=list)
    files: list[dict] = field(default_factory=list)
    media_file_index: int = -1
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_schedule_notes: list[str] = field(default_factory=list)
    seek_pending: bool = False

    @property
    def eta_seconds(self) -> Optional[float]:
        remain = max(0, self.total_bytes - self.downloaded_bytes)
        if self.download_rate <= 0 or remain <= 0:
            return 0.0 if remain <= 0 else None
        return remain / self.download_rate


class DownloadEngine(abc.ABC):
    """Backend that actually speaks BitTorrent (libtorrent, qBittorrent, …)."""

    @abc.abstractmethod
    async def add(
        self,
        magnet_or_url: str,
        config: DownloadConfig,
        *,
        download_id: Optional[str] = None,
    ) -> DownloadState:
        ...

    @abc.abstractmethod
    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        ...

    @abc.abstractmethod
    async def pause(self, download_id: str) -> DownloadState:
        ...

    @abc.abstractmethod
    async def resume(self, download_id: str) -> DownloadState:
        ...

    @abc.abstractmethod
    async def get(self, download_id: str) -> Optional[DownloadState]:
        ...

    @abc.abstractmethod
    async def list(self) -> list[DownloadState]:
        ...

    @abc.abstractmethod
    async def set_playhead(self, download_id: str, offset_bytes: int) -> DownloadState:
        ...

    @abc.abstractmethod
    async def tick(self) -> None:
        """Advance engine / reapply schedules (called by manager loop)."""
        ...


@dataclass
class _SimTorrent:
    state: DownloadState
    piece_length: int = 256 * 1024
    pieces: list[PieceInfo] = field(default_factory=list)
    finished: set[int] = field(default_factory=set)
    last_playhead: int = 0
    priorities: dict[int, int] = field(default_factory=dict)
    was_underrun: bool = False
    last_status: Optional[DownloadStatus] = None


class SimulatedEngine(DownloadEngine):
    """
    Deterministic in-memory engine used to validate mode policies without
    libtorrent. Approximates piece completion using worker allocations.
    """

    def __init__(self, *, default_size: int = 64 * 1024 * 1024):
        self._items: dict[str, _SimTorrent] = {}
        self._default_size = default_size
        self._lock = asyncio.Lock()

    async def add(
        self,
        magnet_or_url: str,
        config: DownloadConfig,
        *,
        download_id: Optional[str] = None,
    ) -> DownloadState:
        async with self._lock:
            did = download_id or uuid.uuid4().hex[:12]
            size = self._default_size
            piece_len = 256 * 1024
            n_pieces = max(1, math_ceil_div(size, piece_len))
            pieces = []
            for i in range(n_pieces):
                offset = i * piece_len
                length = min(piece_len, size - offset)
                pieces.append(
                    PieceInfo(
                        index=i,
                        offset=offset,
                        length=length,
                        rarity=float((i * 7) % 13),
                    )
                )

            status = (
                DownloadStatus.STREAMING
                if config.mode is DownloadMode.STREAM
                else DownloadStatus.DOWNLOADING
            )
            if config.mode is DownloadMode.SEED:
                for p in pieces:
                    p.finished = True
                status = DownloadStatus.SEEDING

            selected = list(config.selective.file_indexes)

            state = DownloadState(
                id=did,
                magnet_or_url=magnet_or_url,
                mode=config.mode,
                status=status,
                config=config,
                save_path=config.save_path,
                name=f"sim-{did}",
                info_hash=did * 2 if len(did) < 40 else did[:40],
                total_bytes=size,
                downloaded_bytes=size if status is DownloadStatus.SEEDING else 0,
                progress=1.0 if status is DownloadStatus.SEEDING else 0.0,
                selected_files=selected,
            )
            item = _SimTorrent(state=state, piece_length=piece_len, pieces=pieces)
            item.last_status = status
            self._items[did] = item

        mode_label = config.mode.value
        if config.mode is DownloadMode.STREAM:
            await activity.say(
                f"Started streaming “{state.name}” — filling the buffer near the playhead",
                level=ActivityLevel.GOOD,
                area="download",
            )
        elif config.mode is DownloadMode.SEED:
            await activity.say(
                f"Seeding “{state.name}”",
                level=ActivityLevel.GOOD,
                area="download",
            )
        else:
            await activity.say(
                f"Started download “{state.name}” in {mode_label} mode",
                level=ActivityLevel.GOOD,
                area="download",
            )
        return state

    async def remove(self, download_id: str, *, delete_files: bool = False) -> None:
        async with self._lock:
            item = self._items.pop(download_id, None)
        if item:
            await activity.say(
                f"Removed “{item.state.name or download_id}”",
                area="download",
            )

    async def pause(self, download_id: str) -> DownloadState:
        item = self._require(download_id)
        item.state.status = DownloadStatus.PAUSED
        item.state.updated_at = datetime.now(timezone.utc)
        await activity.say(
            f"Paused “{item.state.name or download_id}”",
            area="download",
        )
        return item.state

    async def resume(self, download_id: str) -> DownloadState:
        item = self._require(download_id)
        item.state.status = (
            DownloadStatus.STREAMING
            if item.state.mode is DownloadMode.STREAM
            else DownloadStatus.DOWNLOADING
        )
        item.state.updated_at = datetime.now(timezone.utc)
        await activity.say(
            f"Resumed “{item.state.name or download_id}”",
            level=ActivityLevel.GOOD,
            area="download",
        )
        return item.state

    async def get(self, download_id: str) -> Optional[DownloadState]:
        item = self._items.get(download_id)
        return item.state if item else None

    async def list(self) -> list[DownloadState]:
        return [t.state for t in self._items.values()]

    async def set_playhead(self, download_id: str, offset_bytes: int) -> DownloadState:
        item = self._require(download_id)
        offset_bytes = max(0, min(offset_bytes, max(0, item.state.total_bytes - 1)))
        old = item.state.playhead
        item.state.playhead = offset_bytes
        if should_reprioritize_seek(old, offset_bytes, item.state.config.stream):
            item.state.seek_pending = True
            item.last_playhead = offset_bytes
            item.state.underrun = False
            await activity.say(
                f"Seek on “{item.state.name}” — rebuilding the stream buffer",
                area="download",
            )
        item.state.updated_at = datetime.now(timezone.utc)
        return item.state

    async def tick(self) -> None:
        async with self._lock:
            for item in list(self._items.values()):
                self._tick_one(item)

    def _tick_one(self, item: _SimTorrent) -> None:
        st = item.state
        if st.status in (
            DownloadStatus.PAUSED,
            DownloadStatus.ERROR,
            DownloadStatus.STOPPED,
            DownloadStatus.COMPLETED,
            DownloadStatus.SEEDING,
        ):
            return

        if st.seek_pending:
            st.last_schedule_notes = ["seek: bands rebuilt around new playhead"]
            st.seek_pending = False

        schedule = schedule_for_mode(
            st.mode,
            item.pieces,
            config=st.config,
            playhead=st.playhead,
            file_size=st.total_bytes,
            progress=st.progress,
            previously_underrun=st.underrun,
        )
        st.last_schedule_notes = list(schedule.notes)
        st.buffered_bytes_ahead = schedule.buffered_bytes_ahead
        st.buffered_seconds_ahead = schedule.buffered_seconds_ahead

        if schedule.underrun and not item.was_underrun:
            activity.say_nowait(
                f"Buffer on “{st.name}” is running low — catching up near the playhead",
                level=ActivityLevel.WARN,
                area="download",
            )
        elif item.was_underrun and not schedule.underrun:
            activity.say_nowait(
                f"Buffer on “{st.name}” looks healthy again",
                level=ActivityLevel.GOOD,
                area="download",
            )
        item.was_underrun = schedule.underrun
        st.underrun = schedule.underrun

        item.priorities = {a.index: a.priority for a in schedule.assignments}

        budget = max(1, schedule.workers.total)
        if st.mode is DownloadMode.STREAM and st.underrun:
            budget = max(budget, schedule.workers.urgent + schedule.workers.sequential)

        band_rank = {
            PieceBand.URGENT: 0,
            PieceBand.SEQUENTIAL: 1,
            PieceBand.LOOKAHEAD: 2,
            PieceBand.SPECULATIVE: 3,
            PieceBand.NORMAL: 4,
            PieceBand.DEFERRED: 5,
        }
        ordered = sorted(
            (
                a
                for a in schedule.assignments
                if a.priority > 0 and not item.pieces[a.index].finished
            ),
            key=lambda a: (band_rank.get(a.band, 9), -a.priority, a.index),
        )

        completed_bytes = 0
        for a in ordered[:budget]:
            piece = item.pieces[a.index]
            if piece.finished:
                continue
            piece.finished = True
            item.finished.add(piece.index)
            completed_bytes += piece.length

        st.downloaded_bytes = sum(p.length for p in item.pieces if p.finished)
        st.progress = st.downloaded_bytes / st.total_bytes if st.total_bytes else 0.0
        st.download_rate = float(completed_bytes)
        if st.mode is DownloadMode.LIMITED:
            st.num_peers = min(st.config.limited.max_peers, 3)
            cap = st.config.limited.download_rate_limit_kbps * 1024
            if cap > 0:
                st.download_rate = min(st.download_rate, float(cap))
        elif (
            st.mode is DownloadMode.MIRROR
            and st.progress < st.config.mirror.prefer_webseeds_until_progress
        ):
            st.num_peers = 12
            st.download_rate *= 1.5
        else:
            st.num_peers = 8

        st.updated_at = datetime.now(timezone.utc)

        if st.progress >= 1.0:
            if st.mode is DownloadMode.SEED:
                st.status = DownloadStatus.SEEDING
            else:
                st.status = DownloadStatus.COMPLETED
            if item.last_status != st.status:
                activity.say_nowait(
                    f"Finished “{st.name}”"
                    + (" — now seeding" if st.status is DownloadStatus.SEEDING else ""),
                    level=ActivityLevel.GOOD,
                    area="download",
                )
                item.last_status = st.status

    def _require(self, download_id: str) -> _SimTorrent:
        item = self._items.get(download_id)
        if not item:
            raise KeyError(download_id)
        return item


def math_ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _default_engine() -> DownloadEngine:
    try:
        from .hybrid_engine import build_default_engine

        return build_default_engine()
    except Exception:
        pass
    return SimulatedEngine()


class DownloadManager:
    """Owns an engine and a background tick loop."""

    def __init__(self, engine: Optional[DownloadEngine] = None, tick_interval: float = 0.5):
        self.engine = engine or _default_engine()
        self.tick_interval = tick_interval
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._health_every = 10
        self._ticks = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        activity.set_manager_running(True)
        start = getattr(self.engine, "start", None)
        if callable(start):
            await start()
        kind = type(self.engine).__name__
        await activity.say(
            f"Downloader is ready ({kind})",
            level=ActivityLevel.GOOD,
            area="system",
        )
        self._task = asyncio.create_task(self._loop(), name="download-manager")

    async def stop(self) -> None:
        self._running = False
        activity.set_manager_running(False)
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        stop = getattr(self.engine, "stop", None)
        if callable(stop):
            try:
                await stop()
            except Exception:
                pass
        await activity.say("Downloader stopped", area="system")

    async def _refresh_counts(self) -> None:
        states = await self.engine.list()
        active = [
            s
            for s in states
            if s.status
            in (
                DownloadStatus.DOWNLOADING,
                DownloadStatus.STREAMING,
                DownloadStatus.METADATA,
                DownloadStatus.QUEUED,
            )
        ]
        streaming = sum(1 for s in active if s.status is DownloadStatus.STREAMING)
        underruns = sum(1 for s in active if s.underrun)
        activity.update_download_counts(
            active=len(active),
            streaming=streaming,
            underruns=underruns,
        )

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.engine.tick()
                self._ticks += 1
                if self._ticks % self._health_every == 0:
                    await self._refresh_counts()
            except Exception as exc:
                await activity.say(
                    f"Downloader hiccup (will keep going): {exc}",
                    level=ActivityLevel.WARN,
                    area="download",
                )
            await asyncio.sleep(self.tick_interval)
