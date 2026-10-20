"""WebSocket stream: live metrics/info + bidirectional commands.

Protocol (JSON text frames):

Server → client:
  {"type":"hello","version":"...","commands":[...]}
  {"type":"metrics","data":{...}}
  {"type":"status","download_id":"...","data":{...}}
  {"type":"buffer","download_id":"...","data":{...}}
  {"type":"activity","data":{...}}
  {"type":"ack","cmd":"...","ok":true,"result":{...}}
  {"type":"error","error":"..."}

Client → server:
  {"cmd":"subscribe","download_id":"abc"}
  {"cmd":"unsubscribe"}
  {"cmd":"ping"}
  {"cmd":"metrics"}
  {"cmd":"boost","download_id":"...","duration":45,"multiplier":3}
  {"cmd":"pause"|"resume","download_id":"..."}
  {"cmd":"playhead","download_id":"...","offset_bytes":123}
  {"cmd":"abr","download_id":"...","height":720}
  {"cmd":"media_file","download_id":"...","index":0}
  {"cmd":"open_player","download_id":"...","player":"mpv"}
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import __version__
from ..activity import activity
from ..downloader.hybrid_engine import HybridEngine
from .downloads import _cpp_owner, _manager, _to_out

router = APIRouter(tags=["websocket"])


class _WsRequest:
    """Adapter so existing `_manager(request)` works with WebSocket scope."""

    def __init__(self, websocket: WebSocket):
        self.app = websocket.scope["app"]  # type: ignore[index]


async def _build_metrics(mgr) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True}
    engine = mgr.engine
    if isinstance(engine, HybridEngine) and engine.cpp is not None:
        try:
            client = engine.cpp._require_client()
            r = await client.get("/v1/metrics.json", timeout=10.0)
            if r.status_code < 400 and r.content:
                out["engine"] = r.json()
        except Exception as exc:
            out["engine_error"] = str(exc)
    snap = activity.snapshot(recent_limit=5)
    out["activity"] = {
        "phrase": snap.phrase,
        "healthy": snap.ok,
        "downloads_active": snap.downloads_active,
        "downloads_streaming": snap.downloads_streaming,
        "underruns": snap.underruns,
    }
    try:
        items = await engine.list()
        out["downloads"] = [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status.value
                if hasattr(s.status, "value")
                else str(s.status),
                "progress": s.progress,
                "download_rate": s.download_rate,
                "num_peers": s.num_peers,
                "underrun": s.underrun,
            }
            for s in items[:40]
        ]
    except Exception:
        out["downloads"] = []
    return out


async def _dispatch(
    websocket: WebSocket, msg: dict[str, Any], subscribed: Optional[str]
) -> tuple[Optional[str], dict[str, Any]]:
    cmd = str(msg.get("cmd") or "").strip().lower()
    mgr = _manager(_WsRequest(websocket))  # type: ignore[arg-type]
    download_id = msg.get("download_id") or subscribed

    if cmd == "ping":
        return subscribed, {"cmd": cmd, "ok": True, "result": {"pong": True}}
    if cmd == "subscribe":
        did = str(msg.get("download_id") or "")
        if not did:
            return subscribed, {
                "cmd": cmd,
                "ok": False,
                "error": "download_id required",
            }
        return did, {"cmd": cmd, "ok": True, "result": {"download_id": did}}
    if cmd == "unsubscribe":
        return None, {"cmd": cmd, "ok": True, "result": {}}
    if cmd == "metrics":
        data = await _build_metrics(mgr)
        return subscribed, {"cmd": cmd, "ok": True, "result": data}

    if not download_id:
        return subscribed, {
            "cmd": cmd,
            "ok": False,
            "error": "download_id required (or subscribe first)",
        }

    try:
        if cmd == "boost":
            cpp = _cpp_owner(mgr.engine, str(download_id))
            if cpp is None:
                return subscribed, {
                    "cmd": cmd,
                    "ok": False,
                    "error": "cpp engine required",
                }
            result = await cpp.boost(
                str(download_id),
                duration=float(msg.get("duration", 45)),
                multiplier=float(msg.get("multiplier", 3)),
            )
            return subscribed, {"cmd": cmd, "ok": True, "result": result}

        if cmd == "pause":
            state = await mgr.engine.pause(str(download_id))
            return subscribed, {
                "cmd": cmd,
                "ok": True,
                "result": _to_out(state).model_dump(mode="json"),
            }

        if cmd == "resume":
            state = await mgr.engine.resume(str(download_id))
            return subscribed, {
                "cmd": cmd,
                "ok": True,
                "result": _to_out(state).model_dump(mode="json"),
            }

        if cmd == "playhead":
            state = await mgr.engine.set_playhead(
                str(download_id), int(msg.get("offset_bytes", 0))
            )
            return subscribed, {
                "cmd": cmd,
                "ok": True,
                "result": _to_out(state).model_dump(mode="json"),
            }

        if cmd == "abr":
            cpp = _cpp_owner(mgr.engine, str(download_id))
            if cpp is None:
                return subscribed, {
                    "cmd": cmd,
                    "ok": False,
                    "error": "cpp engine required",
                }
            result = await cpp.set_abr(
                str(download_id), height=int(msg.get("height", 0))
            )
            return subscribed, {"cmd": cmd, "ok": True, "result": result}

        if cmd == "media_file":
            cpp = _cpp_owner(mgr.engine, str(download_id))
            if cpp is None:
                return subscribed, {
                    "cmd": cmd,
                    "ok": False,
                    "error": "cpp engine required",
                }
            result = await cpp.set_media_file(
                str(download_id), int(msg.get("index", 0))
            )
            return subscribed, {"cmd": cmd, "ok": True, "result": result}

        if cmd == "open_player":
            from ..downloader.external_players import open_player

            cpp = _cpp_owner(mgr.engine, str(download_id))
            if cpp is None or not getattr(cpp, "media_base", None):
                return subscribed, {
                    "cmd": cmd,
                    "ok": False,
                    "error": "cpp media URL unavailable",
                }
            player = str(msg.get("player", "mpv"))
            use_hls = bool(msg.get("use_hls", False))
            if use_hls:
                url = (
                    f"http://127.0.0.1:8000/api/v1/downloads/"
                    f"{download_id}/hls/index.m3u8"
                )
            else:
                url = cpp.media_url(str(download_id))
            state = await mgr.engine.get(str(download_id))
            result = open_player(
                player, url, title=getattr(state, "name", None)
            )
            return subscribed, {"cmd": cmd, "ok": True, "result": result}

        return subscribed, {
            "cmd": cmd,
            "ok": False,
            "error": f"unknown cmd '{cmd}'",
        }
    except Exception as exc:
        return subscribed, {"cmd": cmd, "ok": False, "error": str(exc)}


@router.websocket("/api/v1/ws")
async def stream_ws(websocket: WebSocket):
    await websocket.accept()
    subscribed: Optional[str] = None
    interval = 1.5

    await websocket.send_json(
        {
            "type": "hello",
            "version": __version__,
            "commands": [
                "subscribe",
                "unsubscribe",
                "ping",
                "metrics",
                "boost",
                "pause",
                "resume",
                "playhead",
                "abr",
                "media_file",
                "open_player",
            ],
        }
    )

    async def push_loop():
        while True:
            try:
                mgr = _manager(_WsRequest(websocket))  # type: ignore[arg-type]
                data = await _build_metrics(mgr)
                await websocket.send_json({"type": "metrics", "data": data})
                if subscribed:
                    st = await mgr.engine.get(subscribed)
                    if st is not None:
                        await websocket.send_json(
                            {
                                "type": "status",
                                "download_id": subscribed,
                                "data": _to_out(st).model_dump(mode="json"),
                            }
                        )
                    cpp = _cpp_owner(mgr.engine, subscribed)
                    if cpp is not None:
                        try:
                            buf = await cpp.buffer_health(subscribed)
                            await websocket.send_json(
                                {
                                    "type": "buffer",
                                    "download_id": subscribed,
                                    "data": buf,
                                }
                            )
                        except Exception:
                            pass
                snap = activity.snapshot(recent_limit=5)
                await websocket.send_json(
                    {
                        "type": "activity",
                        "data": {
                            "phrase": snap.phrase,
                            "events": [
                                {
                                    "time": e.time.isoformat(),
                                    "level": e.level.value,
                                    "area": e.area,
                                    "message": e.message,
                                }
                                for e in snap.recent
                            ],
                        },
                    }
                )
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                try:
                    await websocket.send_json(
                        {"type": "error", "error": f"push: {exc}"}
                    )
                except Exception:
                    return
            await asyncio.sleep(interval)

    async def recv_loop():
        nonlocal subscribed
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "error": "invalid json"}
                )
                continue
            if not isinstance(msg, dict):
                await websocket.send_json(
                    {"type": "error", "error": "message must be object"}
                )
                continue
            subscribed, ack = await _dispatch(websocket, msg, subscribed)
            await websocket.send_json({"type": "ack", **ack})

    push_task = asyncio.create_task(push_loop())
    try:
        await recv_loop()
    except WebSocketDisconnect:
        pass
    finally:
        push_task.cancel()
        try:
            await push_task
        except asyncio.CancelledError:
            pass
