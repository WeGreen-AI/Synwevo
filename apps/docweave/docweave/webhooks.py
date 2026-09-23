"""Signed webhooks for job events (off by default, configured at deployment level).

    DOCWEAVE_WEBHOOK_URLS=https://automation.example.com/hooks/docweave
    DOCWEAVE_WEBHOOK_SECRET=<shared secret>
    DOCWEAVE_WEBHOOK_EVENTS=job.completed,job.completed_with_errors,job.failed,job.cancelled
    DOCWEAVE_WEBHOOK_ALLOW_PRIVATE=10.0.0.0/8,automation.internal     # only if you need it

Each delivery carries the event id, a timestamp and
``X-DocWeave-Signature: v1=<hmac-sha256(secret, "<timestamp>.<body>")>``.  Receivers should
reject a timestamp older than a few minutes and ignore an event id they already processed
(deliveries can repeat; ordering is not guaranteed – the event ``seq`` is monotonic per job).

Delivery never blocks a job: failures are retried with a growing delay and recorded in
``webhook_deliveries``; the artifacts stay downloadable regardless.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .store import Store, new_id, now

BACKOFF_S = (30, 120, 600, 1800, 7200)
DEFAULT_EVENTS = ("job.completed", "job.completed_with_errors", "job.failed", "job.cancelled")
LAST_SEQ_KEY = "webhook_last_seq"


def endpoints() -> list[str]:
    return [u.strip() for u in (os.environ.get("DOCWEAVE_WEBHOOK_URLS") or "").split(",") if u.strip()]


def wanted_events() -> tuple[str, ...]:
    raw = (os.environ.get("DOCWEAVE_WEBHOOK_EVENTS") or "").strip()
    return tuple(e.strip() for e in raw.split(",") if e.strip()) or DEFAULT_EVENTS


def secret() -> str:
    return os.environ.get("DOCWEAVE_WEBHOOK_SECRET") or ""


def _allow_private() -> list[str]:
    return [x.strip() for x in (os.environ.get("DOCWEAVE_WEBHOOK_ALLOW_PRIVATE") or "").split(",") if x.strip()]


class UrlRejected(ValueError):
    pass


def check_url(url: str) -> str:
    """Reject callbacks that would reach into private networks unless explicitly allowed.

    Returns the url. Use `resolve_checked` when you are about to connect: this function answers
    "is this address acceptable", and the answer stops being true the moment it returns.
    """
    resolve_checked(url)
    return url


def resolve_checked(url: str) -> list:
    """Validate the callback host **and return the exact addresses that were validated.**

    The caller must connect to one of these rather than to the hostname again. Resolving twice —
    once to check and once to connect — is a hole, not a detail: a name under someone else's
    control can answer with a public address for the check and `169.254.169.254` or `127.0.0.1`
    a moment later for the connection. The check then proves nothing about where the request
    actually goes, which is the whole point of having it.
    """
    u = urlparse(url)
    if u.scheme not in ("https", "http"):
        raise UrlRejected(f"回调地址必须是 http(s)：{url}")
    if u.scheme == "http" and (os.environ.get("DOCWEAVE_WEBHOOK_ALLOW_HTTP") or "").lower() not in ("1", "true", "on", "yes"):
        raise UrlRejected("回调默认要求 https；确需 http 时设置 DOCWEAVE_WEBHOOK_ALLOW_HTTP=1")
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    allow = _allow_private()
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UrlRejected(f"无法解析回调地址主机 {host}：{e}") from e

    ok = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_global or host in allow or any(_in_allow(ip, a) for a in allow):
            ok.append(str(ip))
            continue
        raise UrlRejected(f"回调地址解析到内网地址 {ip}；如确需内网回调，请把它加入 DOCWEAVE_WEBHOOK_ALLOW_PRIVATE")
    if not ok:
        raise UrlRejected(f"回调地址主机 {host} 没有解析出任何可用地址")
    return ok


def _in_allow(ip, entry: str) -> bool:
    try:
        return ip in ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return False


def sign(body: bytes, ts: int, key: str) -> str:
    return "v1=" + hmac.new(key.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def verify(body: bytes, ts: str | int, signature: str, key: str, tolerance_s: int = 300) -> bool:
    """Reference implementation for receivers (also used by the tests)."""
    try:
        if abs(time.time() - int(ts)) > tolerance_s:
            return False
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(sign(body, int(ts), key), signature or "")


class WebhookDispatcher:
    """Turns job events into deliveries and retries them with a growing delay."""

    def __init__(self, store: Store, jobs=None, start: bool = True):
        self.store = store
        self.jobs = jobs
        self._wake = threading.Event()
        self._stop = False
        self.enabled = bool(endpoints())
        self.last_error: str | None = None
        self._thread = None
        if start and self.enabled:
            if not secret():
                self.last_error = "已配置 DOCWEAVE_WEBHOOK_URLS 但缺少 DOCWEAVE_WEBHOOK_SECRET，回调未启用"
                self.enabled = False
            else:
                self._thread = threading.Thread(target=self._loop, name="webhooks", daemon=True)
                self._thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    # ---------------------------------------------------------- queue
    def enqueue_new_events(self) -> int:
        eps = endpoints()
        if not eps:
            return 0
        row = self.store.one("SELECT value FROM meta WHERE key=?", (LAST_SEQ_KEY,))
        last = int(row["value"]) if row else 0
        want = wanted_events()
        evs = self.store.all("SELECT seq, id, job_id, type FROM job_events WHERE seq>? ORDER BY seq LIMIT 500", (last,))
        n = 0
        for e in evs:
            if e["type"] in want:
                for url in eps:
                    try:
                        self.store.execute(
                            "INSERT OR IGNORE INTO webhook_deliveries (id, event_id, event_seq, endpoint, status, attempts, next_attempt_at, created_at) "
                            "VALUES (?,?,?,?,?,0,?,?)", (new_id("wh"), e["id"], e["seq"], url, "pending", now(), now()))
                        n += 1
                    except Exception as ex:  # noqa: BLE001
                        self.last_error = str(ex)
        if evs:
            self.store.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (LAST_SEQ_KEY, str(evs[-1]["seq"])))
        return n

    def due(self) -> list[dict]:
        return self.store.all("SELECT * FROM webhook_deliveries WHERE status='pending' AND next_attempt_at<=? ORDER BY created_at LIMIT 50", (now(),))

    def deliveries(self, limit: int = 50) -> list[dict]:
        return self.store.all("SELECT * FROM webhook_deliveries ORDER BY created_at DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------- send
    def payload(self, event_id: str) -> tuple[bytes, dict] | None:
        e = self.store.one("SELECT * FROM job_events WHERE id=?", (event_id,))
        if not e:
            return None
        job = self.store.one("SELECT id, project_id, status, counts, run_no, tenant, created_at, finished_at FROM jobs WHERE id=?", (e["job_id"],))
        body = {"id": e["id"], "type": e["type"], "version": e["version"], "occurred_at": e["occurred_at"],
                "tenant": e["tenant"], "job": job, "data": e["data"]}
        return json.dumps(body, ensure_ascii=False, default=str).encode(), body

    def send_one(self, d: dict) -> bool:
        made = self.payload(d["event_id"])
        if not made:
            self.store.update("webhook_deliveries", {"id": d["id"]}, {"status": "dropped", "last_error": "事件已不存在"})
            return False
        body, _ = made
        ts = int(time.time())
        attempts = (d["attempts"] or 0) + 1
        try:
            # Validate and connect to *the same* address. Calling check_url and then letting
            # urllib resolve the name again is the hole this closes.
            validated = resolve_checked(d["endpoint"])
            url = d["endpoint"]
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "DocWeave-Webhook/1",
                "X-DocWeave-Event": d["event_id"] and json.loads(body.decode())["type"],
                "X-DocWeave-Event-Id": d["event_id"],
                "X-DocWeave-Delivery": d["id"],
                "X-DocWeave-Timestamp": str(ts),
                "X-DocWeave-Signature": sign(body, ts, secret()),
            })
            opener = _pinned_opener(validated)
            with opener.open(req, timeout=float(os.environ.get("DOCWEAVE_WEBHOOK_TIMEOUT_S", "10"))) as resp:
                code = resp.status
            self.store.update("webhook_deliveries", {"id": d["id"]},
                              {"status": "delivered", "attempts": attempts, "last_code": code, "delivered_at": now(), "last_error": None})
            return True
        except urllib.error.HTTPError as e:
            retriable = e.code == 429 or e.code >= 500
            self._failed(d, attempts, e.code, f"HTTP {e.code}", retriable)
        except (UrlRejected, urllib.error.URLError, OSError, ValueError) as e:
            self._failed(d, attempts, None, str(e)[:300], not isinstance(e, UrlRejected))
        return False

    def _failed(self, d: dict, attempts: int, code: int | None, err: str, retriable: bool) -> None:
        self.last_error = err
        if not retriable or attempts >= len(BACKOFF_S) + 1:
            self.store.update("webhook_deliveries", {"id": d["id"]},
                              {"status": "failed", "attempts": attempts, "last_code": code, "last_error": err})
            return
        delay = BACKOFF_S[min(attempts - 1, len(BACKOFF_S) - 1)]
        self.store.update("webhook_deliveries", {"id": d["id"]},
                          {"status": "pending", "attempts": attempts, "last_code": code, "last_error": err,
                           "next_attempt_at": now() + delay})

    def run_once(self) -> int:
        self.enqueue_new_events()
        sent = 0
        for d in self.due():
            if self.send_one(d):
                sent += 1
        return sent

    def _loop(self) -> None:
        while not self._stop:
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001 - never kill the dispatcher
                self.last_error = f"{type(e).__name__}: {e}"
            self._wake.wait(5.0)
            self._wake.clear()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"回调地址发生跳转（{newurl}），出于安全不跟随", headers, fp)


def _pinned_opener(validated: list):
    """An opener that connects **only** to an address `resolve_checked` approved.

    Two things it does not do, both deliberate:

    * **It does not resolve the hostname again.** urllib would, and a name under someone else's
      control can answer with a public address for the check and a private one for the
      connection. Pinning is what makes the check mean anything.
    * **It does not use the ambient proxy.** `build_opener` installs a `ProxyHandler` from the
      environment by default, and a proxy both carries the signed event body somewhere nobody
      chose and performs its own resolution — which would bypass the address check entirely.
    """
    import http.client

    allowed = set(validated)

    class _PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            # Connect to the approved address; keep the hostname for SNI and certificate
            # validation, so pinning does not quietly weaken TLS.
            hostname = self.host
            sock = socket.create_connection((validated[0], self.port), self.timeout)
            self.sock = self._context.wrap_socket(sock, server_hostname=hostname)

    class _PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.create_connection((validated[0], self.port), self.timeout)

    class _HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_PinnedHTTPSConnection, req, context=self._context)

    class _HTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(_PinnedHTTPConnection, req)

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),        # no ambient proxy
        _HTTPSHandler(), _HTTPHandler(), _NoRedirect())
