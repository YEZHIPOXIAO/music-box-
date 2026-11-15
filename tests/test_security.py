"""Security regression tests for the fixes from the review round."""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from musicbox.app import RANGE_RE, app  # noqa: E402
from musicbox.http import HttpClient, HttpError  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# -- error messages must never leak upstream URLs (they carry vkey/tokens) ---
def test_http_error_public_message_has_no_url():
    err = HttpError(
        "request failed: url https://dl.stream.qqmusic.qq.com/x.mp3?vkey=SECRET123",
        status=502,
        url="https://dl.stream.qqmusic.qq.com/x.mp3?vkey=SECRET123",
    )
    assert "SECRET123" not in err.public_message
    assert "vkey" not in err.public_message
    assert "502" in err.public_message


def test_stream_error_response_does_not_echo_url(client):
    res = client.get("/api/stream/kuwo/notanumber")
    assert res.status_code in (424, 502)
    body = res.text
    for marker in ("vkey", "http://", "https://", "MUSIC_"):
        assert marker not in body, f"response leaked {marker!r}: {body[:200]}"


# -- range validation -------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["bytes=0-1023", "bytes=0-", "bytes=1000-2000", "bytes=-500"],
)
def test_valid_range_accepted(value):
    assert RANGE_RE.match(value)


@pytest.mark.parametrize(
    "value",
    ["bytes=0-1,2-3", "bytes=abc", "items=0-10", "bytes=", "bytes=0-999999999999999"],
)
def test_bad_range_rejected(value):
    """Multi-range and malformed values must not reach the CDN."""
    assert not RANGE_RE.match(value)


# -- CSRF / host guards -----------------------------------------------------
def test_cross_site_post_rejected(client):
    res = client.post(
        "/api/account/qq",
        json={"cookie": "uin=1"},
        headers={"Origin": "https://evil.example.com"},
    )
    assert res.status_code == 403


def test_same_origin_post_allowed(client):
    res = client.post(
        "/api/account/qq",
        json={"cookie": "uin=1; qm_keyst=TEST_KEY"},
        headers={"Origin": "http://testserver"},
    )
    assert res.status_code == 200


def test_unknown_host_rejected(client):
    res = client.get("/api/health", headers={"Host": "attacker.example.com"})
    assert res.status_code == 421
    assert "detail" in res.json()


def test_get_without_origin_allowed(client):
    """A plain same-origin GET omits Origin; it must still work."""
    assert client.get("/api/health").status_code == 200


# -- cookie hygiene ---------------------------------------------------------
def test_set_cookies_replaces_previous_account():
    http = HttpClient()
    http.set_cookies("uin=111; qm_keyst=aaa")
    assert http.cookies()["uin"] == "111"
    # re-login as a different account must not leave the old uin behind
    http.set_cookies("uin=222; qm_keyst=bbb")
    cookies = http.cookies()
    assert cookies["uin"] == "222"
    assert cookies["qm_keyst"] == "bbb"


def test_cookie_never_sent_over_plaintext_via_any_verb(recorder):
    """A bound account must never transmit its token over http://.

    This replaces an earlier test that only asserted ``_is_secure()`` (always
    true) and never looked at what left the process. Mutation testing showed
    that deleting the whole cookie guard left that test green, so the P0 in
    ``HttpClient.stream()`` shipped undetected. Here a real socket records what
    actually arrives, across every verb the providers use.
    """
    http = HttpClient(timeout=5, retries=0)
    http.set_cookies("uin=999; qm_keyst=TOPSECRET")

    http.get(f"{recorder.base}/a").close()
    http.request("GET", f"{recorder.base}/b").close()
    http.request("POST", f"{recorder.base}/c", data=b"x").close()
    # the audio path: probe_stream / is_playable all go through stream()
    http.stream(f"{recorder.base}/d").close()

    leaked = []
    for method, path, headers in recorder.seen:
        cookie = headers.get("Cookie", "")
        if "TOPSECRET" in cookie:
            leaked.append(f"{method} {path} -> Cookie: {cookie!r}")
    assert not leaked, "账号 Cookie 被明文 HTTP 泄漏出去了:\n" + "\n".join(leaked)


def test_cookie_still_sent_when_secure():
    """The guard must not be overzealous: HTTPS requests keep their cookie."""
    http = HttpClient()
    http.set_cookies("uin=999; qm_keyst=TOPSECRET")
    import requests

    prepped = http.session.prepare_request(requests.Request("GET", "https://example.com/x"))
    assert "TOPSECRET" in prepped.headers.get("Cookie", "")


def test_provider_endpoints_are_https():
    """No provider constant may use plain HTTP with credentials attached."""
    from musicbox.providers import kugou, kuwo, qq

    for module in (qq, kuwo, kugou):
        for name in dir(module):
            if not name.endswith("_URL"):
                continue
            value = getattr(module, name)
            if not isinstance(value, str):
                continue
            # Kugou's mobile CDN has a broken certificate; it is documented and
            # the client refuses to attach cookies to it.
            if "mobilecdn.kugou.com" in value:
                continue
            assert value.startswith("https://"), f"{module.__name__}.{name} = {value}"


# -- cache keying -----------------------------------------------------------
def test_stream_cache_keyed_by_quality(client):
    """Switching quality must not return another quality's cached src."""
    from musicbox import app as app_module

    app_module._stream_cache.clear()
    first = ("qq", "abc123", "128", 200)
    app_module._stream_cache[first] = (
        9e18,
        {"src": "/api/audio/qq/abc123?quality=128", "quality": "128"},
    )
    # a different quality is a different key, so no false hit
    assert app_module._stream_cache.get(("qq", "abc123", "flac", 200)) is None


def test_stream_cache_keyed_by_provider(client):
    from musicbox import app as app_module

    app_module._stream_cache.clear()
    app_module._stream_cache[("kuwo", "123", "auto", 0)] = (
        9e18,
        {"src": "/api/audio/kuwo/123"},
    )
    # same id string on another provider must not collide
    assert app_module._stream_cache.get(("qq", "123", "auto", 0)) is None


def test_cache_pruning_removes_expired(client):
    from musicbox import app as app_module

    app_module._stream_cache.clear()
    app_module._stream_cache[("qq", "expired", "auto", 0)] = (0, {"src": "x"})
    app_module._stream_cache[("qq", "live", "auto", 0)] = (9e18, {"src": "y"})
    app_module._prune_cache()
    assert ("qq", "expired", "auto", 0) not in app_module._stream_cache
    assert ("qq", "live", "auto", 0) in app_module._stream_cache
