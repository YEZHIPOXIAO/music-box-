"""Regression test for the Windows text-mode key bug.

os.open() without O_BINARY opened the key file in text mode on Windows, so a
0x0A byte in the random key was written as CRLF: 32 bytes became 33, Fernet's
constructor rejected it, and the cipher silently downgraded to the HMAC scheme
while still trying to read Fernet rows. This produced "unreadable account" for
roughly one key in eight — a genuinely intermittent bug that only the
whole-suite run exposed.
"""

from __future__ import annotations

import os
import tempfile


import pytest  # noqa: E402

import musicbox.storage as st  # noqa: E402
from musicbox import config as cfg  # noqa: E402
from musicbox.storage import Cipher, _load_key, _read_key  # noqa: E402


@pytest.fixture()
def temp_home(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("MUSICBOX_HOME", tmp)
        monkeypatch.setattr(cfg.settings, "data_dir", __import__("pathlib").Path(tmp))
        yield tmp


def test_key_file_is_exactly_32_bytes_when_it_contains_a_newline_byte(temp_home):
    """Force the pathological case: a key whose bytes include 0x0A."""
    path = cfg.settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:  # write in binary, bypassing os.open
        fh.write(b"\n" * 32)
    assert len(path.read_bytes()) == 32

    # The reader must accept it, and a fresh load must not corrupt it.
    key = _read_key(path)
    assert len(key) == 32


def test_os_open_path_writes_binary(temp_home):
    """_load_key must create a 32-byte file regardless of key contents."""
    for _ in range(40):  # enough attempts to hit a 0x0A byte with high odds
        path = cfg.settings.key_path
        if path.exists():
            path.unlink()
        key = _load_key()
        assert len(key) == 32, "密钥长度错误：os.open 可能又开了文本模式"
        assert len(path.read_bytes()) == 32, "密钥文件长度错误（CRLF 转换？）"


def test_crlf_expanded_key_file_is_repaired(temp_home):
    """A 1.0.0-era key file (33 bytes, CRLF-expanded) must still be usable."""
    path = cfg.settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    original = b"A" * 10 + b"\n" + b"B" * 21  # 32 bytes with one 0x0A
    expanded = original.replace(b"\n", b"\r\n")  # what text mode wrote
    path.write_bytes(expanded)
    assert len(expanded) == 33

    repaired = _read_key(path)
    assert len(repaired) == 32
    assert repaired == original


def test_corrupt_key_file_raises_instead_of_downgrading(temp_home):
    """A damaged key must fail loudly, not silently switch schemes."""
    path = cfg.settings.key_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"short")
    with pytest.raises(ValueError):
        _load_key()


def test_scheme_stable_across_repeated_construction(temp_home):
    """Two Cipher instances must agree, or saved accounts become unreadable."""
    schemes = set()
    token = None
    for _ in range(6):
        cipher = Cipher()
        schemes.add(cipher.scheme)
        if token is None:
            token = cipher.encrypt("account-cookie")
        assert cipher.decrypt(token) == "account-cookie"
    assert len(schemes) == 1, f"加密方案在多次构造间发生了漂移: {schemes}"


def test_key_is_stable_across_processes(temp_home):
    """Simulate a restart: a new Cipher must read the key written earlier."""
    first = Cipher()
    token = first.encrypt("persisted")
    # drop any cached module state by re-reading from disk
    second = Cipher()
    assert second.decrypt(token) == "persisted"
    assert second.scheme == first.scheme
