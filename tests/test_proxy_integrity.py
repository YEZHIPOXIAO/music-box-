"""Proxy correctness: length agreement, ticket integrity, cache bounds.

Each test here corresponds to a mutation that survived the previous suite.
"""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from musicbox import app as app_module  # noqa: E402
from musicbox.models import StreamInfo  # noqa: E402


class FakeUpstream:
    def __init__(self, data: bytes, status=200, headers=None):
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

    def stream(self, url, headers=None, **kwargs):
        return self.upstream


@pytest.fixture()
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture()
def setup(client, monkeypatch):
    def install(upstream):
        monkeypatch.setattr(app_module, "streamer", FakeStreamer(upstream))
        provider = app_module.get_provider("kuwo")
        monkeypatch.setattr(
            provider,
            "stream",
            lambda i, quality="auto", extra=None: StreamInfo(
                url="https://cdn.example/a.mp3", quality="128", bitrate=128, ext="mp3"
            ),
        )
        app_module._stream_cache.clear()
        return provider

    return install


# -- M6: Content-Length must describe the bytes we actually send ------------
def test_content_length_dropped_for_encoded_upstream(client, setup):
    """A gzip upstream's Content-Length is the compressed size.

    requests decodes the body, so forwarding that header tells the browser to
    wait for bytes that never arrive. The header must be omitted instead.
    """
    body = b"ID3" + b"\x00" * 5000  # ~5003 bytes after decoding
    upstream = FakeUpstream(body, headers={"Content-Length": "17", "Content-Encoding": "gzip"})
    setup(upstream)
    res = client.get("/api/audio/kuwo/1")
    assert res.status_code == 200
    declared = res.headers.get("content-length")
    assert declared is None, f"gzip 上游的压缩长度不应转发，实际转发 {declared}"
    assert len(res.content) == len(body)


def test_content_length_kept_for_identity_upstream(client, setup):
    """The guard must not drop a length that is actually correct."""
    body = b"ID3" + b"\x01" * 1000
    upstream = FakeUpstream(body, headers={"Content-Length": str(len(body))})
    setup(upstream)
    res = client.get("/api/audio/kuwo/2")
    assert res.headers.get("content-length") == str(len(body))
    assert len(res.content) == len(body)


def test_content_length_matches_body_when_no_header(client, setup):
    upstream = FakeUpstream(b"ID3" + b"\x02" * 500, headers={})
    setup(upstream)
    res = client.get("/api/audio/kuwo/3")
    assert len(res.content) == 503


# -- M7: ticket integrity is covered by test_registry_kugou_ticket.py -------
# (round-trip, tampering, binding, length, opacity and replay live there now;
#  the versions here used the old two-argument signature and were removed.)


# -- M8: the cache hard cap must be enforced -------------------------------
def test_stream_cache_hard_cap_is_enforced(client):
    app_module._stream_cache.clear()
    for i in range(2100):
        app_module._stream_cache[("qq", f"t{i}", "auto", i)] = (9e18, {"src": f"/x{i}"})
    assert len(app_module._stream_cache) > 2048, "前置条件：已塞入超上限条目"
    app_module._prune_cache()
    assert len(app_module._stream_cache) <= 2048, (
        f"缓存上限未生效，仍有 {len(app_module._stream_cache)} 条"
    )
    app_module._stream_cache.clear()


def test_prune_keeps_the_newest_entries(client):
    app_module._stream_cache.clear()
    for i in range(2100):
        app_module._stream_cache[("qq", f"t{i}", "auto", 0)] = (9e18 + i, {"src": f"/x{i}"})
    app_module._prune_cache()
    assert ("qq", "t2099", "auto", 0) in app_module._stream_cache
    assert ("qq", "t0", "auto", 0) not in app_module._stream_cache
    app_module._stream_cache.clear()
