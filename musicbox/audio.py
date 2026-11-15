"""Real audio inspection.

Providers label quality optimistically (Kuwo happily answers "320" for a
128 kbps file). Rather than repeat that claim, we read the first few kilobytes
of the actual stream and report what the bytes say.
"""

from __future__ import annotations

from typing import Any

# MPEG-1/2/2.5 Layer III bitrate tables (kbps)
_MPEG1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_MPEG2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0]
_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _skip_id3(data: bytes) -> int:
    """Return the offset just past an ID3v2 tag, or 0."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    size = (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
    return 10 + size


def _mp3_frame_info(data: bytes, start: int) -> tuple[int, int] | None:
    """Find the first valid MPEG audio frame; return (bitrate_kbps, sample_rate)."""
    limit = min(len(data) - 4, start + 8192)
    for i in range(start, max(limit, start)):
        b1, b2 = data[i], data[i + 1]
        if b1 != 0xFF or (b2 & 0xE0) != 0xE0:
            continue
        version_bits = (b2 >> 3) & 0x03  # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
        layer_bits = (b2 >> 1) & 0x03
        if version_bits == 1 or layer_bits != 1:  # reserved version / not layer III
            continue
        b3 = data[i + 2]
        bitrate_index = (b3 >> 4) & 0x0F
        rate_index = (b3 >> 2) & 0x03
        if bitrate_index in (0, 15) or rate_index == 3:
            continue
        table = _MPEG1_L3 if version_bits == 3 else _MPEG2_L3
        bitrate = table[bitrate_index]
        rates = _SAMPLE_RATES.get(version_bits)
        if not rates or not bitrate:
            continue
        return bitrate, rates[rate_index]
    return None


def inspect_audio(head: bytes) -> dict[str, Any]:
    """Classify a stream from its first bytes.

    Returns ``{"format": "mp3"|"flac"|"m4a"|"aac"|"unknown", "bitrate": kbps|0}``.
    """
    if not head:
        return {"format": "unknown", "bitrate": 0}

    if head[:4] == b"fLaC":
        return {"format": "flac", "bitrate": 0}

    if len(head) >= 12 and head[4:8] == b"ftyp":
        return {"format": "m4a", "bitrate": 0}

    start = _skip_id3(head)
    frame = _mp3_frame_info(head, start)
    if frame:
        bitrate, _rate = frame
        return {"format": "mp3", "bitrate": bitrate}

    # ADTS AAC: 0xFFF sync, layer bits 00
    for i in range(0, min(len(head) - 2, 4096)):
        if head[i] == 0xFF and (head[i + 1] & 0xF6) == 0xF0:
            return {"format": "aac", "bitrate": 0}

    return {"format": "unknown", "bitrate": 0}


def quality_label(bitrate: int, fmt: str) -> str:
    """Map a measured bitrate to the label we show to the user."""
    if fmt == "flac":
        return "flac"
    if bitrate >= 300:
        return "320"
    if bitrate >= 180:
        return "192"
    if bitrate >= 110:
        return "128"
    if bitrate > 0:
        return f"{bitrate}k"
    return "unknown"


def measure_stream(client: Any, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch just the head of a stream and classify it.

    Never raises: an unreachable stream is reported as ``unknown`` so callers
    can still hand the URL to the browser.
    """
    try:
        resp = client.stream(url, headers=headers)
        try:
            head = resp.raw.read(16384, decode_content=False) if resp.raw else b""
        finally:
            resp.close()
        return inspect_audio(head or b"")
    except Exception:  # noqa: BLE001 - classification is best effort
        return {"format": "unknown", "bitrate": 0}


def is_playable(client: Any, url: str, headers: dict[str, str] | None = None) -> bool:
    """Check that a media URL really answers with audio.

    QQ hands out a ``purl`` for tracks whose file then 404s; probing the first
    byte keeps us from sending the browser a dead link.
    """
    try:
        resp = client.stream(url, headers=headers)
        try:
            if resp.status_code >= 400:
                return False
            head = resp.raw.read(2048, decode_content=False) if resp.raw else b""
        finally:
            resp.close()
    except Exception:  # noqa: BLE001
        return False
    if not head:
        return False
    info = inspect_audio(head)
    return info["format"] != "unknown" or head[:1] == b"\x00"


# Kuwo answers unplayable rids with one fixed placeholder file. Recording its
# identity lets us reject it even for a track whose real duration is short.
KNOWN_PLACEHOLDERS: dict[tuple[int, int], str] = {
    # (byte size, bitrate kbps) -> why
    (181521, 128): "kuwo-placeholder",
}


def is_placeholder(total_bytes: int, bitrate: int) -> bool:
    return (total_bytes, bitrate) in KNOWN_PLACEHOLDERS


def probe_stream(
    client: Any,
    url: str,
    headers: dict[str, str] | None = None,
    expected_seconds: float = 0,
) -> dict[str, Any]:
    """Fetch the head of a stream and report what it really is.

    Kuwo serves a *different song* as a placeholder for tracks it will not
    stream (every unplayable rid answers with the same file). Comparing the
    measured duration against the one we asked for is the only reliable way to
    catch that, so we compute it from size + bitrate for CBR MP3.
    """
    result: dict[str, Any] = {
        "format": "unknown",
        "bitrate": 0,
        "seconds": 0.0,
        "duration_matches": True,
    }
    try:
        resp = client.stream(url, headers=headers)
        try:
            if resp.status_code >= 400:
                result["error"] = f"HTTP {resp.status_code}"
                return result
            head = resp.raw.read(32768, decode_content=False) if resp.raw else b""
            length = resp.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else 0
        finally:
            resp.close()
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:120]
        return result

    measured = inspect_audio(head or b"")
    result.update(measured)
    result["placeholder"] = False

    if total and measured["bitrate"]:
        id3 = _skip_id3(head or b"")
        audio_bytes = max(total - id3, 0)
        seconds = audio_bytes * 8 / (measured["bitrate"] * 1000)
        result["seconds"] = round(seconds, 1)
        if is_placeholder(total, measured["bitrate"]):
            result["placeholder"] = True
            result["duration_matches"] = False
        elif expected_seconds:
            # A 12 s floor is far too loose for short tracks: Kuwo's placeholder
            # file (~11 s of audio) slipped through a 22 s track that way.
            tolerance = max(8.0, expected_seconds * 0.15)
            result["duration_matches"] = abs(seconds - expected_seconds) <= tolerance
    return result
