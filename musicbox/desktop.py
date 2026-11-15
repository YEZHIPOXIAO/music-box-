"""Desktop shell: run MusicBox in a native window instead of a browser tab.

Two front-ends, chosen automatically:

* **pywebview** (preferred) — a real native window hosting the existing
  player UI via WebView2. Same UI, no browser chrome, no address bar.
* **tkinter** (fallback) — a self-contained native player that talks to the
  local HTTP API directly. Used when WebView2/pywebview is unavailable.

Both start the FastAPI server on a private loopback port, so the audio proxy
and cookie handling keep working exactly as before.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import webbrowser

import uvicorn

from .config import settings

log = logging.getLogger("musicbox.desktop")

WINDOW_TITLE = "MusicBox"
WINDOW_SIZE = (1180, 780)


def _free_port(preferred: int = 8777) -> int:
    """Find a loopback port, preferring the configured one."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", candidate))
                return sock.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("无法分配本地端口")


class LocalServer:
    """The API server, running on a daemon thread for the app's lifetime."""

    def __init__(self, port: int | None = None) -> None:
        self.port = port or _free_port()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 15.0) -> str:
        config = uvicorn.Config(
            "musicbox.app:app",
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        # uvicorn installs signal handlers in run(); disabling them lets it run
        # on a background thread without hijacking Ctrl+C from the GUI.
        self._server.install_signal_handlers = lambda: None
        self._thread = threading.Thread(
            target=self._server.run, name="musicbox-http", daemon=True
        )
        self._thread.start()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._server.started:
                return self.url
            time.sleep(0.05)
        raise RuntimeError(f"服务在 {timeout:.0f}s 内未能启动")

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def run_webview(url: str) -> int:
    """Native window hosting the player UI."""
    import webview  # imported here so the tkinter path needs no pywebview

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=(900, 620),
        background_color="#0d1117",
        text_select=False,
    )
    # gui=None lets pywebview pick EdgeChromium on Windows.
    webview.start(gui=None, debug=False)
    return 0 if window is not None else 1


def run_tk(url: str) -> int:  # pragma: no cover - requires a display
    """Self-contained tkinter player (no WebView2 required)."""
    from .tk_player import MusicBoxApp

    app = MusicBoxApp(url)
    app.mainloop()
    return 0


def main(*, force: str = "", port: int | None = None, browser: bool = False) -> int:
    """Entry point used by ``python -m musicbox --desktop``."""
    settings.ensure_dirs()
    server = LocalServer(port)
    url = server.start()
    log.info("MusicBox 桌面版已启动：%s", url)

    if browser:
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return 0
        finally:
            server.stop()

    try:
        mode = force
        if not mode:
            try:
                import webview  # noqa: F401

                mode = "webview"
            except ImportError:
                mode = "tk"
        if mode == "webview":
            try:
                return run_webview(url)
            except Exception as exc:  # noqa: BLE001 - fall back, never crash
                log.warning("WebView 启动失败（%s），改用 tkinter 界面", exc)
                mode = "tk"
        if mode == "tk":
            return run_tk(url)
        raise SystemExit(f"未知界面模式: {mode}")
    finally:
        server.stop()
