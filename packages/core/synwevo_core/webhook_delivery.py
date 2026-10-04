"""Sending a signed webhook safely: the parts every application's dispatcher needs and none should
write a second time.

* **Where it may go.** `resolve_checked` validates a callback address and returns the exact
  addresses it validated; `pinned_opener` connects only to one of those, without the ambient proxy
  and without following redirects; `RequestDeadline` bounds the whole attempt, DNS included.
* **How it is signed.** Standard Webhooks: ``v1,<base64 HMAC-SHA256(secret, "<id>.<ts>.<body>")>``,
  with a ``whsec_`` secret; one signature per secret while a rotation overlaps.
* **When to try again.** The Standard Webhooks schedule with jitter, ``Retry-After`` as a minimum.

What an application owns stays with it: its subscriptions and who may manage them, which events
exist and who may be told about them, its delivery log and its dispatcher loop. The policy a
deployment sets (private addresses allowed, plain http allowed, the timeout) is passed in by the
caller, so each application reads its own configuration.
"""
from __future__ import annotations

import base64
import datetime
import email.utils
import hashlib
import hmac
import http.client
import ipaddress
import random
import secrets as _secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, TimeoutError as FutureTimeout
from urllib.parse import urlparse

from .errors import ServiceError

# Standard Webhooks' example schedule: after the first attempt, 9 more over ~3.1 days.
BACKOFF_S = (5, 300, 1800, 7200, 18000, 36000, 50400, 72000, 86400)
JITTER = 0.2
RETRY_AFTER_CAP_S = 86400
MAX_URL_LEN = 2048
SECRET_PREFIX = "whsec_"
DEFAULT_TIMEOUT_S = 15.0

# libc resolution cannot be cancelled. Bound abandoned resolver threads across all dispatchers;
# they only resolve names, never send, and release their slot if libc eventually returns.
_DNS_SLOTS = threading.BoundedSemaphore(16)


# ----------------------------------------------------------------------------- address policy
class UrlRejected(ValueError):
    """The address is not acceptable by policy (scheme, private network, …). Not retried."""


class UrlUnresolvable(UrlRejected):
    """The host did not resolve. Usually transient (a resolver hiccup), so deliveries retry it;
    it stays a `UrlRejected` so creating a subscription for an unresolvable host is refused."""


class ResolverBusy(UrlUnresolvable):
    """No resolver was free in time: this process's fault, not the host's. A caller that counts
    attempts against a receiver can tell the two apart; one that does not sees an unresolved host."""


def resolve_checked(url: str, *, deadline: float | None = None, allow_http: bool = False,
                    allow_private=(), timeout: float = DEFAULT_TIMEOUT_S,
                    http_refusal: str = "回调默认要求 https") -> list:
    """Validate the callback host **and return the exact addresses that were validated.**

    The caller must connect to one of these rather than to the hostname again. Resolving twice —
    once to check and once to connect — is a hole, not a detail: a name under someone else's
    control can answer with a public address for the check and `169.254.169.254` or `127.0.0.1`
    a moment later for the connection. The check then proves nothing about where the request
    actually goes, which is the whole point of having it.

    `allow_private` lists hosts and networks the deployment's operator permits although they are
    not global addresses; `allow_http` permits plain http. Both default to the safe answer.
    """
    u = urlparse(url)
    if u.scheme not in ("https", "http"):
        raise UrlRejected(f"回调地址必须是 http(s)：{url}")
    if u.scheme == "http" and not allow_http:
        raise UrlRejected(http_refusal)
    host = u.hostname or ""
    if not host:
        raise UrlRejected(f"回调地址缺少主机名：{url}")
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError as e:
        raise UrlRejected(f"回调地址的端口无效：{url}") from e
    allow = list(allow_private)
    try:
        infos = _resolve(host, port, deadline if deadline is not None else time.monotonic() + timeout)
    except socket.gaierror as e:
        raise UrlUnresolvable("无法解析回调地址主机") from e

    ok = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_global or host in allow or any(_in_allow(ip, a) for a in allow):
            ok.append(str(ip))
            continue
        raise UrlRejected("回调地址不被允许（内网地址需由运营方配置允许列表）")
    if not ok:
        raise UrlUnresolvable("无法解析回调地址主机")
    return ok


