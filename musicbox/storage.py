"""Local encrypted store for account cookies, playlists and history.

Privacy design
--------------
* The application has no telemetry; normal provider/API/CDN requests leave the machine.
* Account cookies use Fernet by default. The local key is stored next to the
  database; same-user processes may read both. POSIX mode bits do not establish
  equivalent Windows ACL isolation.
* The key file is optional: set ``MUSICBOX_KEY`` to supply your own, or set
  ``MUSICBOX_NO_ENCRYPT=1`` to keep the database plain (not recommended).
* The browser never receives a cookie: the server proxies audio so provider
  URLs (which contain vkey/tokens) never reach the page either.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .config import settings


def _load_key() -> bytes:
    """Return the 32-byte key that encrypts account cookies.

    Created once with O_EXCL so two concurrent starts cannot each mint a key
    and overwrite each other's (which would make the first one's rows
    undecryptable).
    """
    env_key = os.environ.get("MUSICBOX_KEY")
    if env_key:
        # Derive a key from a passphrase with a real KDF, not a bare hash.
        return hashlib.scrypt(
            env_key.encode("utf-8"), salt=b"musicbox-v1", n=16384, r=8, p=1, dklen=32
        )
    settings.ensure_dirs()
    path = settings.key_path
    if path.exists():
        return _read_key(path)
    # O_BINARY matters on Windows: without it os.open() uses text mode and a
    # 0x0A byte in the key is rewritten as CRLF, turning 32 bytes into 33 and
    # making every password unusable.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:  # another process won the race
        return _read_key(path)
    try:
        os.write(fd, os.urandom(32))
    finally:
        os.close(fd)
    return _read_key(path)


def _read_key(path) -> bytes:
    key = path.read_bytes()
    if len(key) == 33 and b"\r\n" in key:  # repair a text-mode key from 1.0.0
        key = key.replace(b"\r\n", b"\n")
    if len(key) != 32:
        raise ValueError(f"密钥文件损坏（{len(key)} 字节，应为 32）：{path}")
    return key


class Cipher:
    """Authenticated encryption for stored account cookies.

    Uses Fernet (AES-128-CBC + HMAC-SHA256) when ``cryptography`` is present.
    The stdlib fallback is HMAC-SHA256 based (encrypt-then-MAC), never a
    prefix hash, and both schemes tag their output so a row written by one can
    always be read back.
    """

    def __init__(self) -> None:
        self.enabled = os.environ.get("MUSICBOX_NO_ENCRYPT", "") not in {"1", "true", "yes"}
        self.scheme = "plain"
        self._fernet = None
        self._key: bytes | None = None
        if not self.enabled:
            return
        try:
            from cryptography.fernet import Fernet

            self._key = _load_key()
            self._fernet = Fernet(base64.urlsafe_b64encode(self._key))
            self.scheme = "fernet"
        except ImportError:
            # cryptography missing: fall back to the stdlib construction
            self._key = _load_key()
            self.scheme = "hmac"
        except ValueError:
            # A damaged key file must surface, not silently downgrade the
            # scheme and then fail to read existing rows.
            raise
        except Exception:  # noqa: BLE001 - unexpected key file problem
            self._key = _load_key()
            self.scheme = "hmac"

    def _get_key(self) -> bytes:
        if self._key is None:
            self._key = _load_key()
        return self._key

    def encrypt(self, text: str) -> str:
        if not text:
            return ""
        if not self.enabled:
            return text
        if self._fernet is not None:
            return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")
        key = self._get_key()
        raw = text.encode("utf-8")
        nonce = os.urandom(16)
        stream = _keystream(key, nonce, len(raw))
        body = bytes(a ^ b for a, b in zip(raw, stream))
        tag = hmac.new(key, nonce + body, hashlib.sha256).hexdigest()
        return f"x2:{nonce.hex()}:{tag}:{base64.b64encode(body).decode('ascii')}"

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        if not self.enabled:
            return token
        if token.startswith("x2:"):
            return self._decrypt_hmac(token)
        if token.startswith("x1:"):  # legacy rows from an older build
            return self._decrypt_legacy(token)
        if self._fernet is None:
            raise ValueError("该账号用 Fernet 加密，但当前缺少 cryptography 依赖")
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

    def _decrypt_hmac(self, token: str) -> str:
        key = self._get_key()
        try:
            _, nonce_hex, tag, body_b64 = token.split(":", 3)
        except ValueError as exc:
            raise ValueError("账号数据格式已损坏") from exc
        nonce = bytes.fromhex(nonce_hex)
        raw = base64.b64decode(body_b64)
        expected = hmac.new(key, nonce + raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, tag):
            raise ValueError("账号数据校验失败（密钥已更换？）")
        stream = _keystream(key, nonce, len(raw))
        return bytes(a ^ b for a, b in zip(raw, stream)).decode("utf-8")

    def _decrypt_legacy(self, token: str) -> str:
        """Read the pre-1.0.1 XOR format so existing accounts survive."""
        key = self._get_key()
        _, tag, body = token.split(":", 2)
        raw = base64.b64decode(body)
        if hashlib.sha256(key + raw).hexdigest()[:16] != tag:
            raise ValueError("账号数据校验失败（密钥已更换？）")
        stream = _keystream(key, b"", len(raw))
        return bytes(a ^ b for a, b in zip(raw, stream)).decode("utf-8")


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(key + nonce + counter.to_bytes(8, "big")).digest()
        counter += 1
    return bytes(out[:length])


class Store:
    def __init__(self, path=None) -> None:
        settings.ensure_dirs()
        self.path = str(path or settings.db_path)
        self.cipher = Cipher()
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    provider   TEXT PRIMARY KEY,
                    cookie_enc TEXT NOT NULL,
                    nickname   TEXT DEFAULT '',
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlists (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS playlist_items (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    provider   TEXT NOT NULL,
                    track_id   TEXT NOT NULL,
                    payload    TEXT NOT NULL,
                    position   INTEGER NOT NULL,
                    UNIQUE(playlist_id, provider, track_id)
                );
                CREATE TABLE IF NOT EXISTS history (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider  TEXT NOT NULL,
                    track_id  TEXT NOT NULL,
                    payload   TEXT NOT NULL,
                    played_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_history_time ON history(played_at DESC);
                """
            )

    # -- accounts --------------------------------------------------------
    def save_account(self, provider: str, cookie: str, nickname: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO accounts(provider, cookie_enc, nickname, updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(provider) DO UPDATE SET
                    cookie_enc=excluded.cookie_enc,
                    nickname=excluded.nickname,
                    updated_at=excluded.updated_at
                """,
                (provider, self.cipher.encrypt(cookie), nickname, time.time()),
            )

    def load_account(self, provider: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT provider, cookie_enc, nickname, updated_at FROM accounts WHERE provider=?",
                (provider,),
            ).fetchone()
        if not row:
            return None
        try:
            cookie = self.cipher.decrypt(row["cookie_enc"])
        except Exception:  # noqa: BLE001 - corrupted / key rotated
            return {"provider": provider, "cookie": "", "broken": True, "nickname": row["nickname"]}
        return {
            "provider": row["provider"],
            "cookie": cookie,
            "nickname": row["nickname"],
            "updatedAt": row["updated_at"],
        }

    def accounts(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT provider, nickname, updated_at FROM accounts ORDER BY provider"
            ).fetchall()
        return [
            {"provider": r["provider"], "nickname": r["nickname"], "updatedAt": r["updated_at"]}
            for r in rows
        ]

    def delete_account(self, provider: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE provider=?", (provider,))
        return cur.rowcount > 0

    # -- playlists -------------------------------------------------------
    def create_playlist(self, name: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO playlists(name, created_at) VALUES(?,?)", (name, time.time())
            )
            return int(cur.lastrowid)

    def playlists(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.id, p.name, p.created_at,
                       (SELECT COUNT(*) FROM playlist_items i WHERE i.playlist_id=p.id) AS count
                FROM playlists p ORDER BY p.created_at
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def add_to_playlist(self, playlist_id: int, track: dict[str, Any]) -> bool:
        with self.connect() as conn:
            pos = conn.execute(
                "SELECT COALESCE(MAX(position),0)+1 FROM playlist_items WHERE playlist_id=?",
                (playlist_id,),
            ).fetchone()[0]
            try:
                conn.execute(
                    """
                    INSERT INTO playlist_items(playlist_id, provider, track_id, payload, position)
                    VALUES(?,?,?,?,?)
                    """,
                    (playlist_id, track["provider"], track["id"], json.dumps(track), pos),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def playlist_tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM playlist_items WHERE playlist_id=? ORDER BY position",
                (playlist_id,),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def remove_from_playlist(self, playlist_id: int, provider: str, track_id: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM playlist_items WHERE playlist_id=? AND provider=? AND track_id=?",
                (playlist_id, provider, track_id),
            )
        return cur.rowcount > 0

    def delete_playlist(self, playlist_id: int) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM playlist_items WHERE playlist_id=?", (playlist_id,))
            cur = conn.execute("DELETE FROM playlists WHERE id=?", (playlist_id,))
        return cur.rowcount > 0

    # -- history ---------------------------------------------------------
    def record_history(self, track: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO history(provider, track_id, payload, played_at) VALUES(?,?,?,?)",
                (track["provider"], track["id"], json.dumps(track), time.time()),
            )
            conn.execute(
                """
                DELETE FROM history WHERE id NOT IN (
                    SELECT id FROM history ORDER BY played_at DESC LIMIT 500
                )
                """
            )

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT payload, played_at FROM history ORDER BY played_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out = []
        for row in rows:
            item = json.loads(row["payload"])
            item["playedAt"] = row["played_at"]
            out.append(item)
        return out

    def clear_history(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM history")
        return cur.rowcount

    # -- privacy ---------------------------------------------------------
    def wipe(self) -> None:
        """Delete every local trace: accounts, playlists, history.

        SQLite keeps deleted rows in free pages, so the rows are vacuumed away
        as well — otherwise the "delete everything" promise would be false.
        """
        with self.connect() as conn:
            conn.executescript(
                "PRAGMA secure_delete=ON;"
                "DELETE FROM accounts;"
                "DELETE FROM playlist_items;"
                "DELETE FROM playlists;"
                "DELETE FROM history;"
            )
        with self.connect() as conn:
            conn.execute("VACUUM")
