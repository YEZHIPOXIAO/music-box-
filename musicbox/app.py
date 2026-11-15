"""FastAPI application: JSON API + streaming proxy + static UI."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Iterator

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import settings
from .http import HttpClient, HttpError
from .providers import ProviderError, all_infos, get_provider
from .storage import Store

log = logging.getLogger("musicbox")

store = Store()
streamer = HttpClient(timeout=30.0, retries=1)
_stream_cache: dict[tuple[str, str, str, int], tuple[float, dict[str, Any]]] = {}
# Provider calls are blocking; cap how many run at once so a slow platform
# cannot starve the whole request pool.
_probe_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="musicbox-provider")

# A per-launch token that the page must present. It stops any other local
# process, or a website the user happens to visit, from driving this API.
LAUNCH_TOKEN = secrets.token_urlsafe(32)
# Single-range only: "bytes=start-end", "bytes=start-", or suffix "bytes=-N".
RANGE_RE = re.compile(r"^bytes=(?:[0-9]{1,12}-[0-9]{0,12}|-[0-9]{1,12})$")
CONTENT_RANGE_RE = re.compile(r"^bytes [0-9]+-[0-9]+/[0-9]+$")
QUALITY_CHOICES = {"auto", "flac", "320", "128"}


def _restore_accounts() -> None:
    """Re-hydrate saved cookies into each provider session."""
    for row in store.accounts():
        saved = store.load_account(row["provider"])
        if not saved:
            continue
        if saved.get("broken"):
            log.warning("账号 %s 的本地密文无法解密（密钥可能已更换）", row["provider"])
            continue
        if not saved.get("cookie"):
            continue
        try:
            get_provider(row["provider"]).apply_cookie(saved["cookie"])
            log.info("已恢复 %s 账号登录态", row["provider"])
        except Exception as exc:  # noqa: BLE001 - a bad cookie must not kill startup
            log.warning("恢复 %s 账号失败: %s", row["provider"], type(exc).__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _restore_accounts()
    yield


app = FastAPI(
    title="MusicBox",
    version=__version__,
    description="一个本地运行的聚合音乐播放器，可绑定 QQ音乐 / 酷狗 / 酷我 账号。",
    docs_url="/api/docs",
    redoc_url=None,
    lifespan=lifespan,
)


def _same_origin(request: Request) -> bool:
    """Reject cross-site calls: only this app's own page may drive the API."""
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True  # same-origin fetch() omits Origin for GET; curl/CLI too
    host = request.headers.get("host", "")
    parsed_host = origin.split("//", 1)[-1].split("/", 1)[0]
    return parsed_host == host


@app.middleware("http")
async def _guard(request: Request, call_next):
    # Block DNS-rebinding: only accept the loopback host names this server uses.
    host = (urlsplit("//" + request.headers.get("host", "")).hostname or "").lower()
    if host not in settings.allowed_hosts and not settings.allow_external_host:
        return JSONResponse(
            status_code=421,
            content={"detail": f"拒绝的主机名 {host}；如确需外部访问，请设置 MUSICBOX_ALLOW_HOST=1"},
        )
    if request.url.path.startswith("/api/login/") and (
        request.headers.get("sec-fetch-site") == "cross-site" or not _same_origin(request)
    ):
        return JSONResponse(status_code=403, content={"detail": "跨站登录请求已被拒绝"})
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _same_origin(request):
        return JSONResponse(status_code=403, content={"detail": "跨站请求已被拒绝"})
    return await call_next(request)


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------
class LoginPayload(BaseModel):
    cookie: str = Field(min_length=1, max_length=16384, description="浏览器里复制出来的整段 Cookie")
    nickname: str = Field(default="", max_length=64)


class PlaybackSourcePayload(BaseModel):
    mode: str = "official"
    baseUrl: str = Field(default="", max_length=256)
    path: str = Field(default="", max_length=160)


class PlaylistPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TrackPayload(BaseModel):
    provider: str
    id: str
    title: str = ""
    artists: list[str] = Field(default_factory=list)
    album: str = ""
    duration: int = 0
    cover: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": __version__,
        "providers": [info.id for info in all_infos()],
        "privacy": {
            **settings.privacy_report(),
            "encryptedAccounts": store.cipher.enabled,
            "encryptionScheme": store.cipher.scheme,
        },
    }


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    accounts = {a["provider"]: a for a in store.accounts()}
    out = []
    for info in all_infos():
        provider = get_provider(info.id)
        state = provider.account_state()
        out.append(
            {
                **info.__dict__,
                "loggedIn": state["loggedIn"],
                "cookieCount": state["cookieCount"],
                "readyForVip": state.get("readyForVip", False),
                "uin": state.get("uin", ""),
                "savedAt": accounts.get(info.id, {}).get("updatedAt"),
                "nickname": accounts.get(info.id, {}).get("nickname", ""),
            }
        )
    return {"providers": out, "quality": settings.default_quality}


def _search_one(pid: str, q: str, page: int, limit: int) -> tuple[str, list[dict[str, Any]], str]:
    """Run one provider search; returns (provider, tracks, error)."""
    started = time.time()
    try:
        tracks = get_provider(pid).search(q, page=page, limit=limit)
    except (ProviderError, HttpError, ValueError) as exc:
        log.info("搜索失败 provider=%s q=%r 耗时=%.1fs err=%s", pid, q, time.time() - started, exc)
        return pid, [], _safe_detail(exc)
    except Exception as exc:  # noqa: BLE001 - never let one provider break the page
        log.warning("搜索异常 provider=%s err=%s: %s", pid, type(exc).__name__, exc)
        return pid, [], f"内部错误 {type(exc).__name__}"
    log.info("搜索完成 provider=%s q=%r 命中=%d 耗时=%.1fs", pid, q, len(tracks), time.time() - started)
    return pid, [t.to_dict() for t in tracks], ""


def _safe_detail(exc: BaseException) -> str:
    """A message safe to show a browser.

    ``str(HttpError)`` embeds the full upstream URL, which carries the vkey and
    therefore the user's entitlement. The full text still goes to the log; only
    a stable, URL-free description reaches the response.
    """
    if isinstance(exc, HttpError):
        return exc.public_message
    if isinstance(exc, ValueError):
        # Upstream parse errors embed the response body in their message.
        return "上游返回了无法解析的数据"
    return str(exc)


@app.get("/api/search")
def search(
    q: str = Query(min_length=1, max_length=120),
    provider: str = Query("all"),
    page: int = Query(1, ge=1, le=50),
    limit: int = Query(20, ge=1, le=50),
) -> dict[str, Any]:
    targets = [p for p in (provider.split(",") if provider != "all" else [i.id for i in all_infos()]) if p]
    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    # Query the platforms in parallel: total latency becomes the slowest
    # platform instead of their sum.
    futures = [_probe_pool.submit(_search_one, pid, q, page, limit) for pid in targets]
    for future in futures:
        pid, tracks, error = future.result()
        if error:
            errors.append({"provider": pid, "error": error})
        results.extend(tracks)
    return {"query": q, "count": len(results), "tracks": results, "errors": errors}


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------
@app.post("/api/account/{provider}")
def login(provider: str, payload: LoginPayload) -> dict[str, Any]:
    target = get_provider(provider)
    try:
        bundle = target.validate_cookie(payload.cookie)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(status_code=400, detail="登录凭证不完整或平台不匹配，请在对应官方页面完成登录") from exc
    try:
        store.save_account(provider, bundle, payload.nickname)
    except (OSError, sqlite3.Error) as exc:
        log.error("保存登录失败 provider=%s exception=%s", provider, type(exc).__name__)
        raise HTTPException(status_code=503, detail="登录凭证保存失败：请确认应用数据目录可写，再重新绑定") from exc
    target.apply_cookie(bundle)
    _stream_cache.clear()
    state = target.account_state()
    return {"ok": True, "provider": provider, "cookieCount": state["cookieCount"], "names": state["cookieNames"]}


@app.delete("/api/account/{provider}")
def logout(provider: str) -> dict[str, Any]:
    target = get_provider(provider)
    target.clear_account()
    removed = store.delete_account(provider)
    _stream_cache.clear()
    return {"ok": True, "removed": removed}


