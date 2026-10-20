# torrent-engine Python SDK

```bash
pip install -e sdk/python
```

```python
from torrent_engine import TorrentEngine

eng = TorrentEngine("http://127.0.0.1:17890")
print(eng.health())
job = eng.add("magnet:?xt=urn:btih:…", mode="stream")
print(job["id"], eng.media_url(job["id"]))
```

Does **not** spawn the binary — start `torrent-engine` yourself (or point `base_url` at an already-running instance).
