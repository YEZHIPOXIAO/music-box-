"""Unit tests that never touch the network."""

from __future__ import annotations

import os
import tempfile

import pytest


from musicbox.models import Track  # noqa: E402
from musicbox.providers.kugou import KugouProvider  # noqa: E402
from musicbox.providers.kuwo import KuwoProvider  # noqa: E402
from musicbox.providers.qq import QQProvider, parse_lrc  # noqa: E402
from musicbox.storage import Cipher, Store  # noqa: E402


# --------------------------------------------------------------------- lrc
def test_parse_lrc_basic():
    raw = "[ti:晴天]\n[ar:周杰伦]\n[00:12.50]故事的小黄花\n[01:05.00]从出生那年就飘着\n"
    lyrics = parse_lrc(raw)
    assert len(lyrics.lines) == 2
    assert lyrics.lines[0].time == pytest.approx(12.5)
    assert lyrics.lines[0].text == "故事的小黄花"
    assert lyrics.lines[1].time == pytest.approx(65.0)


def test_parse_lrc_multiple_stamps_per_line():
    raw = "[00:01.00][00:31.00]副歌\n"
    lyrics = parse_lrc(raw)
    assert [round(l.time, 2) for l in lyrics.lines] == [1.0, 31.0]


def test_parse_lrc_empty_is_instrumental_only_when_marked():
    assert parse_lrc("").lines == []
    assert parse_lrc("").instrumental is False
    assert parse_lrc("[ti:x]\n纯音乐").instrumental is True


# ------------------------------------------------------------- track model
def test_track_artist_text():
    track = Track(provider="qq", id="abc", title="晴天", artists=["周杰伦"])
    assert track.artist_text == "周杰伦"
    payload = track.to_dict()
    assert payload["artistText"] == "周杰伦"
    assert payload["provider"] == "qq"


def test_track_without_artists():
    assert Track(provider="kuwo", id="1", title="x").artist_text == "未知歌手"


# ---------------------------------------------------------------- quality
def test_qq_quality_chain():
    assert QQProvider._quality_chain("auto") == ["flac", "320", "128"]
    assert QQProvider._quality_chain("320") == ["320", "128"]
    assert QQProvider._quality_chain("128") == ["128"]
    assert QQProvider._quality_chain("flac") == ["flac", "320", "128"]


# -------------------------------------------------------------- parsing
def test_kugou_track_mapping():
    provider = KugouProvider()
    row = {
        "FileHash": "B3A52A7A958BF0AED0EBFBA2E9A818B7",
        "SongName": "晴天",
        "SingerName": "周杰伦",
        "AlbumName": "叶惠美",
        "Duration": 269,
        "Privilege": 10,
        "Image": "http://imge.kugou.com/stdmusic/{size}/2023/x.jpg",
        "AlbumID": 966846,
        "HQFileHash": "1B56126A8A03924F1DD066259C095CBC",
        "SQFileHash": "",
    }
    track = provider._to_track(row)
    assert track.id == "b3a52a7a958bf0aed0ebfba2e9a818b7"
    assert track.artists == ["周杰伦"]
    assert "{size}" not in track.cover and "240" in track.cover
    assert track.extra["paid"] is True
    assert track.extra["hash320"] == "1b56126a8a03924f1dd066259c095cbc"


def test_kugou_free_track_not_marked_paid():
    provider = KugouProvider()
    track = provider._to_track({"FileHash": "AB", "SongName": "x", "Privilege": 0})
    assert track.extra["paid"] is False


def test_kuwo_track_mapping_strips_entities():
    provider = KuwoProvider()
    track = provider._to_track(
        {
            "MUSICRID": "MUSIC_51685512",
            "SONGNAME": "晴天&nbsp;(Live)",
            "ARTIST": "周杰伦",
            "ALBUM": "叶惠美",
            "DURATION": "269",
            "FORMATS": "MP3128|FLAC",
            "web_albumpic_short": "120/s4s76/43/761738347.jpg",
        }
    )
    assert track.id == "51685512"
    assert track.title == "晴天 (Live)"
    assert track.extra["hasLossless"] is True
    assert track.cover.startswith("https://img1.kuwo.cn/")


def test_qq_track_mapping():
    provider = QQProvider()
    track = provider._to_track(
        {
            "mid": "0039MnYb0qxYhV",
            "name": "晴天",
            "singer": [{"name": "周杰伦"}],
            "album": {"mid": "000MkMni19ClKG", "name": "叶惠美"},
            "interval": 269,
            "size128": 4317292,
            "pay": {"payplay": 1},
        }
    )
    assert track.id == "0039MnYb0qxYhV"
    assert track.album == "叶惠美"
    assert "000MkMni19ClKG" in track.cover
    assert track.extra["sizes"]["128"] == 4317292


# --------------------------------------------------------------- storage
def test_cipher_roundtrip():
    cipher = Cipher()
    secret = "uin=12345; qm_keyst=abc%3D"
    token = cipher.encrypt(secret)
    assert token != secret
    assert cipher.decrypt(token) == secret


def test_cipher_rejects_tampering():
    cipher = Cipher()
    token = cipher.encrypt("hello")
    tampered = token[:-4] + "AAAA"
    with pytest.raises(Exception):
        cipher.decrypt(tampered)


def test_store_account_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        store.save_account("qq", "a=1; b=2", "tester")
        saved = store.load_account("qq")
        assert saved["cookie"] == "a=1; b=2"
        assert saved["nickname"] == "tester"
        # raw database must not contain the plaintext cookie
        raw = open(store.path, "rb").read()
        assert b"a=1; b=2" not in raw
        assert store.delete_account("qq") is True
        assert store.load_account("qq") is None


def test_store_playlists_and_dedup():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        pid = store.create_playlist("我的收藏")
        track = Track(provider="qq", id="mid1", title="晴天").to_dict()
        assert store.add_to_playlist(pid, track) is True
        assert store.add_to_playlist(pid, track) is False  # duplicate
        assert len(store.playlist_tracks(pid)) == 1
        assert store.playlists()[0]["count"] == 1
        assert store.remove_from_playlist(pid, "qq", "mid1") is True
        assert store.playlist_tracks(pid) == []
        assert store.delete_playlist(pid) is True


def test_store_history_cap_and_clear():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        for i in range(3):
            store.record_history(Track(provider="kuwo", id=str(i), title=f"s{i}").to_dict())
        assert len(store.history()) == 3
        assert store.clear_history() == 3
        assert store.history() == []


def test_store_wipe_removes_everything():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        store.save_account("kugou", "x=1")
        pid = store.create_playlist("p")
        store.add_to_playlist(pid, Track(provider="qq", id="a", title="t").to_dict())
        store.record_history(Track(provider="qq", id="a", title="t").to_dict())
        store.wipe()
        assert store.accounts() == []
        assert store.playlists() == []
        assert store.history() == []
