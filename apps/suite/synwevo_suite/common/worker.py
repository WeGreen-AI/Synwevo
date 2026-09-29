"""A periodic background task that stops with the process (synwevo_core.workers)."""
from __future__ import annotations

import logging
import threading
from typing import Callable

from synwevo_core import workers

log = logging.getLogger("synwevo.suite")


class Ticker:
    """Run ``fn`` every ``interval`` seconds on a daemon thread until :meth:`stop`. A failing tick
    is logged and the next one runs as scheduled; ``wake()`` runs the next tick now."""

    def __init__(self, name: str, fn: Callable[[], object], interval: float):
        self.name, self.fn, self.interval = name, fn, interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, api=None) -> "Ticker":
        self.thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self.thread.start()
        workers.register(self, self.thread)
        if api is not None:
            hooks = getattr(api.state, "shutdown_hooks", None)
            if hooks is not None:
                hooks.append(self.stop)
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.fn()
            except Exception:  # noqa: BLE001 — one bad tick must not end the worker
                log.exception("%s tick failed", self.name)
            self._wake.wait(self.interval)
            self._wake.clear()

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
