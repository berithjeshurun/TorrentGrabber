"""Language-agnostic torrent-engine HTTP client (Python)."""

from .client import (
    AsyncTorrentEngine,
    EngineError,
    TorrentEngine,
    __sdk_version__,
)

__all__ = ["TorrentEngine", "AsyncTorrentEngine", "EngineError"]
__version__ = __sdk_version__
