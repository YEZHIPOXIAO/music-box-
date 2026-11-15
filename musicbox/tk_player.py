"""A dependency-free native player built on tkinter.

Why this exists: the WebView shell needs the Edge WebView2 runtime. On a
machine without it (locked-down Windows, some VMs) the app should still open a
window rather than dying. This UI talks to the same local API, so behaviour,
account binding and quality handling are identical.

Audio playback uses the OS default handler via a tiny local HTTP proxy — tkinter
has no audio engine, and shipping one would add a heavy dependency.
"""

from __future__ import annotations

import http.client
import json
import threading
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import messagebox, ttk
from typing import Any

# Palette mirrors web/style.css so the two front-ends feel like one product.
BG = "#0d1117"
BG_SOFT = "#151b24"
BG_CARD = "#1a212c"
LINE = "#263040"
TEXT = "#e6edf3"
MUTED = "#8b98a9"
ACCENT = "#31c27c"
DANGER = "#f0553d"

PROVIDER_NAMES = {"qq": "QQ音乐", "kugou": "酷狗音乐", "kuwo": "酷我音乐"}
QUALITY_LABELS = [("自动（最高可用）", "auto"), ("无损", "flac"), ("320K", "320"), ("128K", "128")]


class Api:
    """Thin client for the local FastAPI app.

    Reuses one keep-alive connection: creating a fresh TCP connection for every
    keystroke-driven search is measurable on Windows, where loopback handshakes
    cost ~1 ms and the socket churn keeps the OS timer busy.
    """

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def _connection(self) -> http.client.HTTPConnection:
        if self._conn is None:
            parsed = urllib.parse.urlsplit(self.base)
            self._conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=30)
        return self._conn

    def _call(self, path: str, method: str = "GET", payload: Any = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json", "Connection": "keep-alive"}
        with self._lock:  # one connection, so calls are serialised
            for attempt in (1, 2):
                try:
                    conn = self._connection()
                    conn.request(method, path, body=body, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    if resp.status >= 400:
                        try:
                            detail = json.loads(raw).get("detail", "")
                        except Exception:  # noqa: BLE001
                            detail = raw.decode("utf-8", "replace")[:120]
                        raise RuntimeError(detail or f"HTTP {resp.status}")
                    return json.loads(raw or b"null")
                except (http.client.HTTPException, OSError):
                    # A stale keep-alive socket is normal; reconnect once.
                    self.close()
                    if attempt == 2:
                        raise RuntimeError("无法连接本地服务") from None
        return None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    def get(self, path: str) -> Any:
        return self._call(path)

    def post(self, path: str, payload: Any) -> Any:
        return self._call(path, "POST", payload)

    def delete(self, path: str) -> Any:
        return self._call(path, "DELETE")


def _fmt_time(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "0:00"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


class MusicBoxApp(tk.Tk):
    """Main window: search, results, playlists, accounts."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.api = Api(base_url)
        self.title("MusicBox")
        self.geometry("1080x720")
        self.minsize(880, 600)
        self.configure(bg=BG)
        self._tracks: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._quality = tk.StringVar(value="auto")
        self._filter = tk.StringVar(value="all")

        self._build_style()
        self._build_layout()
        self._load_providers()

    # -- styling ---------------------------------------------------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("TButton", background=BG_CARD, foreground=TEXT, borderwidth=0, padding=(10, 6))
        style.map("TButton", background=[("active", LINE)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#06231a",
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#28a86a")])
        style.configure("TEntry", fieldbackground=BG_CARD, foreground=TEXT, borderwidth=0)
        style.configure("TCombobox", fieldbackground=BG_CARD, background=BG_CARD, foreground=TEXT)
        style.configure("Treeview", background=BG_CARD, fieldbackground=BG_CARD, foreground=TEXT,
                        borderwidth=0, rowheight=26)
        style.configure("Treeview.Heading", background=BG_SOFT, foreground=MUTED, borderwidth=0)
        style.map("Treeview", background=[("selected", "#22303f")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_SOFT, foreground=MUTED, padding=(14, 7))
        style.map("TNotebook.Tab", background=[("selected", BG_CARD)], foreground=[("selected", TEXT)])

    # -- layout ----------------------------------------------------------
    def _build_layout(self) -> None:
        top = ttk.Frame(self, style="TFrame", padding=(16, 12, 16, 8))
        top.pack(fill="x")
        ttk.Label(top, text="🎵 MusicBox", style="Title.TLabel").pack(side="left")

        self.quality_box = ttk.Combobox(
            top, values=[label for label, _ in QUALITY_LABELS], state="readonly", width=16
        )
        self.quality_box.current(0)
        self.quality_box.pack(side="right", padx=(8, 0))
        self.quality_box.bind("<<ComboboxSelected>>", self._on_quality)
        ttk.Label(top, text="音质", style="Muted.TLabel").pack(side="right")

        self.provider_box = ttk.Combobox(top, values=["全部平台"], state="readonly", width=14)
        self.provider_box.current(0)
        self.provider_box.pack(side="right", padx=(8, 16))
        self.provider_box.bind("<<ComboboxSelected>>", self._on_filter)
        ttk.Label(top, text="来源", style="Muted.TLabel").pack(side="right")

        bar = ttk.Frame(self, style="TFrame", padding=(16, 0, 16, 8))
        bar.pack(fill="x")
        self.query = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.query, font=("Microsoft YaHei UI", 11))
        entry.pack(side="left", fill="x", expand=True, ipady=6)
        entry.bind("<Return>", lambda _e: self.do_search())
        ttk.Button(bar, text="搜索", style="Accent.TButton", command=self.do_search).pack(
            side="left", padx=(8, 0)
        )

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        self._build_search_tab()
        self._build_playlist_tab()
        self._build_account_tab()

        status = ttk.Frame(self, style="TFrame", padding=(16, 0, 16, 10))
        status.pack(fill="x")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status, style="Muted.TLabel").pack(side="left")
        self.now_playing = tk.StringVar(value="未在播放")
        ttk.Label(status, textvariable=self.now_playing, style="Muted.TLabel").pack(side="right")

    def _build_search_tab(self) -> None:
        frame = ttk.Frame(self.notebook, style="TFrame", padding=8)
        self.notebook.add(frame, text="搜索")
        columns = ("title", "artist", "album", "time", "source")
        self.results = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        for key, label, width in (
            ("title", "歌曲", 300),
            ("artist", "歌手", 180),
            ("album", "专辑", 200),
            ("time", "时长", 70),
            ("source", "来源", 100),
        ):
            self.results.heading(key, text=label)
            self.results.column(key, width=width, anchor="w" if key != "time" else "e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.results.yview)
        self.results.configure(yscrollcommand=scroll.set)
        self.results.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.results.bind("<Double-1>", lambda _e: self.play_selected())

        actions = ttk.Frame(frame, style="TFrame")
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(actions, text="▶ 播放", style="Accent.TButton", command=self.play_selected).pack(side="left")
        ttk.Button(actions, text="＋ 加入歌单", command=self.add_selected).pack(side="left", padx=8)

    def _build_playlist_tab(self) -> None:
        frame = ttk.Frame(self.notebook, style="TFrame", padding=8)
        self.notebook.add(frame, text="我的歌单")

        row = ttk.Frame(frame, style="TFrame")
        row.pack(fill="x", pady=(0, 8))
        self.new_playlist = tk.StringVar()
        ttk.Entry(row, textvariable=self.new_playlist, width=28).pack(side="left", ipady=4)
        ttk.Button(row, text="新建歌单", command=self.create_playlist).pack(side="left", padx=8)
        ttk.Button(row, text="刷新", command=self.load_playlists).pack(side="left")

        self.playlist_names: dict[str, int] = {}
        self.playlist_box = ttk.Combobox(row, values=[], state="readonly", width=24)
        self.playlist_box.pack(side="left", padx=(16, 8))
        self.playlist_box.bind("<<ComboboxSelected>>", lambda _e: self.open_playlist())

        self.playlist_tracks = ttk.Treeview(
            frame, columns=("title", "artist"), show="headings", selectmode="browse"
        )
        self.playlist_tracks.heading("title", text="歌曲")
        self.playlist_tracks.heading("artist", text="歌手")
        self.playlist_tracks.column("title", width=420)
        self.playlist_tracks.column("artist", width=220)
        self.playlist_tracks.pack(fill="both", expand=True)
        self.playlist_tracks.bind("<Double-1>", lambda _e: self.play_from_playlist())

    def _build_account_tab(self) -> None:
        frame = ttk.Frame(self.notebook, style="TFrame", padding=12)
        self.notebook.add(frame, text="账号")
        ttk.Label(
            frame,
            text="粘贴浏览器 Cookie 即可绑定。Cookie 加密保存在本机，不会上传。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        self.account_rows: dict[str, dict[str, Any]] = {}
        for pid in ("qq", "kugou", "kuwo"):
            card = ttk.Frame(frame, style="Card.TFrame", padding=12)
            card.pack(fill="x", pady=6)
            head = ttk.Frame(card, style="Card.TFrame")
            head.pack(fill="x")
            name = ttk.Label(head, text=PROVIDER_NAMES[pid], style="TLabel")
            name.pack(side="left")
            state = ttk.Label(head, text="未绑定", style="Muted.TLabel")
            state.pack(side="right")

            entry_row = ttk.Frame(card, style="Card.TFrame")
            entry_row.pack(fill="x", pady=(8, 0))
            var = tk.StringVar()
            entry = ttk.Entry(entry_row, textvariable=var, show="•")
            entry.pack(side="left", fill="x", expand=True, ipady=4)
            ttk.Button(entry_row, text="保存", command=lambda p=pid: self.save_cookie(p)).pack(
                side="left", padx=6
            )
            ttk.Button(entry_row, text="解绑", command=lambda p=pid: self.clear_cookie(p)).pack(
                side="left"
            )
            self.account_rows[pid] = {"state": state, "entry": var}

    # -- helpers ---------------------------------------------------------
    def _run_async(self, work, done) -> None:
        """Run a blocking API call off the UI thread."""

        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda e=exc: self._fail(e))
                return
            self.after(0, lambda r=result: done(r))

        threading.Thread(target=runner, daemon=True).start()

    def _fail(self, exc: Exception) -> None:
        self.status.set(f"出错：{exc}")

    # -- providers / accounts --------------------------------------------
    def _load_providers(self) -> None:
        def work():
            return self.api.get("/api/providers")

        def done(data):
            names = ["全部平台"] + [
                PROVIDER_NAMES.get(p["id"], p["id"]) for p in data.get("providers", [])
            ]
            self.provider_box.configure(values=names)
            for provider in data.get("providers", []):
                row = self.account_rows.get(provider["id"])
                if not row:
                    continue
                if provider.get("loggedIn"):
                    hint = "已绑定" if provider.get("readyForVip") else "已绑定（缺会员凭证）"
                    row["state"].configure(text=hint, foreground=ACCENT)
                else:
                    row["state"].configure(text="未绑定", foreground=MUTED)

        self._run_async(work, done)

    def save_cookie(self, provider: str) -> None:
        cookie = self.account_rows[provider]["entry"].get().strip()
        if not cookie:
            self.status.set("请先粘贴 Cookie")
            return

        def work():
            return self.api.post(f"/api/account/{provider}", {"cookie": cookie})

        def done(result):
            self.account_rows[provider]["entry"].set("")
            self.status.set(f"{PROVIDER_NAMES[provider]} 绑定成功（{result.get('cookieCount')} 个 Cookie）")
            self._load_providers()

        self._run_async(work, done)

    def clear_cookie(self, provider: str) -> None:
        if not messagebox.askyesno("解绑", f"解绑 {PROVIDER_NAMES[provider]}？本地保存的 Cookie 会被删除。"):
            return

        def work():
            return self.api.delete(f"/api/account/{provider}")

        def done(_result):
            self.status.set(f"已解绑 {PROVIDER_NAMES[provider]}")
            self._load_providers()

        self._run_async(work, done)

    # -- search ----------------------------------------------------------
    def _on_quality(self, _event=None) -> None:
        index = self.quality_box.current()
        self._quality.set(QUALITY_LABELS[index][1])

    def _on_filter(self, _event=None) -> None:
        label = self.provider_box.get()
        self._filter.set("all" if label == "全部平台" else
                         next((k for k, v in PROVIDER_NAMES.items() if v == label), "all"))

    def do_search(self) -> None:
        keyword = self.query.get().strip()
        if not keyword:
            self.status.set("请输入关键词")
            return
        self.status.set(f"正在搜索「{keyword}」…")

        def work():
            query = urllib.parse.quote(keyword)
            return self.api.get(f"/api/search?q={query}&provider={self._filter.get()}&limit=40")

        def done(data):
            self._tracks = data.get("tracks", [])
            self.results.delete(*self.results.get_children())
            for track in self._tracks:
                self.results.insert(
                    "",
                    "end",
                    values=(
                        track.get("title", ""),
                        track.get("artistText", ""),
                        track.get("album", ""),
                        _fmt_time(track.get("duration", 0)),
                        PROVIDER_NAMES.get(track.get("provider"), track.get("provider")),
                    ),
                )
            errors = data.get("errors") or []
            suffix = f"（{len(errors)} 个平台失败）" if errors else ""
            self.status.set(f"「{keyword}」共 {len(self._tracks)} 首{suffix}")

        self._run_async(work, done)

    def _selected_track(self) -> dict[str, Any] | None:
        selection = self.results.selection()
        if not selection:
            return None
        return self._tracks[self.results.index(selection[0])]

    # -- playback --------------------------------------------------------
    def play_selected(self) -> None:
        track = self._selected_track()
        if not track:
            self.status.set("请先选中一首歌")
            return
        self._play(track)

    def _play(self, track: dict[str, Any]) -> None:
        self._current = track
        self.now_playing.set(f"♪ {track.get('title')} - {track.get('artistText', '')}")
        self.status.set("正在解析播放地址…")
        extra = track.get("extra") or {}
        params = [f"quality={self._quality.get()}"]
        if track.get("duration"):
            params.append(f"duration={int(track['duration'])}")
        for name in ("hash320", "hashFlac"):
            if extra.get(name):
                params.append(f"{name}={extra[name]}")
        if extra.get("hasLossless"):
            params.append("hasLossless=true")

        def resolve():
            # /api/stream returns metadata; the audio itself is a binary
            # response, so tkinter cannot decode it and we hand the proxy URL
            # to the OS player instead of shipping an audio engine.
            path = f"/api/stream/{track['provider']}/{track['id']}?{'&'.join(params)}"
            return self.api.get(path)["src"]

        def play(src):
            webbrowser.open(f"{self.api.base}{src}")
            self.status.set("已交给系统播放器")
            self.api.post("/api/history", track)

        self._run_async(resolve, play)

    # -- playlists -------------------------------------------------------
    def load_playlists(self) -> None:
        def work():
            return self.api.get("/api/playlists")

        def done(data):
            names = [p["name"] for p in data.get("playlists", [])]
            self.playlist_names = {p["name"]: p["id"] for p in data.get("playlists", [])}
            self.playlist_box.configure(values=names)
            self.status.set(f"{len(names)} 个歌单")

        self._run_async(work, done)

    def create_playlist(self) -> None:
        name = self.new_playlist.get().strip()
        if not name:
            self.status.set("请输入歌单名")
            return

        def work():
            return self.api.post("/api/playlists", {"name": name})

        def done(_result):
            self.new_playlist.set("")
            self.status.set(f"已创建「{name}」")
            self.load_playlists()

        self._run_async(work, done)

    def add_selected(self) -> None:
        track = self._selected_track()
        if not track:
            self.status.set("请先选中一首歌")
            return
        name = self.playlist_box.get()
        playlist_id = self.playlist_names.get(name)
        if not playlist_id:
            self.status.set("请先在「我的歌单」里选择或新建一个歌单")
            self.notebook.select(1)
            self.load_playlists()
            return

        def work():
            return self.api.post(f"/api/playlists/{playlist_id}/items", track)

        def done(result):
            self.status.set("已在歌单中" if result.get("duplicate") else f"已加入「{name}」")

        self._run_async(work, done)

    def open_playlist(self) -> None:
        playlist_id = self.playlist_names.get(self.playlist_box.get())
        if not playlist_id:
            return

        def work():
            return self.api.get(f"/api/playlists/{playlist_id}")

        def done(data):
            self._playlist_items = data.get("tracks", [])
            self.playlist_tracks.delete(*self.playlist_tracks.get_children())
            for track in self._playlist_items:
                self.playlist_tracks.insert(
                    "", "end", values=(track.get("title", ""), track.get("artistText", ""))
                )
            self.status.set(f"{len(self._playlist_items)} 首")

        self._run_async(work, done)

    def play_from_playlist(self) -> None:
        selection = self.playlist_tracks.selection()
        if not selection:
            return
        items = getattr(self, "_playlist_items", [])
        index = self.playlist_tracks.index(selection[0])
        if 0 <= index < len(items):
            self._play(items[index])


def run(base_url: str) -> int:  # pragma: no cover - needs a display
    app = MusicBoxApp(base_url)
    app.mainloop()
    return 0
