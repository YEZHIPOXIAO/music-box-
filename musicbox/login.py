"""Account login flows.

QQ Music uses a direct QR handshake followed by a music credential exchange.
Kugou and Kuwo use the QR login provided by their official web pages inside an
owned WebView. Not finding a standalone API does not mean their pages lack QR
login. Passwords, if offered by the official page, never pass through this code.
"""

from __future__ import annotations

import ast
import base64
import json
import re
import secrets
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse, urlencode
from dataclasses import dataclass, field
from typing import Any

from .http import HttpClient, HttpError


class LoginError(RuntimeError):
    """A login flow failed in a way worth showing the user."""


@dataclass
class QrSession:
    """State of one in-flight QR login."""

    provider: str
    token: str
    image_png: bytes
    created_at: float = field(default_factory=time.time)
    status: str = "waiting"  # waiting | scanned | confirmed | expired | failed
    message: str = ""
    cookies: dict[str, str] = field(default_factory=dict)

    @property
    def image_data_url(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.image_png).decode("ascii")

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "token": self.token,
            "image": self.image_data_url,
            "status": self.status,
            "message": self.message,
        }


def hash33(text: str) -> int:
    """QQ's ``ptqrtoken`` derivation: rolling 33x hash, 31-bit mask."""
    value = 0
    for char in text:
        value = (value * 33 + ord(char)) & 0x7FFFFFFF
    return value