@app.post("/api/account/{provider}/verify")
def verify_account(provider: str) -> dict[str, Any]:
    target = get_provider(provider)
    try:
        result = target.check_membership()
    except (HttpError, ValueError, TypeError, KeyError) as exc:
        result = {"provider": provider, "credentialPresent": target.account_state()["loggedIn"],
                  "identityVerified": False, "membershipVerified": False, "isVip": None,
                  "message": "平台账号检测暂时失败，会员状态未知；不代表你没有VIP"}
    from .backup_api import get
    result["playbackSource"] = get(provider)["mode"]
    if result["playbackSource"] == "backup":
        result["message"] += "。当前启用本机备用API，其登录状态独立于官方网页登录"
    return result


@app.get("/api/account/{provider}/check")
def account_check(provider: str) -> dict[str, Any]:
    target = get_provider(provider)
    state = target.account_state()
    return {"provider": provider, **state}


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------
@app.get('/api/playback-source/{provider}')
def playback_source(provider: str):
    from .backup_api import get
    try:
        return get(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/playback-source/{provider}')
def set_playback_source(provider: str, payload: PlaybackSourcePayload):
    from .backup_api import save
    try:
        result = save(provider, payload.mode, payload.baseUrl, payload.path)
        _stream_cache.clear()
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_playback(target, track_id, quality, extra=None):
    from .backup_api import resolve, get
    # Reuse a recently authorized URL for the metadata request and subsequent
    # Range reads. This does not grant new rights or extend platform expiry.
    # Hash account material; never store a plaintext credential in a cache key.
    def identity():
        raw = json.dumps(target.http.cookie_records(), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()
    before = identity()
    choice = get(target.id)
    key = ('resolved', target.id, track_id, quality,
           json.dumps(extra or {}, sort_keys=True), before, json.dumps(choice, sort_keys=True))
    cached = _stream_cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    try:
        alternate = resolve(target.id, track_id, quality, extra)
    except (ValueError, __import__('requests').RequestException) as exc:
        raise ProviderError('备用 API 调用失败，请检查本机服务、路径和登录状态；可单独切回官方接入') from exc
    info = alternate if alternate is not None else target.stream(track_id, quality=quality, extra=extra)
    if identity() == before:
        expiry = min(time.time() + 30, info.expires_at) if info.expires_at else time.time() + 30
        _stream_cache[key] = (expiry, info)
        _prune_cache()
    return info

@app.get("/api/stream/{provider}/{track_id}")
def stream_info(
    provider: str,
    track_id: str,
    quality: str = Query("auto"),
    album: str = Query("", description="酷狗需要 album_id 时可传"),
    duration: int = Query(0, ge=0, description="曲目时长，用于校验返回的音频是否是同一首歌"),
    hash320: str = Query("", description="酷狗 320K 文件 hash"),
    hashFlac: str = Query("", description="酷狗无损文件 hash"),
    hasLossless: bool = Query(False, description="酷我该曲是否有无损"),
) -> dict[str, Any]:
    target = get_provider(provider)
    if quality not in QUALITY_CHOICES:
        quality = "auto"
    extra: dict[str, Any] = {}
    if album:
        extra["albumId"] = album
    if duration:
        extra["duration"] = duration
    # The high-quality candidate hashes come from search results and must
    # survive the round trip to the audio endpoint, or every VIP user silently
    # gets 128K. They are handed over as a signed opaque token instead of raw
    # fields so the play URL stays short and the values cannot be edited into
    # something a provider would choke on.
    for name, value in (("hash320", hash320), ("hashFlac", hashFlac)):
        if value:
            extra[name] = value
    if hasLossless:
        extra["hasLossless"] = True

    ticket = _encode_ticket(provider, track_id, extra)
    key = (provider, track_id, quality, duration, ticket)
    now = time.time()
    cached = _stream_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    try:
        info = _resolve_playback(target, track_id, quality, extra)
    except ProviderError as exc:
        raise HTTPException(status_code=424, detail=_safe_detail(exc)) from exc
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=exc.public_message) from exc

    src = f"/api/audio/{provider}/{track_id}?quality={quality}"
    if duration:
        src += f"&duration={duration}"
    if ticket:
        src += f"&ticket={ticket}"
    data = {
        "provider": provider,
        "id": track_id,
        "quality": info.quality,
        "bitrate": info.bitrate,
        "ext": info.ext,
        # The page plays through /api/audio/... so the provider URL with its
        # vkey/token never reaches the browser.
        "src": src,
    }
    # Expiry is computed after resolution: the call itself can take seconds.
    _prune_cache()
    _stream_cache[key] = (time.time() + settings.stream_cache_ttl, data)
    return data


def _prune_cache() -> None:
    """Drop expired entries so a long-running server does not grow forever."""
    now = time.time()
    for k in [k for k, (exp, _) in _stream_cache.items() if exp <= now]:
        _stream_cache.pop(k, None)
    # Hard cap: keep the newest entries if a user browses a huge catalogue.
    limit = max(16, settings.stream_cache_max)
    if len(_stream_cache) > limit:
        for k in sorted(_stream_cache, key=lambda k: _stream_cache[k][0])[: len(_stream_cache) - limit]:
            _stream_cache.pop(k, None)


def _iter_stream(resp) -> Iterator[bytes]:
    try:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                yield chunk
    finally:
        resp.close()


def _ticket_key() -> bytes:
    """Key used to seal tickets. Derived from the launch token, so tickets die
    with the process — a deliberate contract: they are short-lived handles, not
    portable credentials."""
    return hashlib.sha256(f"musicbox-ticket:{LAUNCH_TOKEN}".encode("utf-8")).digest()


def _encode_ticket(provider: str, track_id: str, extra: dict[str, Any]) -> str:
    """Seal provider extras into a short, opaque, tamper-evident handle.

    Only the fields a provider needs to reach a high-quality candidate are
    carried. The payload is encrypted (not merely signed and base64'd) so the
    hashes are not readable by the page, and it is bound to the track it was
    issued for so it cannot be replayed against another song.
    """
    if not extra:
        return ""
    payload = {"p": provider, "t": track_id, "x": extra}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    key = _ticket_key()
    nonce = os.urandom(8)
    stream = hashlib.sha256(key + nonce).digest() * (len(raw) // 32 + 1)
    body = bytes(a ^ b for a, b in zip(raw, stream))
    tag = hmac.new(key, nonce + body, hashlib.sha256).digest()[:10]
    return base64.urlsafe_b64encode(nonce + tag + body).decode("ascii").rstrip("=")


def _decode_ticket(ticket: str, provider: str = "", track_id: str = "") -> dict[str, Any]:
    """Inverse of _encode_ticket; returns {} for anything untrusted or foreign."""
    if not ticket:
        return {}
    try:
        padded = ticket + "=" * (-len(ticket) % 4)
        blob = base64.urlsafe_b64decode(padded)
    except (ValueError, binascii.Error):
        return {}
    if len(blob) < 19:
        return {}
    nonce, tag, body = blob[:8], blob[8:18], blob[18:]
    key = _ticket_key()
    expected = hmac.new(key, nonce + body, hashlib.sha256).digest()[:10]
    if not hmac.compare_digest(tag, expected):
        return {}
    stream = hashlib.sha256(key + nonce).digest() * (len(body) // 32 + 1)
    raw = bytes(a ^ b for a, b in zip(body, stream))
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(payload, dict):
        return {}
    # Bind to the track: a ticket for song A must never unlock song B.
    if provider and payload.get("p") != provider:
        return {}
    if track_id and str(payload.get("t")) != str(track_id):
        return {}
    extra = payload.get("x")
    if not isinstance(extra, dict):
        return {}
    allowed = {"albumId", "duration", "hash320", "hashFlac", "hasLossless"}
    return {k: v for k, v in extra.items() if k in allowed}


@app.get("/api/audio/{provider}/{track_id}")
def audio(
    request: Request,
    provider: str,
    track_id: str,
    quality: str = Query("auto"),
    duration: int = Query(0, ge=0),
    ticket: str = Query(""),
):
    target = get_provider(provider)
    if quality not in QUALITY_CHOICES:
        quality = "auto"
    extra = _decode_ticket(ticket, provider, track_id)
    if duration and "duration" not in extra:
        extra["duration"] = duration
    try:
        info = _resolve_playback(target, track_id, quality, extra or None)
    except ProviderError as exc:
        raise HTTPException(status_code=424, detail=_safe_detail(exc)) from exc
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=exc.public_message) from exc

    headers = {"User-Agent": settings.user_agent, "Referer": target.homepage}
    range_header = request.headers.get("range", "")
    if range_header:
        # Forward only a well-formed single range; anything else is dropped
        # rather than handed to the CDN verbatim.
        if RANGE_RE.match(range_header.strip()):
            headers["Range"] = range_header.strip()

    upstream = None
    try:
        upstream = streamer.stream(info.url, headers=headers)
        if upstream.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"音频源返回 {upstream.status_code}")

        media_type = mimetypes.guess_type(f"a.{info.ext}")[0] or "audio/mpeg"
        out_headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        # Only forward a length we know matches the bytes we will send.
        # requests decodes a gzip body by default, so an upstream
        # Content-Length describes the *compressed* size; forwarding it made
        # the browser wait forever for bytes that were never coming.
        length = upstream.headers.get("Content-Length")
        encoding = (upstream.headers.get("Content-Encoding") or "identity").lower()
        if length and length.isdigit() and encoding in ("", "identity"):
            out_headers["Content-Length"] = length
        content_range = upstream.headers.get("Content-Range")
        if content_range and CONTENT_RANGE_RE.match(content_range.strip()):
            out_headers["Content-Range"] = content_range.strip()
        response = StreamingResponse(
            _iter_stream(upstream),
            status_code=upstream.status_code,
            media_type=media_type,
            headers=out_headers,
        )
        upstream = None  # ownership passed to the response generator
        return response
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=exc.public_message) from exc
    finally:
        if upstream is not None:
            upstream.close()


