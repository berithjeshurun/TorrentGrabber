#!/usr/bin/env bash
# Plug-and-play against a running torrent-engine (default port 17890).
set -euo pipefail
BASE="${TORRENT_ENGINE_URL:-http://127.0.0.1:17890}"
MAGNET="${1:?usage: curl_stream.sh 'magnet:?xt=…'}"

curl -s "$BASE/v1/health" | head -c 200; echo

ID=$(curl -s -X POST "$BASE/v1/torrents" \
  -H "Content-Type: application/json" \
  -d "{\"magnet\":\"$MAGNET\",\"mode\":\"stream\"}" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

echo "id=$ID"
echo "media=$BASE/v1/torrents/$ID/media"

# Wait for some sequential bytes, then start HLS (retry on 503)
for i in $(seq 1 40); do
  BUF=$(curl -s "$BASE/v1/torrents/$ID/buffer")
  echo "$BUF" | head -c 180; echo
  CODE=$(curl -s -o /tmp/hls.json -w "%{http_code}" -X POST "$BASE/v1/torrents/$ID/hls")
  if [ "$CODE" = "200" ]; then
    echo "HLS ready: $BASE/v1/torrents/$ID/hls/index.m3u8"
    exit 0
  fi
  sleep 2
done
echo "timed out waiting for HLS" >&2
exit 1
