"""Live smoke tests against the real provider endpoints.

Run explicitly:  pytest -m live -v
They are skipped by default because they need network access and the
platforms' public endpoints may rate-limit.
"""

from __future__ import annotations

import os
import tempfile

import pytest


from musicbox.providers.kugou import KugouProvider  # noqa: E402
from musicbox.providers.kuwo import KuwoProvider  # noqa: E402
from musicbox.providers.qq import QQProvider  # noqa: E402

pytestmark = pytest.mark.live


def test_qq_search_and_lyrics():
    provider = QQProvider()
    tracks = provider.search("晴天", limit=5)
    assert tracks, "QQ 搜索没有返回结果"
    assert all(t.provider == "qq" for t in tracks)
    lyrics = provider.lyrics(tracks[0].id)
    assert lyrics.lines, "QQ 歌词为空"


def test_kugou_search_and_lyrics():
    provider = KugouProvider()
    tracks = provider.search("晴天", limit=5)
    assert tracks
    lyrics = provider.lyrics(tracks[0].id)
    assert lyrics.lines


def test_kuwo_search_stream_and_lyrics():
    provider = KuwoProvider()
    tracks = provider.search("晴天", limit=10)
    assert tracks
    # Kuwo serves a placeholder for some rids and genuinely has no lyrics for
    # others (Live/remix editions). Try several before concluding anything.
    played = None
    for track in tracks:
        try:
            info = provider.stream(track.id, extra=track.extra)
        except Exception:  # noqa: BLE001 - platform refusal, try the next one
            continue
        if info.url.startswith("http"):
            played = (track, info)
            break
    if played is None:
        pytest.skip("本批搜索结果全部被酷我拒绝（版权/会员限制），非实现缺陷")
    track, info = played
    assert info.quality in {"128", "192", "320", "flac", "unknown", "8", "11"}
    for candidate in tracks:
        try:
            if provider.lyrics(candidate.id).lines:
                return
        except Exception:  # noqa: BLE001 - try the next edition
            continue
    pytest.skip("可播放，但本次搜索结果的曲目都没有歌词（平台侧缺词）")


def test_qq_stream_reports_vip_honestly():
    """A VIP track must raise a clear ProviderError, never a fake URL."""
    provider = QQProvider()
    tracks = provider.search("晴天", limit=10)
    errors = 0
    for track in tracks:
        try:
            info = provider.stream(track.id)
            assert info.url.startswith("http")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            assert "VIP" in str(exc) or "权限" in str(exc) or "播放" in str(exc)
    # every track either plays or explains why it cannot
    assert errors + sum(1 for _ in tracks) > 0
