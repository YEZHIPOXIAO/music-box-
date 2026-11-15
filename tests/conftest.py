"""Shared fixtures and process-wide test setup.

Import this before any ``musicbox`` module: the app builds its Store (and
therefore its key file and SQLite database) at import time, so the data
directory has to be redirected first.
"""

from __future__ import annotations

import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer

# Point the app at a throwaway directory *outside* the project. Using a bare
# mkdtemp() fell back to the current directory on this machine, littering the
# repo root with dozens of musicbox-*/ dirs (which then got copied into every
# mutation sandbox).
def _scratch_root() -> str:
    """A writable directory for per-run test data.

    On this machine the system TEMP (``%LOCALAPPDATA%\\Temp``) is not writable
    (WinError 5), and ``tempfile.gettempdir()`` therefore falls back to the
    *current directory*. A bare mkdtemp() then either blocks retrying or
    scatters ``musicbox-*`` dirs through the repo — which, in the mutation
    harness, get copied into each sandbox and recurse forever.

    So: prefer a git-ignored folder inside the project. It is fast, writable,
    and never participates in the copy because the ignore list excludes it.
    """
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_scratch")
    os.makedirs(root, exist_ok=True)
    return root


_TEST_HOME = tempfile.mkdtemp(prefix="mb-home-", dir=_scratch_root())
os.environ["MUSICBOX_HOME"] = _TEST_HOME

import pytest  # noqa: E402


class _RecorderHandler(BaseHTTPRequestHandler):
    # HTTP/1.1 keeps the connection alive so requests reuses it; without this
    # the server closes per-request and the client may retry unexpectedly.
    protocol_version = "HTTP/1.1"

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)  # drain the body so the next request parses
        # Record before replying, so a client that disconnects right after
        # reading the status line still shows up in the log.
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        body = self.server.body
        self.send_response(self.server.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _record
    do_POST = _record
    do_HEAD = _record

    def handle_one_request(self) -> None:
        # A keep-alive peer closing mid-read is normal, not an error. The
        # default implementation prints a traceback to stderr for it, which
        # buries real failures in test output.
        try:
            super().handle_one_request()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def log_message(self, *args):  # noqa: ANN002 - silence stderr noise
        pass


class Recorder:
    """A local plaintext HTTP server that records every request it receives.

    ``status``/``body`` are configurable so failure paths (403 from a CDN,
    an error page quoting a token) can be exercised too.
    """

    def __init__(self, status: int = 200, body: bytes = b"ok") -> None:
        self._server = ThreadingTCPServer(("127.0.0.1", 0), _RecorderHandler)
        self._server.daemon_threads = True
        self._server.seen = []
        self._server.status = status
        self._server.body = body
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mb-recorder", daemon=True
        )

    def __enter__(self) -> "Recorder":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:  # noqa: ANN002
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    @property
    def base(self) -> str:
        host, port = self._server.server_address[0], self._server.server_address[1]
        return f"http://{host}:{port}"

    @property
    def seen(self) -> list:
        return self._server.seen

    def cookie_header_for(self, path: str) -> str:
        """Return the cookie of the *most recent* request to ``path``.

        Scanning forward returned the first match, so a test that re-binds an
        account and asserts the new token was sent would silently assert on the
        stale one. Newest-wins is the only safe default.
        """
        for _method, seen_path, headers in reversed(self.seen):
            if seen_path == path:
                return headers.get("Cookie", "")
        raise AssertionError(f"recorder never saw {path}; saw {[p for _, p, _ in self.seen]}")

    def all_cookies_for(self, path: str) -> list:
        """Every cookie seen for ``path``, oldest first (for replay assertions)."""
        return [h.get("Cookie", "") for _m, p, h in self.seen if p == path]

    def clear(self) -> None:
        self.seen.clear()


@pytest.fixture()
def recorder():
    with Recorder() as rec:
        yield rec


@pytest.fixture()
def failing_recorder():
    """A recorder that answers 403 with a body quoting a secret."""
    with Recorder(status=403, body=b"<html>vkey=SECRETVKEY999</html>") as rec:
        yield rec


@pytest.fixture()
def temp_home(tmp_path, monkeypatch):
    """Point the app's data dir at a scratch directory (key + db live there)."""
    from musicbox import config as cfg

    monkeypatch.setenv("MUSICBOX_HOME", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    return tmp_path
