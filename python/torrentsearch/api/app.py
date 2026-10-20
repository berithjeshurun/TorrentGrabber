"""FastAPI application — multi-provider torrent search with SQL persistence."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from sqlmodel.ext.asyncio.session import AsyncSession
from .. import __version__
from ..activity import ActivityLevel, activity
from ..db import DATABASE_URL, get_async_db, init_db_async
from ..db import repository as repo
from ..db.models import SearchQuery, TorrentResult
from ..export import fetch_url_of, magnet_of
from ..models import (
    Category as DomainCategory,
    MagnetRequiresFetch,
    SearchResults,
    Torrent,
)
from ..network import NetworkClient
from ..provider_selection import resolve_enabled_provider_ids
from ..search import SearchService, create_builtin_providers
from .downloads import router as downloads_router
from .library import router as library_router
from .party import router as party_router
from .ws import router as ws_router
from .schemas import (
    ActivityEventOut,
    ActivityFeedOut,
    HealthOut,
    PaginatedSearches,
    PaginatedTorrents,
    ProviderErrorOut,
    ProviderOut,
    ResolveMagnetOut,
    SearchDetail,
    SearchRequest,
    SearchResponse,
    SearchSummary,
    TorrentOut,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from ..downloader.engine import DownloadManager

    await activity.say("Starting TorrentSearch…", area="system")
    await init_db_async()
    await activity.say("Database is ready", level=ActivityLevel.GOOD, area="system")

    manager = DownloadManager()
    await manager.start()
    app.state.download_manager = manager
    try:
        yield
    finally:
        from ..downloader.hls_session import hls_manager

        await hls_manager.stop_all()
        await manager.stop()
        await activity.say("Shutting down — goodbye", area="system")


app = FastAPI(
    title="TorrentSearch API",
    description=(
        "Multi-provider torrent search + multi-mode downloader. "
        "Fully async. Plain-language activity feed at /api/v1/activity."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.include_router(downloads_router)
app.include_router(library_router)
app.include_router(party_router)
app.include_router(ws_router)


class _UIHeadersMiddleware(BaseHTTPMiddleware):
    """Allow the WebTorrent service worker to control the whole /ui/ app."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/ui/"):
            if path.endswith("sw.min.js"):
                response.headers["Service-Worker-Allowed"] = "/ui/"
                response.headers["Cache-Control"] = "no-cache"
            if path.endswith(".js"):
                response.headers.setdefault(
                    "Content-Type", "text/javascript; charset=utf-8"
                )
        return response


app.add_middleware(_UIHeadersMiddleware)
# Outermost: browsers calling the API / HLS / media from another origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "Accept-Ranges",
        "Content-Length",
        "Content-Range",
        "Content-Type",
    ],
)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")


@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/ui/")


def _safe_category(value: Optional[str]) -> DomainCategory:
    if not value:
        return DomainCategory.ALL
    try:
        return DomainCategory(value)
    except ValueError:
        return DomainCategory.ALL


def _torrent_out_from_domain(t, *, rank: Optional[int] = None) -> TorrentOut:
    return TorrentOut(
        id=t.id,
        name=t.name,
        size=t.size,
        seeders=t.seeders,
        peers=t.peers,
        provider=t.provider_name,
        upload_date=t.upload_date,
        category=t.category.value if t.category else None,
        details_page_url=t.details_page_url,
        file_download_link=t.file_download_link,
        magnet=magnet_of(t),
        magnet_fetch_url=fetch_url_of(t),
        nsfw=t.is_nsfw,
        dead=t.is_dead,
        rank=rank,
    )


def _torrent_out_from_row(row: TorrentResult) -> TorrentOut:
    return TorrentOut(
        db_id=row.id,
        search_id=row.search_id,
        id=row.torrent_id,
        name=row.name,
        size=row.size,
        seeders=row.seeders,
        peers=row.peers,
        provider=row.provider,
        upload_date=row.upload_date,
        category=row.category,
        details_page_url=row.details_page_url,
        file_download_link=row.file_download_link,
        magnet=row.magnet,
        magnet_fetch_url=row.magnet_fetch_url,
        nsfw=row.nsfw,
        dead=row.dead,
        rank=row.rank,
    )


def _error_out(err) -> ProviderErrorOut:
    return ProviderErrorOut(
        provider_id=err.provider_id,
        provider=err.provider_name,
        url=err.provider_url,
        kind=err.kind,
        message=str(err.cause) if hasattr(err, "cause") else err.message,
    )


def _search_summary(row: SearchQuery) -> SearchSummary:
    return SearchSummary.model_validate(row)


