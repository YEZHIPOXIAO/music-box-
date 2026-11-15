"""Command line entry point: ``musicbox`` / ``python -m musicbox``."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

from . import __version__
from .config import settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musicbox",
        description="聚合音乐播放器：绑定 QQ音乐 / 酷狗 / 酷我 账号，本地运行。",
    )
    parser.add_argument("--host", default=os.environ.get("MUSICBOX_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MUSICBOX_PORT", 8777)))
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    parser.add_argument(
        "--desktop",
        action="store_true",
        help="打开原生桌面窗口（默认；无图形环境时用 --web）",
    )
    parser.add_argument(
        "--ui",
        choices=("auto", "webview", "tk", "browser"),
        default="auto",
        help="桌面窗口后端：webview（原生窗口）/ tk（纯 tkinter）/ browser（浏览器标签页）",
    )
    parser.add_argument("--version", action="version", version=f"musicbox {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings.ensure_dirs()

    try:
        import uvicorn
    except ImportError:  # pragma: no cover
        print("缺少依赖：请先执行 pip install -r requirements.txt", file=sys.stderr)
        return 1

    # Desktop window is the default experience; --ui browser keeps the old
    # "open a tab" behaviour and is what you want on a headless machine.
    if args.ui != "browser":
        from . import desktop

        try:
            return desktop.main(force="" if args.ui == "auto" else args.ui, port=args.port)
        except Exception as exc:  # noqa: BLE001 - never leave the user with nothing
            print(f"桌面窗口启动失败（{exc}），改用浏览器模式。", file=sys.stderr)

    url = f"http://{args.host}:{args.port}"
    print("MusicBox", __version__)
    print(f"  数据目录: {settings.data_dir}")
    print(f"  打开:     {url}")
    print("  隐私:     本机保存账号；业务请求会访问平台及CDN，请勿对外暴露服务")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    uvicorn.run(
        "musicbox.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=os.environ.get("MUSICBOX_LOG", "info"),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
