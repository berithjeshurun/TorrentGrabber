"""Downloader HTTP routes (fully async)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.responses import Response

from ..activity import activity
from ..db import get_async_db
from ..db.models import TorrentResult
from ..downloader.engine import DownloadManager, DownloadState
from ..downloader.ffmpeg_util import ffmpeg_available, probe_url
from ..downloader.hls_session import hls_manager
from ..downloader.cpp_engine import CppEngineClient, EngineHttpError
from ..downloader.hybrid_engine import HybridEngine
from ..downloader.modes import PRESETS, DownloadConfig, DownloadMode
from ..downloader.node_engine import NodeWebTorrentEngine
from ..downloader.persist import (
    delete_download,
    get_download,
    list_downloads,
    upsert_download,
)
from ..models import Category as DomainCategory
from ..models import MagnetRequiresFetch, Torrent
from ..network import NetworkClient
from ..search import SearchService, create_builtin_providers
from .download_schemas import (
    CreateDownloadRequest,
    DownloadOut,
    ModeInfo,
    PlayheadUpdate,
    PresetInfo,
)

router = APIRouter(prefix="/api/v1/downloads", tags=["downloads"])

MODE_DESCRIPTIONS = {
    DownloadMode.GENERAL: "Classic rarest-first download with optional endgame",
    DownloadMode.STREAM: "Playhead-driven sequential + speculative bands for smooth playback",
    DownloadMode.SELECTIVE: "Download only selected files from a multi-file torrent",
    DownloadMode.PREVIEW: "Fetch head + sparse mid/end samples to inspect content",
    DownloadMode.SEED: "Seeding / ratio-focused upload after completion",
    DownloadMode.LIMITED: "Low peer/rate profile for metered or quiet use",
    DownloadMode.FASTEST: "High parallelism with small sequential head-start",
    DownloadMode.MIRROR: "BitTorrent plus optional HTTP webseeds for faster start",
}


def _manager(request: Request) -> DownloadManager:
    mgr = getattr(request.app.state, "download_manager", None)
    if mgr is None:
        raise HTTPException(status_code=503, detail="Downloader not initialized")
    return mgr


def _to_out(state: DownloadState) -> DownloadOut:
    return DownloadOut(
        id=state.id,
        magnet_or_url=state.magnet_or_url,
        mode=state.mode.value,
        status=state.status.value,
        name=state.name,
        info_hash=state.info_hash,
        save_path=state.save_path,
        total_bytes=state.total_bytes,
        downloaded_bytes=state.downloaded_bytes,
        progress=state.progress,
        download_rate=state.download_rate,
        upload_rate=state.upload_rate,
        num_peers=state.num_peers,
        playhead=state.playhead,
        buffered_bytes_ahead=state.buffered_bytes_ahead,
        buffered_seconds_ahead=state.buffered_seconds_ahead,
        underrun=state.underrun,
        eta_seconds=state.eta_seconds,
        error=state.error,
        schedule_notes=list(state.last_schedule_notes),
        files=list(getattr(state, "files", None) or []),
        media_file_index=int(getattr(state, "media_file_index", -1) or -1),
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


async def _resolve_row_magnet(row: TorrentResult) -> str | None:
    if row.magnet:
        return row.magnet
    if not row.magnet_fetch_url:
        return None
    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
        service = SearchService(providers, nsfw=True)
        try:
            cat = DomainCategory(row.category) if row.category else DomainCategory.ALL
        except ValueError:
            cat = DomainCategory.ALL
        stub = Torrent(
            id=row.torrent_id,
            name=row.name,
            size=row.size,
            seeders=row.seeders,
            peers=row.peers,
            provider_name=row.provider,
            magnet_uri=MagnetRequiresFetch(row.magnet_fetch_url),
            details_page_url=row.details_page_url,
            file_download_link=row.file_download_link,
            category=cat,
        )
        magnet = await service.resolve_magnet(stub)
    if magnet:
        row.magnet = magnet
    return magnet


@router.get("/modes", response_model=list[ModeInfo])
async def list_modes():
    return [
        ModeInfo(id=m.value, description=MODE_DESCRIPTIONS[m]) for m in DownloadMode
    ]


@router.get("/presets", response_model=list[PresetInfo])
async def list_presets():
    return [PresetInfo(id=k, mode=v.mode.value) for k, v in PRESETS.items()]


@router.post("", response_model=DownloadOut)
async def create_download(
    body: CreateDownloadRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    magnet = body.magnet
    search_result_id = body.torrent_result_id
    if not magnet and search_result_id is not None:
        row = await db.get(TorrentResult, search_result_id)
        if row is None:
            raise HTTPException(status_code=404, detail="torrent_result_id not found")
        magnet = await _resolve_row_magnet(row)
        if not magnet:
            raise HTTPException(
                status_code=400,
                detail="Could not resolve a magnet for this torrent",
            )
        await db.flush()
    if not magnet:
        raise HTTPException(status_code=400, detail="magnet or torrent_result_id required")

    if body.preset:
        if body.preset not in PRESETS:
            raise HTTPException(status_code=400, detail=f"Unknown preset '{body.preset}'")
        config = PRESETS[body.preset].model_copy(deep=True)
    elif body.config:
        config = body.config
    else:
        config = DownloadConfig(mode=body.mode)

    if body.config is None and not body.preset:
        config.mode = body.mode
    if body.save_path:
        config.save_path = body.save_path

    mgr = _manager(request)
    state = await mgr.engine.add(magnet, config)
    await upsert_download(db, state, search_result_id=search_result_id)
    return _to_out(state)


@router.get("", response_model=list[DownloadOut])
async def get_downloads(request: Request, db: AsyncSession = Depends(get_async_db)):
    mgr = _manager(request)
    states = await mgr.engine.list()
    for st in states:
        await upsert_download(db, st)
    if not states:
        rows = await list_downloads(db)
        return [
            DownloadOut(
                id=r.id,
                magnet_or_url=r.magnet_or_url,
                mode=r.mode,
                status=r.status,
                name=r.name,
                info_hash=r.info_hash,
                save_path=r.save_path,
                total_bytes=r.total_bytes,
                downloaded_bytes=r.downloaded_bytes,
                progress=r.progress,
                download_rate=r.download_rate,
                upload_rate=r.upload_rate,
                num_peers=r.num_peers,
                playhead=r.playhead,
                buffered_bytes_ahead=r.buffered_bytes_ahead,
                buffered_seconds_ahead=r.buffered_seconds_ahead,
                underrun=r.underrun,
                error=r.error,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]
    return [_to_out(s) for s in states]


@router.get("/{download_id}", response_model=DownloadOut)
async def get_one(download_id: str, request: Request, db: AsyncSession = Depends(get_async_db)):
    mgr = _manager(request)
    state = await mgr.engine.get(download_id)
    if state is None:
        row = await get_download(db, download_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Download not found")
        raise HTTPException(status_code=404, detail="Download not active in engine")
    await upsert_download(db, state)
    return _to_out(state)


@router.post("/{download_id}/pause", response_model=DownloadOut)
async def pause(download_id: str, request: Request, db: AsyncSession = Depends(get_async_db)):
    mgr = _manager(request)
    try:
        state = await mgr.engine.pause(download_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Download not found")
    await upsert_download(db, state)
    return _to_out(state)


@router.post("/{download_id}/resume", response_model=DownloadOut)
async def resume(download_id: str, request: Request, db: AsyncSession = Depends(get_async_db)):
    mgr = _manager(request)
    try:
        state = await mgr.engine.resume(download_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Download not found")
    await upsert_download(db, state)
    return _to_out(state)


def _cpp_owner(engine, download_id: str) -> CppEngineClient | None:
    if isinstance(engine, CppEngineClient):
        return engine
    if isinstance(engine, HybridEngine):
        owner = engine.owner_for(download_id)
        if isinstance(owner, CppEngineClient):
            return owner
    return None


@router.get("/{download_id}/media")
async def stream_media(download_id: str, request: Request):
    """
    HTTP Range video/audio stream.
    C++ libtorrent / Node WebTorrent for stream; local disk for aria2 bulk.
    """
    mgr = _manager(request)
    engine = mgr.engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")

    range_header = request.headers.get("range")

    try:
        if isinstance(engine, HybridEngine):
            kind, path = await engine.resolve_media_source(download_id)
            if kind == "file" and path is not None:
                return _file_range_response(path, range_header)
            upstream = await engine.proxy_media(download_id, range_header)
            return await _proxy_upstream(upstream)

        if isinstance(engine, (CppEngineClient, NodeWebTorrentEngine)):
            upstream = await engine.proxy_media(download_id, range_header)
            return await _proxy_upstream(upstream)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(status_code=404, detail="Download not found")
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Stream worker error: {exc}"
        ) from exc

    raise HTTPException(
        status_code=503,
        detail="No media backend for this download",
    )


async def _proxy_upstream(upstream):
    if upstream.status_code == 503:
        body = await upstream.aread()
        await upstream.aclose()
        raise HTTPException(
            status_code=503, detail=body.decode("utf-8", errors="replace")
        )
    if upstream.status_code >= 400:
        detail = (await upstream.aread()).decode("utf-8", errors="replace")
        await upstream.aclose()
        raise HTTPException(status_code=upstream.status_code, detail=detail)

    async def _body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    headers = {
        k: upstream.headers[k]
        for k in ("content-type", "content-length", "content-range", "accept-ranges")
        if k in upstream.headers
    }
    return StreamingResponse(
        _body(),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
        headers=headers,
    )


def _mime_for_path(path) -> str:
    n = path.name.lower()
    if n.endswith(".mp4") or n.endswith(".m4v"):
        return "video/mp4"
    if n.endswith(".webm"):
        return "video/webm"
    if n.endswith(".mkv"):
        return "video/x-matroska"
    if n.endswith(".mov"):
        return "video/quicktime"
    return "application/octet-stream"


def _file_range_response(path, range_header: str | None):
    from pathlib import Path as P

    path = P(path)
    total = path.stat().st_size
    mime = _mime_for_path(path)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    if not range_header:
        headers["Content-Length"] = str(total)

        def _iter():
            with path.open("rb") as f:
                while True:
                    chunk = f.read(1024 * 256)
                    if not chunk:
                        break
                    yield chunk

        return StreamingResponse(_iter(), media_type=mime, headers=headers)

    import re

    m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not m:
        return FileResponse(path, media_type=mime, headers=headers)
    start = int(m.group(1)) if m.group(1) else 0
    end = int(m.group(2)) if m.group(2) else total - 1
    if start >= total or end >= total or start > end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total}"},
        )
    length = end - start + 1
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    headers["Content-Length"] = str(length)

    def _iter_range():
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        _iter_range(), status_code=206, media_type=mime, headers=headers
    )


async def _ffmpeg_input(request: Request, download_id: str) -> tuple[object, str]:
    """Return (engine, ffmpeg -i argument) as http URL or local file path."""
    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")

    cpp = _cpp_owner(engine, download_id)
    if cpp is not None:
        path = await cpp.resolve_media_path(download_id)
        if path and path.is_file():
            return engine, str(path)
        return engine, cpp.media_url(download_id)

    if isinstance(engine, HybridEngine):
        kind, path = await engine.resolve_media_source(download_id)
        if kind == "file" and path is not None:
            return engine, str(path)
        if engine.media_base:
            return engine, f"{engine.media_base}/media/{download_id}"
        raise HTTPException(status_code=503, detail="No media source yet")

    if isinstance(engine, NodeWebTorrentEngine):
        if not engine.media_base:
            raise HTTPException(status_code=503, detail="Stream engine has no media base")
        return engine, f"{engine.media_base}/media/{download_id}"

    raise HTTPException(status_code=503, detail="No media backend")


@router.get("/{download_id}/tracks")
async def list_tracks(download_id: str, request: Request):
    """Probe tracks — C++ engine owns this when available."""
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is not None:
        try:
            return await cpp.get_tracks(download_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    # Legacy Node/aria2 path only
    _, src = await _ffmpeg_input(request, download_id)
    if not ffmpeg_available():
        raise HTTPException(
            status_code=503,
            detail="ffprobe/ffmpeg missing — place binaries in python/etc/",
        )
    try:
        result = await probe_url(src)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    state = await engine.get(download_id)
    result.file_name = state.name if state else None
    return result.to_dict()


@router.post("/{download_id}/hls")
async def start_hls(
    download_id: str,
    request: Request,
    audio: int | None = Query(default=None, description="Absolute stream index for audio"),
    subtitle: int | None = Query(
        default=None,
        description="Optional subtitle extract (prefer POST /subtitle to avoid remux)",
    ),
    start_seconds: float | None = Query(default=None),
    audio_only: bool = Query(default=False),
    burn_subtitle: int | None = Query(default=None),
    audio_bitrate: str | None = Query(default=None),
    audio_channels: str | None = Query(default=None),
    loudnorm: bool | None = Query(default=None),
    deinterlace: str | None = Query(default=None),
    tonemap: str | None = Query(default=None),
    lut_cube: str | None = Query(default=None),
    abr: bool | None = Query(default=None),
):
    """Start / switch audio HLS — C++ engine when available."""
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is not None:
        try:
            data = await cpp.start_hls(
                download_id,
                audio=audio,
                start_seconds=start_seconds,
                audio_only=audio_only,
                burn_subtitle=burn_subtitle,
                audio_bitrate=audio_bitrate,
                audio_channels=audio_channels,
                loudnorm=loudnorm,
                deinterlace=deinterlace,
                tonemap=tonemap,
                lut_cube=lut_cube,
                abr=abr,
            )
            if subtitle is not None and burn_subtitle is None:
                sub = await cpp.set_subtitle(download_id, index=subtitle)
                data["subtitle_index"] = sub.get("subtitle_index")
                data["subtitle_url"] = sub.get("subtitle_url")
            return data
        except EngineHttpError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.data) from exc
        except Exception as exc:
            msg = str(exc)
            low = msg.lower()
            status = (
                503
                if any(
                    s in low
                    for s in (
                        "retry",
                        "not ready",
                        "prematurely",
                        "exited early",
                        "timed out",
                    )
                )
                else 502
            )
            detail: dict | str = msg
            if status == 503:
                detail = {"error": msg, "retry": True, "reason": "hls_transient"}
            raise HTTPException(status_code=status, detail=detail) from exc

    _, src = await _ffmpeg_input(request, download_id)
    if not ffmpeg_available():
        raise HTTPException(
            status_code=503,
            detail="ffmpeg missing — place binaries in python/etc/",
        )
    try:
        sess = await hls_manager.ensure(
            download_id,
            src,
            audio_index=audio,
            subtitle_index=subtitle,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    sub_ready = bool(
        sess.subtitle_index is not None
        and sess.subtitle_vtt.is_file()
        and sess.subtitle_vtt.stat().st_size > 0
    )
    return {
        "download_id": download_id,
        "playlist_url": f"/api/v1/downloads/{download_id}/hls/index.m3u8",
        "subtitle_url": (
            f"/api/v1/downloads/{download_id}/hls/subs.vtt?t={int(sess.subtitle_vtt.stat().st_mtime)}"
            if sub_ready
            else None
        ),
        "audio_index": sess.audio_index,
        "subtitle_index": sess.subtitle_index,
        "ready": sess.playlist.is_file(),
    }


@router.post("/{download_id}/poster")
async def make_poster(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required for poster")
    try:
        return await cpp.ensure_poster(download_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/poster.jpg")
async def get_poster(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=404, detail="not found")
    upstream = await cpp.fetch_poster(download_id)
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="poster missing")
    return StreamingResponse(
        upstream.aiter_bytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{download_id}/thumbs")
async def make_thumbs(
    download_id: str,
    request: Request,
    interval: int = Query(default=10, ge=2, le=120),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required for thumbs")
    try:
        return await cpp.ensure_thumbs(download_id, interval=interval)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/thumbs/{filename}")
async def get_thumbs_file(download_id: str, filename: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=404, detail="not found")
    upstream = await cpp.fetch_thumbs_file(download_id, filename)
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="not ready")
    media = "image/jpeg" if filename.endswith(".jpg") else "text/vtt"
    return StreamingResponse(
        upstream.aiter_bytes(),
        media_type=media,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{download_id}/clip")
async def export_clip(
    download_id: str,
    request: Request,
    start: float = Query(..., ge=0),
    end: float = Query(..., gt=0),
    container: str = Query(default="mp4"),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.export_clip(
            download_id, start=start, end=end, container=container
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/remux")
async def remux_export(
    download_id: str,
    request: Request,
    container: str = Query(default="mp4"),
    video: int | None = Query(default=None),
    audio: int | None = Query(default=None),
    subtitle: int | None = Query(default=None),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.remux(
            download_id,
            container=container,
            video=video,
            audio=audio,
            subtitle=subtitle,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/cast")
async def cast_export(
    download_id: str,
    request: Request,
    profile: str = Query(default="chromecast"),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.cast_export(download_id, profile=profile)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/waveform")
async def waveform_export(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.waveform(download_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/chapters")
async def chapters_export(
    download_id: str,
    request: Request,
    threshold: float = Query(default=0.35, gt=0, lt=1),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.chapters(download_id, threshold=threshold)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/exports/{filename}")
async def get_export_file(download_id: str, filename: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=404, detail="not found")
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    upstream = await cpp.fetch_export(download_id, filename)
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail="not found")
    media = "application/octet-stream"
    if filename.endswith(".mp4"):
        media = "video/mp4"
    elif filename.endswith(".mkv"):
        media = "video/x-matroska"
    elif filename.endswith(".json"):
        media = "application/json"
    elif filename.endswith(".png"):
        media = "image/png"
    return StreamingResponse(
        upstream.aiter_bytes(),
        media_type=media,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{download_id}/buffer")
async def buffer_health(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.buffer_health(download_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/resume-position")
async def get_resume_position(
    download_id: str,
    db: AsyncSession = Depends(get_async_db),
    file_index: int = Query(default=0),
):
    from sqlmodel import select

    from ..db.models import WatchProgress

    row = (
        await db.exec(
            select(WatchProgress).where(
                WatchProgress.download_id == download_id,
                WatchProgress.file_index == file_index,
            )
        )
    ).first()
    if row is None:
        return {
            "download_id": download_id,
            "file_index": file_index,
            "position_sec": 0,
            "found": False,
        }
    return {
        "download_id": row.download_id,
        "file_index": row.file_index,
        "position_sec": row.position_sec,
        "duration_sec": row.duration_sec,
        "title": row.title,
        "updated_at": row.updated_at,
        "found": True,
    }


@router.put("/{download_id}/resume-position")
async def put_resume_position(
    download_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    from datetime import datetime, timezone

    from sqlmodel import select

    from ..db.models import WatchProgress

    body = await request.json()
    file_index = int(body.get("file_index", 0))
    position_sec = float(body.get("position_sec", 0))
    duration_sec = body.get("duration_sec")
    title = body.get("title")
    row = (
        await db.exec(
            select(WatchProgress).where(
                WatchProgress.download_id == download_id,
                WatchProgress.file_index == file_index,
            )
        )
    ).first()
    now = datetime.now(timezone.utc)
    if row is None:
        row = WatchProgress(
            download_id=download_id,
            file_index=file_index,
            position_sec=position_sec,
            duration_sec=float(duration_sec) if duration_sec is not None else None,
            title=title,
            updated_at=now,
        )
        db.add(row)
    else:
        row.position_sec = position_sec
        if duration_sec is not None:
            row.duration_sec = float(duration_sec)
        if title:
            row.title = title
        row.updated_at = now
    await db.commit()
    await db.refresh(row)
    return {
        "download_id": row.download_id,
        "file_index": row.file_index,
        "position_sec": row.position_sec,
        "duration_sec": row.duration_sec,
        "found": True,
    }


@router.post("/{download_id}/boost")
async def boost_ahead(
    download_id: str,
    request: Request,
    duration: float = Query(default=45, ge=1, le=600),
    multiplier: float = Query(default=3.0, ge=1, le=8),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.boost(
            download_id, duration=duration, multiplier=multiplier
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/abr")
async def set_abr(
    download_id: str,
    request: Request,
    height: int = Query(default=0, ge=0, le=2160),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.set_abr(download_id, height=height)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/invite")
async def friend_invite(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.invite(download_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/cast/session")
async def cast_session(
    download_id: str,
    request: Request,
    mode: str = Query(default="export"),
    profile: str = Query(default="chromecast"),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await cpp.cast_session(
            download_id, mode=mode, profile=profile
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/open-player")
async def open_external_player(
    download_id: str,
    request: Request,
    player: str = Query(default="mpv", description="mpv | vlc | auto"),
    use_hls: bool = Query(default=False),
):
    """Launch mpv/VLC against the engine media (or HLS) URL."""
    from ..downloader.external_players import open_player

    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")
    cpp = _cpp_owner(engine, download_id)
    if cpp is None or not getattr(cpp, "media_base", None):
        raise HTTPException(status_code=503, detail="C++ engine media URL unavailable")
    if use_hls:
        base = str(request.base_url).rstrip("/")
        url = f"{base}/api/v1/downloads/{download_id}/hls/index.m3u8"
    else:
        url = cpp.media_url(download_id)
    try:
        return open_player(player, url, title=state.name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{download_id}/cast/sidecar")
async def cast_sidecar(
    download_id: str,
    request: Request,
    target: str = Query(default="chromecast"),
):
    """Optional webtorrent-cli Chromecast/AirPlay/DLNA (TORRENTSEARCH_CAST_BACKEND=webtorrent)."""
    from ..downloader.cast_sidecar import start_webtorrent_cast

    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")
    magnet = getattr(state, "magnet", None) or getattr(state, "uri", None)
    if not magnet:
        # Fall back to status magnet from engine if present
        cpp = _cpp_owner(engine, download_id)
        if cpp is not None:
            try:
                st = await cpp.get(download_id)
                magnet = getattr(st, "magnet", None) if st else None
            except Exception:
                magnet = None
    if not magnet:
        raise HTTPException(
            status_code=400,
            detail="No magnet URI available for webtorrent-cli cast",
        )
    try:
        return start_webtorrent_cast(str(magnet), target=target)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{download_id}/media-file")
async def set_media_file(
    download_id: str,
    request: Request,
    index: int = Query(..., description="Torrent file index"),
):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        data = await cpp.set_media_file(download_id, index)
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{download_id}/playlist.m3u")
async def download_playlist(download_id: str, request: Request):
    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        body = await cpp.playlist_m3u(download_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=body, media_type="audio/x-mpegurl")


@router.post("/{download_id}/subtitle")
async def set_subtitle(
    download_id: str,
    request: Request,
    index: int | None = Query(
        default=None,
        description="Absolute ffprobe stream index; omit to disable subs",
    ),
):
    """Soft-sub switch without restarting video/audio — C++ when available."""
    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")

    cpp = _cpp_owner(engine, download_id)
    if cpp is not None:
        try:
            return await cpp.set_subtitle(download_id, index=index)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not ffmpeg_available():
        raise HTTPException(status_code=503, detail="ffmpeg missing")

    sess = hls_manager.get(download_id)
    if sess is None:
        _, src = await _ffmpeg_input(request, download_id)
        try:
            sess = await hls_manager.ensure(download_id, src, audio_index=None)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        sess = await hls_manager.set_subtitle(download_id, index)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    ready = bool(
        sess.subtitle_index is not None
        and sess.subtitle_vtt.is_file()
        and sess.subtitle_vtt.stat().st_size > 0
    )
    return {
        "download_id": download_id,
        "subtitle_index": sess.subtitle_index,
        "subtitle_url": (
            f"/api/v1/downloads/{download_id}/hls/subs.vtt?t={int(sess.subtitle_vtt.stat().st_mtime)}"
            if ready
            else None
        ),
        "ready": ready,
        "error": sess.sub_error,
    }


def _external_subs_dir(download_id: str):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "data" / "external-subs" / download_id
    root.mkdir(parents=True, exist_ok=True)
    return root


@router.get("/{download_id}/subtitles/external")
async def search_external_subtitles(
    download_id: str,
    request: Request,
    languages: str | None = Query(default=None),
    query: str | None = Query(default=None),
):
    """OpenSubtitles search for this download (Webtor-style)."""
    from .. import opensubtitles
    from ..ranking import parse_query_episode

    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")
    q = (query or state.name or download_id).strip()
    season, episode, _ = parse_query_episode(q)
    return await opensubtitles.search_subtitles(
        query=q,
        languages=languages,
        season=season,
        episode=episode,
    )


@router.post("/{download_id}/subtitles/external")
async def attach_external_subtitle(
    download_id: str,
    request: Request,
    file_id: int = Query(..., description="OpenSubtitles file_id"),
):
    """Download an OpenSubtitles file, convert to WebVTT, and attach."""
    from .. import opensubtitles

    engine = _manager(request).engine
    state = await engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")
    try:
        raw = await opensubtitles.download_subtitle(file_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    vtt = opensubtitles.srt_to_vtt(raw["content"])
    path = _external_subs_dir(download_id) / "external.vtt"
    path.write_text(vtt, encoding="utf-8")
    return {
        "download_id": download_id,
        "file_id": file_id,
        "file_name": raw.get("file_name"),
        "subtitle_url": f"/api/v1/downloads/{download_id}/subtitles/external.vtt",
        "ready": True,
        "remaining": raw.get("remaining"),
    }


@router.get("/{download_id}/subtitles/external.vtt")
async def get_external_subtitle_vtt(download_id: str):
    path = _external_subs_dir(download_id) / "external.vtt"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No external subtitle attached")
    return FileResponse(path, media_type="text/vtt")


@router.get("/{download_id}/hls/{filename:path}")
async def hls_file(download_id: str, filename: str, request: Request):
    """Serve HLS playlist / segments / WebVTT — C++ when available."""
    if ".." in filename or filename.startswith("/") or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    engine = _manager(request).engine
    cpp = _cpp_owner(engine, download_id)
    if cpp is not None:
        try:
            upstream = await cpp.fetch_hls_file(download_id, filename)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if upstream.status_code == 404:
            await upstream.aclose()
            raise HTTPException(status_code=404, detail=f"{filename} not ready yet")
        return await _proxy_upstream(upstream)

    sess = hls_manager.get(download_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="No HLS session — POST /hls first")
    path = sess.work_dir / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{filename} not ready yet")

    media = "application/vnd.apple.mpegurl"
    if filename.endswith(".ts"):
        media = "video/mp2t"
    elif filename.endswith(".vtt"):
        media = "text/vtt"
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.post("/{download_id}/playhead", response_model=DownloadOut)
async def update_playhead(
    download_id: str,
    body: PlayheadUpdate,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
):
    mgr = _manager(request)
    state = await mgr.engine.get(download_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Download not found")

    offset = body.offset_bytes
    if offset is None and body.offset_seconds is not None:
        bps = body.bitrate_bps or state.config.stream.bitrate_bps
        if not bps:
            raise HTTPException(
                status_code=400,
                detail="bitrate_bps required when using offset_seconds",
            )
        offset = int(body.offset_seconds * (bps / 8.0))
        state.config.stream.bitrate_bps = bps
    if offset is None:
        raise HTTPException(status_code=400, detail="offset_bytes or offset_seconds required")

    state = await mgr.engine.set_playhead(download_id, offset)
    await upsert_download(db, state)
    return _to_out(state)


@router.delete("/{download_id}")
async def remove_download(
    download_id: str,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    delete_files: bool = False,
):
    mgr = _manager(request)
    try:
        await mgr.engine.remove(download_id, delete_files=delete_files)
    except KeyError:
        pass
    await delete_download(db, download_id)
    await hls_manager.stop(download_id)
    await activity.say(f"Removed download {download_id}", area="download")
    return {"deleted": True, "id": download_id}