@app.get("/api/fallback")
def fallback(
    title: str = Query(min_length=1),
    artist: str = Query(""),
    exclude: str = Query(""),
    limit: int = Query(6, ge=1, le=20),
) -> dict[str, Any]:
    """Find the same song on the other platforms.

    Used when a track cannot be played on its own provider: the UI offers
    playable alternatives instead of a dead end.
    """
    query = f"{title} {artist}".strip()
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def _one(pid: str):
        try:
            return pid, get_provider(pid).search(query, limit=limit), ""
        except (ProviderError, HttpError, ValueError) as exc:
            return pid, [], _safe_detail(exc)
        except Exception as exc:  # noqa: BLE001
            return pid, [], f"内部错误 {type(exc).__name__}"

    targets = [i.id for i in all_infos() if i.id != exclude]
    for future in [_probe_pool.submit(_one, pid) for pid in targets]:
        pid, tracks, error = future.result()
        if error:
            errors.append({"provider": pid, "error": error})
            continue
        # prefer the exact same title, then everything else
        exact = [t for t in tracks if t.title.strip().lower() == title.strip().lower()]
        rest = [t for t in tracks if t not in exact]
        for track in (exact + rest)[:limit]:
            candidates.append(track.to_dict())
    return {"query": query, "tracks": candidates, "errors": errors}


