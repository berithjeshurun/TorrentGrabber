"""Smoke: C++ engine tracks + HLS via CppEngineClient."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "python"))

from torrentsearch.downloader.cpp_engine import CppEngineClient  # noqa: E402
from torrentsearch.downloader.modes import DownloadConfig, DownloadMode  # noqa: E402

MAGNET = (
    "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"
    "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337"
    "&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F"
    "&xs=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2Fsintel.torrent"
)


async def main() -> None:
    eng = CppEngineClient()
    await eng.start()
    try:
        st = await eng.add(
            MAGNET, DownloadConfig(mode=DownloadMode.STREAM), download_id="smokehls"
        )
        print("added", st.id, st.status.value, flush=True)
        tracks = None
        for _ in range(60):
            await eng.tick()
            st = await eng.get("smokehls")
            assert st
            print(
                f"  peers={st.num_peers} progress={st.progress:.1%} status={st.status.value}",
                flush=True,
            )
            try:
                r = await eng.proxy_media("smokehls", "bytes=0-1")
                body = await r.aread()
                code = r.status_code
                await r.aclose()
                print(f"  media {code} bytes={len(body)}", flush=True)
                if code in (200, 206):
                    try:
                        tracks = await eng.get_tracks("smokehls")
                    except Exception as e:
                        print("  tracks:", e, flush=True)
                    if tracks and (tracks.get("video") or tracks.get("audio")):
                        break
            except Exception as e:
                print("  media:", e, flush=True)
                if "connection" in str(e).lower() or "connect" in str(e).lower():
                    raise SystemExit(f"engine died during tracks/media: {e}")
            await asyncio.sleep(2)
        if not tracks:
            raise SystemExit("tracks smoke failed")
        print(
            "tracks video=",
            len(tracks.get("video") or []),
            "audio=",
            len(tracks.get("audio") or []),
            "subs=",
            len(tracks.get("subtitles") or []),
            "needs_hls=",
            tracks.get("needs_hls"),
            flush=True,
        )
        audio_idx = None
        aud = tracks.get("audio") or []
        if aud:
            audio_idx = aud[0].get("index")
        hls = await eng.start_hls("smokehls", audio=audio_idx)
        print("hls", hls, flush=True)
        if not (hls.get("ready") or hls.get("playlist_url")):
            raise SystemExit("hls smoke failed")
        sub = await eng.set_subtitle("smokehls", index=None)
        print("subtitle clear", sub, flush=True)
        print("TRACKS/HLS SMOKE OK", flush=True)
    finally:
        try:
            await eng.remove("smokehls", delete_files=True)
        except Exception:
            pass
        await eng.stop()


if __name__ == "__main__":
    asyncio.run(main())
