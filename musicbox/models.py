"""Unified track model shared by every provider."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ProviderId = Literal["qq", "kugou", "kuwo"]

QUALITY_ORDER = ("flac", "320", "128")


@dataclass
class Track:
    """A provider-neutral song reference.

    ``id`` is whatever the provider needs to resolve the track again
    (songmid for QQ, file hash for Kugou, numeric rid for Kuwo).
    """

    provider: str
    id: str
    title: str
    artists: list[str] = field(default_factory=list)
    album: str = ""
    duration: int = 0  # seconds
    cover: str = ""
    # provider extras needed for playback (extra hashes, album ids, ...)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def artist_text(self) -> str:
        return " / ".join(self.artists) if self.artists else "未知歌手"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["artistText"] = self.artist_text
        return data


@dataclass
class StreamInfo:
    url: str
    quality: str
    bitrate: int = 0
    ext: str = "mp3"
    size: int = 0
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LyricLine:
    time: float
    text: str


@dataclass
class Lyrics:
    raw: str = ""
    lines: list[LyricLine] = field(default_factory=list)
    translated: list[LyricLine] = field(default_factory=list)
    instrumental: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "lines": [{"time": l.time, "text": l.text} for l in self.lines],
            "translated": [{"time": l.time, "text": l.text} for l in self.translated],
            "instrumental": self.instrumental,
        }