class QQLogin:
    """QQ Music QR login.

    Verified against the live service: ptqrshow answers a PNG plus a qrsig
    cookie, and ptqrlogin reports 66 (waiting) / 67 (scanned) / 65 (expired) /
    0 (success). On success the redirect URL carries the ticket that becomes
    the account cookie.
    """

    APPID = "716027609"
    DAID = "383"
    THIRD_AID = "100497308"
    QR_URL = "https://ssl.ptlogin2.qq.com/ptqrshow"
    POLL_URL = "https://ssl.ptlogin2.qq.com/ptqrlogin"

    def __init__(self, http: HttpClient | None = None) -> None:
        self.http = http or HttpClient(timeout=12, retries=0, credential_hosts=(
            "ssl.ptlogin2.qq.com", "ssl.ptlogin2.graph.qq.com", "xui.ptlogin2.qq.com", "graph.qq.com", "u.y.qq.com"))
        self.lock = threading.Lock()
        self.http.session.headers.update(
            {
                "Referer": "https://y.qq.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            }
        )
        self._session: QrSession | None = None
        self._ptqrtoken = 0

    def start(self) -> QrSession:
        params = {
            "appid": self.APPID,
            "e": 2,
            "l": "M",
            "s": 3,
            "d": 72,
            "v": 4,
            "t": time.time(),
            "daid": self.DAID,
            "pt_3rd_aid": self.THIRD_AID,
        }
        resp = self.http.get(self.QR_URL, params=params)
        if resp.status_code != 200:
            raise LoginError(f"获取二维码失败：HTTP {resp.status_code}")
        if not resp.content.startswith(b"\x89PNG"):
            raise LoginError("二维码接口返回的不是图片，可能被风控拦截")
        qrsig = self.http.cookies().get("qrsig", "")
        if not qrsig:
            raise LoginError("二维码接口未返回 qrsig，无法继续登录")
        self._ptqrtoken = hash33(qrsig)
        self._session = QrSession(
            provider="qq",
            token=uuid.uuid4().hex,
            image_png=resp.content,
            message="用手机 QQ 扫描二维码",
        )
        return self._session

    def poll(self) -> QrSession:
        if self._session is None:
            raise LoginError("尚未开始扫码，请先获取二维码")
        session = self._session
        if session.status in {"confirmed", "expired", "failed"}:
            return session
        if time.time() - session.created_at > 180:
            session.status = "expired"
            session.message = "二维码已过期，请刷新"
            return session

        params = {
            "u1": "https://graph.qq.com/oauth2.0/login_jump",
            "ptqrtoken": self._ptqrtoken,
            "ptredirect": 0,
            "h": 1,
            "t": 1,
            "g": 1,
            "from_ui": 1,
            "ptlang": 2052,
            "action": f"0-0-{int(time.time() * 1000)}",
            "js_ver": 20102616,
            "js_type": 1,
            "login_sig": "",
            "pt_uistyle": 40,
            "aid": self.APPID,
            "daid": self.DAID,
            "pt_3rd_aid": self.THIRD_AID,
            "has_onekey": 1,
        }
        try:
            text = self.http.get_text(self.POLL_URL, params=params)
        except HttpError as exc:
            session.message = "轮询失败，请重试"
            raise LoginError(f"轮询失败：{exc.public_message}") from exc

        code, redirect = self._parse(text)
        if code == "66":
            session.status = "waiting"
            session.message = "等待手机扫码"
        elif code == "67":
            session.status = "scanned"
            session.message = "已扫码，请在手机上确认"
        elif code == "65":
            session.status = "expired"
            session.message = "二维码已过期，请刷新"
        elif code == "0" and redirect:
            self._finish(redirect, session)
        else:
            session.status = "failed"
            session.message = f"登录未完成（code={code or '未知'}）"
        return session

    def _parse(self, text: str) -> tuple[str, str]:
        """ptqrlogin answers ``ptuiCB('66','0','','0','msg','')``."""
        if "ptuiCB" not in text:
            return "", ""
        match = re.fullmatch(r"\s*ptuiCB\((.*)\)\s*;?\s*", text, re.S)
        if not match:
            return "", ""
        try:
            parts = ast.literal_eval("(" + match.group(1) + ")")
        except (SyntaxError, ValueError):
            return "", ""
        if not isinstance(parts, tuple) or not all(isinstance(p, str) for p in parts):
            return "", ""
        code = parts[0] if parts else ""
        redirect = parts[2] if len(parts) > 2 else ""
        return code, redirect

    @staticmethod
    def _trusted(url: str, host: str, path: str) -> str:
        parsed = urlparse(url)
        if (parsed.scheme != "https" or parsed.hostname != host or parsed.path != path
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise LoginError("登录跳转地址不可信，已终止")
        return url

    def _finish(self, redirect: str, session: QrSession) -> None:
        """QQ identity -> OAuth code (state checked) -> QQ Music credential.

        Protocol reference: @yakult-green-tea/qq-music-api 3.3.6 ptlogin
        (MIT). Global QQ cookies remain transient and never enter the store.
        """
        from .credentials import dump_credentials
        try:
            check = self._trusted(redirect, "ssl.ptlogin2.graph.qq.com", "/check_sig")
            with self.http.get(check, allow_redirects=False) as response:
                if response.status_code != 302:
                    raise LoginError("QQ 身份确认未完成，请重新扫码")
                jump = self._trusted(response.headers.get("Location", ""), "graph.qq.com", "/oauth2.0/login_jump")
            graph = self.http.cookies("graph.qq.com")
            if not all(graph.get(k) for k in ("p_uin", "p_skey", "pt_oauth_token")):
                raise LoginError("QQ 授权临时凭证不完整，请重新扫码")
            with self.http.get(jump, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise LoginError("QQ 授权页面不可达")
            state = secrets.token_urlsafe(24)
            gtk = 5381
            for char in graph["p_skey"]:
                gtk = (gtk * 33 + ord(char)) & 0x7fffffff
            form = {
                "response_type": "code", "client_id": self.THIRD_AID,
                "redirect_uri": "https://y.qq.com/portal/wx_redirect.html?" + urlencode({"login_type": "1", "surl": "https://y.qq.com/"}),
                "scope": "get_user_info,get_app_friends", "state": state,
                "switch": "", "from_ptlogin": "1", "src": "1", "update_auth": "1",
                "openapi": "1010_1030", "g_tk": str(gtk),
                "auth_time": str(int(time.time() * 1000)), "ui": str(uuid.uuid4()),
            }
            with self.http.post("https://graph.qq.com/oauth2.0/authorize", data=form,
                                allow_redirects=False) as response:
                if response.status_code != 302:
                    raise LoginError("QQ 授权未返回音乐授权码")
                callback = self._trusted(response.headers.get("Location", ""), "y.qq.com", "/portal/wx_redirect.html")
            query = parse_qs(urlparse(callback).query)
            if query.get("state") != [state] or len(query.get("code", [])) != 1:
                raise LoginError("QQ 授权状态校验失败")
            code = query["code"][0]
            if not re.fullmatch(r"[A-Fa-f0-9]{32}", code):
                raise LoginError("QQ 音乐授权码无效")
            # The authorization code is all the music endpoint needs. Never
            # forward global QQ p_skey/pt_oauth_token into its music session.
            self.http.clear_cookies()
            payload = {
                "comm": {"g_tk": 5381, "platform": "yqq", "ct": 24, "cv": 0},
                "req": {"module": "QQConnectLogin.LoginServer", "method": "QQLogin", "param": {"code": code}},
            }
            result = self.http.post_json("https://u.y.qq.com/cgi-bin/musicu.fcg", payload, allow_redirects=False)
            if not isinstance(result, dict):
                raise LoginError("QQ 音乐凭证响应格式无效")
            req = result.get("req")
            if not isinstance(req, dict) or not isinstance(req.get("data"), dict):
                raise LoginError("QQ 音乐凭证响应不完整")
            data = req["data"]
            if result.get("code") != 0 or req.get("code") != 0 or data.get("loginType") != 2:
                raise LoginError("QQ 音乐拒绝凭证交换，请重新登录")
            musicid, key = str(data.get("musicid", "")), data.get("musickey", "")
            if not musicid.isdigit() or int(musicid) <= 0 or not isinstance(key, str) or not key:
                raise LoginError("QQ 音乐未返回有效身份与密钥")
            records = [{"name": name, "value": value, "domain": ".qq.com", "path": "/", "secure": True}
                       for name, value in {"uin": musicid, "qqmusic_uin": musicid, "qqmusic_key": key, "qm_keyst": key, "login_type": "2"}.items()]
            session.cookies = {c["name"]: c["value"] for c in records}
            self._credential_bundle = dump_credentials(records)
            session.status = "confirmed"
            session.message = "QQ音乐凭证已获取；会员权限以实际播放结果为准"
        except (HttpError, ValueError, KeyError, TypeError, LoginError):
            session.status = "failed"
            session.message = "QQ 身份已确认，但音乐凭证交换失败，请重新扫码或使用官方网页登录"
        finally:
            self.http.clear_cookies()

    def close(self) -> None:
        timer = getattr(self, "expiry_timer", None)
        if timer is not None:
            timer.cancel()
        self.http.close()

    def cookie_string(self) -> str:
        if self._session is None or not self._session.cookies:
            return ""
        return getattr(self, "_credential_bundle", "")


# ---------------------------------------------------------------------------
# WebView-based login for platforms without a QR API
# ---------------------------------------------------------------------------
WEB_LOGIN_URLS = {
    "kugou": "https://www.kugou.com/",
    "kuwo": "https://www.kuwo.cn/",
    "qq": "https://y.qq.com/",
}


def web_login_hint(provider: str) -> str:
    return {
        "kugou": "在酷狗官方页面选择扫码登录，手机确认后点「完成并绑定」",
        "kuwo": "在酷我官方页面选择扫码登录，手机确认后点「完成并绑定」",
        "qq": "在打开的 QQ音乐页面里扫码登录，完成后点「已完成登录」",
    }.get(provider, "在打开的页面里完成登录")
