"""Placeholder / wrong-song detection tests."""

from __future__ import annotations

from musicbox.audio import KNOWN_PLACEHOLDERS, is_placeholder, probe_stream


class _FakeRaw:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def read(self, n=-1, decode_content=False):  # noqa: ARG002, ANN001
        chunk = self._data[self._pos : self._pos + n] if n >= 0 else self._data[self._pos :]
        self._pos += len(chunk)
        return chunk


class _FakeResponse:
    def __init__(self, data: bytes, headers: dict[str, str], status: int = 200) -> None:
        self.status_code = status
        self.headers = headers
        self.raw = _FakeRaw(data)

    def close(self) -> None:
        pass


class _FakeClient:
    def __init__(self, data: bytes, headers: dict[str, str], status: int = 200) -> None:
        self._resp = _FakeResponse(data, headers, status)

    def stream(self, url: str, headers=None):  # noqa: ARG002, ANN001
        return self._resp


def _mp3_128() -> bytes:
    return bytes([0xFF, 0xFB, 0x90, 0xC4]) + b"\x00" * 200


def test_placeholder_table_is_documented():
    assert (181521, 128) in KNOWN_PLACEHOLDERS
    assert is_placeholder(181521, 128) is True
    assert is_placeholder(4195720, 128) is False


def test_kuwo_placeholder_is_rejected_for_a_long_track():
    """The 181521-byte placeholder must not pass as a 226 s song."""
    client = _FakeClient(_mp3_128(), {"Content-Length": "181521"})
    probe = probe_stream(client, "http://x/a.mp3", expected_seconds=226)
    assert probe["placeholder"] is True
    assert probe["duration_matches"] is False


def test_kuwo_placeholder_is_rejected_for_a_short_track():
    """Regression: a 22 s track used to slip through the 12 s tolerance."""
    client = _FakeClient(_mp3_128(), {"Content-Length": "181521"})
    probe = probe_stream(client, "http://x/a.mp3", expected_seconds=22)
    assert probe["duration_matches"] is False


def test_real_file_matching_duration_is_accepted():
    # 4 MB at 128 kbps ≈ 262 s
    client = _FakeClient(_mp3_128(), {"Content-Length": "4195720"})
    probe = probe_stream(client, "http://x/a.mp3", expected_seconds=262)
    assert probe["placeholder"] is False
    assert probe["duration_matches"] is True
    assert probe["bitrate"] == 128


def test_mismatched_real_file_is_rejected():
    client = _FakeClient(_mp3_128(), {"Content-Length": "4195720"})  # ~262 s
    probe = probe_stream(client, "http://x/a.mp3", expected_seconds=120)
    assert probe["duration_matches"] is False


def test_dead_url_reports_error():
    client = _FakeClient(b"", {}, status=404)
    probe = probe_stream(client, "http://x/a.mp3", expected_seconds=200)
    assert probe["error"]
    assert probe["format"] == "unknown"


def test_no_expected_duration_still_detects_placeholder():
    client = _FakeClient(_mp3_128(), {"Content-Length": "181521"})
    probe = probe_stream(client, "http://x/a.mp3")
    assert probe["placeholder"] is True
