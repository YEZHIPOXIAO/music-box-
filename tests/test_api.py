"""API tests using FastAPI's TestClient (no live network)."""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from musicbox.app import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    data = client.get("/api/health").json()
    assert data["ok"] is True
    assert set(data["providers"]) == {"qq", "kugou", "kuwo"}
    assert data["privacy"]["telemetry"] is False
    assert data["privacy"]["cloud"] is False


def test_providers_metadata(client):
    data = client.get("/api/providers").json()
    ids = {p["id"] for p in data["providers"]}
    assert ids == {"qq", "kugou", "kuwo"}
    for p in data["providers"]:
        assert p["name"]
        assert p["color"].startswith("#")
        assert p["qualities"]


def test_index_serves_ui(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "MusicBox" in res.text
    assert "referrer" in res.text  # no-referrer policy


def test_static_assets(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_search_rejects_empty_query(client):
    assert client.get("/api/search", params={"q": ""}).status_code == 422


def test_unknown_provider_is_reported_not_crashed(client):
    res = client.get("/api/search", params={"q": "晴天", "provider": "spotify"})
    # unknown provider id is collected into errors, never a 500
    assert res.status_code == 200
    body = res.json()
    assert body["errors"] and body["errors"][0]["provider"] == "spotify"


def test_login_rejects_garbage_cookie(client):
    res = client.post("/api/account/qq", json={"cookie": ";;;  "})
    assert res.status_code == 400


def test_login_and_logout_cycle(client):
    res = client.post("/api/account/qq", json={"cookie": "uin=1; qm_keyst=abc"})
    assert res.status_code == 200
    assert res.json()["cookieCount"] == 2

    check = client.get("/api/account/qq/check").json()
    assert check["loggedIn"] is True
    assert "qm_keyst" in check["cookieNames"]

    res = client.delete("/api/account/qq")
    assert res.json()["ok"] is True
    assert client.get("/api/account/qq/check").json()["loggedIn"] is False


def test_playlist_crud(client):
    created = client.post("/api/playlists", json={"name": "测试歌单"}).json()
    pid = created["id"]
    assert created["ok"] is True

    track = {
        "provider": "qq",
        "id": "0039MnYb0qxYhV",
        "title": "晴天",
        "artists": ["周杰伦"],
        "album": "叶惠美",
        "duration": 269,
        "cover": "",
        "extra": {},
    }
    assert client.post(f"/api/playlists/{pid}/items", json=track).json()["ok"] is True
    dup = client.post(f"/api/playlists/{pid}/items", json=track).json()
    assert dup["duplicate"] is True

    detail = client.get(f"/api/playlists/{pid}").json()
    assert len(detail["tracks"]) == 1
    assert detail["tracks"][0]["title"] == "晴天"

    assert client.delete(f"/api/playlists/{pid}/items/qq/0039MnYb0qxYhV").json()["ok"] is True
    assert client.get(f"/api/playlists/{pid}").json()["tracks"] == []
    assert client.delete(f"/api/playlists/{pid}").json()["ok"] is True


def test_history_crud(client):
    track = {"provider": "kuwo", "id": "51685512", "title": "晴天"}
    assert client.post("/api/history", json=track).json()["ok"] is True
    hist = client.get("/api/history").json()["history"]
    assert hist and hist[0]["title"] == "晴天"
    assert client.delete("/api/history").json()["removed"] >= 1


def test_wipe_requires_confirmation(client):
    assert client.post("/api/privacy/wipe", json={}).status_code == 400
    assert client.post("/api/privacy/wipe", json={"confirm": True}).json()["ok"] is True


def test_stream_unknown_track_reports_upstream_error(client):
    # A bogus id must produce a clean 424, not a stack trace.
    res = client.get("/api/stream/kuwo/notanumber")
    assert res.status_code in (424, 502)
    assert "detail" in res.json()
