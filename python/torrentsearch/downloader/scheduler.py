"""
Piece-priority schedulers.

Stream algorithm (strengthened)
-------------------------------
1. Track playhead byte offset (from player or estimated).
2. Partition the active file into bands:
     URGENT     [playhead, playhead + urgent)
     SEQUENTIAL [urgent_end, urgent_end + sequential)
     LOOKAHEAD  [seq_end, seq_end + lookahead)
     SPECULATIVE remaining unfinished pieces (scatter/rarest/hybrid)
3. Allocate worker slots by ratio; on buffer underrun, steal speculative
   slots into sequential/urgent (adaptive). Hysteresis via recover_seconds
   prevents flapping when buffer hovers near the underrun threshold.
4. On seek: if jump > threshold, rebuild bands immediately.
5. Speculative workers never target the urgent/sequential/lookahead window
   (anti-overlap). Scatter uses even stride across the file.
6. Deadline gradient: pieces closer to playhead get tighter soft deadlines.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from .modes import (
    DownloadConfig,
    DownloadMode,
    FastestConfig,
    GeneralConfig,
    MirrorConfig,
    PieceBand,
    PreviewConfig,
    StreamConfig,
)


@dataclass
class PieceInfo:
    index: int
    offset: int
    length: int
    finished: bool = False
    # Optional rarity score from engine (higher = rarer). None if unknown.
    rarity: Optional[float] = None


@dataclass
class PieceAssignment:
    index: int
    band: PieceBand
    priority: int  # libtorrent-style 0..7 (7 highest)
    deadline_ms: Optional[int] = None


@dataclass
class WorkerPlan:
    """How many parallel request slots each band should get."""

    urgent: int = 0
    sequential: int = 0
    lookahead: int = 0
    speculative: int = 0
    normal: int = 0

    @property
    def total(self) -> int:
        return (
            self.urgent
            + self.sequential
            + self.lookahead
            + self.speculative
            + self.normal
        )


@dataclass
class ScheduleResult:
    assignments: list[PieceAssignment] = field(default_factory=list)
    workers: WorkerPlan = field(default_factory=WorkerPlan)
    buffered_bytes_ahead: int = 0
    buffered_seconds_ahead: Optional[float] = None
    underrun: bool = False
    notes: list[str] = field(default_factory=list)


def _priority_for_band(band: PieceBand) -> int:
    return {
        PieceBand.URGENT: 7,
        PieceBand.SEQUENTIAL: 6,
        PieceBand.LOOKAHEAD: 4,
        PieceBand.SPECULATIVE: 2,
        PieceBand.NORMAL: 3,
        PieceBand.DEFERRED: 0,
    }[band]


def contiguous_bytes_ahead(
    pieces: Sequence[PieceInfo],
    playhead: int,
) -> int:
    """How many contiguous finished bytes exist starting at playhead."""
    relevant = sorted(
        (p for p in pieces if p.offset + p.length > playhead),
        key=lambda p: p.offset,
    )
    cursor = playhead
    for p in relevant:
        if p.offset > cursor:
            break
        if not p.finished:
            break
        cursor = max(cursor, p.offset + p.length)
    return max(0, cursor - playhead)


def estimate_seconds(bytes_ahead: int, bitrate_bps: Optional[int]) -> Optional[float]:
    if not bitrate_bps:
        return None
    return (bytes_ahead * 8) / float(bitrate_bps)


def detect_underrun(
    *,
    buffered_bytes: int,
    buffered_seconds: Optional[float],
    cfg: StreamConfig,
    previously_underrun: bool = False,
) -> bool:
    """
    Underrun with hysteresis: enter below underrun_seconds (or byte floor),
    exit only after recover_seconds (or 2x byte floor) to avoid thrashing.
    """
    if buffered_seconds is not None:
        if previously_underrun:
            return buffered_seconds < cfg.recover_seconds
        return buffered_seconds < cfg.underrun_seconds

    floor = int(cfg.playhead_window_bytes * cfg.underrun_byte_fraction)
    if previously_underrun:
        return buffered_bytes < floor * 2
    return buffered_bytes < floor


def allocate_stream_workers(cfg: StreamConfig, *, underrun: bool) -> WorkerPlan:
    """
    Split max_workers across bands.

    Normal (healthy buffer):
      ~50% sequential+urgent, ~25% lookahead, ~25% speculative (ratio-driven)

    Underrun:
      speculative → 0, lookahead folded into sequential, urgent kept high.
    """
    n = cfg.max_workers
    if underrun or cfg.speculative_ratio <= 0:
        spec = 0
    else:
        spec = max(0, int(round(n * cfg.speculative_ratio)))
        spec = min(spec, n // 2)

    rest = n - spec
    if underrun:
        # Pour almost everything into urgent + sequential.
        urgent = max(2, rest // 2) if rest else 0
        sequential = max(0, rest - urgent)
        lookahead = 0
    else:
        urgent = max(1, rest // 3) if rest else 0
        sequential = max(1, rest // 2) if rest > 1 else max(0, rest - urgent)
        used = urgent + sequential
        lookahead = max(0, rest - used)

    # Fix rounding if over budget.
    while urgent + sequential + lookahead + spec > n:
        if spec > 0:
            spec -= 1
        elif lookahead > 0:
            lookahead -= 1
        elif sequential > 1:
            sequential -= 1
        else:
            urgent = max(0, urgent - 1)

    # Fill any unused slots into sequential (prefer contiguous progress).
    used = urgent + sequential + lookahead + spec
    if used < n:
        sequential += n - used

    return WorkerPlan(
        urgent=urgent,
        sequential=sequential,
        lookahead=lookahead,
        speculative=spec,
    )


def _pieces_in_range(
    pieces: Sequence[PieceInfo],
    start: int,
    end: int,
    *,
    unfinished_only: bool = True,
) -> list[PieceInfo]:
    out = []
    for p in pieces:
        if unfinished_only and p.finished:
            continue
        if p.offset < end and (p.offset + p.length) > start:
            out.append(p)
    out.sort(key=lambda p: p.offset)
    return out


def _pick_speculative(
    candidates: list[PieceInfo],
    limit: int,
    strategy: str,
    rng: random.Random,
    *,
    stride: int = 8,
) -> list[PieceInfo]:
    if limit <= 0 or not candidates:
        return []
    strat = strategy.lower()
    pool = list(candidates)
    if strat == "rarest":
        pool.sort(key=lambda p: (p.rarity is None, -(p.rarity or 0), p.index))
        return pool[:limit]
    if strat == "scatter":
        # Even coverage: take every Nth piece by index, then fill randomly.
        by_idx = sorted(pool, key=lambda p: p.index)
        spaced: list[PieceInfo] = []
        step = max(1, stride)
        for offset in range(step):
            spaced.extend(by_idx[offset::step])
        # Deduplicate while preserving order.
        seen: set[int] = set()
        ordered: list[PieceInfo] = []
        for p in spaced:
            if p.index not in seen:
                seen.add(p.index)
                ordered.append(p)
        if len(ordered) < limit:
            rest = [p for p in pool if p.index not in seen]
            rng.shuffle(rest)
            ordered.extend(rest)
        return ordered[:limit]
    # hybrid: half rarest, half scatter
    half = max(1, limit // 2)
    by_rare = sorted(
        pool, key=lambda p: (p.rarity is None, -(p.rarity or 0), p.index)
    )
    rare = by_rare[:half]
    rare_ids = {p.index for p in rare}
    rest = [p for p in pool if p.index not in rare_ids]
    scatter = _pick_speculative(
        rest, limit - len(rare), "scatter", rng, stride=stride
    )
    return (rare + scatter)[:limit]


def _deadline_for_piece(
    piece: PieceInfo,
    *,
    playhead: int,
    band: PieceBand,
    cfg: StreamConfig,
) -> Optional[int]:
    if cfg.piece_deadline_ms <= 0:
        return None
    if band is PieceBand.URGENT:
        base = cfg.urgent_deadline_ms or cfg.piece_deadline_ms
        # Closer to playhead → tighter deadline.
        dist = max(0, piece.offset - playhead)
        window = max(1, cfg.playhead_window_bytes)
        # Linear blend: at playhead = base, at window edge = piece_deadline_ms.
        t = min(1.0, dist / window)
        return int(base + t * max(0, cfg.piece_deadline_ms - base))
    if band is PieceBand.SEQUENTIAL:
        return cfg.piece_deadline_ms
    return None


def schedule_stream(
    pieces: Sequence[PieceInfo],
    *,
    playhead: int,
    file_size: int,
    cfg: StreamConfig,
    rng: Optional[random.Random] = None,
    previously_underrun: bool = False,
) -> ScheduleResult:
    rng = rng or random.Random()
    playhead = max(0, min(playhead, max(0, file_size - 1)))

    buffered = contiguous_bytes_ahead(pieces, playhead)
    seconds = estimate_seconds(buffered, cfg.bitrate_bps)
    underrun = detect_underrun(
        buffered_bytes=buffered,
        buffered_seconds=seconds,
        cfg=cfg,
        previously_underrun=previously_underrun,
    )

    # Expand windows using target buffer seconds when bitrate known.
    urgent_bytes = cfg.playhead_window_bytes
    lookahead_bytes = cfg.lookahead_bytes
    if cfg.bitrate_bps and cfg.target_buffer_seconds:
        target_bytes = int((cfg.bitrate_bps / 8.0) * cfg.target_buffer_seconds)
        urgent_bytes = max(urgent_bytes, target_bytes // 3)
        lookahead_bytes = max(lookahead_bytes, target_bytes - urgent_bytes)

    # Under pressure, grow the sequential band so catch-up is contiguous.
    seq_mult = cfg.sequential_multiplier
    if underrun:
        seq_mult = max(seq_mult, 1.5)

    urgent_start = playhead
    urgent_end = min(file_size, playhead + urgent_bytes)
    sequential_bytes = int(urgent_bytes * seq_mult)
    sequential_end = min(file_size, urgent_end + sequential_bytes)
    lookahead_end = min(file_size, sequential_end + lookahead_bytes)

    workers = allocate_stream_workers(cfg, underrun=underrun)

    # Cap piece lists by worker capacity so we don't over-assign.
    urgent_pcs = _pieces_in_range(pieces, urgent_start, urgent_end)[
        : max(workers.urgent * 4, 4)
    ]
    seq_pcs = _pieces_in_range(pieces, urgent_end, sequential_end)[
        : max(workers.sequential * 4, 4)
    ]
    look_pcs = _pieces_in_range(pieces, sequential_end, lookahead_end)[
        : max(workers.lookahead * 4, 1) if workers.lookahead else 0
    ]

    protected = {p.index for p in urgent_pcs + seq_pcs + look_pcs}
    speculative_pool = [
        p for p in pieces if not p.finished and p.index not in protected
    ]
    spec_limit = max(workers.speculative * 8, workers.speculative)
    spec_pcs = _pick_speculative(
        speculative_pool,
        spec_limit,
        cfg.speculative_strategy,
        rng,
        stride=cfg.speculative_stride_pieces,
    )

    assignments: list[PieceAssignment] = []
    notes: list[str] = []

    def add_all(batch: Iterable[PieceInfo], band: PieceBand) -> None:
        pri = _priority_for_band(band)
        for p in batch:
            assignments.append(
                PieceAssignment(
                    index=p.index,
                    band=band,
                    priority=pri,
                    deadline_ms=_deadline_for_piece(
                        p, playhead=playhead, band=band, cfg=cfg
                    ),
                )
            )

    add_all(urgent_pcs, PieceBand.URGENT)
    add_all(seq_pcs, PieceBand.SEQUENTIAL)
    add_all(look_pcs, PieceBand.LOOKAHEAD)
    add_all(spec_pcs, PieceBand.SPECULATIVE)

    assigned = {a.index for a in assignments}
    for p in pieces:
        if not p.finished and p.index not in assigned:
            assignments.append(
                PieceAssignment(
                    index=p.index,
                    band=PieceBand.DEFERRED,
                    priority=0,
                    deadline_ms=None,
                )
            )

    if underrun:
        notes.append("underrun: speculative workers reallocated to sequential/urgent")
    if previously_underrun and not underrun:
        notes.append("buffer recovered; speculative workers restored")
    notes.append(
        f"workers urgent={workers.urgent} seq={workers.sequential} "
        f"look={workers.lookahead} spec={workers.speculative}"
    )
    notes.append(
        f"buffer={buffered}B"
        + (f" ({seconds:.1f}s)" if seconds is not None else "")
    )

    return ScheduleResult(
        assignments=assignments,
        workers=workers,
        buffered_bytes_ahead=buffered,
        buffered_seconds_ahead=seconds,
        underrun=underrun,
        notes=notes,
    )


def schedule_general(
    pieces: Sequence[PieceInfo],
    *,
    progress: float,
    cfg: GeneralConfig,
) -> ScheduleResult:
    endgame = progress >= cfg.endgame_threshold
    assignments: list[PieceAssignment] = []
    unfinished = [p for p in pieces if not p.finished]
    if endgame:
        # Boost remaining; prefer rarer pieces slightly.
        unfinished.sort(
            key=lambda p: (p.rarity is None, -(p.rarity or 0), p.index)
        )
        for p in unfinished:
            assignments.append(
                PieceAssignment(p.index, PieceBand.URGENT, priority=7)
            )
        notes = [f"endgame active at progress={progress:.3f}"]
    else:
        for p in unfinished:
            assignments.append(
                PieceAssignment(p.index, PieceBand.NORMAL, priority=3)
            )
        notes = ["rarest-first (engine-native)"]
        if cfg.prefer_fastest_peers:
            notes.append("prefer_fastest_peers enabled")

    return ScheduleResult(
        assignments=assignments,
        workers=WorkerPlan(normal=cfg.max_workers),
        notes=notes,
    )


def schedule_preview(
    pieces: Sequence[PieceInfo],
    *,
    file_size: int,
    cfg: PreviewConfig,
) -> ScheduleResult:
    assignments: list[PieceAssignment] = []
    wanted_ranges: list[tuple[int, int]] = [(0, min(file_size, cfg.head_bytes))]
    if cfg.include_tail_bytes > 0 and file_size > cfg.include_tail_bytes:
        wanted_ranges.append((file_size - cfg.include_tail_bytes, file_size))

    if cfg.sample_count > 0 and file_size > cfg.head_bytes:
        span = file_size - cfg.head_bytes - cfg.include_tail_bytes
        if span > 0:
            for i in range(cfg.sample_count):
                center = cfg.head_bytes + int((i + 0.5) * span / cfg.sample_count)
                start = max(cfg.head_bytes, center - cfg.sample_bytes // 2)
                end = min(file_size - cfg.include_tail_bytes, start + cfg.sample_bytes)
                if end > start:
                    wanted_ranges.append((start, end))

    wanted_indexes: set[int] = set()
    for start, end in wanted_ranges:
        for p in _pieces_in_range(pieces, start, end):
            if p.index in wanted_indexes:
                continue
            wanted_indexes.add(p.index)
            assignments.append(
                PieceAssignment(p.index, PieceBand.URGENT, priority=7)
            )

    for p in pieces:
        if not p.finished and p.index not in wanted_indexes:
            assignments.append(
                PieceAssignment(p.index, PieceBand.DEFERRED, priority=0)
            )

    return ScheduleResult(
        assignments=assignments,
        workers=WorkerPlan(urgent=cfg.max_workers),
        notes=[f"preview targets {len(wanted_indexes)} pieces"],
    )


def schedule_fastest(
    pieces: Sequence[PieceInfo],
    *,
    cfg: FastestConfig,
) -> ScheduleResult:
    assignments: list[PieceAssignment] = []
    head = _pieces_in_range(pieces, 0, cfg.sequential_boost_bytes)
    head_ids = {p.index for p in head}
    for p in head:
        assignments.append(PieceAssignment(p.index, PieceBand.SEQUENTIAL, priority=6))
    for p in pieces:
        if not p.finished and p.index not in head_ids:
            assignments.append(PieceAssignment(p.index, PieceBand.NORMAL, priority=4))
    seq_workers = max(1, cfg.max_workers // 4)
    return ScheduleResult(
        assignments=assignments,
        workers=WorkerPlan(
            sequential=seq_workers,
            normal=cfg.max_workers - seq_workers,
        ),
        notes=["fastest: small sequential boost + high parallelism"],
    )


def schedule_mirror(
    pieces: Sequence[PieceInfo],
    *,
    progress: float,
    cfg: MirrorConfig,
    general: GeneralConfig,
) -> ScheduleResult:
    result = schedule_general(pieces, progress=progress, cfg=general)
    result.workers = WorkerPlan(normal=cfg.max_workers)
    if progress < cfg.prefer_webseeds_until_progress:
        result.notes = [
            f"mirror: prefer webseeds until progress>={cfg.prefer_webseeds_until_progress}",
            *result.notes,
        ]
        if cfg.webseed_urls:
            result.notes.append(f"webseeds={len(cfg.webseed_urls)}")
    else:
        result.notes = ["mirror: swarm-primary after webseed bootstrap", *result.notes]
    return result


def schedule_for_mode(
    mode: DownloadMode,
    pieces: Sequence[PieceInfo],
    *,
    config: DownloadConfig,
    playhead: int = 0,
    file_size: int = 0,
    progress: float = 0.0,
    previously_underrun: bool = False,
) -> ScheduleResult:
    size = file_size or max((p.offset + p.length for p in pieces), default=0)
    if mode is DownloadMode.STREAM:
        stream_cfg = config.stream
        if config.limited.disable_speculative and config.mode is DownloadMode.LIMITED:
            stream_cfg = stream_cfg.model_copy(update={"speculative_ratio": 0.0})
        return schedule_stream(
            pieces,
            playhead=playhead,
            file_size=size,
            cfg=stream_cfg,
            previously_underrun=previously_underrun,
        )
    if mode is DownloadMode.PREVIEW:
        return schedule_preview(pieces, file_size=size, cfg=config.preview)
    if mode is DownloadMode.FASTEST:
        return schedule_fastest(pieces, cfg=config.fastest)
    if mode is DownloadMode.LIMITED:
        limited_general = GeneralConfig(
            max_workers=config.limited.max_workers,
            prefer_fastest_peers=False,
        )
        return schedule_general(pieces, progress=progress, cfg=limited_general)
    if mode is DownloadMode.SEED:
        return ScheduleResult(notes=["seed mode: no download schedule"])
    if mode is DownloadMode.MIRROR:
        return schedule_mirror(
            pieces,
            progress=progress,
            cfg=config.mirror,
            general=config.general,
        )
    # general / selective → general piece policy (file filter applied in engine)
    return schedule_general(pieces, progress=progress, cfg=config.general)


def should_reprioritize_seek(
    old_playhead: int,
    new_playhead: int,
    cfg: StreamConfig,
) -> bool:
    return abs(new_playhead - old_playhead) >= cfg.seek_reprioritize_bytes
