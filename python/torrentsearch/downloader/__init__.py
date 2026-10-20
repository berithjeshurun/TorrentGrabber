"""Downloader package — multi-mode BitTorrent download/stream policies."""

from .engine import DownloadEngine, DownloadManager, DownloadState, DownloadStatus, SimulatedEngine
from .modes import PRESETS, DownloadConfig, DownloadMode
from .scheduler import (
    PieceAssignment,
    PieceInfo,
    ScheduleResult,
    schedule_for_mode,
    schedule_stream,
    should_reprioritize_seek,
)

__all__ = [
    "DownloadEngine",
    "DownloadManager",
    "DownloadState",
    "DownloadStatus",
    "SimulatedEngine",
    "PRESETS",
    "DownloadConfig",
    "DownloadMode",
    "PieceAssignment",
    "PieceInfo",
    "ScheduleResult",
    "schedule_for_mode",
    "schedule_stream",
    "should_reprioritize_seek",
]
