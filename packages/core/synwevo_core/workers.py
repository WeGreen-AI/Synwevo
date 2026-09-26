"""The background threads of one process, in one place, so a shutdown can stop them all.

Every worker of this platform — the job runner and its supervisor, the webhook dispatcher, the
copy runner, the mailer, the notification dispatcher, the session sweeper, the member-import
runner — is an object with a ``stop()`` method that owns one or more daemon threads. Each
registers itself here when it starts. The end of an application's lifespan (uvicorn shutting
down, a test client leaving its ``with`` block) stops the workers it started through its own
shutdown hook; :func:`stop_all` is the net under that: it stops every worker still alive in
the process, for a test teardown or a tool that wants a clean exit.

Entries are weak. A worker that has been garbage-collected is simply gone; one whose thread is
still running is kept alive by that thread's own reference to it, which is exactly the case
this module exists for.
"""
from __future__ import annotations

import logging
import threading
import time
import weakref

log = logging.getLogger("synwevo.workers")

_lock = threading.Lock()
_live: "weakref.WeakKeyDictionary[object, list[threading.Thread]]" = weakref.WeakKeyDictionary()


def register(worker: object, *threads: threading.Thread | None) -> None:
    """Remember ``worker`` (anything with ``stop()``) and the threads it started."""
    with _lock:
        _live.setdefault(worker, []).extend(t for t in threads if t is not None)


def live() -> list[object]:
    """The registered workers that are still referenced by something (a running thread, an app)."""
    with _lock:
        return list(_live.keys())


def stop_all(wait: float | None = 3.0) -> list[str]:
    """Ask every registered worker to stop and wait up to ``wait`` seconds for their threads.

    Returns the names of threads still alive afterwards (a thread in the middle of a long slice
    of work finishes it first; it holds no lease past the next supervisor tick of another
    process). Safe to call more than once and from any thread.
    """
    with _lock:
        items = [(w, list(ts)) for w, ts in _live.items()]
    for worker, _ in items:
        try:
            worker.stop()
        except Exception:                                    # noqa: BLE001 — one worker's failure must not keep others alive
            log.exception("stopping %s failed", type(worker).__name__)
    left: list[str] = []
    deadline = time.monotonic() + (wait or 0.0)
    me = threading.current_thread()
    for _, threads in items:
        for t in threads:
            if t is me or not t.is_alive():
                continue
            t.join(max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                left.append(t.name)
    return left
