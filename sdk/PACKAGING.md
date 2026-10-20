# Packaging torrent-engine + SDK for other apps

## What you ship

```
your-app/
  torrent-engine/                 ← from engine/dist/latest or a GitHub Release
    torrent-engine.exe
    *.dll
    config.yaml                   ← set update.manifest_url
    ffmpeg/  (or point ffmpeg_dir)
  (optional) embed SDK in your language
```

Host apps **do not** need the Python FastAPI Cinema UI. They only need the engine binary + this SDK (or OpenAPI-generated client).

## Integration pattern

1. **On app start** — spawn engine, parse `ENGINE_READY port=N` from stdout.
2. **Talk HTTP** — `http://127.0.0.1:N/v1/...` via SDK.
3. **On quit** — kill the engine process.
4. **Updates** — either:
   - call `torrent-engine --update` (engine self-update), and/or
   - ship engine updates inside your own app installer, and
   - call SDK `check_sdk_update()` / `checkSdkUpdate()` for library bumps.

## Update flow

| Component | Command / API | What updates |
|-----------|---------------|--------------|
| Engine binary | `torrent-engine --check-update` / `--update` | exe + DLLs from release zip |
| Engine (running) | `GET /v1/update/check` | status only (no apply) |
| Python SDK | `eng.check_sdk_update()` | reports newer pip package |
| JS SDK | `eng.checkSdkUpdate()` | reports newer npm package |

Manifest: [`../update/manifest.json`](../update/manifest.json)

1. Bump `VERSION` + `engine/include/version.hpp` + SDK versions + manifest.
2. Build Release → `engine/scripts/export_dist.ps1`.
3. Zip `engine/dist/latest` as `torrent-engine-windows-x64.zip`, compute SHA-256.
4. Upload to GitHub Releases; put the asset URL + sha256 into the hosted manifest.
5. Set `update.manifest_url` (or `TORRENT_ENGINE_UPDATE_URL`) to that JSON URL.

```bat
torrent-engine --version
torrent-engine --check-update
torrent-engine --update
```

## Desktop app recipe (Electron / Tauri / WPF / …)

```text
App process
  └─ child: torrent-engine.exe --port 0 --data-dir %APPDATA%/MyApp/engine-data
       stdout: ENGINE_READY port=54321
  └─ SDK client → http://127.0.0.1:54321
```

- Keep `bind: 127.0.0.1`.
- Optional later: spawn with a random `--token` (not required today).
- Prefer **your installer** for major upgrades; use `--update` for engine hotfixes between app releases.

## Language coverage

| Language | Use |
|----------|-----|
| Python | `pip install` / vendor `sdk/python` |
| JS / TS | `npm install` / vendor `sdk/javascript` |
| Go / C# / Kotlin / Rust / … | Generate from `sdk/openapi/openapi.yaml` |

## Version compatibility

- Engine `/v1/health` and `/v1/version` expose `version`, `openapi`, `sdk_compat_min`.
- SDKs should warn if engine `version` &lt; their expected minimum, or if manifest `engine.min_sdk` &gt; SDK version.
