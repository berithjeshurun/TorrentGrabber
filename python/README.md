# TorrentSearch Python

Rich CLI + **FastAPI service** for multi-provider torrent search.  
1:1 port of the [TorrentSearch](https://github.com/prajwalch/TorrentSearch) Android search layer. Every API search is auto-saved to SQL.

## Install

```bash
cd python
python -m pip install -r requirements.txt
# or editable:
python -m pip install -e .
```

## FastAPI service

```bash
# Start API (SQLite DB created automatically as ./torrentsearch.db)
python -m torrentsearch.api
# or:
torrentsearch-api

# Custom DB / port
set DATABASE_URL=sqlite:///G:/data/torrents.db
set PORT=8080
python -m torrentsearch.api
```

Open docs: http://127.0.0.1:8000/docs  
Web UI: http://127.0.0.1:8000/ui/ (also redirects from `/`)

**Stream (real playback):** server-side Node WebTorrent (UDP trackers work) →  
`GET /api/v1/downloads/{id}/media` → HTML5 `<video>`. Requires **Node.js** on PATH.  
Click **Demo stream (Sintel)** to verify. Prefer MP4/WebM releases.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health + plain-language status phrase |
| `GET` | `/api/v1/activity` | Recent human-readable activity feed |
| `GET` | `/api/v1/providers` | List provider IDs |
| `POST` | `/api/v1/search` | Search (auto-saves to SQL) |
| `GET` | `/api/v1/searches` | Search history |
| `GET` | `/api/v1/searches/{id}` | Saved search + results |
| `DELETE` | `/api/v1/searches/{id}` | Delete a saved search |
| `GET` | `/api/v1/torrents` | Query all saved torrents |
| `GET` | `/api/v1/downloads/modes` | List download modes |
| `GET` | `/api/v1/downloads/presets` | Named download presets |
| `POST` | `/api/v1/downloads` | Start a download (magnet or saved torrent id) |
| `GET` | `/api/v1/downloads` | List active downloads |
| `GET` | `/api/v1/downloads/{id}` | Download status (progress, buffer, underrun) |
| `POST` | `/api/v1/downloads/{id}/playhead` | Update stream playhead (bytes or seconds) |
| `POST` | `/api/v1/downloads/{id}/pause` | Pause |
| `POST` | `/api/v1/downloads/{id}/resume` | Resume |
| `DELETE` | `/api/v1/downloads/{id}` | Remove download |
| `GET` | `/api/v1/library/continue` | Continue-watching shelf |
| `GET` | `/api/v1/library/downloads` | Recent downloads library |
| `GET` | `/api/v1/downloads/{id}/subtitles/external` | OpenSubtitles search |
| `POST` | `/api/v1/downloads/{id}/subtitles/external?file_id=` | Attach OpenSubtitles → WebVTT |

Search results are **deduped by infohash**, episode-filtered when the query implies SxxExx / trailing ep number, and ranked by title match + seeders + resolution/group (Magnetio/Cauldron-style).

OpenSubtitles needs `TORRENTSEARCH_OPENSUBTITLES_KEY` (and optional `TORRENTSEARCH_OPENSUBTITLES_LANG=en`).

### Download modes

| Mode | Behavior |
|------|----------|
| `general` | Rarest-first + endgame near completion |
| `stream` | Playhead bands (urgent → sequential → lookahead) + speculative workers; underrun steals speculative slots |
| `preview` | Head + sparse mid/end samples only |
| `selective` | Chosen file indexes (multi-file) |
| `fastest` | High parallelism + small sequential head-start |
| `limited` | Low peers / rate caps |
| `seed` | Upload / ratio policy |
| `mirror` | BitTorrent + optional HTTP webseeds for fast start |

Presets: `general`, `stream`, `stream_smooth`, `stream_catchup`, `preview`, `limited`, `seed`, `fastest`, `mirror`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/downloads ^
  -H "Content-Type: application/json" ^
  -d "{\"magnet\":\"magnet:?xt=urn:btih:…\",\"preset\":\"stream\"}"

curl -X POST http://127.0.0.1:8000/api/v1/downloads/DOWNLOAD_ID/playhead ^
  -H "Content-Type: application/json" ^
  -d "{\"offset_seconds\":120,\"bitrate_bps\":8000000}"
```

The downloader currently runs on a **simulated engine** (piece-priority policies + tick loop) so modes can be exercised without libtorrent. A real libtorrent backend can implement the same `DownloadEngine` interface.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/search ^
  -H "Content-Type: application/json" ^
  -d "{\"query\":\"ubuntu\",\"max_results\":20,\"providers\":[\"knaben\",\"torrentscsv\"]}"

curl http://127.0.0.1:8000/api/v1/searches
curl http://127.0.0.1:8000/api/v1/searches/1
curl "http://127.0.0.1:8000/api/v1/torrents?q=ubuntu&min_seeders=5"
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v1/activity
```

The API is **fully async** (async SQL, parallel providers, background downloader).  
Console + `GET /api/v1/activity` show plain-language status (“Looking up…”, “Buffer catching up”, etc.).

Pass `"save": false` in the search body to skip persistence.  
Default DB file: `torrentsearch.db` (override with `DATABASE_URL` or `TORRENTSEARCH_DB`).

## CLI

```bash
python -m torrentsearch providers
python -m torrentsearch search "ubuntu"
python -m torrentsearch search "ubuntu" -n 30 -o results.json
python -m torrentsearch search "matrix" -o hits.csv
python -m torrentsearch details "https://nyaa.si/view/…" --provider nyaasi
```

Magnets that need a details-page scrape are resolved automatically (`--no-resolve-magnets` to skip).

### Search flags

| Flag | Meaning |
|------|---------|
| `-c / --category` | `All`, `Anime`, `Apps`, `Books`, `Games`, `Movies`, `Music`, `Porn`, `Series`, `Other` |
| `-p / --providers` | Comma-separated provider IDs |
| `--all-providers` | Every SFW provider (or all with `--nsfw`) |
| `-f / --format` | `table`, `json`, `csv`, `tsv`, `xml`, `magnets` |
| `-o / --dump PATH` | Write results to file |

## Layout

```
python/torrentsearch/
  api/          FastAPI app + schemas (+ downloads)
  db/           SQLModel tables + repository (auto-save)
  downloader/   Multi-mode piece schedulers + engine
  providers/    46 built-in scrapers/APIs
  cli.py ui.py  Rich CLI
  search.py     Parallel search orchestration
```

## Notes

- Cloudflare-protected providers may fail without cookies.
- Does not host or distribute copyrighted content — comply with local law.