# --------------------------------------------------------------------------
# login helpers
# --------------------------------------------------------------------------
_qr_sessions: dict[str, tuple[Any, float]] = {}
_qr_lock = threading.RLock()
_QR_MAX = 4
_QR_TTL = 180


def _qr_sweep_locked() -> None:
    now = time.monotonic()
    for token, (flow, created) in list(_qr_sessions.items()):
        if now - created > _QR_TTL:
            # Do not close a requests.Session while its flow is exchanging an
            # authorization code on another thread. Its timer will retry via
            # the per-flow cancellation path once the request finishes.
            lock = getattr(flow, "lock", None)
            if lock is not None and not lock.acquire(blocking=False):
                continue
            try:
                _qr_sessions.pop(token, None)
                flow.close()
            finally:
                if lock is not None:
                    lock.release()


@app.post("/api/login/{provider}/qr")
def start_qr_login(provider: str) -> dict[str, Any]:
    """Start a QR login. Only QQ Music exposes one; others report honestly."""
    if provider != "qq":
        raise HTTPException(
            status_code=424,
            detail={
                "kugou": "请使用酷狗官方页面的扫码登录，确认后由桌面端绑定凭证。",
                "kuwo": "请使用酷我官方页面的扫码登录，确认后由桌面端绑定凭证。",
            }.get(provider, "该平台不支持扫码登录"),
        )
    from .login import LoginError, QQLogin

    # Reserve a slot before performing network work; concurrent starts cannot
    # exceed the resource bound. TTL/cancel/terminal paths all close Sessions.
    with _qr_lock:
        _qr_sweep_locked()
        if len(_qr_sessions) >= _QR_MAX:
            raise HTTPException(status_code=429, detail="扫码会话已达上限，请先取消旧二维码")
        flow = QQLogin()
        token = secrets.token_urlsafe(24)
        _qr_sessions[token] = (flow, time.monotonic())
        timer = threading.Timer(_QR_TTL, lambda: cancel_qr_login("qq", token))
        timer.daemon = True
        flow.expiry_timer = timer
        timer.start()
    try:
        session = flow.start()
        session.token = token
    except (LoginError, HttpError) as exc:
        with _qr_lock:
            _qr_sessions.pop(token, None)
        flow.close()
        raise HTTPException(status_code=502, detail="获取二维码失败，请稍后重试") from exc
    return {
        "session": session.token,
        "image": session.image_data_url,
        "status": session.status,
        "message": session.message,
    }


