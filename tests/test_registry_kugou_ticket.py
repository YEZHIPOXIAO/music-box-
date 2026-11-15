"""Tests for the three mutations that survived, plus ticket hardening.

Every test here was verified against a deliberately reverted implementation:
it passes on the fixed code and fails when the fix is removed.
"""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from musicbox import app as app_module
from musicbox.models import StreamInfo
from musicbox.providers.kugou import KugouProvider
from musicbox.storage import Cipher


# ---------------------------------------------------------------- M5: startup
def test_cipher_refuses_to_construct_on_corrupt_key(temp_home):
    """The startup path is Store() -> Cipher(), not _load_key().

    The earlier test asserted on _load_key(), so reverting Cipher's
    ``except ValueError: raise`` back to a silent downgrade still passed.
    """
    (temp_home / ".secret.key").write_bytes(b"short")
    with pytest.raises(ValueError):
        Cipher()


def test_cipher_does_not_silently_change_scheme(temp_home):
    (temp_home / ".secret.key").write_bytes(b"x" * 40)
    try:
        cipher = Cipher()
    except ValueError:
        return  # correct: refused
    pytest.fail(f"损坏密钥被静默降级为 scheme={cipher.scheme}，既有账号将无法解密")


# --------------------------------------------------------------- M10: registry
def test_registry_builds_once_under_concurrent_first_access(monkeypatch):
    """Without the lock, _build runs once per racing thread.

    The window has to be widened deliberately: real Provider() construction is
    fast enough that the GIL often serialises the threads anyway, which is why
    the previous version of this test was vacuous.
    """
    from musicbox.providers import Registry

    builds, lock = [], threading.Lock()
    original = Registry._build

    def slow_counting(self):
        time.sleep(0.01)
        built = original(self)
        with lock:
            builds.append(tuple(sorted(built)))
        return built

    monkeypatch.setattr(Registry, "_build", slow_counting)

    reg = Registry()  # a fresh instance, so no earlier lifespan can pre-warm it
    assert not reg._providers, "precondition: a new Registry starts empty"

    barrier = threading.Barrier(8)
    results = []

    def grab():
        barrier.wait()
        results.append(reg.providers())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(builds) == 1, f"_build 执行了 {len(builds)} 次 —— 锁没起作用"
    assert len({id(r) for r in results}) == 1, "不同线程拿到了不同 dict"


def test_login_bound_on_first_access_survives():
    """The real harm of an orphaned build: a bound cookie lands on a lost instance."""
    from musicbox.providers import Registry

    reg = Registry()
    assert not reg._providers
    barrier = threading.Barrier(8)

    def grab():
        provider = reg.get("qq")
        barrier.wait()
        provider.apply_cookie("uin=4242; qm_keyst=abc")

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert reg.get("qq").http.cookies().get("uin") == "4242", (
        "账号绑定在并发首次访问时丢失：cookie 挂到了被丢弃的实例上"
    )


# ------------------------------------------------------------------ M11: kugou
def test_kugou_bound_account_token_is_sent_over_https():
    """A literal Cookie header would shadow the jar and the token never leaves."""
    import requests

    provider = KugouProvider()
    provider.apply_cookie("token=KG_SECRET_TOKEN; userid=777; KugooID=777")
    prepared = provider.http.session.prepare_request(
        requests.Request("GET", "https://songsearch.kugou.com/song_search_v2")
    )
    cookie = prepared.headers.get("Cookie", "")
    assert "KG_SECRET_TOKEN" in cookie, f"账号 token 没有发出去: {cookie!r}"


def test_kugou_device_ids_live_in_the_cookie_jar():
    provider = KugouProvider()
    assert not provider.http.session.headers.get("Cookie"), (
        "字面量 Cookie header 会覆盖 jar，导致绑定账号后 token 永远发不出去"
    )
    jar = provider.http.session.cookies.get_dict()
    for name in ("kg_mid", "kg_dfid"):
        assert name in jar, f"设备 id {name} 必须在 jar 里，实际={jar}"


def test_kugou_login_keeps_device_ids():
    """Logging in used to clear the whole jar, dropping the device fingerprint."""
    provider = KugouProvider()
    before = provider.http.session.cookies.get_dict()
    provider.apply_cookie("token=T; userid=1")
    after = provider.http.session.cookies.get_dict()
    missing = [k for k in ("kg_mid", "kg_dfid") if k not in after]
    assert not missing, f"登录后设备 id 被清掉: {missing}；前={before} 后={after}"
    assert after.get("token") == "T", "登录 token 本身必须生效"


def test_kugou_logout_keeps_device_ids():
    provider = KugouProvider()
    provider.apply_cookie("token=T; userid=1")
    provider.clear_account()
    jar = provider.http.session.cookies.get_dict()
    assert "kg_mid" in jar, "解绑不应丢掉设备指纹"


# ------------------------------------------------------------------ tickets
def _stream_stub(monkeypatch):
    """Capture the extra a provider receives, without any network."""
    seen = {}

    def fake_stream(tid, quality="auto", extra=None):
        seen.clear()
        seen.update(extra or {})
        return StreamInfo(url="https://c/x.mp3", quality="128", ext="mp3")

    provider = app_module.get_provider("kuwo")
    monkeypatch.setattr(provider, "stream", fake_stream)
    return seen


class _OkUpstream:
    status_code = 200
    headers = {"Content-Length": "3"}

    def iter_content(self, chunk_size=65536):
        yield b"ID3"

    def close(self):
        pass


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(app_module, "streamer", type("S", (), {"stream": lambda *a, **k: _OkUpstream()})())
    app_module._stream_cache.clear()
    with TestClient(app_module.app) as c:
        yield c


