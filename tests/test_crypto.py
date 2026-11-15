"""Encryption and erase-correctness tests."""

from __future__ import annotations

import base64
import hashlib
import os
import tempfile


import pytest  # noqa: E402

from musicbox.storage import Cipher, Store  # noqa: E402


def _legacy_token(key: bytes, plaintext: bytes) -> str:
    """Reproduce the pre-1.0.1 format so we can prove old rows still open."""
    stream = hashlib.sha256(key + (0).to_bytes(8, "big")).digest()
    body = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hashlib.sha256(key + body).hexdigest()[:16]
    return "x1:" + tag + ":" + base64.b64encode(body).decode("ascii")


def test_cipher_roundtrip():
    cipher = Cipher()
    secret = "uin=12345; qm_keyst=abc%3D; token=xyz"
    token = cipher.encrypt(secret)
    assert token != secret
    assert secret not in token
    assert cipher.decrypt(token) == secret


def test_cipher_uses_a_vetted_scheme():
    """Fernet when available; otherwise the HMAC construction, never plain."""
    cipher = Cipher()
    assert cipher.scheme in {"fernet", "hmac"}
    assert cipher.scheme != "plain"


def test_each_encryption_is_unique():
    """A nonce/IV must be used: identical plaintext must not give equal tokens."""
    cipher = Cipher()
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_tampered_token_rejected():
    cipher = Cipher()
    token = cipher.encrypt("important")
    tampered = token[:-6] + ("AAAAAA" if token[-6:] != "AAAAAA" else "BBBBBB")
    with pytest.raises(Exception):
        cipher.decrypt(tampered)


def test_legacy_rows_still_decrypt():
    """Accounts saved by an older build must not become unreadable."""
    cipher = Cipher()
    legacy = _legacy_token(cipher._get_key(), b"old-account-cookie=1")
    assert cipher.decrypt(legacy) == "old-account-cookie=1"


def test_encryption_can_be_disabled_explicitly():
    os.environ["MUSICBOX_NO_ENCRYPT"] = "1"
    try:
        cipher = Cipher()
        assert cipher.enabled is False
        assert cipher.encrypt("plain") == "plain"
    finally:
        os.environ.pop("MUSICBOX_NO_ENCRYPT", None)


def test_plaintext_never_hits_the_database_file():
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        store.save_account("qq", "uin=424242; qm_keyst=SUPERSECRET")
        raw = open(store.path, "rb").read()
        assert b"SUPERSECRET" not in raw
        assert b"424242" not in raw


def test_wipe_erases_recoverable_bytes():
    """A 'delete everything' button must not leave rows in free pages."""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "db.sqlite3"))
        store.save_account("kugou", "token=LEAKME")
        pid = store.create_playlist("secret playlist name")
        store.wipe()
        raw = open(store.path, "rb").read()
        assert b"LEAKME" not in raw
        assert b"secret playlist name" not in raw
        assert store.accounts() == []
        assert store.playlists() == []


def test_key_file_is_created_once_and_reused():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MUSICBOX_HOME"] = tmp
        try:
            from musicbox import config as cfg

            cfg.settings.data_dir = __import__("pathlib").Path(tmp)
            cipher_a = Cipher()
            token = cipher_a.encrypt("shared")
            cipher_b = Cipher()  # re-loads the key from disk
            assert cipher_b.decrypt(token) == "shared"
            key_file = cfg.settings.key_path
            assert key_file.exists()
            assert len(key_file.read_bytes()) == 32
        finally:
            os.environ.pop("MUSICBOX_HOME", None)
