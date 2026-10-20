"""Watch-party + session control routes (3rd-party controllable)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..downloader.hybrid_engine import HybridEngine
from .downloads import _manager

router = APIRouter(prefix="/api/v1/party", tags=["party"])


@router.post("")
async def party_create(request: Request):
    """Shared clock room — magnet + optional host_peer; BT remains data plane."""
    engine = _manager(request).engine
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    body = await request.json()
    try:
        return await engine.cpp.party_create(
            magnet=body.get("magnet", ""),
            host_peer=body.get("host_peer"),
            torrent_id=body.get("torrent_id"),
            room_id=body.get("id"),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("")
async def party_list(request: Request):
    engine = _manager(request).engine
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await engine.cpp.party_list()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{room_id}")
async def party_get(room_id: str, request: Request):
    engine = _manager(request).engine
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await engine.cpp.party_get(room_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/{room_id}/sync")
async def party_sync(room_id: str, request: Request):
    engine = _manager(request).engine
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    body = await request.json()
    try:
        return await engine.cpp.party_sync(
            room_id,
            position_sec=float(body.get("position_sec", 0)),
            paused=bool(body.get("paused", True)),
            who=str(body.get("who", "peer")),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/{room_id}")
async def party_delete(room_id: str, request: Request):
    engine = _manager(request).engine
    if not isinstance(engine, HybridEngine) or engine.cpp is None:
        raise HTTPException(status_code=503, detail="C++ engine required")
    try:
        return await engine.cpp.party_delete(room_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
