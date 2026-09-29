"""Typed errors for every way a call to Synwevo Docs can fail.

One base class, `SynwevoError`, carries what the server said (status, machine-readable code,
message, the field it blames, details, the request id to quote to an operator) and how long it
asked the caller to wait. Subclasses exist per status class so callers can `except` the case
they handle (a 409 conflict, a 404) and let everything else propagate.

Both error formats the server speaks are parsed: the `/api/v1` envelope
``{"error": {"code", "message", "field", "request_id", "details"}}`` (the default) and RFC 9457
``application/problem+json`` (sent when the caller asks for it). A body that is neither — a
proxy's HTML error page, an empty 502 — still becomes a typed error with the status and the
``X-Request-ID`` header, never a JSON decoding exception.
"""
from __future__ import annotations

import email.utils
import json
import re
import time
from typing import Any, Mapping

__all__ = [
    "SynwevoError", "TransportError", "APIError", "BadRequestError", "AuthenticationError",
    "PermissionDeniedError", "NotFoundError", "ConflictError", "GoneError", "PayloadTooLargeError",
    "RangeNotSatisfiableError", "RateLimitError", "ServerError", "IntegrityError", "ValidationError",
    "WaitTimeoutError", "WebhookVerificationError", "error_from_response", "parse_retry_after",
]


