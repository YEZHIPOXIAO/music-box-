"""Kugou Music provider.

Endpoints (verified against the live service):
  * search  https://songsearch.kugou.com/song_search_v2
  * stream  https://m.kugou.com/app/i/getSongInfo.php?cmd=playInfo&hash=...
  * lyrics  https://krcs.kugou.com/search  ->  https://lyrics.kugou.com/download
  * album   http://mobilecdn.kugou.com/api/v3/album/info

Kugou marks paid tracks with ``Privilege`` / ``PayType``; those answer without a
``url`` and are reported as unavailable rather than silently substituted.
"""

from __future__ import annotations

import base64
import html
import re
import time
import uuid
from typing import Any

from ..audio import probe_stream, quality_label
from ..models import Lyrics, StreamInfo, Track
from ..http import HttpError
from .base import Provider, ProviderError
from .qq import parse_lrc

SEARCH_URL = "https://songsearch.kugou.com/song_search_v2"
MOBILE_SEARCH_URL = "http://mobilecdn.kugou.com/api/v3/search/song"
PLAY_URL = "https://m.kugou.com/app/i/getSongInfo.php"
KRCS_URL = "https://krcs.kugou.com/search"
LYRIC_URL = "https://lyrics.kugou.com/download"
COVER_TMPL = "https://imge.kugou.com/stdmusic/240/{path}"

# Kugou privilege levels: 0 = free, 8/10 = paid/VIP
PAID_PRIVILEGE = {8, 10, 16}


def _strip_tags(text: str) -> str:
    """Kugou marks matched keywords as ``<0>keyword</0>``; strip the markup."""
    if not text:
        return ""
    cleaned = re.sub(r"</?\d+>", "", text)
    return html.unescape(cleaned).strip()


