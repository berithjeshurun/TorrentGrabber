"""Start torrent-engine separately, then: python sdk/examples/python_quickstart.py MAGNET"""

from __future__ import annotations

import sys
import time

# Allow running from repo without install
sys.path.insert(0, str(__file__).rsplit("examples", 1)[0] + "python")

from torrent_engine import EngineError, TorrentEngine


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python_quickstart.py 'magnet:?xt=…'")
        sys.exit(2)
    magnet = sys.argv[1]
    base = sys.environ.get("TORRENT_ENGINE_URL", "http://127.0.0.1:17890")
    with TorrentEngine(base) as eng:
        print("health:", eng.health().get("ok"))
        job = eng.add(magnet, mode="stream")
        tid = job["id"]
        print("id:", tid)
        print("media:", eng.media_url(tid))
        for _ in range(40):
            buf = eng.buffer(tid)
            print(
                "ahead_bytes=",
                buf.get("contiguous_ahead_bytes"),
                "warn=",
                buf.get("warning"),
            )
            try:
                hls = eng.start_hls(tid)
                print("playlist:", hls.get("playlist_url") or eng.hls_playlist_url(tid))
                return
            except EngineError as e:
                if e.status_code == 503 and isinstance(e.data, dict) and e.data.get("retry"):
                    time.sleep(2)
                    continue
                raise
        raise SystemExit("timed out waiting for HLS gate")


if __name__ == "__main__":
    main()