def test_ticket_is_bound_to_track_id(client, monkeypatch):
    """A ticket issued for song A must not unlock song B's extras."""
    seen = _stream_stub(monkeypatch)
    ticket = app_module._encode_ticket("kuwo", "TRACK_A", {"hash320": "TRACK_A_HASH"})
    client.get(f"/api/audio/kuwo/TRACK_B?ticket={ticket}")
    assert seen.get("hash320") != "TRACK_A_HASH", "ticket 未绑定 track_id：可跨曲目重放"


def test_ticket_is_bound_to_provider(client, monkeypatch):
    seen = _stream_stub(monkeypatch)
    ticket = app_module._encode_ticket("kugou", "T1", {"hash320": "KG_HASH"})
    client.get(f"/api/audio/kuwo/T1?ticket={ticket}")
    assert seen.get("hash320") != "KG_HASH", "ticket 未绑定 provider：可跨平台重放"


def test_ticket_length_is_bounded():
    ticket = app_module._encode_ticket(
        "kugou",
        "0a1b2c3d4e5f",
        {"hash320": "a" * 32, "hashFlac": "b" * 32, "albumId": "966846",
         "duration": 9999, "hasLossless": True},
    )
    assert len(ticket) < 512, f"ticket 过长会撑爆 src URL: {len(ticket)}"


def test_ticket_does_not_expose_raw_hash():
    """The docstring says opaque; base64 alone would be readable."""
    ticket = app_module._encode_ticket("kugou", "T1", {"hash320": "HASHABC123"})
    try:
        blob = base64.urlsafe_b64decode(ticket + "=" * (-len(ticket) % 4))
    except Exception:  # noqa: BLE001
        return
    assert b"HASHABC123" not in blob, "ticket 里的 hash 可被直接读出，并非不透明"


def test_tampered_ticket_is_rejected():
    """Flip a byte inside the MAC, not the ciphertext.

    The ticket layout is nonce(8) + tag(10) + body, so mutating the last byte
    only corrupts ciphertext — which the tag check would catch anyway, but a
    mutation that removes the check would still reject it via bad UTF-8. To
    prove the *tag comparison* is load-bearing, the tamper has to hit the tag.
    """
    ticket = app_module._encode_ticket("kuwo", "T1", {"hash320": "abc123"})
    blob = bytearray(base64.urlsafe_b64decode(ticket + "=" * (-len(ticket) % 4)))
    blob[8] ^= 0xFF  # first byte of the 10-byte tag
    forged = base64.urlsafe_b64encode(bytes(blob)).decode().rstrip("=")
    assert app_module._decode_ticket(forged, "kuwo", "T1") == {}


def test_ticket_with_wrong_signature_is_rejected():
    """A well-formed ticket whose tag was replaced must not decode."""
    ticket = app_module._encode_ticket("kuwo", "T1", {"hash320": "abc123"})
    blob = bytearray(base64.urlsafe_b64decode(ticket + "=" * (-len(ticket) % 4)))
    blob[8:18] = b"\x00" * 10  # zero the whole tag
    forged = base64.urlsafe_b64encode(bytes(blob)).decode().rstrip("=")
    assert app_module._decode_ticket(forged, "kuwo", "T1") == {}


def test_ticket_body_from_another_ticket_is_rejected():
    """Splice A's ciphertext onto B's nonce/tag: the MAC must not verify."""
    a = app_module._encode_ticket("kuwo", "T1", {"hash320": "AAAA"})
    b = app_module._encode_ticket("kuwo", "T1", {"hash320": "BBBB"})
    raw_a = base64.urlsafe_b64decode(a + "=" * (-len(a) % 4))
    raw_b = base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))
    spliced = raw_b[:18] + raw_a[18:]
    forged = base64.urlsafe_b64encode(spliced).decode().rstrip("=")
    decoded = app_module._decode_ticket(forged, "kuwo", "T1")
    assert decoded.get("hash320") != "AAAA", "拼接的 ticket 不应通过验签"


def test_garbage_ticket_is_rejected():
    for bad in ("", "nodot", "a.b", "!!!.???", ".", "x" * 500, "AAAA"):
        assert app_module._decode_ticket(bad, "kuwo", "T1") == {}


def test_ticket_only_carries_known_fields():
    ticket = app_module._encode_ticket("qq", "T1", {"hash320": "a", "evil": "rm -rf", "duration": 1})
    decoded = app_module._decode_ticket(ticket, "qq", "T1")
    assert "evil" not in decoded
    assert decoded["hash320"] == "a"


def test_ticket_from_previous_launch_is_rejected(monkeypatch):
    """Tickets are short-lived handles: a restart invalidates them explicitly."""
    ticket = app_module._encode_ticket("kuwo", "T1", {"hash320": "h"})
    monkeypatch.setattr(app_module, "LAUNCH_TOKEN", "a-different-token")
    assert app_module._decode_ticket(ticket, "kuwo", "T1") == {}, "旧启动签发的 ticket 应被拒绝"


def test_ticket_duration_wins_over_query(client, monkeypatch):
    """Documented precedence: the sealed ticket is authoritative."""
    seen = _stream_stub(monkeypatch)
    ticket = app_module._encode_ticket("kuwo", "1", {"duration": 200})
    client.get(f"/api/audio/kuwo/1?duration=999&ticket={ticket}")
    assert seen["duration"] == 200
