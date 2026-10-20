"""Unit tests for strengthened stream/general schedulers."""

from __future__ import annotations

import asyncio

from torrentsearch.downloader.engine import DownloadManager, DownloadStatus, SimulatedEngine
from torrentsearch.downloader.modes import DownloadConfig, DownloadMode, PreviewConfig, StreamConfig
from torrentsearch.downloader.scheduler import (
    PieceBand,
    PieceInfo,
    allocate_stream_workers,
    contiguous_bytes_ahead,
    detect_underrun,
    schedule_for_mode,
    schedule_stream,
    should_reprioritize_seek,
)


def _pieces(size: int = 32 * 1024 * 1024, piece_len: int = 256 * 1024) -> list[PieceInfo]:
    out = []
    i = 0
    offset = 0
    while offset < size:
        length = min(piece_len, size - offset)
        out.append(PieceInfo(index=i, offset=offset, length=length, rarity=float(i % 7)))
        i += 1
        offset += length
    return out


def test_contiguous_bytes_ahead():
    pcs = _pieces(size=1024 * 1024, piece_len=256 * 1024)
    for p in pcs[:2]:
        p.finished = True
    assert contiguous_bytes_ahead(pcs, 0) == 512 * 1024
    assert contiguous_bytes_ahead(pcs, 100) == 512 * 1024 - 100


def test_underrun_steals_speculative_workers():
    cfg = StreamConfig(max_workers=8, speculative_ratio=0.25)
    healthy = allocate_stream_workers(cfg, underrun=False)
    assert healthy.speculative > 0
    assert healthy.total == 8
    crisis = allocate_stream_workers(cfg, underrun=True)
    assert crisis.speculative == 0
    assert crisis.lookahead == 0
    assert crisis.urgent + crisis.sequential == crisis.total == 8


def test_underrun_hysteresis():
    cfg = StreamConfig(underrun_seconds=8, recover_seconds=16, bitrate_bps=8_000_000)
    # 5 seconds buffered → enter underrun
    assert detect_underrun(
        buffered_bytes=0, buffered_seconds=5.0, cfg=cfg, previously_underrun=False
    )
    # 12 seconds still underrun while recovering
    assert detect_underrun(
        buffered_bytes=0, buffered_seconds=12.0, cfg=cfg, previously_underrun=True
    )
    # 20 seconds → exit
    assert not detect_underrun(
        buffered_bytes=0, buffered_seconds=20.0, cfg=cfg, previously_underrun=True
    )


def test_stream_bands_and_anti_overlap():
    pcs = _pieces()
    cfg = StreamConfig(
        playhead_window_bytes=2 * 1024 * 1024,
        lookahead_bytes=4 * 1024 * 1024,
        max_workers=8,
        speculative_ratio=0.25,
    )
    result = schedule_stream(pcs, playhead=0, file_size=32 * 1024 * 1024, cfg=cfg)
    by_band = {}
    for a in result.assignments:
        by_band.setdefault(a.band, set()).add(a.index)
    active = (
        by_band.get(PieceBand.URGENT, set())
        | by_band.get(PieceBand.SEQUENTIAL, set())
        | by_band.get(PieceBand.LOOKAHEAD, set())
    )
    spec = by_band.get(PieceBand.SPECULATIVE, set())
    assert active.isdisjoint(spec)
    assert result.workers.total == 8
    # Urgent pieces near playhead should carry deadlines.
    urgent = [a for a in result.assignments if a.band is PieceBand.URGENT]
    assert urgent
    assert all(a.deadline_ms is not None for a in urgent)


def test_stream_seek_recenters_bands():
    pcs = _pieces()
    cfg = StreamConfig(
        playhead_window_bytes=1 * 1024 * 1024,
        lookahead_bytes=2 * 1024 * 1024,
        max_workers=8,
        speculative_ratio=0.25,
    )
    mid = 16 * 1024 * 1024
    result = schedule_stream(pcs, playhead=mid, file_size=32 * 1024 * 1024, cfg=cfg)
    urgent = [a for a in result.assignments if a.band is PieceBand.URGENT]
    assert urgent
    # First urgent piece should overlap the new playhead region.
    first = pcs[urgent[0].index]
    assert first.offset + first.length > mid
    assert first.offset < mid + cfg.playhead_window_bytes


def test_seek_reprioritize():
    cfg = StreamConfig(seek_reprioritize_bytes=1_000_000)
    assert should_reprioritize_seek(0, 2_000_000, cfg)
    assert not should_reprioritize_seek(0, 100, cfg)


def test_general_endgame():
    pcs = _pieces(size=1 * 1024 * 1024)
    for p in pcs[:-2]:
        p.finished = True
    cfg = DownloadConfig(mode=DownloadMode.GENERAL)
    result = schedule_for_mode(
        DownloadMode.GENERAL,
        pcs,
        config=cfg,
        progress=0.97,
        file_size=1 * 1024 * 1024,
    )
    assert any("endgame" in n for n in result.notes)
    assert all(
        a.band == PieceBand.URGENT
        for a in result.assignments
        if a.priority > 0
    )


def test_preview_defers_bulk():
    pcs = _pieces(size=128 * 1024 * 1024)
    cfg = DownloadConfig(
        mode=DownloadMode.PREVIEW,
        preview=PreviewConfig(
            head_bytes=4 * 1024 * 1024,
            sample_count=2,
            sample_bytes=512 * 1024,
            include_tail_bytes=1 * 1024 * 1024,
        ),
    )
    result = schedule_for_mode(
        DownloadMode.PREVIEW,
        pcs,
        config=cfg,
        file_size=128 * 1024 * 1024,
    )
    deferred = sum(1 for a in result.assignments if a.band == PieceBand.DEFERRED)
    urgent = sum(1 for a in result.assignments if a.band == PieceBand.URGENT)
    assert urgent > 0
    assert deferred > urgent
    assert urgent + deferred == len(pcs)


def test_simulated_stream_advances_near_playhead():
    async def _run():
        engine = SimulatedEngine(default_size=8 * 1024 * 1024)
        mgr = DownloadManager(engine, tick_interval=0.01)
        await mgr.start()
        try:
            cfg = DownloadConfig(mode=DownloadMode.STREAM)
            state = await engine.add("magnet:?xt=urn:btih:deadbeef", cfg)
            await engine.set_playhead(state.id, 0)
            for _ in range(5):
                await engine.tick()
            st = await engine.get(state.id)
            assert st is not None
            assert st.downloaded_bytes > 0
            assert st.status in (DownloadStatus.STREAMING, DownloadStatus.COMPLETED)
            assert st.buffered_bytes_ahead >= 0
            # Seek far ahead and ensure schedule notes mention seek rebuild.
            await engine.set_playhead(state.id, 4 * 1024 * 1024)
            await engine.tick()
            st2 = await engine.get(state.id)
            assert st2 is not None
            assert any("seek" in n or "workers" in n for n in st2.last_schedule_notes)
        finally:
            await mgr.stop()

    asyncio.run(_run())
