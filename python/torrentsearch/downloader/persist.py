"""SQL persistence for downloads (SQLModel + async)."""

from __future__ import annotations

from typing import Optional

from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession

from ..db.models import DownloadRecord, utcnow
from ..downloader.engine import DownloadState


async def upsert_download(
    session: AsyncSession,
    state: DownloadState,
    *,
    search_result_id: Optional[int] = None,
) -> DownloadRecord:
    row = await session.get(DownloadRecord, state.id)
    if row is None:
        row = DownloadRecord(
            id=state.id,
            magnet_or_url=state.magnet_or_url,
            mode=state.mode.value,
            status=state.status.value,
            config_json=state.config.model_dump_json(),
            save_path=state.save_path,
        )
        session.add(row)
    row.mode = state.mode.value
    row.status = state.status.value
    row.config_json = state.config.model_dump_json()
    row.save_path = state.save_path
    row.name = state.name
    row.info_hash = state.info_hash
    row.total_bytes = state.total_bytes
    row.downloaded_bytes = state.downloaded_bytes
    row.progress = state.progress
    row.download_rate = state.download_rate
    row.upload_rate = state.upload_rate
    row.num_peers = state.num_peers
    row.playhead = state.playhead
    row.buffered_bytes_ahead = state.buffered_bytes_ahead
    row.buffered_seconds_ahead = state.buffered_seconds_ahead
    row.underrun = state.underrun
    row.error = state.error
    row.updated_at = state.updated_at or utcnow()
    if search_result_id is not None:
        row.search_result_id = search_result_id
    await session.flush()
    return row


async def list_downloads(
    session: AsyncSession, *, limit: int = 50, offset: int = 0
) -> list[DownloadRecord]:
    stmt = (
        select(DownloadRecord)
        .order_by(desc(DownloadRecord.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await session.exec(stmt)
    return list(result.all())


async def get_download(session: AsyncSession, download_id: str) -> Optional[DownloadRecord]:
    return await session.get(DownloadRecord, download_id)


async def delete_download(session: AsyncSession, download_id: str) -> bool:
    row = await session.get(DownloadRecord, download_id)
    if row is None:
        return False
    await session.delete(row)
    return True