class SynwevoError(Exception):
    """Base class. ``status`` is None for failures that never got an HTTP answer."""

    def __init__(self, status: int | None, code: str | None, message: str, request_id: str | None = None,
                 field: str | None = None, details: dict | None = None, retry_after: float | None = None, *,
                 method: str | None = None, url: str | None = None, headers: Mapping[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.request_id = request_id
        self.field = field
        self.details = details or {}
        self.retry_after = retry_after
        self.method = method
        self.url = url
        self.headers = dict(headers or {})

    def __str__(self) -> str:
        head = " ".join(str(x) for x in (self.status, self.code) if x is not None)
        tail = []
        if self.field:
            tail.append(f"field={self.field}")
        if self.request_id:
            tail.append(f"request_id={self.request_id}")
        return (f"[{head}] " if head else "") + self.message + (f" ({', '.join(tail)})" if tail else "")

    def to_dict(self) -> dict:
        """The error as the CLI prints it: the server's fields plus the status and error class."""
        out = {"type": type(self).__name__, "status": self.status, "code": self.code, "message": self.message,
               "field": self.field, "request_id": self.request_id, "details": self.details or None,
               "retry_after": self.retry_after}
        return {k: v for k, v in out.items() if v is not None}


class TransportError(SynwevoError):
    """No usable HTTP answer: connection refused, reset, DNS failure, timeout, TLS error."""


class APIError(SynwevoError):
    """The server answered with an error status."""


class BadRequestError(APIError):
    """400: the request is invalid; ``field`` names the offending input."""


class AuthenticationError(APIError):
    """401: credential missing, invalid, expired or revoked (also a token endpoint refusal)."""


class PermissionDeniedError(APIError):
    """403: the credential is valid but lacks the scope (or the link was revoked)."""


class NotFoundError(APIError):
    """404: does not exist, or belongs to another workspace (never disclosed)."""


class ConflictError(APIError):
    """409: idempotency conflict, stale revision, job state, preflight errors (``has_errors``)."""


class GoneError(APIError):
    """410: artifact deleted, upload expired or aborted, event cursor no longer valid."""


class PayloadTooLargeError(APIError):
    """413: the file exceeds the deployment's size limit."""


class RangeNotSatisfiableError(APIError):
    """416: a resumable upload chunk started past what the server has; ``details['received']``."""


class RateLimitError(APIError):
    """429: too many requests; ``retry_after`` says how long to wait."""


class ServerError(APIError):
    """5xx: the server failed; quote ``request_id`` to the operator."""


class IntegrityError(SynwevoError):
    """Bytes did not match their SHA-256 (a download, an archive entry, or an upload)."""


class ValidationError(SynwevoError, ValueError):
    """Rejected on the client before sending: a value the server documents as invalid."""

    def __init__(self, message: str, field: str | None = None, allowed: list | tuple | None = None):
        super().__init__(None, "client_validation", message, field=field,
                         details={"allowed": list(allowed)} if allowed else None)


class WaitTimeoutError(SynwevoError, TimeoutError):
    """`wait_for_job` gave up; ``job`` is the last state seen (the job itself keeps running)."""

    def __init__(self, message: str, job: dict | None = None):
        super().__init__(None, "wait_timeout", message)
        self.job = job


class WebhookVerificationError(Exception):
    """A webhook request failed verification. Answer it with 400/401 and do not act on it."""


_BY_STATUS = {400: BadRequestError, 401: AuthenticationError, 403: PermissionDeniedError, 404: NotFoundError,
              409: ConflictError, 410: GoneError, 413: PayloadTooLargeError, 416: RangeNotSatisfiableError,
              429: RateLimitError}


def parse_retry_after(headers: Mapping[str, str], now: float | None = None) -> float | None:
    """Seconds to wait from ``Retry-After`` (delta or HTTP-date), else the IETF ``RateLimit``
    header when it says nothing is left (``r=0;t=<seconds>``). None when neither says anything."""
    get = _getter(headers)
    raw = (get("retry-after") or "").strip()
    if raw:
        if re.fullmatch(r"\d+(\.\d+)?", raw):
            return float(raw)
        try:
            when = email.utils.parsedate_to_datetime(raw)
            if when is not None:
                return max(0.0, when.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, IndexError):
            pass
    # One item per policy ("key" for the credential, "workspace" for the workspace's total): the
    # wait is the longest reset among the policies with nothing left.
    waits = []
    for item in (get("ratelimit") or "").split(","):
        m_r, m_t = re.search(r"(?:^|;)\s*r=(\d+)", item), re.search(r"(?:^|;)\s*t=(\d+)", item)
        if m_r and m_t and int(m_r.group(1)) == 0:
            waits.append(float(m_t.group(1)))
    return max(waits) if waits else None


def _getter(headers: Mapping[str, str]):
    try:
        lowered = {str(k).lower(): v for k, v in headers.items()}
    except AttributeError:
        lowered = {}
    return lambda name: lowered.get(name)


def _field_from_pointer(pointer: str | None) -> str | None:
    if not pointer or not isinstance(pointer, str):
        return None
    segs = [s.replace("~1", "/").replace("~0", "~") for s in pointer.lstrip("#").split("/") if s]
    return ".".join(segs) or None


def error_from_response(status: int, headers: Mapping[str, str], body: bytes, *, method: str | None = None,
                        url: str | None = None) -> APIError:
    """Build the typed error for one HTTP error response."""
    get = _getter(headers)
    request_id = get("x-request-id")
    code, message, field, details = None, None, None, {}
    ctype = (get("content-type") or "").split(";")[0].strip().lower()
    parsed: Any = None
    if body:
        try:
            parsed = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            parsed = None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):          # the v1 envelope
        e = parsed["error"]
        code, message, field = e.get("code"), e.get("message"), e.get("field")
        request_id = e.get("request_id") or request_id
        details = e.get("details") if isinstance(e.get("details"), dict) else {}
    elif isinstance(parsed, dict) and (ctype == "application/problem+json" or "type" in parsed and "status" in parsed):
        typ = parsed.get("type") or ""
        code = parsed.get("code") or (typ.rsplit(":", 1)[-1] if typ.startswith("urn:") else None)
        message = parsed.get("detail") or parsed.get("title")
        request_id = parsed.get("request_id") or request_id
        errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
        field = next((_field_from_pointer(x.get("pointer")) for x in errors if isinstance(x, dict) and x.get("pointer")), None)
        details = parsed.get("details") if isinstance(parsed.get("details"), dict) else {}
        if errors:
            details = {**details, "errors": errors}
    elif isinstance(parsed, dict) and isinstance(parsed.get("error"), str):          # OAuth token endpoint
        code, message = parsed["error"], parsed.get("error_description") or parsed["error"]
    if not message:
        text = body[:300].decode("utf-8", "replace").strip() if body else ""
        message = f"HTTP {status}" + (f"：{text}" if text and not text.startswith("<") else "")
    retry_after = parse_retry_after(headers)
    if retry_after is None and isinstance(details.get("retry_after"), (int, float)):
        retry_after = float(details["retry_after"])
    cls = _BY_STATUS.get(status) or (ServerError if status >= 500 else APIError)
    return cls(status, code or f"http_{status}", message, request_id, field if isinstance(field, str) else None,
               details, retry_after, method=method, url=url, headers=headers)
