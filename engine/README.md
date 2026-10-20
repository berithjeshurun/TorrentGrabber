# torrent-engine

Headless **C++20 / libtorrent** streaming & download sidecar. No Python or Node required at runtime.

Other apps talk to it over localhost HTTP (`127.0.0.1`).

## Build (Windows)

Requirements: VS 2022 C++ workload, CMake 3.20+, git.

```bat
:: from repo root
tools\vcpkg\bootstrap-vcpkg.bat
cmake -S engine -B engine\build ^
  -DCMAKE_TOOLCHAIN_FILE=%CD%\tools\vcpkg\scripts\buildsystems\vcpkg.cmake ^
  -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
```

Binary: `engine\build\Release\torrent-engine.exe`

### Export binary folder (OpenAPI + Release)

```powershell
.\engine\scripts\export_dist.ps1
```

Writes `engine\dist\torrent-engine-0.1.0\` (and `engine\dist\latest\`) with exe, DLLs, `config.yaml`, `openapi.yaml`, `README.md`, and ffmpeg if found under `python\etc`.

### Multi-language SDK kit

Other apps (any language) should use the **[`sdk/`](../sdk/)** kit:

- OpenAPI contract → `sdk/openapi/openapi.yaml`
- Python client → `pip install -e sdk/python`
- JS/TS client → `npm install ./sdk/javascript`
- Examples → `sdk/examples/`

The engine is a **plug-and-play localhost sidecar**: start the exe, read `ENGINE_READY port=N`, call `/v1/*`.

Point at ffmpeg (already in this repo):

```bat
engine\build\Release\torrent-engine.exe --ffmpeg-dir python\etc --data-dir engine-data
```

Stdout will print:

```
ENGINE_READY port=12345
```

## API (v1)

| Method | Path | Body / notes |
|--------|------|----------------|
| GET | `/v1/health` | version + session + HLS metrics |
| GET | `/v1/metrics` | Prometheus text (HLS starts/gates/errors) |
| POST | `/v1/share` | create+seed from local `path` (friend/private) |
| GET | `/v1/session` | listen_port, public_host, config |
| GET | `/v1/playlist.m3u` | session M3U of active media |
| POST | `/v1/torrents` | `{"magnet":"<magnet|path|http(s) .torrent>","mode":"friend","peers":[...]}` |
| GET | `/v1/torrents` | list |
| GET | `/v1/torrents/{id}` | status |
| POST | `/v1/torrents/{id}/pause` | |
| POST | `/v1/torrents/{id}/resume` | |
| POST | `/v1/torrents/{id}/peers` | `{"ip":"...","port":6881}` direct dial |
| GET | `/v1/torrents/{id}/torrent` | download .torrent (share jobs) |
| DELETE | `/v1/torrents/{id}?delete_files=true` | |
| POST | `/v1/torrents/{id}/playhead` | `{"offset_bytes":123}` — TorrServer-style NOW/NEXT/READAHEAD/HIGH/SPEC piece bands + 2s priority watchdog |
| GET | `/v1/torrents/{id}/media` | HTTP Range video bytes |
| GET | `/v1/torrents/{id}/tracks` | ffprobe tracks (+ hdr / interlaced) |
| POST | `/v1/torrents/{id}/hls` | live HLS — sequential gate (503+retry), pipe MKV/TS, ABR |
| POST | `/v1/torrents/{id}/media-file` | `{"index":N}` switch multi-file media |
| GET | `/v1/torrents/{id}/playlist.m3u` | M3U for VLC/mpv LAN share |
| POST | `/v1/torrents/{id}/subtitle?index=N` | WebVTT softsubs |
| GET | `/v1/torrents/{id}/hls/{file}` | `index.m3u8` / `master.m3u8`, segs, VTT |
| POST | `/v1/torrents/{id}/poster` | cover frame @ ~10% |
| GET | `/v1/torrents/{id}/poster.jpg` | poster JPEG |
| POST | `/v1/torrents/{id}/thumbs` | scrub sprite + `thumbs.vtt` |
| GET | `/v1/torrents/{id}/thumbs/{file}` | sprite / VTT |
| POST | `/v1/torrents/{id}/clip` | `{start,end,container}` → share clip |
| POST | `/v1/torrents/{id}/remux` | remux/select tracks → MP4/MKV |
| POST | `/v1/torrents/{id}/cast?profile=chromecast` | cast-safe H.264+AAC |
| POST | `/v1/torrents/{id}/waveform` | audiowaveform + silence regions |
| POST | `/v1/torrents/{id}/chapters` | scene-detect chapters |
| GET | `/v1/torrents/{id}/exports/{file}` | download export |
| GET | `/v1/torrents/{id}/buffer` | buffer health / underrun (3rd-party) |
| POST | `/v1/torrents/{id}/boost` | expand lookahead temporarily |
| POST | `/v1/torrents/{id}/abr` | preferred ladder height (0=auto) |
| GET | `/v1/torrents/{id}/invite` | magnet + dial + `qr_text` / deep_link |
| POST | `/v1/torrents/{id}/cast/session` | cast URL + token (export or HLS) |
| POST | `/v1/party` | watch-party room (shared clock) |
| GET | `/v1/party` | list rooms |
| GET/POST | `/v1/party/{id}` / `…/sync` | poll / push party clock |
| DELETE | `/v1/party/{id}` | delete room |

All of the above are the **control plane for 3rd-party apps** (OpenAPI). Cinema UI only calls the same routes.

### Instant play (HLS readiness)

- Status / `/buffer` expose `sequential_bytes` and `buffer_ahead_seconds`.
- `POST …/hls` returns **503** with `retry: true`, `reason`, `sequential_bytes` until ~16 MiB contiguous (pipeable MKV/AVI/TS) or near-complete for MP4/MOV.
- Pipeable containers use a **piece-waited stdin pipe** into ffmpeg (`pipe:0`); on pipe failure the engine **falls back to HTTP `/media`**.
- MP4/MOV set `preferred_mode: progressive` — use Range `/media` (or mpv/VLC) until nearly complete.
- Piece bands (NOW/NEXT/READAHEAD/HIGH/SPEC) + deadline gradient; re-armed by a ~2s watchdog; cleared on pause.
- Far seeks **boost** then restart HLS; ABR height auto-suggests from download rate when unset.

### Metrics

- `GET /v1/metrics` — Prometheus text
- `GET /v1/metrics.json` — JSON (underruns, piece waits, HLS starts/errors/restarts, peers)

### Optional FastAPI sidecars (Python)

| Env / API | Purpose |
|-----------|---------|
| `POST /api/v1/downloads/{id}/open-player?player=mpv\|vlc` | Launch local mpv/VLC (`TORRENTSEARCH_MPV` / `TORRENTSEARCH_VLC`) |
| `POST /api/v1/downloads/{id}/cast/sidecar?target=chromecast` | `webtorrent-cli` cast when `TORRENTSEARCH_CAST_BACKEND=webtorrent` |
| `GET /api/v1/metrics` | Proxies engine Prometheus metrics |
| `WS /api/v1/ws` | Live metrics/status/buffer + commands (`boost`, `pause`, `abr`, …) |
| `GET/PUT …/resume-position` | Continue-watching positions |

### Live HLS (`hls:` in config.yaml)

- `audio_bitrate` / `audio_channels` (`stereo`|`keep`), `hls_time: 2`, `low_latency`
- `loudnorm`, `deinterlace: auto|on|off`, `tonemap: auto|off|hable|…` (**HDR→SDR only**)
- `lut_cube`: path to a `.cube` (samples in [`luts/`](luts/))
- `abr: false` — set `true` for 360/720/1080 master playlist
- API can override per request; `start_seconds` restarts encode for far seeks; `burn_subtitle` burns PGS/VobSub; `audio_only` for listen-while-downloading

Modes: `general`, `stream`, `limited`, `fastest`, `preview`, `selective`, `seed`, `mirror`, `friend`.

## Friend share (1-on-1 private)

**Alice** seeds a local file:

```http
POST /v1/share
{"path":"D:/Videos/clip.mkv","private":true}
```

Send the returned `magnet` (+ `public_host`:`listen_port` if set) to Bob out-of-band.

**Bob** receives:

```http
POST /v1/torrents
{"magnet":"<alice magnet>","mode":"friend","peers":[{"ip":"<alice>","port":6881}]}
```

`friend` disables DHT/LSD/PEX for that swarm.

### Share over the internet (BT port, not HTTP)

The HTTP API stays on `127.0.0.1`. Reachability for friends is **BitTorrent**:

1. Set `share.listen_port` (e.g. `6881`) in `config.yaml`
2. Set `share.public_host` to your Tailscale / LAN / public IP, **or** `auto_detect_public_host: true`
3. Forward **UDP+TCP** that port on the router (or use Tailscale / rely on UPnP/NAT-PMP)
4. `POST /v1/share` — returned `magnet` includes `&x.pe=host:port` when `public_host` is set; also see `dial_endpoint` / `GET /v1/session`

Bob adds the magnet with `mode=friend` (or `POST .../peers` with Alice’s `dial_endpoint`).

## Config (`config.yaml`)

Loaded automatically from (first match wins):

1. `--config PATH`
2. `%CD%\config.yaml` (current working directory)
3. `<exe-dir>\config.yaml` (shipped next to `torrent-engine.exe`)

Tune session peers and per-mode workers there — see [config.yaml](config.yaml).
CLI flags (`--port`, `--data-dir`, …) override YAML.

## Test player (3rd-party harness)

Standalone HTML UI that talks to the engine’s `/v1` API (add / HLS /media / buffer / boost / ABR / invite):

```powershell
# 1) start engine (note ENGINE_READY port=N)
engine\build\Release\torrent-engine.exe --ffmpeg-dir python\etc --data-dir engine-data

# 2) serve the player (default http://127.0.0.1:8765/)
.\engine\scripts\serve_test_player.ps1

# 3) paste http://127.0.0.1:N into the Connect field
```

Source: [`test-player/index.html`](test-player/index.html) (also copied into `engine/dist/*/test-player/` on export).

## Smoke test

```powershell
.\engine\scripts\smoke.ps1          # Range /media
.\engine\scripts\smoke_tracks.ps1   # tracks + HLS (+ soft-sub clear)
.\engine\scripts\smoke_hls_opts.ps1 # poster / thumbs / HLS options
```

Python FastAPI will auto-spawn this binary when `engine\build\Release\torrent-engine.exe`
exists and route Cinema tracks / multi-audio HLS / softsubs through it.

## Design

- **Download / stream core:** libtorrent (DHT, LSD, uTP, piece deadlines, sequential bands)
- **Friend share:** private torrent create/seed + direct peer dial (`x.pe` / `/peers`)
- **Browser-friendly MKV:** ffmpeg HLS + AAC; subtitles extracted to WebVTT without remux restart
- **Python UI** (optional later): call this engine instead of Node/aria2
