"""Download modes and tunable policy configs."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DownloadMode(str, Enum):
    GENERAL = "general"
    STREAM = "stream"
    SELECTIVE = "selective"
    PREVIEW = "preview"
    SEED = "seed"
    LIMITED = "limited"
    FASTEST = "fastest"
    MIRROR = "mirror"


class PieceBand(str, Enum):
    """Priority bands applied to torrent pieces."""

    URGENT = "urgent"  # at/near playhead — must have
    SEQUENTIAL = "sequential"  # contiguous ahead of playhead
    LOOKAHEAD = "lookahead"  # buffer window
    SPECULATIVE = "speculative"  # scatter / rarest elsewhere
    DEFERRED = "deferred"  # ignore for now (priority 0)
    NORMAL = "normal"  # general rarest-first


class StreamConfig(BaseModel):
    """
    Stream-mode scheduler.

    Workers are logical request slots (parallel piece fetches), split across bands.
    Speculative workers fill distant ranges so the sequential band stays ahead
    of the playhead without carrying the whole torrent alone.
    """

    # How much data is "must have" right after the playhead.
    playhead_window_bytes: int = Field(default=8 * 1024 * 1024, ge=256 * 1024)
    # Extra contiguous buffer after the urgent window.
    lookahead_bytes: int = Field(default=24 * 1024 * 1024, ge=0)
    # Soft deadline pressure near playhead (ms). 0 disables.
    piece_deadline_ms: int = Field(default=3000, ge=0)
    # Steeper deadlines for pieces closest to playhead (ms).
    urgent_deadline_ms: int = Field(default=1500, ge=0)
    # Total parallel piece requests.
    max_workers: int = Field(default=8, ge=1, le=64)
    # Fraction of workers reserved for speculative scatter (0–0.5 recommended).
    speculative_ratio: float = Field(default=0.25, ge=0.0, le=0.75)
    # Minimum seconds of media we try to keep buffered (bitrate-aware when known).
    target_buffer_seconds: float = Field(default=30.0, ge=1.0)
    # If buffered seconds fall below this, steal speculative slots into sequential.
    underrun_seconds: float = Field(default=8.0, ge=0.5)
    # Exit underrun only after buffer recovers past this (hysteresis).
    recover_seconds: float = Field(default=16.0, ge=1.0)
    # Byte-only underrun floor when bitrate is unknown (fraction of playhead window).
    underrun_byte_fraction: float = Field(default=0.5, ge=0.1, le=1.0)
    # Speculative picks: "scatter" | "rarest" | "hybrid"
    speculative_strategy: str = Field(default="hybrid")
    # Even spacing for scatter picks across the file (piece stride hint).
    speculative_stride_pieces: int = Field(default=8, ge=1, le=256)
    # Re-center bands when playhead jumps more than this (seek).
    seek_reprioritize_bytes: int = Field(default=2 * 1024 * 1024, ge=0)
    # Optional bitrate hint (bits/s). Improves second-based buffer math.
    bitrate_bps: Optional[int] = Field(default=None, ge=1)
    # Keep this many pieces beyond the urgent window at sequential priority.
    sequential_multiplier: float = Field(
        default=1.0,
        ge=0.25,
        le=4.0,
        description="Sequential band size as a multiple of the urgent window",
    )


class GeneralConfig(BaseModel):
    """Classic download — rarest-first with optional endgame / peer bias."""

    max_workers: int = Field(default=16, ge=1, le=128)
    prefer_fastest_peers: bool = False
    endgame_threshold: float = Field(
        default=0.95,
        ge=0.5,
        le=0.999,
        description="Progress ratio that triggers endgame (multi-peer last pieces)",
    )
    enable_dht: bool = True
    enable_lsd: bool = True
    enable_pex: bool = True


class SelectiveConfig(BaseModel):
    """Download only chosen file indexes (multi-file torrents)."""

    file_indexes: list[int] = Field(default_factory=list)
    # Also pull tiny companion files (subs/nfo) under this size.
    auto_include_max_bytes: int = Field(default=512 * 1024, ge=0)


class PreviewConfig(BaseModel):
    """Sample head + sparse mid/end for 'is this the right file?'."""

    head_bytes: int = Field(default=16 * 1024 * 1024, ge=1024 * 1024)
    sample_count: int = Field(default=4, ge=0, le=32)
    sample_bytes: int = Field(default=1 * 1024 * 1024, ge=64 * 1024)
    include_tail_bytes: int = Field(default=2 * 1024 * 1024, ge=0)
    max_workers: int = Field(default=6, ge=1, le=32)


class SeedConfig(BaseModel):
    """After download (or if already complete): upload policy."""

    target_ratio: float = Field(default=1.5, ge=0.0)
    seed_time_seconds: Optional[int] = Field(default=None, ge=0)
    upload_rate_limit_kbps: Optional[int] = Field(default=None, ge=0)


class LimitedConfig(BaseModel):
    """Metered / quiet profile."""

    max_peers: int = Field(default=20, ge=1, le=200)
    download_rate_limit_kbps: int = Field(default=512, ge=0)
    upload_rate_limit_kbps: int = Field(default=64, ge=0)
    max_workers: int = Field(default=4, ge=1, le=16)
    disable_speculative: bool = True


class FastestConfig(BaseModel):
    """Bias toward finishing quickly on strong swarms (less swarm-friendly)."""

    max_workers: int = Field(default=24, ge=1, le=128)
    prefer_fastest_peers: bool = True
    sequential_boost_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=0,
        description="Small sequential head-start so files open sooner",
    )


class MirrorConfig(BaseModel):
    """BitTorrent + optional HTTP webseeds for faster start."""

    prefer_webseeds_until_progress: float = Field(default=0.05, ge=0.0, le=1.0)
    max_workers: int = Field(default=16, ge=1, le=128)
    webseed_urls: list[str] = Field(default_factory=list)


class DownloadConfig(BaseModel):
    mode: DownloadMode = DownloadMode.GENERAL
    save_path: str = Field(default="./downloads")
    # Shared caps (modes may override via their nested config).
    download_rate_limit_kbps: Optional[int] = None
    upload_rate_limit_kbps: Optional[int] = None
    max_connections: Optional[int] = None

    stream: StreamConfig = Field(default_factory=StreamConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    selective: SelectiveConfig = Field(default_factory=SelectiveConfig)
    preview: PreviewConfig = Field(default_factory=PreviewConfig)
    seed: SeedConfig = Field(default_factory=SeedConfig)
    limited: LimitedConfig = Field(default_factory=LimitedConfig)
    fastest: FastestConfig = Field(default_factory=FastestConfig)
    mirror: MirrorConfig = Field(default_factory=MirrorConfig)


# Sensible product presets
PRESETS: dict[str, DownloadConfig] = {
    "general": DownloadConfig(mode=DownloadMode.GENERAL),
    "stream": DownloadConfig(mode=DownloadMode.STREAM),
    "stream_smooth": DownloadConfig(
        mode=DownloadMode.STREAM,
        stream=StreamConfig(
            playhead_window_bytes=12 * 1024 * 1024,
            lookahead_bytes=48 * 1024 * 1024,
            max_workers=12,
            speculative_ratio=0.2,
            target_buffer_seconds=45,
            underrun_seconds=10,
            recover_seconds=20,
            sequential_multiplier=1.25,
        ),
    ),
    "stream_catchup": DownloadConfig(
        mode=DownloadMode.STREAM,
        stream=StreamConfig(
            playhead_window_bytes=16 * 1024 * 1024,
            lookahead_bytes=32 * 1024 * 1024,
            max_workers=16,
            speculative_ratio=0.1,
            underrun_seconds=12,
            recover_seconds=24,
            sequential_multiplier=1.5,
            piece_deadline_ms=2000,
            urgent_deadline_ms=800,
        ),
    ),
    "preview": DownloadConfig(mode=DownloadMode.PREVIEW),
    "limited": DownloadConfig(mode=DownloadMode.LIMITED),
    "seed": DownloadConfig(mode=DownloadMode.SEED),
    "fastest": DownloadConfig(mode=DownloadMode.FASTEST),
    "mirror": DownloadConfig(mode=DownloadMode.MIRROR),
}
