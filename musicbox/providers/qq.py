"""QQ Music provider.

Endpoints (verified against the live service):
  * search   https://c.y.qq.com/soso/fcgi-bin/client_search_cp
  * vkey     https://u.y.qq.com/cgi-bin/musicu.fcg  (vkey.GetVkeyServer)
  * lyrics   https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg
  * playlist https://u.y.qq.com/cgi-bin/musicu.fcg  (uniform_get_Dissinfo)

QQ only hands out a playable ``purl`` for tracks the account is entitled to.
Without a cookie the anonymous account can still play a large part of the
catalogue; VIP-only tracks answer with an empty purl and we report that
honestly instead of faking a stream.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..audio import probe_stream, quality_label
from ..models import Lyrics, StreamInfo, Track
from .base import Provider, ProviderError

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
VKEY_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg"

# filename prefixes understood by CgiGetVkey
QUALITY_FILES = {
    "flac": ("F000{mid}.flac", "flac", 999),
    "320": ("M800{mid}.mp3", "mp3", 320),
    "128": ("M500{mid}.mp3", "mp3", 128),
    "aac": ("C400{mid}.m4a", "m4a", 96),
}
QUALITY_RANK = {"flac": 4, "320": 3, "128": 2, "aac": 1}


class QQProvider(Provider):
    id = "qq"
    name = "QQ音乐"
    color = "#31c27c"
    homepage = "https://y.qq.com/"
    qualities = ("flac", "320", "128")
    login_methods = ("cookie",)

    def __init__(self) -> None:
        super().__init__()
        self.http.session.headers.update({"Referer": "https://y.qq.com/"})

    def login_note(self) -> str:
        return (
            "在浏览器登录 y.qq.com 后复制整段 Cookie 粘贴进来；"
            "VIP 歌曲需要带 VIP 账号的 Cookie 才能取到播放地址。"
        )

    # -- account ---------------------------------------------------------
    def _account(self) -> dict[str, str]:
        """Read the uin / music key out of the bound cookie.

        QQ decides whether to hand out a VIP purl from the ``uin`` sent in the
        vkey request, so the cookie has to be translated into those two values
        rather than merely being present on the session.
        """
        cookies = self.http.cookies("u.y.qq.com")
        uin = ""
        for key in ("qqmusic_uin", "uin", "wxuin"):
            raw = cookies.get(key, "")
            if not raw:
                continue
            digits = "".join(ch for ch in raw if ch.isdigit())
            if digits:
                uin = str(int(digits))  # strip the "o" prefix and leading zeros
                break
        music_key = ""
        for key in ("qqmusic_key", "qm_keyst"):
            value = cookies.get(key, "")
            if value:
                music_key = value
                break
        return {"uin": uin, "key": music_key}

    def account_state(self) -> dict[str, Any]:
        state = super().account_state()
        account = self._account()
        state["uin"] = account["uin"]
        state["hasMusicKey"] = bool(account["key"])
        # A cookie with a uin but no music key cannot unlock VIP tracks.
        state["readyForVip"] = bool(account["uin"] and account["key"])
        return state

    # -- search ----------------------------------------------------------
    def search(self, keyword: str, page: int = 1, limit: int = 20) -> list[Track]:
        params = {
            "p": page,
            "n": limit,
            "w": keyword,
            "format": "json",
            "cr": 1,
            "new_json": 1,
            "t": 0,
        }
        data = self.http.get_json(SEARCH_URL, params=params)
        songs = (((data or {}).get("data") or {}).get("song") or {}).get("list") or []
        return [self._to_track(song) for song in songs]

    def _to_track(self, song: dict[str, Any]) -> Track:
        mid = song.get("mid") or song.get("songmid") or ""
        singers = song.get("singer") or []
        names = [s.get("name", "") for s in singers if s.get("name")]
        album = song.get("album") or {}
        album_mid = album.get("mid") or song.get("albummid") or ""
        interval = song.get("interval") or 0
        pay = song.get("pay") or {}
        return Track(
            provider=self.id,
            id=mid,
            title=html.unescape(song.get("name") or song.get("songname") or ""),
            artists=names,
            album=html.unescape(album.get("name") or song.get("albumname") or ""),
            duration=int(interval),
            cover=COVER_URL.format(mid=album_mid) if album_mid else "",
            extra={
                "albumMid": album_mid,
                "sizes": {
                    "128": song.get("size128") or 0,
                    "320": song.get("size320") or 0,
                    "flac": song.get("sizeflac") or 0,
                },
                "payPlay": pay.get("payplay", 0),
            },
        )

    # -- playback --------------------------------------------------------
    def _vkey(self, mid: str, quality: str) -> tuple[str, str, int]:
        filename, ext, bitrate = QUALITY_FILES[quality]
        account = self._account()
        param: dict[str, Any] = {
            "guid": "10000",
            "songmid": [mid],
            "songtype": [0],
            "filename": [filename.format(mid=mid)],
            "uin": account["uin"] or "0",
            "loginflag": 1,
            "platform": "20",
        }
        if account["key"]:
            param["authst"] = account["key"]
        payload = {
            "req_0": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": param,
            },
            "comm": {
                "uin": int(account["uin"]) if account["uin"] else 0,
                "format": "json",
                "ct": 24,
                "cv": 0,
            },
        }
        data = self.http.post_json(VKEY_URL, payload)
        req = (data or {}).get("req_0") or {}
        if req.get("code") not in (0, None):
            raise ProviderError(f"QQ vkey 接口返回 code={req.get('code')}")
        body = req.get("data") or {}
        infos = body.get("midurlinfo") or []
        if not infos:
            raise ProviderError("QQ 未返回播放信息")
        purl = infos[0].get("purl") or ""
        if not purl:
            return "", ext, bitrate
        sip = (body.get("sip") or ["https://dl.stream.qqmusic.qq.com/"])[0]
        return sip.rstrip("/") + "/" + purl.lstrip("/"), ext, bitrate

    def stream(self, track_id: str, quality: str = "auto", extra: dict | None = None) -> StreamInfo:
        if not track_id:
            raise ProviderError("缺少 QQ songmid")
        wanted = self._quality_chain(quality)
        errors: list[str] = []
        dead: list[str] = []
        for q in wanted:
            try:
                url, ext, bitrate = self._vkey(track_id, q)
            except ProviderError as exc:
                errors.append(f"{q}: {exc}")
                continue
            if not url:
                continue
            # A returned purl is not proof of a file: QQ answers 404 for
            # tracks it no longer serves. Verify before handing it over.
            probe = probe_stream(
                self.http,
                url,
                headers={"Referer": self.homepage},
                expected_seconds=float((extra or {}).get("duration") or 0),
            )
            if probe.get("error") or probe["format"] == "unknown":
                dead.append(q)
                continue
            if not probe["duration_matches"]:
                dead.append(q)
                continue
            measured = probe["bitrate"]
            ext = "m4a" if probe["format"] == "m4a" else probe["format"]
            return StreamInfo(
                url=url,
                quality=quality_label(measured, probe["format"]) if measured else q,
                bitrate=measured or bitrate,
                ext=ext,
            )
        if dead and not errors:
            raise ProviderError(
                f"QQ音乐返回的地址已失效（{', '.join(dead)}），通常表示该曲目当前不可播放。"
            )
        detail = "; ".join(errors) if errors else "该曲目需要 VIP 或版权受限"
        raise ProviderError(
            f"QQ音乐无法提供播放地址（{detail}）。请确认账号已登录且拥有该曲目权限。"
        )

    @staticmethod
    def _quality_chain(quality: str) -> list[str]:
        if quality in QUALITY_FILES:
            return [quality] + [q for q in ("320", "128") if QUALITY_RANK[q] < QUALITY_RANK[quality]]
        return ["flac", "320", "128"]

    # -- lyrics ----------------------------------------------------------
    def lyrics(self, track_id: str, extra: dict | None = None) -> Lyrics:
        params = {
            "songmid": track_id,
            "format": "json",
            "nobase64": 1,
            "g_tk": 5381,
            "loginUin": 0,
            "hostUin": 0,
            "platform": "yqq",
            "needNewCode": 0,
        }
        data = self.http.get_json(LYRIC_URL, params=params)
        text = (data or {}).get("lyric") or ""
        translated = (data or {}).get("trans") or ""
        return parse_lrc(html.unescape(text), html.unescape(translated))

    # -- playlist --------------------------------------------------------
    def playlist(self, playlist_id: str) -> dict[str, Any]:
        payload = {
            "req_0": {
                "module": "music.srfDissInfo.aiDissInfo",
                "method": "uniform_get_Dissinfo",
                "param": {
                    "disstid": int(playlist_id),
                    "enc_host_uin": "",
                    "tag": 1,
                    "userinfo": 1,
                    "song_begin": 0,
                    "song_num": 200,
                },
            },
            "comm": {"uin": 0, "format": "json", "ct": 24, "cv": 0},
        }
        data = self.http.post_json(VKEY_URL, payload)
        body = ((data or {}).get("req_0") or {}).get("data") or {}
        songs = body.get("songlist") or []
        info = body.get("dirinfo") or {}
        return {
            "name": info.get("title") or f"QQ 歌单 {playlist_id}",
            "cover": info.get("picurl") or "",
            "tracks": [self._to_track(s) for s in songs],
        }


TIMESTAMP = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def parse_lrc(raw: str, translated: str = "") -> Lyrics:
    """Parse LRC text into timed lines (shared by all providers)."""
    lines = _parse_lrc_lines(raw)
    trans = _parse_lrc_lines(translated)
    return Lyrics(
        raw=raw,
        lines=lines,
        translated=trans,
        instrumental=not lines and "纯音乐" in raw,
    )


def _parse_lrc_lines(raw: str) -> list:
    from ..models import LyricLine

    out: list[LyricLine] = []
    if not raw:
        return out
    for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stamps = TIMESTAMP.findall(line)
        text = TIMESTAMP.sub("", line).strip()
        if not stamps:
            continue
        for minute, second, frac in stamps:
            fraction = float(f"0.{frac}") if frac else 0.0
            out.append(
                LyricLine(time=int(minute) * 60 + int(second) + fraction, text=text)
            )
    out.sort(key=lambda l: l.time)
    return out
