"""Periodic validation of the governed DeepSeek model id against GET /models.

Model ids are governed route configuration, never constants in code. DeepSeek retires ids
(deepseek-chat / deepseek-reasoner stopped on 2026-07-24), so a route must stop before a
person's request fails upstream: at start, whenever a new provider revision is published, and
then every ``interval_seconds``, this worker lists the platform account's models with the
sealed platform key. A missing id closes paid dispatch on that provider revision (audited,
administrators alerted through the inbox and mail) until an administrator publishes again.
A failed check is recorded and closes nothing.

No chat request, price, grant or credential discovery happens here; the key only travels in
the request header to the official origin (or a loopback FIXTURE double in tests).
"""
from __future__ import annotations

import logging
import math
import threading
import time

from synwevo_core import workers
from synwevo_core.billing import BillingError
from synwevo_core.errors import ServiceError

from .deepseek import DeepSeekError, list_models
from .provider import Credential

log = logging.getLogger("synwevo.agent.model_check")
#: How often the worker looks for a newly published revision between full checks.
POLL_SECONDS = 60


class ModelCheck:
    def __init__(self, settings, *, base_url: str, interval_seconds: float = 3600, timeout_s: float = 15.0,
                 transport=None, clock=time.monotonic):
        for value in (interval_seconds, timeout_s):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("invalid_model_check_interval")
        if not 60 <= interval_seconds <= 86400 or not 0 < timeout_s <= 60:
            raise ValueError("invalid_model_check_interval")
        self.settings, self.base_url = settings, base_url
        self.interval_seconds, self.timeout_s = interval_seconds, timeout_s
        self._transport = transport  # Callable returning a trusted FIXTURE transport, tests only.
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._checked: tuple[int, float] | None = None  # (revision, monotonic time) of the last check
        self.last: dict | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def due(self) -> bool:
        """A check is due for a revision never checked by this worker, or after the interval."""
        revision = self.settings.read()["revision"]
        return (self._checked is None or self._checked[0] != revision
                or self._clock() - self._checked[1] >= self.interval_seconds)

    def run_once(self) -> dict | None:
        """One check of the current revision. Returns the recorded observation, or None when
        there is no enabled, current route to check (nothing is recorded then)."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            try:
                resolved = self.settings.resolve()
            except (BillingError, ServiceError):
                return None  # Expired/corrupt/revoked governance already refuses every send.
            if resolved is None:
                return None
            revision, model = resolved.revision, resolved.route["requested_model"]
            credential = Credential("platform", resolved.api_key, model, provider="deepseek")
            resolved = None
            transport = self._transport() if self._transport is not None else None
            try:
                listed = list_models(credential, transport=transport, base_url=self.base_url, timeout_s=self.timeout_s)
            except DeepSeekError as exc:
                state, reason = "models_check_failed", exc.code
            else:
                state, reason = ("model_listed" if model in listed else "model_missing"), "models_listing"
            credential = None
            self._checked = (revision, self._clock())
            # Pinned to the revision that was checked: a publication during the request makes
            # this observation stale, and record_health ignores it.
            closure = self.settings.record_health(state=state, requested_model=model, reason_code=reason,
                                                  revision=revision)
            self.last = {"state": state, "reason_code": reason, "requested_model": model,
                         "revision": revision, "closure": closure}
            return self.last
        finally:
            self._lock.release()

    def start(self) -> ModelCheck:
        if self._stop.is_set():
            raise RuntimeError("model_check_closed")
        if not self.running:
            self._thread = threading.Thread(target=self._loop, name="agent-model-check", daemon=True)
            workers.register(self, self._thread)
            self._thread.start()
        return self

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self.due():
                    self.run_once()
            except Exception as exc:  # noqa: BLE001 - one failed round must not end the worker
                log.warning("model id check failed: %s", type(exc).__name__)
            if self._stop.wait(min(POLL_SECONDS, self.interval_seconds)):
                break

    def stop(self) -> None:
        self._stop.set()

    def close(self, wait: float = 3) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(wait)
        return not self.running
