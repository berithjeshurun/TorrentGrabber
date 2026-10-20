# TorrentGrabber

Multi-provider torrent **search** (Python / Android) + **torrent-engine** (C++ libtorrent cinema sidecar) + **SDK** for other apps.

## Layout

| Path | Role |
|------|------|
| `engine/` | C++ `torrent-engine` HTTP sidecar (download + HLS stream) |
| `sdk/` | Multi-language clients + OpenAPI + packaging guide |
| `python/` | Search API + optional Cinema UI (uses the engine) |
| `app/` | Android search client |
| `update/manifest.json` | Version manifest for `--update` / SDK checks |
| `VERSION` | Current release version |

## Quick start (engine + SDK)

```bat
:: build engine (requires vcpkg — see engine/README.md)
cmake --build engine\build --config Release --target torrent-engine

engine\build\Release\torrent-engine.exe --ffmpeg-dir python\etc --port 17890
```

```bat
torrent-engine --version
torrent-engine --check-update
torrent-engine --update
```

Other apps: read [`sdk/PACKAGING.md`](sdk/PACKAGING.md) and [`sdk/README.md`](sdk/README.md).

## Updates

Host `update/manifest.json` on HTTPS, set:

```yaml
# config.yaml
update:
  manifest_url: "https://…/manifest.json"
```

or `TORRENT_ENGINE_UPDATE_URL`. Then `torrent-engine --update` self-replaces the Windows build from the release zip.
