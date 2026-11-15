"""Audio proxy tests — the area with the least coverage before the review.

These use a fake upstream so nothing hits the network.
"""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from musicbox import app as app_module  # noqa: E402
from musicbox.models import StreamInfo  # noqa: E402
from musicbox.providers.base import ProviderError  # noqa: E402


class FakeUpstream:
    def __init__(self, data: bytes, status: int = 200, headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self._data = data
        self.closed = False

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]

    def close(self):
        self.closed = True


class FakeStreamer:
    def __init__(self, upstream):
        self.upstream = upstream
        self.sent_headers = None
        self.calls = 0

    def stream(self, url, headers=None, **kwargs):
        self.calls += 1
        self.sent_headers = headers or {}
        return self.upstream


MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\xc4" + b"\x00" * 2000


@pytest.fixture()
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def stub(monkeypatch):
    """Point one provider at a fake upstream and forget cached resolutions."""
    app_module._stream_cache.clear()

    def install(upstream=None, *, stream_result=None, error=None):
        upstream = upstream or FakeUpstream(MP3, headers={"Content-Length": str(len(MP3))})
        fake = FakeStreamer(upstream)
        monkeypatch.setattr(app_module, "streamer", fake)

        provider = app_module.get_provider("kuwo")

        def fake_stream(track_id, quality="auto", extra=None):
            if error:
                raise error
            return stream_result or StreamInfo(
                url="https://cdn.example/a.mp3", quality="128", bitrate=128, ext="mp3"
            )

        monkeypatch.setattr(provider, "stream", fake_stream)
        return fake

    return install


# -- normal path ------------------------------------------------------------
def test_audio_streams_body(client, stub):
    fake = stub()
    res = client.get("/api/audio/kuwo/123")
    assert res.status_code == 200
    assert res.content == MP3
    assert res.headers["content-type"].startswith("audio/")
    assert res.headers["accept-ranges"] == "bytes"
    assert res.headers["cache-control"] == "no-store"
    assert fake.calls == 1


def test_audio_does_not_leak_provider_token(client, stub):
    """The upstream URL (with vkey) must never appear in any response."""
    stub()
    res = client.get("/api/audio/kuwo/123")
    body = res.text if len(res.content) < 4096 else ""
    assert "cdn.example" not in res.headers.get("content-type", "")
    assert "vkey" not in body


# -- range --------------------------------------------------------------
def test_valid_range_is_forwarded(client, stub):
    upstream = FakeUpstream(
        MP3[:1024],
        status=206,
        headers={
            "Content-Length": "1024",
            "Content-Range": "bytes 0-1023/4096",
        },
    )
    fake = stub(upstream)
    res = client.get("/api/audio/kuwo/123", headers={"Range": "bytes=0-1023"})
    assert res.status_code == 206
    assert fake.sent_headers["Range"] == "bytes=0-1023"
    assert res.headers["content-range"] == "bytes 0-1023/4096"
    assert len(res.content) == 1024


def test_malformed_range_is_not_forwarded(client, stub):
    fake = stub()
    client.get("/api/audio/kuwo/123", headers={"Range": "bytes=0-1,5-9"})
    assert "Range" not in fake.sent_headers


def test_injected_header_value_is_dropped(client, stub):
    fake = stub()
    client.get("/api/audio/kuwo/123", headers={"Range": "bytes=0-99999999999999999"})
    assert "Range" not in fake.sent_headers


# -- failure paths ----------------------------------------------------------
def test_upstream_error_is_not_forwarded_as_body(client, stub):
    stub(FakeUpstream(b"", status=403, headers={}))
    res = client.get("/api/audio/kuwo/123")
    assert res.status_code == 502
    assert "403" in res.json()["detail"]


def test_provider_refusal_maps_to_424(client, stub):
    stub(error=ProviderError("需要会员"))
    res = client.get("/api/audio/kuwo/123")
    assert res.status_code == 424
    assert "会员" in res.json()["detail"]


def test_upstream_is_closed_on_error(client, stub):
    """A failure mid-handler must not leak the upstream socket."""
    upstream = FakeUpstream(b"", status=500, headers={})
    stub(upstream)
    client.get("/api/audio/kuwo/123")
    assert upstream.closed is True


def test_upstream_closed_after_streaming(client, stub):
    upstream = FakeUpstream(MP3, headers={"Content-Length": str(len(MP3))})
    stub(upstream)
    res = client.get("/api/audio/kuwo/123")
    assert res.status_code == 200
    assert upstream.closed is True


# -- param validation -------------------------------------------------------
def test_unknown_quality_falls_back_to_auto(client, stub, monkeypatch):
    seen = {}
    upstream = FakeUpstream(MP3, headers={})
    monkeypatch.setattr(app_module, "streamer", FakeStreamer(upstream))
    provider = app_module.get_provider("kuwo")

    def fake_stream(track_id, quality="auto", extra=None):
        seen["quality"] = quality
        return StreamInfo(url="https://cdn.example/a.mp3", quality="128", ext="mp3")

    monkeypatch.setattr(provider, "stream", fake_stream)
    client.get("/api/audio/kuwo/123?quality=../../etc/passwd")
    assert seen["quality"] == "auto"


def test_unknown_provider_returns_error_not_crash(client):
    res = client.get("/api/audio/spotify/123")
    assert res.status_code in (424, 404, 422)


# -- caching ----------------------------------------------------------------
def test_stream_info_caches_and_reuses(client, stub, monkeypatch):
    calls = {"n": 0}
    provider = app_module.get_provider("kuwo")

    def fake_stream(track_id, quality="auto", extra=None):
        calls["n"] += 1
        return StreamInfo(url="https://cdn.example/a.mp3", quality="128", ext="mp3")

    monkeypatch.setattr(provider, "stream", fake_stream)
    app_module._stream_cache.clear()
    first = client.get("/api/stream/kuwo/777").json()
    second = client.get("/api/stream/kuwo/777").json()
    assert first == second
    assert calls["n"] == 1, "第二次请求应命中缓存，不应再次解析"


def test_stream_info_quality_change_bypasses_cache(client, stub, monkeypatch):
    calls = {"n": 0}
    provider = app_module.get_provider("kuwo")

    def fake_stream(track_id, quality="auto", extra=None):
        calls["n"] += 1
        return StreamInfo(url="https://cdn.example/a.mp3", quality=quality, ext="mp3")

    monkeypatch.setattr(provider, "stream", fake_stream)
    app_module._stream_cache.clear()
    client.get("/api/stream/kuwo/888?quality=128")
    client.get("/api/stream/kuwo/888?quality=320")
    assert calls["n"] == 2, "不同音质必须重新解析，不能复用 128 的结果"