@app.get("/api/login/{provider}/poll")
def poll_qr_login(provider: str, session: str) -> dict[str, Any]:
    from .login import LoginError

    if provider != "qq":
        raise HTTPException(status_code=400, detail="会话与平台不匹配")
    with _qr_lock:
        _qr_sweep_locked()
        entry = _qr_sessions.get(session)
    if entry is None:
        raise HTTPException(status_code=404, detail="登录会话不存在或已过期")
    flow, _ = entry
    # Per-flow lock prevents parallel polling/duplicate persistence without
    # holding the global table lock across slow upstream calls.
    with flow.lock:
        with _qr_lock:
            if session not in _qr_sessions:
                raise HTTPException(status_code=404, detail="登录会话已结束")
        try:
            state = flow.poll()
            if state.status == "confirmed":
                target = get_provider(provider)
                bundle = target.validate_cookie(flow.cookie_string())
                store.save_account(provider, bundle, "")
                target.apply_cookie(bundle)
                _stream_cache.clear()
        except (OSError, sqlite3.Error) as exc:
            with _qr_lock:
                _qr_sessions.pop(session, None)
            flow.close()
            log.error("扫码凭证保存失败 provider=%s exception=%s", provider, type(exc).__name__)
            raise HTTPException(status_code=503, detail="扫码确认成功，但凭证保存失败：应用数据目录不可写，请重新打开新版再扫码") from exc
        except (LoginError, HttpError, ValueError) as exc:
            with _qr_lock:
                _qr_sessions.pop(session, None)
            flow.close()
            raise HTTPException(status_code=502, detail="音乐登录凭证交换失败") from exc
        if state.status in {"confirmed", "expired", "failed"}:
            with _qr_lock:
                _qr_sessions.pop(session, None)
            flow.close()

    return {
        "session": session,
        "image": "",
        "status": state.status,
        "message": state.message,
    }


@app.delete("/api/login/{provider}/qr")
def cancel_qr_login(provider: str, session: str) -> dict[str, Any]:
    if provider != "qq":
        raise HTTPException(status_code=400, detail="会话与平台不匹配")
    with _qr_lock:
        entry = _qr_sessions.get(session)
    if entry is None:
        return {"ok": True, "removed": False}
    flow, _ = entry
    with flow.lock:
        with _qr_lock:
            removed = _qr_sessions.pop(session, None)
        if removed:
            flow.close()
    return {"ok": True, "removed": bool(removed)}