def _resolve(host: str, port: int, deadline: float):
    if not _DNS_SLOTS.acquire(timeout=max(0, deadline - time.monotonic())):
        raise ResolverBusy("无法解析回调地址主机：解析超时")
    result = Future()

    def run():
        try:
            result.set_result(socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP))
        except Exception as exc:
            result.set_exception(exc)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=run, name="webhook-dns", daemon=True).start()
    except Exception:
        _DNS_SLOTS.release()
        raise
    try:
        return result.result(timeout=max(0, deadline - time.monotonic()))
    except FutureTimeout as exc:
        raise UrlUnresolvable("无法解析回调地址主机：解析超时") from exc


def _in_allow(ip, entry: str) -> bool:
    try:
        return ip in ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return False


def validate_url(url, *, resolve=resolve_checked) -> str:
    """A subscription URL, checked the way a delivery will check it. Raises 400 with field=url.
    `resolve` is the caller's own `resolve_checked`, carrying its deployment's policy."""
    if not isinstance(url, str) or not url.strip():
        raise ServiceError(400, "invalid_url", "请提供回调地址（url）", {"field": "url"})
    url = url.strip()
    if len(url) > MAX_URL_LEN or any(ch.isspace() for ch in url):
        raise ServiceError(400, "invalid_url", f"回调地址过长或含空白字符（上限 {MAX_URL_LEN} 字符）", {"field": "url"})
    u = urlparse(url)
    if u.username or u.password:
        # It would be stored in plain text and echoed back by every GET. The signing secret is
        # the authentication a receiver should rely on.
        raise ServiceError(400, "invalid_url", "回调地址不能包含用户名或密码；请用签名密钥校验来源", {"field": "url"})
    try:
        resolve(url)
    except UrlRejected as e:
        raise ServiceError(400, "invalid_url", str(e), {"field": "url"}) from e
    return url


# ----------------------------------------------------------------------------- signatures
def new_secret() -> str:
    return SECRET_PREFIX + base64.b64encode(_secrets.token_bytes(32)).decode("ascii")


def secret_bytes(key: str) -> bytes:
    """Standard Webhooks key material: ``whsec_<base64>`` decodes; anything else is used as-is,
    so an existing deployment secret signs the standard header without being reissued."""
    if key.startswith(SECRET_PREFIX):
        try:
            return base64.b64decode(key[len(SECRET_PREFIX):], validate=True)
        except ValueError:
            pass
    return key.encode("utf-8")


def sign_standard(msg_id: str, ts: int, body: bytes, key: str) -> str:
    mac = hmac.new(secret_bytes(key), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


def signature_header(msg_id: str, ts: int, body: bytes, keys: list[str]) -> str:
    return " ".join(sign_standard(msg_id, ts, body, k) for k in keys if k)


def verify_standard(body: bytes, msg_id: str, ts: str | int, header: str, key: str,
                    tolerance_s: int = 300, now: float | None = None) -> bool:
    """Reference check of ``webhook-signature`` for one secret (any listed signature may match)."""
    try:
        t = int(ts)
    except (TypeError, ValueError):
        return False
    if abs((time.time() if now is None else now) - t) > tolerance_s:
        return False
    want = sign_standard(msg_id, t, body, key)
    return any(hmac.compare_digest(want, s) for s in (header or "").split())


# ----------------------------------------------------------------------------- retries
def retry_after(headers, at: float) -> float | None:
    """Retry-After as seconds (delta-seconds or an HTTP-date); None when absent or unreadable.

    It is read while a receiver's refusal is being handled, so whatever the receiver put there it
    does not raise: an error here would lose the refusal itself. Delta-seconds are ASCII digits
    and nothing else — ``"²".isdigit()`` is true and ``float("²")`` is an error."""
    raw = headers.get("Retry-After") if headers is not None else None
    if not raw:
        return None
    raw = raw.strip()
    if raw.isascii() and raw.isdigit():
        return float(raw)
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        # An HTTP-date is UTC even when it does not say so (RFC 9110 §5.6.7). Read as this server's
        # local time it would mean a different moment on every machine — and one near the end of
        # the calendar cannot be converted at all.
        when = when.replace(tzinfo=datetime.timezone.utc)
    return max(0.0, when.timestamp() - at)


def next_delay(attempts: int, retry_after: float | None = None, *, schedule=BACKOFF_S, rng=random) -> float | None:
    """Seconds until the next attempt after `attempts` failed ones; None when exhausted."""
    if attempts > len(schedule):
        return None
    delay = schedule[attempts - 1] * rng.uniform(1 - JITTER, 1 + JITTER)
    if retry_after:
        delay = max(delay, min(float(retry_after), RETRY_AFTER_CAP_S))
    return delay


# ----------------------------------------------------------------------------- the connection
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"回调地址发生跳转（{newurl}），出于安全不跟随", headers, fp)


