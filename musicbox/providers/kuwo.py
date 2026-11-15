"""Kuwo Music provider.

Endpoints (verified against the live service):
  * search  https://search.kuwo.cn/r.s  (legacy, answers a Python-dict literal)
  * stream  https://antiserver.kuwo.cn/anti.s?type=convert_url&rid=...
  * lyrics  https://m.kuwo.cn/newh5/singles/songinfoandlrc?musicId=...
  * playlist https://nplserver.kuwo.cn/pl.svc?op=getlistinfo&pid=...

Kuwo's public endpoints are the most tolerant of anonymous access, which is why
it is the default fallback when QQ/Kugou refuse a track.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ..audio import probe_stream, quality_label
from ..models import Lyrics, StreamInfo, Track
from ..http import HttpError
from .base import Provider, ProviderError
from .qq import parse_lrc

SEARCH_URL = "https://search.kuwo.cn/r.s"
PLAY_URL = "https://antiserver.kuwo.cn/anti.s"
LYRIC_URL = "https://m.kuwo.cn/newh5/singles/songinfoandlrc"
PLAYLIST_URL = "https://nplserver.kuwo.cn/pl.svc"
COVER_TMPL = "https://img1.kuwo.cn/star/albumcover/{path}"

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
)

_RID = re.compile(r"(\d+)")


def _clean(text: str) -> str:
    return html.unescape((text or "").replace("&nbsp;", " ")).strip()


class KuwoProvider(Provider):
    id = "kuwo"
    name = "酷我音乐"
    color = "#ffcd2d"
    homepage = "https://www.kuwo.cn/"
    qualities = ("320", "128")
    login_methods = ("cookie",)

    def __init__(self) -> None:
        super().__init__()
        self.http.session.headers.update({"Referer": "https://www.kuwo.cn/"})

    def login_note(self) -> str:
        return (
            "在浏览器登录 kuwo.cn 后复制整段 Cookie 粘贴进来；"
            "酷我公开接口对匿名用户较宽松，多数歌曲不登录也能播放。"
        )

    def account_state(self) -> dict[str, Any]:
        state = super().account_state()
        cookies = self.http.cookies("www.kuwo.cn")
        state["userid"] = cookies.get("userid", "")
        state["hasToken"] = bool(cookies.get("kw_token"))
        # Presence of credentials is not proof of a paid subscription.
        state["readyForVip"] = False
        state["membershipVerified"] = False
        return state

    def check_membership(self) -> dict[str, Any]:
        import time
        state = super().check_membership()
        values = self.http.cookies("vip1.kuwo.cn")
        uid = values.get("userid") or values.get("uid")
        sid = values.get("websid") or values.get("sid") or values.get("web_sid")
        if not uid or not sid:
            return {**state, "message": "酷我会员检测需要userid和websid；请重新在官网绑定"}
        with self.http.post("https://vip1.kuwo.cn/vip/v2/user/vip", data={"op":"ui","uid":uid,"sid":sid}) as response:
            if response.status_code != 200:
                return {**state, "message": "酷我会员服务请求失败，状态未知"}
            from ..http import load_json_loose
            result = load_json_loose(response.text)
        if not isinstance(result, dict) or not isinstance(result.get("meta"), dict) or result["meta"].get("code") != 200:
            return {**state, "message": "酷我未确认登录身份，凭证可能已过期；会员状态未知"}
        body = result.get("data") or {}
        fields = [body[k] for k in ("vipLuxuryExpire","vipmExpire") if k in body]
        if len(fields) != 2:
            return {**state, "identityVerified": True, "message": "酷我已确认会话，但未返回完整音乐会员字段"}
        try:
            now = float(result.get("ctime") or time.time())
            expires = [float(v or 0) for v in fields]
            # Values are compared by the official JS against ctime; match its units.
            vip = any(v > now for v in expires)
        except (ValueError, TypeError):
            return {**state, "identityVerified": True, "message": "酷我会员时间字段无法解析"}
        return {**state, "identityVerified": True, "membershipVerified": True, "isVip": vip,
                "message": "酷我已确认音乐会员有效；单曲另购/地区限制仍以平台结果为准" if vip else "酷我本次查询的音乐会员产品未激活；免费听歌时长和其他活动权益未由此接口确认，仍可尝试播放，以平台逐曲授权为准"}

    # -- search ----------------------------------------------------------
    def search(self, keyword: str, page: int = 1, limit: int = 20) -> list[Track]:
        params = {
            "all": keyword,
            "ft": "music",
            "itemset": "web_2013",
            "client": "kt",
            "pn": max(page - 1, 0),
            "rn": limit,
            "rformat": "json",
            "encoding": "utf8",
            "vipver": 1,
        }
        data = self.http.get_json(SEARCH_URL, params=params) or {}
        rows = data.get("abslist") or []
        return [self._to_track(r) for r in rows if r.get("MUSICRID")]

    def _to_track(self, row: dict[str, Any]) -> Track:
        rid_match = _RID.search(str(row.get("MUSICRID") or ""))
        rid = rid_match.group(1) if rid_match else ""
        short = row.get("web_albumpic_short") or ""
        cover = COVER_TMPL.format(path=short.lstrip("/")) if short else ""
        return Track(
            provider=self.id,
            id=rid,
            title=_clean(row.get("SONGNAME") or ""),
            artists=[
                _clean(a)
                for a in _clean(row.get("ARTIST") or "").replace("&", "/").split("/")
                if _clean(a)
            ],
            album=_clean(row.get("ALBUM") or ""),
            duration=int(row.get("DURATION") or 0),
            cover=cover,
            extra={
                "formats": row.get("FORMATS") or "",
                "pay": row.get("PAY") or "0",
                "hasLossless": "FLAC" in (row.get("FORMATS") or ""),
                "duration": int(row.get("DURATION") or 0),
            },
        )

    # -- playback --------------------------------------------------------
    def stream(self, track_id: str, quality: str = "auto", extra: dict | None = None) -> StreamInfo:
        if not track_id.isdigit():
            raise ProviderError("酷我曲目 id 无效")
        rid = f"MUSIC_{track_id}"
        plan: list[tuple[str, str, int]] = []
        if quality in ("flac", "auto") and (extra or {}).get("hasLossless"):
            plan.append(("flac", "flac", 999))
        if quality in ("320", "auto"):
            plan.append(("mp3", "320", 320))
        plan.append(("mp3", "128", 128))

        expected = float((extra or {}).get("duration") or 0)
        wrong_song = False
        for fmt, label, bitrate in plan:
            params = {
                "type": "convert_url",
                "rid": rid,
                "format": fmt,
                "response": "url",
            }
            if fmt == "mp3" and label == "320":
                params["br"] = "320kmp3"
            try:
                if self.account_state()["loggedIn"]:
                    from ..web_auth import KUWO_SEED_COOKIE, kuwo_secret
                    from urllib.parse import unquote
                    import uuid
                    cookies = self.http.cookies("www.kuwo.cn")
                    seed = cookies.get(KUWO_SEED_COOKIE, "")
                    if not seed:
                        raise ProviderError("酷我网页登录凭证缺少官网播放校验字段，请重新打开官网并完成绑定（旧版曾过滤此字段）")
                    result = self.http.get_json("https://www.kuwo.cn/api/v1/www/music/playUrl",
                        params={"mid": track_id, "type": "music", "httpsStatus": 1,
                                "reqId": str(uuid.uuid4()), "plat": "web_www", "from": "",
                                "br": "320kmp3" if label == "320" else "128kmp3"},
                        headers={"Secret": kuwo_secret(seed), "Referer": self.homepage})
                    body = result.get("data") if isinstance(result, dict) else None
                    if not isinstance(body, dict) or not body.get("url") or result.get("code") not in (200, 2001):
                        raise ProviderError("酷我官网未授予该曲目播放地址；请检测账号会员，确认官网同一账号可播放整首")
                    if body.get("isFreePart") or body.get("is_free_part"):
                        raise ProviderError("酷我官网只返回试听片段，当前账号未获得该曲目整首权限")
                    text = body["url"]
                else:
                    text = self.http.get_text(PLAY_URL, params=params).strip()
            except HttpError:
                continue
            if not text.startswith("http"):
                continue

            probe = probe_stream(
                self.http,
                text,
                headers={"Referer": self.homepage},
                expected_seconds=expected,
            )
            if probe.get("error") or probe["format"] == "unknown":
                continue
            if not probe["duration_matches"]:
                # Kuwo answers unplayable rids with a *different* song's file.
                # Never hand that to the user as if it were their track.
                wrong_song = True
                continue

            ext = "m4a" if probe["format"] == "m4a" else probe["format"]
            real_quality = quality_label(probe["bitrate"], probe["format"])
            return StreamInfo(
                url=text,
                quality=real_quality if real_quality != "unknown" else label,
                bitrate=probe["bitrate"] or bitrate,
                ext=ext,
            )

        if wrong_song:
            raise ProviderError(
                "酷我返回的音频与这首曲目对不上（平台用别的歌顶替了不可播放的资源），已拒绝播放。"
            )
        raise ProviderError("酷我无法提供播放地址（可能版权受限或需要会员）。")

    # -- lyrics ----------------------------------------------------------
    def lyrics(self, track_id: str, extra: dict | None = None) -> Lyrics:
        data = self.http.get_json(
            LYRIC_URL,
            params={"musicId": track_id, "httpsStatus": 1},
            headers={"User-Agent": MOBILE_UA, "Referer": "https://m.kuwo.cn/"},
        )
        body = (data or {}).get("data") or {}
        rows = body.get("lrclist") or []
        if not rows:
            raise ProviderError("酷我没有该曲目的歌词")
        lines = [f"[00:00.00]"]
        for row in rows:
            try:
                t = float(row.get("time") or 0)
            except (TypeError, ValueError):
                continue
            minute = int(t // 60)
            second = t - minute * 60
            lines.append(f"[{minute:02d}:{second:05.2f}]{_clean(row.get('lineLyric') or '')}")
        return parse_lrc("\n".join(lines[1:]))

    # -- playlist --------------------------------------------------------
    def playlist(self, playlist_id: str) -> dict[str, Any]:
        if not playlist_id.isdigit():
            raise ProviderError("酷我歌单 id 无效")
        data = self.http.get_json(
            PLAYLIST_URL,
            params={
                "op": "getlistinfo",
                "pid": playlist_id,
                "pn": 0,
                "rn": 200,
                "encode": "utf8",
                "keyset": "pl2012",
                "identity": "kuwo",
                "pcmp4": 1,
                "vipver": "MUSIC_9.1.1.2_W1",
                "newver": 1,
            },
        ) or {}
        tracks = []
        for row in data.get("musiclist") or []:
            rid = str(row.get("id") or "")
            pic = row.get("albumpic") or ""
            tracks.append(
                Track(
                    provider=self.id,
                    id=rid,
                    title=_clean(row.get("name") or ""),
                    artists=[_clean(row.get("artist") or "")],
                    album=_clean(row.get("album") or ""),
                    duration=int(row.get("duration") or 0),
                    cover=pic,
                    extra={},
                )
            )
        return {
            "name": data.get("title") or f"酷我歌单 {playlist_id}",
            "cover": data.get("pic") or "",
            "tracks": tracks,
        }
