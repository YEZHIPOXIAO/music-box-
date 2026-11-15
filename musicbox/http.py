"""HTTP helpers with retries, cookie support and JSON conveniences."""

from __future__ import annotations

import json
import random
import threading
import time
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from .config import settings


class HttpError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, url: str = ""):
        super().__init__(message)
        self.status = status
        self.url = url

    @property
    def public_message(self) -> str:
        """Safe to show a browser: never includes the URL (it carries vkeys)."""
        return f"上游请求失败（{self.status or '网络错误'}）"


class ScopedCookieJar(requests.cookies.RequestsCookieJar):
    """Server refreshes replace the same normalized domain/path/name tuple.

    Requests distinguishes `.www.kuwo.cn` from `www.kuwo.cn` as storage keys,
    unlike browser cookie identity. Preserve the new cookie's host-only flag.
    """
    def set_cookie(self, cookie, *args, **kwargs):
        for old in list(self):
            if (old.name == cookie.name and old.path == cookie.path
                    and old.domain.lstrip('.').lower() == cookie.domain.lstrip('.').lower()
                    and old.domain != cookie.domain):
                self.clear(old.domain, old.path, old.name)
        return super().set_cookie(cookie, *args, **kwargs)


class CredentialSession(requests.Session):
    """Enforce cookie scope at send-time, including EVERY redirect hop.

    Cookies live in a scoped jar. CDN and unrelated hosts never receive them,
    even if requests rebuilds its Cookie header while following a redirect.
    """
    def __init__(self, credential_hosts: tuple[str, ...] = ()):
        super().__init__()
        self.credential_hosts = credential_hosts
        self.cookies = ScopedCookieJar()

    def send(self, request, **kwargs):
        parsed = urlparse(request.url)
        if parsed.scheme not in {"http", "https"}:
            raise requests.RequestException("不支持的请求协议")
        request.headers.pop("Cookie", None)
        if parsed.scheme == "https" and (parsed.hostname or "").lower() in self.credential_hosts:
            scoped = requests.cookies.RequestsCookieJar()
            for cookie in self.cookies:
                if cookie.domain:
                    scoped.set_cookie(cookie)
            request.prepare_cookies(scoped)
        else:
            request.headers["Cookie"] = ""
        return super().send(request, **kwargs)


def _is_secure(url: str) -> bool:
    return urlparse(url).scheme == "https"


