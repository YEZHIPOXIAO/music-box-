"""Audio sniffing tests — the code that stops us repeating provider lies."""

from __future__ import annotations

from musicbox.audio import inspect_audio, quality_label


def _mp3_frame(bitrate_index: int, sample_index: int = 0, version_bits: int = 3) -> bytes:
    """Build a minimal MPEG Layer III frame header."""
    b1 = 0xFF
    b2 = 0xE0 | (version_bits << 3) | (0b01 << 1) | 0b1
    b3 = (bitrate_index << 4) | (sample_index << 2)
    return bytes([b1, b2, b3, 0x00]) + b"\x00" * 64


def test_detect_flac():
    info = inspect_audio(b"fLaC" + b"\x00" * 100)
    assert info["format"] == "flac"


def test_detect_m4a():
    head = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 64
    assert inspect_audio(head)["format"] == "m4a"


def test_detect_128k_mp3():
    info = inspect_audio(_mp3_frame(bitrate_index=9))  # MPEG1 L3 index 9 = 128 kbps
    assert info["format"] == "mp3"
    assert info["bitrate"] == 128


def test_detect_320k_mp3():
    info = inspect_audio(_mp3_frame(bitrate_index=14))  # 320 kbps
    assert info["bitrate"] == 320
    assert quality_label(info["bitrate"], info["format"]) == "320"


def test_detect_mp3_behind_id3_tag():
    # 10-byte header + declared 32-byte body, then a 128 kbps frame
    id3 = b"ID3\x04\x00\x00\x00\x00\x00\x20" + b"\x00" * 32
    info = inspect_audio(id3 + _mp3_frame(bitrate_index=9))
    assert info["format"] == "mp3"
    assert info["bitrate"] == 128


def test_kuwo_claims_320_but_bytes_are_128():
    """Regression: Kuwo labels a 128 kbps file as 320. Bytes must win."""
    measured = inspect_audio(_mp3_frame(bitrate_index=9))
    assert quality_label(measured["bitrate"], measured["format"]) == "128"


def test_unknown_bytes_are_reported_as_unknown():
    assert inspect_audio(b"not audio at all")["format"] == "unknown"
    assert inspect_audio(b"")["format"] == "unknown"
    assert quality_label(0, "unknown") == "unknown"


def test_detect_adts_aac():
    head = bytes([0xFF, 0xF1, 0x50, 0x80]) + b"\x00" * 64
    assert inspect_audio(head)["format"] == "aac"
