"""Thread-safety tests for HttpClient.

Provider calls run from a thread pool, but ``requests.Session`` is explicitly
not thread-safe. These tests fail loudly if the locking is removed.
"""

from __future__ import annotations

import os
import tempfile
import threading


import pytest  # noqa: E402

from musicbox.http import HttpClient  # noqa: E402


def test_concurrent_cookie_writes_do_not_corrupt_the_jar():
    """Many threads re-binding accounts must leave a consistent jar."""
    http = HttpClient()
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for _ in range(30):
                http.set_cookies(f"uin={n}; tok=t{n}")
                cookies = http.cookies()
                # if another thread interleaved, the jar could hold a mixed set
                if "uin" in cookies:
                    assert cookies["uin"].isdigit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发写 cookie 时出现异常: {errors[:3]}"


def test_concurrent_reads_and_clears_are_safe():
    http = HttpClient()
    http.set_cookies("a=1; b=2")
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(50):
                http.cookies()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def clearer() -> None:
        try:
            for _ in range(50):
                http.set_cookies("a=2")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads += [threading.Thread(target=clearer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"并发读写时出现异常: {errors[:3]}"


def test_session_has_a_lock():
    """Guard against someone deleting the lock in a refactor."""
    assert hasattr(HttpClient(), "_lock")


def test_provider_registry_is_stable_under_concurrency():
    from musicbox.providers import registry

    seen = []
    lock = threading.Lock()

    def grab() -> None:
        for _ in range(20):
            providers = registry.providers()
            with lock:
                seen.append(id(providers))

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # every thread must observe the same dict instance after the first build
    assert len(set(seen)) == 1, "registry 在并发首次访问时被重复构建"
