"""Continue-watching / personal library shelf (Webtor-inspired)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db import get_async_db
from ..db.models import DownloadRecord, WatchProgress

router = APIRouter(prefix="/api/v1/library", tags=["library"])


@router.get("/continue")
async def continue_watching(
    db: AsyncSession = Depends(get_async_db),
    limit: int = Query(default=40, ge=1, le=200),
):
    """Recently watched items with resume position (>30s, not near end)."""
    rows = (
        await db.exec(
            select(WatchProgress)
            .where(WatchProgress.position_sec >= 30)
            .order_by(col(WatchProgress.updated_at).desc())
            .limit(limit * 2)
        )
    ).all()

    items = []
    for w in rows:
        if w.duration_sec and w.duration_sec > 0:
            # Skip nearly finished (>92%)
            if w.position_sec / w.duration_sec >= 0.92:
                continue
        dl = await db.get(DownloadRecord, w.download_id)
        pct = None
        if w.duration_sec and w.duration_sec > 0:
            pct = round(100.0 * w.position_sec / w.duration_sec, 1)
        items.append(
            {
                "download_id": w.download_id,
                "file_index": w.file_index,
                "position_sec": w.position_sec,
                "duration_sec": w.duration_sec,
                "percent": pct,
                "title": w.title or (dl.name if dl else None) or w.download_id,
                "status": dl.status if dl else None,
                "progress": dl.progress if dl else None,
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
        )
        if len(items) >= limit:
            break
    return {"items": items, "count": len(items)}


@router.get("/downloads")
async def library_downloads(
    db: AsyncSession = Depends(get_async_db),
    limit: int = Query(default=50, ge=1, le=200),
):
    """All persisted downloads newest-first (library grid)."""
    rows = (
        await db.exec(
            select(DownloadRecord)
            .order_by(col(DownloadRecord.updated_at).desc())
            .limit(limit)
        )
    ).all()
    out = []
    for d in rows:
        out.append(
            {
                "id": d.id,
                "name": d.name or d.id,
                "status": d.status,
                "mode": d.mode,
                "progress": d.progress,
                "info_hash": d.info_hash,
                "total_bytes": d.total_bytes,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
        )
    return {"items": out, "count": len(out)}


@router.delete("/continue/{download_id}")
async def clear_continue(
    download_id: str,
    db: AsyncSession = Depends(get_async_db),
    file_index: Optional[int] = Query(default=None),
):
    q = select(WatchProgress).where(WatchProgress.download_id == download_id)
    if file_index is not None:
        q = q.where(WatchProgress.file_index == file_index)
    rows = (await db.exec(q)).all()
    n = 0
    for r in rows:
        await db.delete(r)
        n += 1
    await db.commit()
    return {"ok": True, "removed": n}
