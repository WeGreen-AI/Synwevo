"""The one place requests are sent: authentication, idempotency keys, retries and error mapping.

Retry policy, and why:

* **What is retried.** A request is retried only when repeating it cannot do something twice:
  GET/HEAD, a POST/PUT that carries an ``Idempotency-Key`` (the server replays the first answer
  or refuses with 409), and the few calls the server documents as naturally repeatable
  (``retry_safe=True``: completing an upload twice, cancelling twice). Unsafe writes are not
  retried on a bare 429: an intermediary might have produced that answer after forwarding them.
  The one exception is Synwevo's own ``rate_limited`` envelope — the gate answers it before the
  route runs, so nothing was executed and the same request can be sent again after Retry-After.
* **When.** Network failures, 408/425/429/500/502/503/504, and two 409s that mean "not yet"
  rather than "no": ``idempotency_in_progress`` (the first attempt with this key is still
  running) and ``sources_not_ready`` (uploaded files are still being parsed).
* **How long.** Exponential backoff with full jitter (AWS Architecture Blog, "Exponential Backoff
  and Jitter"), unless the server said how long: ``Retry-After``, the ``RateLimit`` header's
  reset when nothing is left, or ``details.retry_after``. A server that asks for longer than
  ``max_retry_wait`` gets the error surfaced instead of a silent multi-minute sleep.
* **The key.** One UUID4 per logical call, generated before the first attempt and sent unchanged
  on every retry — a fresh key per attempt would turn a retry into a second creation.
"""
from __future__ import annotations

import random
import math
import time
import uuid
from typing import Any, Callable, Mapping

import httpx

from .errors import APIError, TransportError, ValidationError, error_from_response

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRY_CONFLICT_CODES = frozenset({"idempotency_in_progress", "sources_not_ready"})
SAFE_METHODS = frozenset({"GET", "HEAD"})
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"

# Configuration mistakes: repeating them cannot help.
_NOT_RETRYABLE = (httpx.UnsupportedProtocol,)


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


class ApiDict(dict):
    """A JSON object from the API, with the response metadata alongside the data.

    It is a plain ``dict`` (``json.dumps`` works, equality works); the extra attributes say which
    request produced it and whether it was an idempotent replay of an earlier one.
    """

    status_code: int = 0
    request_id: str | None = None
    replayed: bool = False
    headers: Mapping[str, str] = {}


def wrap(data: Any, resp: httpx.Response) -> Any:
    if isinstance(data, dict):
        out = ApiDict(data)
        out.status_code = resp.status_code
        out.request_id = resp.headers.get("x-request-id")
        out.replayed = resp.headers.get(REPLAY_HEADER, "").lower() == "true" or bool(data.get("idempotent_replay"))
        out.headers = dict(resp.headers)
        return out
    return data


