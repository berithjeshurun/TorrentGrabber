# torrent-engine SDK kit

Language-agnostic kit for apps that want **search-free torrent download + cinema streaming** via the C++ `torrent-engine` sidecar.

```
Your app (any language)
        │  HTTP JSON  (OpenAPI)
        ▼
torrent-engine.exe   ← libtorrent + ffmpeg HLS
        │
        ▼
peers / disk / browser / mpv / cast URL
```

## Is it plug-and-play?

**Yes — as a local sidecar.**

1. Ship or point at `torrent-engine` (+ ffmpeg)
2. Start it → read `ENGINE_READY port=N` from stdout
3. Call `http://127.0.0.1:N/v1/...` from any language

Default bind is **`127.0.0.1`** (control plane stays on the machine). BitTorrent peer traffic uses `share.listen_port` (e.g. 6881), not the HTTP API.

This repo’s Python FastAPI / Cinema UI is **one consumer**. Your Electron, Go, Kotlin, C#, Unity, etc. app can be another — same API.

| Layer | Who owns it |
|-------|-------------|
| Engine binary + OpenAPI | This kit |
| Search / providers / UI / auth | Your app |
| Optional Python FastAPI | Product shell, not required |

## How much can the engine bear?

Hard limits are in `config.yaml` (defaults below). Real capacity is usually **CPU (ffmpeg) + disk + swarm peers**, not HTTP.

| Resource | Default | Practical guidance |
|----------|---------|-------------------|
| Global peer connections | `session.connections_limit: 500` | Fine for a desktop / small NAS |
| Active downloads | `session.active_downloads: 16` | Many quiet torrents OK |
| Stream mode peers | `modes.stream.connections_limit: 400` | One strong stream uses a large share |
| Concurrent **remux** HLS (`-c:v copy`) | — | **~2–4** cinema sessions on a mid PC |
| Concurrent **transcode** / ABR ladder | — | **~1–2** (x264 ultrafast is CPU-heavy) |
| Progressive `/media` Range (no ffmpeg) | — | Cheapest path; many readers OK |
| HTTP API RPS | — | Tiny; localhost JSON, not the bottleneck |

**Rule of thumb**

- **1 user watching 1080p remux** → comfortable  
- **Several background downloads + 1 stream** → designed for this  
- **Multi-tenant cloud with dozens of live HLS encodes** → not the current design; run one engine per machine/user or disable ABR and prefer `/media`

Tune with `config.yaml` → `session.*` and `modes.stream.*`. Watch `GET /v1/metrics.json` (underruns, piece waits, peers).

## Kit layout

```
sdk/
  README.md                 ← you are here
  openapi/openapi.yaml      ← contract (generate clients)
  python/                   ← pip-installable thin client
  javascript/               ← npm-friendly thin client (JS + types)
  examples/                 ← curl, python, node one-liners
```

Generate more languages anytime:

```bash
# example: openapi-generator
openapi-generator-cli generate -i sdk/openapi/openapi.yaml -g go -o sdk/generated/go
openapi-generator-cli generate -i sdk/openapi/openapi.yaml -g csharp -o sdk/generated/csharp
openapi-generator-cli generate -i sdk/openapi/openapi.yaml -g kotlin -o sdk/generated/kotlin
```

## Minimal flow (any language)

```http
POST /v1/torrents
{"magnet":"magnet:?xt=…","mode":"stream"}

GET  /v1/torrents/{id}          # wait until files / progress
GET  /v1/torrents/{id}/media    # progressive Range play
# or
POST /v1/torrents/{id}/hls      # retry on 503 until ready
GET  /v1/torrents/{id}/hls/index.m3u8
GET  /v1/torrents/{id}/buffer   # underrun / sequential_bytes
POST /v1/torrents/{id}/boost    # expand lookahead
```

## Start the engine

```bat
engine\build\Release\torrent-engine.exe --ffmpeg-dir python\etc --data-dir engine-data --port 17890
```

Or use the exported folder from `engine\scripts\export_dist.ps1` (`engine\dist\latest\`).

## Clients in this kit

| Language | Path | Install |
|----------|------|---------|
| Python 3.10+ | [`python/`](python/) | `pip install -e sdk/python` |
| JavaScript / TypeScript | [`javascript/`](javascript/) | `npm install ./sdk/javascript` |
| Any | [`openapi/openapi.yaml`](openapi/openapi.yaml) | codegen / raw HTTP |

## Versioning & updates

SDK clients target engine **OpenAPI `0.1.0`**. Bump clients when `engine/openapi.yaml` changes; re-copy into `sdk/openapi/`.

| Check | How |
|-------|-----|
| Engine version | `torrent-engine --version` or `GET /v1/version` |
| Engine update | `torrent-engine --check-update` / `--update` |
| Running engine | `GET /v1/update/check` or SDK `check_engine_update()` |
| Python SDK | `TorrentEngine(...).check_sdk_update()` |
| JS SDK | `eng.checkSdkUpdate()` |

Manifest template: [`../update/manifest.json`](../update/manifest.json)  
How to ship to other apps: [`PACKAGING.md`](PACKAGING.md)

Set `TORRENT_ENGINE_UPDATE_URL` or `update.manifest_url` in `config.yaml`.
