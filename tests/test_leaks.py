"""Leak tests: no response body may carry an upstream URL.

A vkey lives in the URL, so echoing ``str(exc)`` to a browser hands over the
user's entitlement (and, for a shared link, someone else's). ``_safe_detail``
is the choke point; these tests drive it through every route that can fail.
"""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from musicbox import app as app_module  # noqa: E402
from musicbox.http import HttpError  # noqa: E402

SECRET_URL = "https://dl.stream.qqmusic.qq.com/M500.mp3?guid=7&vkey=SECRETVKEY999&uin=42"
LEAK_MARKERS = ("SECRETVKEY999", "vkey", "dl.stream.qqmusic", "guid=7", "https://", "http://")


@pytest.fixture()
def client():
    with TestClient(app_module.app) as c:
        yield c


def _assert_clean(body: str, where: str) -> None:
    for marker in LEAK_MARKERS:
        assert marker not in body, f"{where} 泄漏了 {marker!r}: {body[:300]}"


# -- the three routes that used to echo str(exc) ----------------------------
def test_search_error_hides_upstream_url(client, monkeypatch):
    provider = app_module.get_provider("qq")

    def boom(*args, **kwargs):
        raise HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)

    monkeypatch.setattr(provider, "search", boom)
    res = client.get("/api/search", params={"q": "晴天", "provider": "qq"})
    assert res.status_code == 200
    errors = res.json()["errors"]
    assert errors, "provider 失败应出现在 errors 里"
    _assert_clean(str(errors), "/api/search")


def test_fallback_error_hides_upstream_url(client, monkeypatch):
    for pid in ("qq", "kugou", "kuwo"):
        monkeypatch.setattr(
            app_module.get_provider(pid),
            "search",
            lambda *a, **k: (_ for _ in ()).throw(
                HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)
            ),
        )
    res = client.get("/api/fallback", params={"title": "晴天", "exclude": "nothing"})
    assert res.status_code == 200
    _assert_clean(res.text, "/api/fallback")


def test_lyrics_error_hides_upstream_url(client, monkeypatch):
    provider = app_module.get_provider("kuwo")

    def boom(*args, **kwargs):
        raise HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)

    monkeypatch.setattr(provider, "lyrics", boom)
    res = client.get("/api/lyrics/kuwo/123")
    assert res.status_code in (404, 502)
    _assert_clean(res.text, "/api/lyrics")


def test_stream_error_hides_upstream_url(client, monkeypatch):
    provider = app_module.get_provider("qq")

    def boom(*args, **kwargs):
        raise HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)

    monkeypatch.setattr(provider, "stream", boom)
    app_module._stream_cache.clear()
    res = client.get("/api/stream/qq/abc?quality=128")
    assert res.status_code == 502
    _assert_clean(res.text, "/api/stream")


def test_audio_error_hides_upstream_url(client, monkeypatch):
    provider = app_module.get_provider("qq")

    def boom(*args, **kwargs):
        raise HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)

    monkeypatch.setattr(provider, "stream", boom)
    res = client.get("/api/audio/qq/abc")
    assert res.status_code == 502
    _assert_clean(res.text, "/api/audio")


# -- unparseable upstream bodies must not be echoed either ------------------
def test_unparseable_body_is_not_echoed(client, monkeypatch):
    """A ValueError carries the raw upstream text; it must not reach the page."""
    provider = app_module.get_provider("kuwo")

    def boom(*args, **kwargs):
        raise ValueError("unparseable response: <html>token=UPSTREAMLEAK</html>")

    monkeypatch.setattr(provider, "search", boom)
    res = client.get("/api/search", params={"q": "x", "provider": "kuwo"})
    body = res.text
    assert "UPSTREAMLEAK" not in body
    assert "unparseable response" not in body


# -- but the full text must still reach the log -----------------------------
def test_full_error_still_reaches_the_log(client, monkeypatch, caplog):
    """Sanitising the response must not blind the operator."""
    provider = app_module.get_provider("qq")

    def boom(*args, **kwargs):
        raise HttpError(f"request failed: {SECRET_URL}", status=502, url=SECRET_URL)

    monkeypatch.setattr(provider, "search", boom)
    with caplog.at_level("INFO", logger="musicbox"):
        client.get("/api/search", params={"q": "晴天", "provider": "qq"})
    assert "SECRETVKEY999" in caplog.text, "日志里应保留完整错误以便排查"
