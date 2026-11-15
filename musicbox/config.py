"""Runtime configuration.

Everything is overridable through environment variables so the package never
depends on a machine-specific path.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def default_data_dir() -> Path:
    """Per-user data directory, independent of the install location."""
    base = os.environ.get("MUSICBOX_HOME")
    if base:
        return Path(base).expanduser().resolve()
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "MusicBox"
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]) / "musicbox"
    return Path.home() / ".local" / "share" / "musicbox"


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    http_timeout: float = field(default_factory=lambda: _env_float("MUSICBOX_TIMEOUT", 12.0))
    http_retries: int = field(default_factory=lambda: _env_int("MUSICBOX_RETRIES", 2))
    stream_cache_ttl: int = field(default_factory=lambda: _env_int("MUSICBOX_STREAM_TTL", 600))
    # Upper bound on cached stream resolutions. Each entry is tiny (~200 B),
    # but the cache is pure overhead for a single local user, so keep it small
    # enough to stay out of the way: 512 covers a long album plus history.
    stream_cache_max: int = field(default_factory=lambda: _env_int("MUSICBOX_CACHE_MAX", 512))
    user_agent: str = DEFAULT_UA
    default_quality: str = os.environ.get("MUSICBOX_QUALITY", "auto")
    # Loopback-only by default; the Host header guard refuses anything else
    # unless the user explicitly opts in (needed for LAN access).
    allow_external_host: bool = os.environ.get("MUSICBOX_ALLOW_HOST", "") in {"1", "true", "yes"}
    # Host header allowlist. "testserver" is the name Starlette's TestClient
    # uses and can never arrive over a real socket.
    allowed_hosts: tuple[str, ...] = (
        "127.0.0.1",
        "localhost",
        "::1",
        "[::1]",
        "testserver",
    ) + tuple(
        h.strip() for h in os.environ.get("MUSICBOX_HOSTS", "").split(",") if h.strip()
    )
    # Providers can be disabled individually, e.g. MUSICBOX_DISABLE=kuwo
    disabled_providers: tuple[str, ...] = tuple(
        p.strip() for p in os.environ.get("MUSICBOX_DISABLE", "").split(",") if p.strip()
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "musicbox.sqlite3"

    @property
    def key_path(self) -> Path:
        return self.data_dir / ".secret.key"

    @property
    def web_dir(self) -> Path:
        return Path(__file__).resolve().parent / "web"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def allow_external_host_effective(self) -> bool:
        return self.allow_external_host

    def privacy_report(self) -> dict[str, object]:
        return {
            "telemetry": False,
            "cloud": False,
            "proxyAudio": True,
            "hostGuard": not self.allow_external_host,
            "secureCookiesOnly": True,
        }


settings = Settings()