@app.get("/api/login/{provider}/web")
def web_login_info(provider: str) -> dict[str, Any]:
    """Tell the desktop shell which official page to open for a manual login."""
    from .login import WEB_LOGIN_URLS, web_login_hint

    url = WEB_LOGIN_URLS.get(provider)
    if not url:
        raise HTTPException(status_code=404, detail="未知平台")
    return {"provider": provider, "url": url, "hint": web_login_hint(provider)}


@app.get("/api/lyrics/{provider}/{track_id}")
def lyrics(provider: str, track_id: str) -> dict[str, Any]:
    target = get_provider(provider)
    try:
        data = target.lyrics(track_id)
    except ProviderError as exc:
        raise HTTPException(status_code=404, detail=_safe_detail(exc)) from exc
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=_safe_detail(exc)) from exc
    return {"provider": provider, "id": track_id, **data.to_dict()}


# --------------------------------------------------------------------------
# playlists
# --------------------------------------------------------------------------
@app.get("/api/playlists")
def list_playlists() -> dict[str, Any]:
    return {"playlists": store.playlists()}


@app.post("/api/playlists")
def create_playlist(payload: PlaylistPayload) -> dict[str, Any]:
    pid = store.create_playlist(payload.name.strip())
    return {"ok": True, "id": pid, "playlists": store.playlists()}


@app.delete("/api/playlists/{playlist_id}")
def delete_playlist(playlist_id: int) -> dict[str, Any]:
    return {"ok": store.delete_playlist(playlist_id)}


@app.get("/api/playlists/{playlist_id}")
def playlist_detail(playlist_id: int) -> dict[str, Any]:
    return {"id": playlist_id, "tracks": store.playlist_tracks(playlist_id)}


@app.post("/api/playlists/{playlist_id}/items")
def playlist_add(playlist_id: int, track: TrackPayload) -> dict[str, Any]:
    added = store.add_to_playlist(playlist_id, track.model_dump())
    return {"ok": added, "duplicate": not added}


@app.delete("/api/playlists/{playlist_id}/items/{provider}/{track_id}")
def playlist_remove(playlist_id: int, provider: str, track_id: str) -> dict[str, Any]:
    return {"ok": store.remove_from_playlist(playlist_id, provider, track_id)}


@app.get("/api/import/{provider}/{playlist_id}")
def import_playlist(provider: str, playlist_id: str) -> dict[str, Any]:
    target = get_provider(provider)
    try:
        data = target.playlist(playlist_id)
    except ProviderError as exc:
        raise HTTPException(status_code=424, detail=_safe_detail(exc)) from exc
    return {
        "provider": provider,
        "name": data.get("name", ""),
        "cover": data.get("cover", ""),
        "tracks": [t.to_dict() for t in data.get("tracks", [])],
    }


# --------------------------------------------------------------------------
# history & privacy
# --------------------------------------------------------------------------
@app.get("/api/history")
def history(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    return {"history": store.history(limit)}


@app.post("/api/history")
def history_add(track: TrackPayload) -> dict[str, Any]:
    store.record_history(track.model_dump())
    return {"ok": True}


@app.delete("/api/history")
def history_clear() -> dict[str, Any]:
    return {"ok": True, "removed": store.clear_history()}


@app.post("/api/privacy/wipe")
def wipe(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    """Delete every local trace (accounts, playlists, history)."""
    if not payload.get("confirm"):
        raise HTTPException(status_code=400, detail="需要 confirm=true 才能清空本地数据")
    failures = []
    for info in all_infos():
        try:
            get_provider(info.id).clear_account()
        except Exception as exc:  # noqa: BLE001
            failures.append(info.id)
            log.warning("清空 %s 内存会话失败: %s", info.id, type(exc).__name__)
    store.wipe()
    _stream_cache.clear()
    log.info("本地数据已清空（失败平台: %s）", failures or "无")
    return {"ok": True, "failedProviders": failures}


# --------------------------------------------------------------------------
# static UI
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(settings.web_dir / "index.html")


app.mount("/static", StaticFiles(directory=str(settings.web_dir)), name="static")


@app.exception_handler(ProviderError)
def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(status_code=424, content={"detail": _safe_detail(exc)})
