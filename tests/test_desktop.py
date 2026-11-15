"""Tests for the desktop shell and the resource optimisations.

These cover behaviour that is easy to regress silently: port selection, server
lifecycle, the keep-alive API client, and cache bounds.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer

import pytest

from musicbox import desktop


# -- port selection ---------------------------------------------------------
def test_free_port_returns_a_bindable_port():
    port = desktop._free_port(0)
    assert 0 < port < 65536
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))  # must still be free


def test_free_port_falls_back_when_preferred_is_taken():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        port = desktop._free_port(busy)
    assert port != busy, "首选端口被占用时必须换一个"


# -- server lifecycle -------------------------------------------------------
def test_local_server_starts_and_serves_health():
    import urllib.request

    server = desktop.LocalServer(port=desktop._free_port(0))
    try:
        url = server.start(timeout=20)
        with urllib.request.urlopen(f"{url}/api/health", timeout=10) as resp:
            body = json.loads(resp.read())
        assert body["ok"] is True
    finally:
        server.stop()


def test_local_server_stop_is_idempotent():
    server = desktop.LocalServer(port=desktop._free_port(0))
    server.start(timeout=20)
    server.stop()
    server.stop()  # must not raise


# -- the tkinter API client ------------------------------------------------
class _Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        payload = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        payload = json.dumps({"echo": json.loads(body or b"null")}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):  # noqa: ANN002
        pass


@pytest.fixture()
def echo_server():
    srv = ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_api_reuses_one_connection(echo_server):
    """A fresh TCP connection per keystroke is the old, slower behaviour."""
    from musicbox.tk_player import Api

    api = Api(echo_server)
    try:
        api.get("/api/health")
        first = api._conn
        assert first is not None
        for _ in range(20):
            api.get("/api/health")
        assert api._conn is first, "连接未被复用：每次请求都新建了 socket"
    finally:
        api.close()


def test_api_surfaces_detail_from_error_body():
    """A 4xx must show the server's message, not a bare status code."""
    from musicbox.tk_player import Api

    class _Err(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            body = json.dumps({"detail": "需要会员"}).encode()
            self.send_response(424)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # noqa: ANN002
            pass

    srv = ThreadingTCPServer(("127.0.0.1", 0), _Err)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        api = Api(f"http://127.0.0.1:{srv.server_address[1]}")
        with pytest.raises(RuntimeError, match="需要会员"):
            api.get("/x")
        api.close()
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def test_api_post_roundtrip(echo_server):
    from musicbox.tk_player import Api

    api = Api(echo_server)
    try:
        assert api.post("/x", {"a": 1})["echo"] == {"a": 1}
    finally:
        api.close()


def test_api_reconnects_after_a_stale_socket(echo_server):
    """A server-side keep-alive timeout must not break the next call."""
    from musicbox.tk_player import Api

    api = Api(echo_server)
    try:
        api.get("/api/health")
        if api._conn is not None:
            api._conn.close()  # simulate the peer dropping the connection
            api._conn = api._conn  # keep the closed object to force the retry path
        assert api.get("/api/health")["path"] == "/api/health"
    finally:
        api.close()


# -- cache bounds -----------------------------------------------------------
def test_cache_respects_configured_max(client=None):
    from musicbox import app as app_module
    from musicbox.config import settings

    app_module._stream_cache.clear()
    limit = settings.stream_cache_max
    for i in range(limit + 50):
        app_module._stream_cache[("qq", f"t{i}", "auto", 0)] = (9e18 + i, {"src": "x"})
    app_module._prune_cache()
    assert len(app_module._stream_cache) <= limit
    app_module._stream_cache.clear()


def test_cache_max_has_a_floor(monkeypatch):
    """A nonsense MUSICBOX_CACHE_MAX must not disable caching entirely."""
    from musicbox import app as app_module
    from musicbox.config import settings

    monkeypatch.setattr(settings, "stream_cache_max", 0)
    app_module._stream_cache.clear()
    for i in range(40):
        app_module._stream_cache[("qq", f"t{i}", "auto", 0)] = (9e18, {"src": "x"})
    app_module._prune_cache()
    assert len(app_module._stream_cache) >= 16, "上限过低时应回退到 16 条下限"
    app_module._stream_cache.clear()


# -- desktop entry ----------------------------------------------------------
def test_desktop_imports_without_webview():
    """The tkinter path must not require pywebview at import time."""
    import importlib

    module = importlib.import_module("musicbox.desktop")
    assert hasattr(module, "run_tk")
    assert hasattr(module, "run_webview")


def test_tk_player_imports_without_a_display():
    """Importing the module must not create a Tk root (headless CI)."""
    import importlib

    module = importlib.import_module("musicbox.tk_player")
    assert hasattr(module, "MusicBoxApp")
    assert hasattr(module, "Api")
