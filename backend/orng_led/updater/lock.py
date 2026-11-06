"""Async + interprocess locks for app update staging/apply.

Never hold a threading.RLock across ``await``. Use ``async_update_lock`` for
coroutines and ``interprocess_update_lock`` around critical filesystem sections.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from orng_led.updater.paths import ManagedPaths, resolve_managed_paths

_ASYNC_LOCK = asyncio.Lock()
# Windows byte-range locks are per process, so two threads in one process
# would both enter the file lock. This map serializes those threads.
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock_for(lock_path: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(lock_path)
        if lock is None:
            lock = threading.Lock()
            _THREAD_LOCKS[lock_path] = lock
        return lock


def async_update_lock() -> asyncio.Lock:
    return _ASYNC_LOCK


@contextmanager
def interprocess_update_lock(
    paths: ManagedPaths | None = None,
    *,
    timeout_s: float = 120.0,
) -> Iterator[None]:
    """Exclusive file lock on ``<app_root>/update.lock``."""
    paths = paths or resolve_managed_paths()
    paths.app_root.mkdir(parents=True, exist_ok=True)
    lock_path = paths.app_root / "update.lock"
    thread_lock = _thread_lock_for(str(lock_path.resolve()))
    thread_lock.acquire()
    fh = None
    deadline = time.monotonic() + timeout_s
    locked = False
    try:
        lock_path.touch(exist_ok=True)
        # r+b (not a+b): Windows append mode does not keep a stable lock byte.
        fh = open(lock_path, "r+b")  # noqa: SIM115
        while True:
            try:
                _lock_file(fh)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Could not acquire update lock: {lock_path}") from None
                time.sleep(0.05)
        acquired = os.environ.get("ORNG_UPDATE_LOCK_ACQUIRED_FLAG", "").strip()
        if acquired:
            Path(acquired).write_text("1", encoding="utf-8")
        hold_s = float(os.environ.get("ORNG_UPDATE_LOCK_HOLD_S") or 0.0)
        if hold_s > 0:
            time.sleep(hold_s)
        yield
    finally:
        if fh is not None:
            if locked:
                try:
                    _unlock_file(fh)
                except OSError:
                    pass
            fh.close()
        thread_lock.release()


def _lock_file(fh) -> None:  # noqa: ANN001
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        # Ensure at least 1 byte exists for locking.
        if fh.read(1) == b"":
            fh.write(b"0")
            fh.flush()
            fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh) -> None:  # noqa: ANN001
    if sys.platform == "win32":
        import msvcrt

        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# Kept for sync-only call sites that never await under the lock.
@contextmanager
def thread_update_lock() -> Iterator[None]:
    """Deprecated sync alias — prefer interprocess_update_lock."""
    with interprocess_update_lock():
        yield
