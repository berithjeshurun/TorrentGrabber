"""Quick end-to-end smoke for real UI API flows."""
from __future__ import annotations

from importlib import import_module

from fastapi.testclient import TestClient


def main() -> None:
    mod = import_module("torrentsearch.api.app")
    with TestClient(mod.app) as client:
        assert client.get("/ui/").status_code == 200
        assert "TorrentSearch" in client.get("/ui/").text

        r = client.post(
            "/api/v1/search",
            json={
                "query": "ubuntu",
                "max_results": 5,
                "providers": ["torrentscsv", "knaben"],
                "resolve_magnets": True,
                "save": True,
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()
        print("search count", data["count"], "search_id", data["search_id"])
        assert data["saved"] and data["search_id"]
        assert data["results"], "expected results"
        assert all(t.get("db_id") for t in data["results"]), data["results"][0]

        # download via torrent_result_id
        row = next((t for t in data["results"] if t.get("magnet") or t.get("db_id")), None)
        assert row
        d = client.post(
            "/api/v1/downloads",
            json={"torrent_result_id": row["db_id"], "preset": "stream"},
        )
        print("download", d.status_code, d.json().get("id"), d.json().get("status"))
        assert d.status_code == 200, d.text

        hist = client.get(f"/api/v1/searches/{data['search_id']}")
        assert hist.status_code == 200
        assert hist.json()["results"]
        print("history ok", len(hist.json()["results"]))

        act = client.get("/api/v1/activity")
        assert act.status_code == 200
        print("activity events", len(act.json()["events"]))
        print("OK")


if __name__ == "__main__":
    main()