@app.get("/health", response_model=HealthOut, tags=["system"])
async def health():
    snap = activity.snapshot()
    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
    return HealthOut(
        status="ok" if snap.ok else "degraded",
        version=__version__,
        database=DATABASE_URL,
        providers=len(providers),
        healthy=snap.ok,
        phrase=snap.phrase,
        searching=snap.searching,
        downloads_active=snap.downloads_active,
        downloads_streaming=snap.downloads_streaming,
        underruns=snap.underruns,
        uptime_seconds=snap.uptime_seconds,
        downloader_running=activity.manager_running,
    )


@app.get("/api/v1/metrics", tags=["system"])
async def engine_metrics(request: Request):
    """Prometheus text from torrent-engine when the C++ sidecar is up."""
    from fastapi.responses import PlainTextResponse

    from ..downloader.hybrid_engine import HybridEngine

    mgr = getattr(request.app.state, "download_manager", None)
    engine = getattr(mgr, "engine", None) if mgr else None
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        return PlainTextResponse(
            "# torrent-engine unavailable\n", media_type="text/plain; version=0.0.4"
        )
    try:
        body = await engine.cpp.engine_metrics()
    except Exception as exc:
        return PlainTextResponse(
            f"# error {exc}\n",
            status_code=502,
            media_type="text/plain; version=0.0.4",
        )
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")


@app.get("/api/v1/activity", response_model=ActivityFeedOut, tags=["system"])
async def activity_feed(limit: int = Query(default=40, ge=1, le=200)):
    snap = activity.snapshot(recent_limit=limit)
    return ActivityFeedOut(
        phrase=snap.phrase,
        healthy=snap.ok,
        events=[
            ActivityEventOut(
                time=e.time,
                level=e.level.value,
                area=e.area,
                message=e.message,
            )
            for e in snap.recent
        ],
    )


@app.get("/api/v1/providers", response_model=list[ProviderOut], tags=["providers"])
async def list_providers():
    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
    return [
        ProviderOut(
            id=p.id,
            name=p.name,
            url=p.url,
            default=p.enabled_by_default,
            cloudflare=p.is_cloudflare_protected,
            safety=p.safety.value,
            categories=sorted(c.value for c in p.supported_categories),
        )
        for p in sorted(providers, key=lambda x: x.name.lower())
    ]


@app.post("/api/v1/search", response_model=SearchResponse, tags=["search"])
async def search(body: SearchRequest, db: AsyncSession = Depends(get_async_db)):
    started = time.perf_counter()

    async with NetworkClient() as network:
        all_providers = create_builtin_providers(network)
        by_id = {p.id: p for p in all_providers}

        if body.providers:
            unknown = set(body.providers) - set(by_id)
            if unknown:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown provider IDs: {', '.join(sorted(unknown))}",
                )
            enabled = set(body.providers)
        else:
            enabled = resolve_enabled_provider_ids(
                all_providers,
                all_providers=body.all_providers,
                nsfw=body.nsfw,
            )

        service = SearchService(
            all_providers,
            enabled_ids=enabled,
            nsfw=body.nsfw,
            hide_dead=body.hide_dead,
            max_results=body.max_results,
            sort_by=body.sort,
            sort_order=body.order,
        )

        results: SearchResults = await service.search(body.query, body.category)

        if body.resolve_magnets:
            pending = [
                t for t in results.torrents if isinstance(t.magnet_uri, MagnetRequiresFetch)
            ]
            if pending:
                await service.resolve_magnets(
                    results.torrents,
                    concurrency=body.resolve_concurrency,
                )

    duration_ms = int((time.perf_counter() - started) * 1000)

    search_id: Optional[int] = None
    saved = False
    out_results: list[TorrentOut]
    if body.save:
        record = await repo.save_search(
            db,
            query=body.query,
            category=body.category.value,
            results=results,
            nsfw=body.nsfw,
            hide_dead=body.hide_dead,
            sort_by=body.sort.value,
            sort_order=body.order.value,
            max_results=body.max_results,
            providers=sorted(enabled),
            resolve_magnets=body.resolve_magnets,
            duration_ms=duration_ms,
        )
        search_id = record.id
        saved = True
        await activity.say(
            f"Saved search #{search_id} ({len(results.torrents)} results, {duration_ms} ms)",
            level=ActivityLevel.GOOD,
            area="search",
        )
        # Return persisted rows so the UI gets db_id for download-by-id.
        rows = await repo.list_results_for_search(db, search_id)
        out_results = [_torrent_out_from_row(r) for r in rows]
    else:
        out_results = [
            _torrent_out_from_domain(t, rank=i)
            for i, t in enumerate(results.torrents, start=1)
        ]

    return SearchResponse(
        search_id=search_id,
        query=body.query,
        category=body.category.value,
        count=len(out_results),
        duration_ms=duration_ms,
        saved=saved,
        errors=[_error_out(e) for e in results.errors],
        results=out_results,
    )


