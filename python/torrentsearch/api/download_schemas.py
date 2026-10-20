"""API schemas for the downloader."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from ..downloader.modes import DownloadConfig, DownloadMode


class CreateDownloadRequest(BaseModel):
    magnet: Optional[str] = Field(default=None, description="magnet:? URI")
    torrent_result_id: Optional[int] = Field(
        default=None, description="Use magnet from a saved search torrent row"
    )
    mode: DownloadMode = DownloadMode.GENERAL
    preset: Optional[str] = Field(
        default=None,
        description="Optional named preset: general, stream, stream_smooth, stream_catchup, preview, limited, seed, fastest, mirror",
    )
    config: Optional[DownloadConfig] = None
    save_path: Optional[str] = None


class PlayheadUpdate(BaseModel):
    offset_bytes: Optional[int] = Field(default=None, ge=0)
    offset_seconds: Optional[float] = Field(default=None, ge=0)
    bitrate_bps: Optional[int] = Field(
        default=None,
        ge=1,
        description="If set with offset_seconds, converts to bytes and updates stream bitrate hint",
    )


class DownloadOut(BaseModel):
    id: str
    magnet_or_url: str
    mode: str
    status: str
    name: Optional[str] = None
    info_hash: Optional[str] = None
    save_path: str
    total_bytes: int
    downloaded_bytes: int
    progress: float
    download_rate: float
    upload_rate: float
    num_peers: int
    playhead: int
    buffered_bytes_ahead: int
    buffered_seconds_ahead: Optional[float] = None
    underrun: bool = False
    eta_seconds: Optional[float] = None
    error: Optional[str] = None
    schedule_notes: list[str] = []
    files: list[dict] = []
    media_file_index: int = -1
    created_at: datetime
    updated_at: datetime


class ModeInfo(BaseModel):
    id: str
    description: str


class PresetInfo(BaseModel):
    id: str
    mode: str