class Transport:
    def __init__(self, base_url: str, credentials, *, timeout: float = 30.0, max_retries: int = 4,
                 user_agent: str, http: httpx.Client | None = None, backoff_base: float = 0.5,
                 backoff_max: float = 30.0, max_retry_wait: float = 120.0, sleep: Callable[[float], None] = time.sleep,
                 rand: Callable[[], float] = random.random, problem_json: bool = False):
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url 必须是 http(s):// 开头的地址，例如 https://docs.example.com")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValidationError("timeout must be a finite positive number", "timeout")
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials
        self.max_retries = max(0, int(max_retries))
        self.user_agent = user_agent
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.max_retry_wait = max_retry_wait
        self.sleep = sleep
        self.rand = rand
        self.problem_json = problem_json
        self._own_http = http is None
        # Redirects are not followed: a redirect would re-send the Authorization header to
        # wherever the Location points.
        self.http = http or httpx.Client(timeout=timeout, follow_redirects=False)
        self.timeout = timeout

    def close(self) -> None:
        if self._own_http:
            self.http.close()

    # ------------------------------------------------------------------ helpers
    def url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else self.base_url + path

    def backoff(self, attempt: int) -> float:
        """Full jitter: uniform in [0, min(cap, base * 2^attempt)]."""
        return self.rand() * min(self.backoff_max, self.backoff_base * (2 ** attempt))

    def _headers(self, extra: Mapping[str, str] | None, idempotency_key: str | None, auth: bool) -> dict:
        h = {"User-Agent": self.user_agent,
             "Accept": "application/problem+json, application/json" if self.problem_json else "application/json"}
        if auth:
            h["Authorization"] = self.credentials.authorization(self.http)
        if idempotency_key is not None:
            h[IDEMPOTENCY_HEADER] = idempotency_key
        h.update(extra or {})
        return h

    @staticmethod
    def _close_files(kwargs: dict) -> None:
        files = kwargs.get("files")
        items = files.values() if isinstance(files, dict) else [v for _, v in (files or [])]
        for v in items:
            fh = v[1] if isinstance(v, tuple) and len(v) > 1 else v
            close = getattr(fh, "close", None)
            if callable(close):
                close()

    # ------------------------------------------------------------------ the request loop
    def send(self, method: str, path: str, *, params: Mapping[str, Any] | None = None, json: Any = None,
             body: Callable[[], dict] | None = None, headers: Mapping[str, str] | None = None,
             idempotency_key: str | None = None, retry_safe: bool | None = None, stream: bool = False,
             auth: bool = True, timeout: float | None = None, single_attempt: bool = False) -> httpx.Response:
        """Send one logical request (with retries) and return the successful response.

        ``body`` builds the request body afresh for each attempt (a file handle consumed by a
        failed attempt cannot be re-read). With ``stream=True`` the returned response is open
        and the caller must close it.
        ``single_attempt`` also disables rate-limit and reactive authentication retries.
        """
        method = method.upper()
        safe = retry_safe if retry_safe is not None else (
            method in SAFE_METHODS or method in ("POST", "PUT") and idempotency_key is not None)
        url = self.url(path)
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        refreshed = False
        while True:
            request_headers = self._headers(headers, idempotency_key, auth)
            kwargs = body() if body is not None else ({"json": json} if json is not None else {})
            try:
                req = self.http.build_request(method, url, params=clean_params or None,
                                              headers=request_headers,
                                              timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
                                              **kwargs)
                resp = self.http.send(req, stream=stream, follow_redirects=False)
            except _NOT_RETRYABLE as e:
                raise TransportError(None, "transport_error", f"{method} {url} 无法发送：{e}", method=method, url=url) from None
            except httpx.TransportError as e:
                if not single_attempt and attempt < self.max_retries and safe:
                    self.sleep(self.backoff(attempt))
                    attempt += 1
                    continue
                raise TransportError(None, "transport_error",
                                     f"{method} {url} 网络错误（{type(e).__name__}）：{e or '连接中断'}",
                                     method=method, url=url) from None
            finally:
                self._close_files(kwargs)
            if 200 <= resp.status_code < 300:
                return resp
            try:
                content = resp.read()
            finally:
                if stream:
                    resp.close()
            err = error_from_response(resp.status_code, resp.headers, content, method=method, url=url)
            if not single_attempt and resp.status_code == 401 and auth and not refreshed and self.credentials.invalidate():
                # An access token can be revoked or expire early; one fresh token, then give up.
                refreshed = True
                continue
            if not single_attempt and attempt < self.max_retries and self._retryable(err, safe):
                delay = err.retry_after if err.retry_after is not None else self.backoff(attempt)
                if delay > self.max_retry_wait:
                    raise err
                self.sleep(delay)
                attempt += 1
                continue
            raise err

    @staticmethod
    def _retryable(err: APIError, safe: bool) -> bool:
        if err.status == 429 and err.code == "rate_limited":
            # The service's own limiter refused the request at the gate, before any handler ran:
            # a definite non-execution, safe to repeat whatever the method.
            return True
        if not safe:
            return False
        if err.status in RETRY_STATUSES:
            return True
        return err.status == 409 and err.code in RETRY_CONFLICT_CODES