class HttpClient:
    """A thin retrying wrapper around ``requests.Session``.

    Each provider gets its own instance so account cookies never leak
    between platforms.

    Cookies are only ever sent over HTTPS: several providers still expose
    plain-HTTP endpoints, and sending an account token there would leak it to
    anyone on the same network.

    ``requests.Session`` is not thread-safe, and provider calls run from a
    thread pool, so every request is serialised through one lock. Upstream
    traffic for a single platform is modest, and correctness beats throughput
    when the thing being corrupted is a cookie jar.
    """

    def __init__(
        self,
        *,
        timeout: float | None = None,
        retries: int | None = None,
        user_agent: str | None = None,
        cookie_domain: str = "",
        credential_hosts: tuple[str, ...] = (),
    ) -> None:
        self.timeout = settings.http_timeout if timeout is None else timeout
        self.retries = settings.http_retries if retries is None else retries
        self._lock = threading.RLock()
        self._pinned: dict[str, str] = {}
        self.cookie_domain = cookie_domain
        self.session = CredentialSession(credential_hosts)
        self.session.headers.update(
            {
                "User-Agent": user_agent or settings.user_agent,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
        )
        self.session.trust_env = True
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=16, max_retries=0
        )
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    # -- cookies ---------------------------------------------------------
    def set_cookies(self, cookie_string: str) -> dict[str, str]:
        """Replace the account's cookies, keeping caller-declared device ids.

        A re-login must not leave the previous account's ``uin``/token behind,
        so the jar is cleared first. Cookies registered with
        :meth:`pin_cookies` survive that wipe: they are device identifiers the
        provider needs on every request, not account credentials.
        """
        parsed: dict[str, str] = {}
        for chunk in cookie_string.replace("\n", ";").split(";"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            name, value = name.strip(), value.strip().strip('"')
            if name:
                parsed[name] = value
        if parsed:
            with self._lock:
                self.session.cookies.clear()
                if self._pinned:
                    self._install_values(self._pinned)
                self._install_values(parsed)
        return parsed

    def pin_cookies(self, cookies: Mapping[str, str]) -> None:
        """Register device cookies that must survive a re-login wipe."""
        self._pinned = dict(cookies)
        with self._lock:
            self._install_values(self._pinned)

    def _install_values(self, values: Mapping[str, str]) -> None:
        for name, value in values.items():
            self.session.cookies.set(name, value, domain=self.cookie_domain, path="/", secure=True)

    def set_cookie_records(self, records: list[dict[str, Any]]) -> None:
        with self._lock:
            self.session.cookies.clear()
            self._install_values(self._pinned)
            for item in records:
                self.session.cookies.set(item["name"], item["value"], domain=item["domain"],
                                         path=item.get("path", "/"), secure=True)

    def cookie_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"name": c.name, "value": c.value, "domain": c.domain,
                     "path": c.path, "secure": c.secure} for c in self.session.cookies]

    def cookies(self, host: str = "", request_path: str = "/") -> dict[str, str]:
        from .credentials import credential_values
        with self._lock:
            if host:
                return credential_values(self.cookie_records(), host, request_path)
            return requests.utils.dict_from_cookiejar(self.session.cookies)

    def close(self) -> None:
        with self._lock:
            self.session.close()

    def clear_cookies(self) -> None:
        """Drop account cookies but keep the pinned device identifiers."""
        with self._lock:
            self.session.cookies.clear()
            if self._pinned:
                self._install_values(self._pinned)

    # -- verbs -----------------------------------------------------------
    def _scrub_insecure(self, url: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Strip credentials from a request bound for a plaintext endpoint.

        Shared by ``request()`` and ``stream()``. It used to live only in
        ``request()``, so every CDN probe and audio fetch leaked the account
        cookie over http:// — and the tests missed it because they never
        observed a real socket.
        """
        if _is_secure(url):
            return kwargs
        kwargs["cookies"] = {}
        headers = kwargs.get("headers")
        headers = dict(headers) if isinstance(headers, Mapping) else {}
        headers["Cookie"] = ""
        kwargs["headers"] = headers
        return kwargs

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        # Never hand an account cookie to a plaintext endpoint.
        kwargs = self._scrub_insecure(url, kwargs)
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with self._lock:
                    resp = self.session.request(method, url, timeout=self.timeout, **kwargs)
                if resp.status_code >= 500 and attempt < self.retries:
                    resp.close()
                    last = HttpError(f"server error {resp.status_code}", resp.status_code, url)
                    time.sleep(0.4 * (attempt + 1) + random.random() * 0.2)
                    continue
                return resp
            except requests.RequestException as exc:  # network hiccup
                last = exc
                if attempt >= self.retries:
                    break
                time.sleep(0.4 * (attempt + 1) + random.random() * 0.2)
        raise HttpError(f"request failed: {last}", url=url)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def get_text(self, url: str, encoding: str | None = None, **kwargs: Any) -> str:
        resp = self.get(url, **kwargs)
        if resp.status_code >= 400:
            raise HttpError(f"HTTP {resp.status_code}", resp.status_code, url)
        if encoding:
            resp.encoding = encoding
        return resp.text

    def get_json(self, url: str, **kwargs: Any) -> Any:
        text = self.get_text(url, **kwargs)
        return load_json_loose(text)

    def post_json(self, url: str, payload: Mapping[str, Any], **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", {}) or {}
        headers.setdefault("Content-Type", "application/json")
        resp = self.post(url, data=json.dumps(payload), headers=headers, **kwargs)
        if resp.status_code >= 400:
            raise HttpError(f"HTTP {resp.status_code}", resp.status_code, url)
        return load_json_loose(resp.text)

    def stream(self, url: str, headers: Mapping[str, str] | None = None, **kwargs: Any):
        """Open a streaming GET (caller closes the response).

        The lock is held only while the request is issued, not while the body
        is consumed: a long download must not block the provider's other calls.
        """
        kwargs = self._scrub_insecure(url, {**kwargs, "headers": dict(headers or {})})
        with self._lock:
            try:
                return self.session.get(url, timeout=max(self.timeout, 30.0), stream=True, **kwargs)
            except requests.RequestException as exc:
                raise HttpError("音频请求失败", url=url) from exc


def load_json_loose(text: str) -> Any:
    """Parse JSON that providers sometimes wrap in JSONP or single quotes."""
    text = text.strip()
    if not text:
        raise ValueError("empty response")
    if text.startswith("\ufeff"):
        text = text[1:]
    # JSONP: callback({...})
    if not text.startswith(("{", "[")) and "(" in text and text.endswith(")"):
        inner = text[text.index("(") + 1 : text.rindex(")")].strip()
        if inner:
            text = inner
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Kuwo's legacy API answers with Python-style dict literals.
        import ast

        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(f"unparseable response: {text[:120]}") from exc