class RequestDeadline:
    """Interrupt even a trickling response or TLS handshake at the total request deadline.

    Closing alone does not interrupt a socket's buffered makefile reader; shutdown does. The
    timer never performs a send, and is cancelled and joined before the attempt releases its
    claim. DNS uses the same deadline with a separately bounded resolver pool.
    """

    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds
        self.lock = threading.Lock()
        self.sock = None
        self.timer = threading.Timer(seconds, self._abort)
        self.timer.daemon = True

    def remaining(self) -> float:
        left = self.end - time.monotonic()
        if left <= 0:
            raise TimeoutError("回调请求超时")
        return left

    def attach(self, sock):
        with self.lock:
            sock.settimeout(self.remaining())
            self.sock = sock

    def _abort(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def __enter__(self):
        self.timer.start()
        return self

    def __exit__(self, *exc):
        self.timer.cancel()
        self.timer.join()
        if self.sock is not None:
            self.sock.close()
        # A header parser can return EOF rather than a timeout when shutdown interrupts it.
        if time.monotonic() >= self.end and (exc[0] is None or not issubclass(exc[0], UrlUnresolvable)):
            raise TimeoutError("回调请求超时")


def connect_pinned(address, timeout, deadline=None):
    # socket.create_connection calls getaddrinfo even for a numeric IP. Avoid the second
    # resolution altogether; only the validated numeric address reaches connect().
    ip, port = address
    sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        if deadline is not None:
            deadline.attach(sock)
        sock.connect((ip, port))
        return sock
    except BaseException:
        sock.close()
        raise


def pinned_opener(validated: list, deadline: RequestDeadline | None = None, *, connect=connect_pinned):
    """An opener that connects **only** to an address `resolve_checked` approved.

    Two things it does not do, both deliberate:

    * **It does not resolve the hostname again.** urllib would, and a name under someone else's
      control can answer with a public address for the check and a private one for the
      connection. Pinning is what makes the check mean anything.
    * **It does not use the ambient proxy.** `build_opener` installs a `ProxyHandler` from the
      environment by default, and a proxy both carries the signed event body somewhere nobody
      chose and performs its own resolution — which would bypass the address check entirely.

    `connect` opens the socket to the approved address (`connect_pinned`); callers pass their own
    only to observe or stand in for the connection in tests.
    """
    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            # Connect to the approved address; keep the hostname for SNI and certificate
            # validation, so pinning does not quietly weaken TLS.
            hostname = self.host
            sock = connect((validated[0], self.port), self.timeout, deadline)
            try:
                self.sock = self._context.wrap_socket(sock, server_hostname=hostname, do_handshake_on_connect=False)
                if deadline is not None:
                    deadline.attach(self.sock)
                self.sock.do_handshake()
            except BaseException:
                sock.close()
                if self.sock is not None:
                    self.sock.close()
                raise

    class _PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = connect((validated[0], self.port), self.timeout, deadline)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_PinnedHTTPConnection, req)

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),        # no ambient proxy
        _HTTPSHandler(), _HTTPHandler(), NoRedirect())