class KugouProvider(Provider):
    id = "kugou"
    name = "酷狗音乐"
    color = "#2ca2f9"
    homepage = "https://www.kugou.com/"
    qualities = ("flac", "320", "128")
    login_methods = ("cookie",)

    def __init__(self) -> None:
        super().__init__()
        self._mid = uuid.uuid4().hex
        self._dfid = uuid.uuid4().hex[:20]
        self.http.session.headers.update({"Referer": "https://www.kugou.com/"})
        # Device ids go in the cookie *jar*, never as a literal Cookie header.
        # A literal header wins over the jar in requests, so a user's bound
        # login token would never be sent and VIP tracks would silently fail.
        self._device_cookies = {
            "kg_mid": self._mid,
            "kg_dfid": self._dfid,
            "kg_dfid_collect": "1",
        }
        # Pinned so a user's login (which clears the jar) does not drop the
        # device fingerprint Kugou expects on every request.
        self.http.pin_cookies(self._device_cookies)

    def login_note(self) -> str:
        return (
            "在浏览器登录 kugou.com 后复制整段 Cookie 粘贴进来；"
            "付费/会员歌曲需要带会员 Cookie 才能取到播放地址。"
        )

    # -- search ----------------------------------------------------------
    def search(self, keyword: str, page: int = 1, limit: int = 20) -> list[Track]:
        # NOTE: the web endpoint rejects the query (0 rows) unless the full
        # client parameter set is present — a bare ``mid`` alone is not enough.
        params = {
            "keyword": keyword,
            "page": page,
            "pagesize": limit,
            "platform": "WebFilter",
            "format": "json",
            "tag": 0,
            "filter": 2,
            "iscorrection": 1,
            "privilege_filter": 0,
            "srcappid": 2919,
            "clientver": 20000,
            "clienttime": int(time.time() * 1000),
            "mid": self._mid,
            "uuid": self._mid,
            "dfid": self._dfid,
        }
        rows: list[dict[str, Any]] = []
        try:
            data = self.http.get_json(SEARCH_URL, params=params)
            rows = ((data or {}).get("data") or {}).get("lists") or []
        except (HttpError, ValueError):  # noqa: BLE001 - fall through to mobile API
            rows = []
        if not rows:
            rows = self._search_mobile(keyword, page, limit)
        return [self._to_track(r) for r in rows if r.get("FileHash")]

    def _search_mobile(self, keyword: str, page: int, limit: int) -> list[dict[str, Any]]:
        """Fallback used when the web search endpoint answers with 0 rows."""
        data = self.http.get_json(
            MOBILE_SEARCH_URL,
            params={
                "format": "json",
                "keyword": keyword,
                "page": page,
                "pagesize": limit,
                "showtype": 1,
            },
        )
        items = ((data or {}).get("data") or {}).get("info") or []
        # normalise mobile keys onto the web-search shape
        return [
            {
                "FileHash": (i.get("hash") or "").upper(),
                "SongName": i.get("songname"),
                "SingerName": i.get("singername"),
                "AlbumName": i.get("album_name"),
                "AlbumID": i.get("album_id"),
                "Duration": i.get("duration"),
                "FileSize": i.get("filesize"),
                "HQFileHash": (i.get("320hash") or "").upper(),
                "SQFileHash": (i.get("sqhash") or "").upper(),
                "Privilege": i.get("privilege"),
                "PayType": i.get("pay_type"),
                "Image": "",
            }
            for i in items
        ]

    def _to_track(self, row: dict[str, Any]) -> Track:
        image = row.get("Image") or ""
        cover = image.replace("{size}", "240") if "{size}" in image else image
        privilege = row.get("Privilege")
        return Track(
            provider=self.id,
            id=(row.get("FileHash") or "").lower(),
            title=_strip_tags(row.get("SongName") or ""),
            artists=[
                part.strip()
                for part in _strip_tags(row.get("SingerName") or "").split("、")
                if part.strip()
            ],
            album=_strip_tags(row.get("AlbumName") or ""),
            duration=int(row.get("Duration") or 0),
            cover=cover,
            extra={
                "albumId": row.get("AlbumID") or "",
                "hash320": (row.get("HQFileHash") or "").lower(),
                "hashFlac": (row.get("SQFileHash") or "").lower(),
                "privilege": privilege,
                "payType": row.get("PayType"),
                "sizes": {
                    "128": row.get("FileSize") or 0,
                    "320": row.get("HQFileSize") or 0,
                    "flac": row.get("SQFileSize") or 0,
                },
                "paid": privilege in PAID_PRIVILEGE,
                "duration": int(row.get("Duration") or 0),
            },
        )

    # -- playback --------------------------------------------------------
    def _play_info(self, song_hash: str, album_id: str = "") -> dict[str, Any]:
        if self._has_account():
            from ..web_auth import kugou_params
            values = self.http.cookies("wwwapi.kugou.com")
            fields = {"hash": song_hash}
            if album_id: fields["album_id"] = str(album_id)
            result = self.http.get_json("https://wwwapi.kugou.com/play/songinfo", params=kugou_params(values, fields))
            if not isinstance(result, dict): raise ProviderError("酷狗网页登录播放接口返回格式异常")
            body = result.get("data") or {}
            if not isinstance(body, dict): raise ProviderError("酷狗网页登录播放响应不完整")
            if result.get("status") not in (None, 1) or not body.get("play_url"):
                raise ProviderError(f"酷狗网页未授予该曲目播放权限（code={result.get('err_code', result.get('error_code', 'unknown'))}）；请在账号页检测会员，确认官网同一账号能播放整首")
            if body.get("is_free_part") in (1, "1", True):
                raise ProviderError("酷狗网页只返回试听片段；当前网页凭证未获得该曲目整首权限")
            return {**body, "url": body["play_url"], "fileSize": body.get("filesize", 0), "bitRate": body.get("bitrate", 0)}
        params = {"cmd": "playInfo", "hash": song_hash, "album_id": album_id, "area_code": 1}
        return self.http.get_json(PLAY_URL, params=params) or {}

    def stream(self, track_id: str, quality: str = "auto", extra: dict | None = None) -> StreamInfo:
        extra = extra or {}
        candidates: list[tuple[str, str, int]] = []
        if quality in ("flac", "auto"):
            if extra.get("hashFlac"):
                candidates.append((extra["hashFlac"], "flac", 999))
        if quality in ("320", "auto"):
            if extra.get("hash320"):
                candidates.append((extra["hash320"], "320", 320))
        candidates.append((track_id, "128", 128))

        # Catalogue payment badges are not entitlement decisions: platform
        # free-listening time or promotions may authorize a full stream.
        # Always ask the platform; never block on local VIP/paid flags.

        failure = ""
        for song_hash, label, bitrate in candidates:
            if not song_hash:
                continue
            try:
                info = self._play_info(song_hash, str(extra.get("albumId") or ""))
            except ProviderError as exc:
                failure = str(exc)
                continue
            except HttpError:
                failure = "酷狗网页播放接口请求失败，请稍后重试或检测账号"
                continue
            url = info.get("url") or ""
            if not url:
                continue
            # Kugou happily returns a URL for a lower bitrate than requested,
            # and occasionally for a dead resource; measure instead of trusting.
            probe = probe_stream(
                self.http,
                url,
                headers={"Referer": self.homepage},
                expected_seconds=float(extra.get("duration") or 0),
            )
            if probe.get("error") or probe["format"] == "unknown":
                continue
            if not probe["duration_matches"]:
                continue
            actual = probe["bitrate"] or int(info.get("bitRate") or bitrate or 128)
            ext = probe["format"] if probe["format"] in ("mp3", "flac", "m4a") else (info.get("extName") or "mp3").lower()
            real = quality_label(actual, probe["format"])
            return StreamInfo(
                url=url,
                quality=real if real != "unknown" else ("flac" if ext == "flac" else label),
                bitrate=actual,
                ext=ext,
                size=int(info.get("fileSize") or 0),
            )
        raise ProviderError(failure or "酷狗无法提供播放地址；请在账号页检测登录/会员，或确认官网是否支持此曲目播放")

    def _has_account(self) -> bool:
        from ..credentials import has_credentials
        return has_credentials(self.id, self.http.cookies("www.kugou.com"))

    def account_state(self) -> dict[str, Any]:
        cookies = self.http.cookies()
        # The jar always holds our own device ids; they are not a login.
        login_cookies = {k: v for k, v in cookies.items() if k not in self._device_cookies}
        state = {
            "provider": self.id,
            "loggedIn": self._has_account(),
            "cookieNames": sorted(cookies.keys()),
            "cookieCount": len(cookies),
            "userid": cookies.get("userid", ""),
            "hasToken": bool(cookies.get("token")),
            "readyForVip": False,
            "membershipVerified": False,
            "credentialKind": "web" if cookies.get("KuGoo") else "token",
        }
        return state

    def check_membership(self) -> dict[str, Any]:
        state = super().check_membership()
        if not state["credentialPresent"]:
            return {**state, "message": "未绑定完整酷狗凭证"}
        result = self.http.get_json("https://vip.kugou.com/recharge/roleinfo", params={"n": time.time()})
        if not isinstance(result, dict):
            return {**state, "message": "酷狗会员接口响应格式异常"}
        body = result.get("data") if isinstance(result.get("data"), dict) else result
        role = body.get("role")
        # An anonymous/default role alone cannot prove an authenticated identity.
        uid = str(body.get("userid") or body.get("user_id") or body.get("uid") or "")
        values = self.http.cookies("vip.kugou.com")
        expected = values.get("KugooID") or values.get("userid") or ""
        known = {"0","1","2","3","4","5","11","13","31","33"}
        # Current service does not echo userid. Real authenticated response has
        # errno=error_code=0; an empty session returns errno105/error_code20017.
        # Never infer identity merely from a cookie or a default role.
        accepted = result.get("errno") == 0 and result.get("error_code") == 0
        same_identity = bool(expected and uid == str(expected))
        if not expected or (uid and uid != str(expected)) or not (accepted or same_identity) or str(role) not in known:
            return {**state, "message": "酷狗会员接口未验证当前会话；会员状态未知，请检查官网或重新绑定"}
        if str(role) != "0" and body.get("isExpiredMember") not in (0, "0", False):
            return {**state, "identityVerified": True, "message": "酷狗返回会员角色，但未确认有效期；状态未知"}
        vip = str(role) != "0"
        labels = {"11":"豪华VIP", "13":"豪华VIP", "31":"音乐包", "33":"豪华音乐包"}
        return {**state, "identityVerified": True, "membershipVerified": True, "isVip": vip,
                "membershipType": labels.get(str(role), "VIP" if vip else "普通账号"),
                "message": "酷狗已确认当前登录会话的" + labels.get(str(role), "VIP") + "有效；单曲另购/地区限制仍以平台结果为准" if vip else "酷狗本次查询为普通账号；免费听歌时长和活动权益未知，仍可尝试播放，以平台逐曲授权为准"}

    # -- lyrics ----------------------------------------------------------
    def lyrics(self, track_id: str, extra: dict | None = None) -> Lyrics:
        params = {
            "ver": 1,
            "man": "yes",
            "client": "mobi",
            "keyword": "",
            "duration": "",
            "hash": track_id,
        }
        try:
            data = self.http.get_json(KRCS_URL, params=params)
        except HttpError as exc:
            # Do not interpolate exc: it carries the upstream URL with its vkey.
            raise ProviderError("酷狗歌词检索失败（上游不可达）") from exc
        candidates = (data or {}).get("candidates") or []
        if not candidates:
            raise ProviderError("酷狗没有该曲目的歌词")
        best = candidates[0]
        dl = self.http.get_json(
            LYRIC_URL,
            params={
                "ver": 1,
                "client": "pc",
                "id": best.get("id"),
                "accesskey": best.get("accesskey"),
                "fmt": "lrc",
                "charset": "utf8",
            },
        )
        content = (dl or {}).get("content") or ""
        if not content:
            raise ProviderError("酷狗歌词下载为空")
        raw = base64.b64decode(content).decode("utf-8", "replace")
        if raw.startswith("\ufeff"):
            raw = raw[1:]
        return parse_lrc(raw)

    # -- playlist --------------------------------------------------------
    def playlist(self, playlist_id: str) -> dict[str, Any]:
        raise ProviderError("酷狗歌单导入暂未开放（官方接口要求签名鉴权），可用搜索添加单曲。")