@app.post(
    "/api/v1/torrents/{db_id}/resolve-magnet",
    response_model=ResolveMagnetOut,
    tags=["search"],
)
async def resolve_torrent_magnet(db_id: int, db: AsyncSession = Depends(get_async_db)):
    """Fetch a magnet for a saved torrent row that still needs a details-page scrape."""
    row = await db.get(TorrentResult, db_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Torrent not found")
    if row.magnet:
        return ResolveMagnetOut(db_id=db_id, magnet=row.magnet, resolved=True)
    if not row.magnet_fetch_url:
        raise HTTPException(status_code=400, detail="No magnet fetch URL for this torrent")

    await activity.say(
        f"Fetching magnet for “{row.name[:60]}”…",
        area="search",
    )
    async with NetworkClient() as network:
        providers = create_builtin_providers(network)
        service = SearchService(providers, nsfw=True)
        # Build a minimal Torrent so SearchService can resolve via provider.
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
            category=_safe_category(row.category),
        )
        magnet = await service.resolve_magnet(stub)

    if not magnet:
        await activity.say(
            f"Couldn’t fetch magnet for “{row.name[:60]}”",
            level=ActivityLevel.WARN,
            area="search",
        )
        return ResolveMagnetOut(db_id=db_id, magnet=None, resolved=False)

    row.magnet = magnet
    await db.flush()
    await activity.say(
        f"Got magnet for “{row.name[:60]}”",
        level=ActivityLevel.GOOD,
        area="search",
    )
    return ResolveMagnetOut(db_id=db_id, magnet=magnet, resolved=True)


@app.get("/api/v1/searches", response_model=PaginatedSearches, tags=["history"])
async def list_searches(
    q: Optional[str] = Query(default=None, description="Filter by query text"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_async_db),
):
    rows, total = await repo.list_searches(db, limit=limit, offset=offset, q=q)
    return PaginatedSearches(
        total=total,
        limit=limit,
        offset=offset,
        items=[_search_summary(r) for r in rows],
    )


@app.get("/api/v1/searches/{search_id}", response_model=SearchDetail, tags=["history"])
async def get_search(search_id: int, db: AsyncSession = Depends(get_async_db)):
    row = await repo.get_search(db, search_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Search not found")
    return SearchDetail(
        id=row.id,
        query=row.query,
        category=row.category,
        nsfw=row.nsfw,
        hide_dead=row.hide_dead,
        sort_by=row.sort_by,
        sort_order=row.sort_order,
        max_results=row.max_results,
        resolve_magnets=row.resolve_magnets,
        result_count=row.result_count,
        error_count=row.error_count,
        duration_ms=row.duration_ms,
        providers=row.providers,
        created_at=row.created_at,
        results=[_torrent_out_from_row(t) for t in row.results],
        errors=[
            ProviderErrorOut(
                provider_id=e.provider_id,
                provider=e.provider_name,
                url=e.provider_url,
                kind=e.kind,
                message=e.message,
            )
            for e in row.errors
        ],
    )


@app.delete("/api/v1/searches/{search_id}", tags=["history"])
async def delete_search(search_id: int, db: AsyncSession = Depends(get_async_db)):
    ok = await repo.delete_search(db, search_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Search not found")
    await activity.say(f"Deleted saved search #{search_id}", area="search")
    return {"deleted": True, "id": search_id}


@app.get("/api/v1/torrents", response_model=PaginatedTorrents, tags=["history"])
async def list_saved_torrents(
    q: Optional[str] = Query(default=None, description="Filter by torrent name"),
    provider: Optional[str] = None,
    search_id: Optional[int] = None,
    min_seeders: Optional[int] = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_async_db),
):
    rows, total = await repo.list_torrents(
        db,
        limit=limit,
        offset=offset,
        q=q,
        provider=provider,
        search_id=search_id,
        min_seeders=min_seeders,
    )
    return PaginatedTorrents(
        total=total,
        limit=limit,
        offset=offset,
        items=[_torrent_out_from_row(r) for r in rows],
    )


def main() -> None:
    import uvicorn

    uvicorn.run(
        "torrentsearch.api.app:app",
        host="0.0.0.0",
        port=int(__import__("os").environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
