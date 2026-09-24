#!/usr/bin/env python3
"""A reference receiver for Synwevo Docs webhooks. Standard library only; copy it freely.

    SYNWEVO_WEBHOOK_SECRET=whsec_... python webhook_receiver.py --port 8080
    # during a secret rotation, both: SYNWEVO_WEBHOOK_SECRET=whsec_new,whsec_old

It does what every receiver has to do, in the order it has to be done:

1. **Read the raw body, bounded.** The signature covers the exact bytes that were sent. Parsing
   the JSON and serialising it again (another key order, other separators, ``1.0`` vs ``1``)
   produces different bytes and a signature that never matches.
2. **Check ``webhook-timestamp``** is within five minutes of now, so a captured request cannot be
   replayed later. Every retry is signed afresh, so a genuine retry always passes this.
3. **Check ``webhook-signature``**: ``v1,<base64 HMAC-SHA256(key, "<webhook-id>.<timestamp>.<body>")>``
   where the key is the base64 part of ``whsec_…``. The header may list several signatures
   (during a rotation the old and the new secret both sign); one match is enough. Compare in
   constant time.
4. **Deduplicate on ``webhook-id``.** It is the event id: the same on every retry and on a manual
   redelivery. Deliveries can repeat and can arrive out of order.
5. **Acknowledge only after successful handling or durable enqueueing.** This small example
   runs `handle()` before answering; failures return 503 so the sender retries. For slow work,
   replace `handle()` with a durable queue insert and process that queue separately. Synwevo
   waits 15 seconds. An in-memory queue followed by 2xx can lose events on a crash.
   Processed duplicates get 2xx; concurrent work gets 503 until it succeeds. Answer 410 only
   if this endpoint should be switched off for good.

Accepted events are printed as one JSON line each on stdout; replace `handle()` with your own.
The optional seen-file survives restart, but is not atomic with your business transaction.
Make your handler idempotent on webhook-id to cover a crash between work and recording the id.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOLERANCE_S = 300
MAX_BODY = 1 << 20          # Synwevo payloads are a few hundred bytes; refuse anything absurd


class Rejected(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


def key_bytes(secret: str) -> bytes:
    if secret.startswith("whsec_"):
        return base64.b64decode(secret[len("whsec_"):])
    return secret.encode("utf-8")


def expected_signature(secret: str, msg_id: str, ts: str, body: bytes) -> str:
    mac = hmac.new(key_bytes(secret), f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode("ascii")


class Receiver:
    """Verification and deduplication, independent of the HTTP server around it."""

    def __init__(self, secrets: list[str], *, tolerance_s: int = TOLERANCE_S, seen_limit: int = 100_000,
                 seen_file: str | None = None, clock=time.time):
        self.secrets = secrets                  # a list, so a rotation can add the new one live
        self.tolerance_s = tolerance_s
        self.clock = clock
        self.seen: OrderedDict[str, float] = OrderedDict()
        self.in_progress: set[str] = set()
        self.seen_limit = seen_limit
        self.seen_file = seen_file
        self.lock = threading.Lock()
        self.accepted = 0
        self.duplicates = 0
        self.rejected = 0
        if seen_file and os.path.exists(seen_file):
            with open(seen_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.seen[line.strip()] = 0.0

    def verify(self, headers, body: bytes) -> dict:
        """Return the parsed event, or raise `Rejected`."""
        msg_id = (headers.get("webhook-id") or "").strip()
        ts = (headers.get("webhook-timestamp") or "").strip()
        sigs = (headers.get("webhook-signature") or "").split()
        if not msg_id or not ts or not sigs:
            raise Rejected(400, "缺少 webhook-id / webhook-timestamp / webhook-signature")
        try:
            sent = int(ts)
        except ValueError:
            raise Rejected(400, "webhook-timestamp 不是整数") from None
        if abs(self.clock() - sent) > self.tolerance_s:
            raise Rejected(401, "时间戳超出允许的偏差（可能是重放）")
        ok = any(hmac.compare_digest(expected_signature(s, msg_id, ts, body).encode(), sig.encode())
                 for s in self.secrets if s for sig in sigs)
        if not ok:
            raise Rejected(401, "签名不匹配")
        try:
            event = json.loads(body)
        except ValueError:
            raise Rejected(400, "请求体不是 JSON") from None
        if not isinstance(event, dict) or event.get("id") != msg_id:
            raise Rejected(400, "webhook-id 与事件 id 不一致")
        return event

    def process(self, event: dict, on_event) -> None:
        """Reserve concurrent work, then record the id only after handling succeeds."""
        msg_id = event["id"]
        with self.lock:
            if msg_id in self.seen:
                self.seen.move_to_end(msg_id)
                self.duplicates += 1
                return
            if msg_id in self.in_progress:
                raise Rejected(503, "事件正在处理，请稍后重试")
            self.in_progress.add(msg_id)
        try:
            on_event(event)
            with self.lock:
                if self.seen_file:
                    with open(self.seen_file, "a", encoding="utf-8") as f:
                        f.write(msg_id + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                self.seen[msg_id] = self.clock()
                while len(self.seen) > self.seen_limit:
                    self.seen.popitem(last=False)
                self.accepted += 1
        except Exception:
            # Exception messages may contain document data or secrets. The sender only needs
            # to know that it should retry; the id is deliberately not marked processed.
            raise Rejected(503, "事件处理未完成，请重试") from None
        finally:
            with self.lock:
                self.in_progress.discard(msg_id)


def handle(event: dict) -> None:
    """Your work (or durable enqueue) goes here. Return successfully before acknowledging."""
    print(json.dumps({"received_at": time.time(), "id": event.get("id"), "type": event.get("type"),
                      "tenant": event.get("tenant"), "job": event.get("job")}, ensure_ascii=False), flush=True)


def make_server(host: str, port: int, receiver: Receiver, on_event=handle) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                return self._answer(413, "请求体过大")
            body = self.rfile.read(length)
            try:
                event = receiver.verify(self.headers, body)
            except Rejected as e:
                with receiver.lock:
                    receiver.rejected += 1
                return self._answer(e.status, e.reason)
            try:
                receiver.process(event, on_event)
            except Rejected as e:
                return self._answer(e.status, e.reason)
            self._answer(204)

        def _answer(self, code: int, reason: str | None = None):
            payload = json.dumps({"error": reason}).encode() if reason else b""
            self.send_response(code)
            if payload:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def log_message(self, fmt, *args):       # keep stdout for events
            print(f"{self.address_string()} {fmt % args}", file=sys.stderr)

    return ThreadingHTTPServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--seen-file", help="persist processed webhook-ids here so a restart still deduplicates")
    ap.add_argument("--tolerance-s", type=int, default=TOLERANCE_S)
    args = ap.parse_args(argv)
    secrets = [s.strip() for s in (os.environ.get("SYNWEVO_WEBHOOK_SECRET") or "").split(",") if s.strip()]
    if not secrets:
        print("请把创建订阅时显示的 whsec_… 密钥设为 SYNWEVO_WEBHOOK_SECRET", file=sys.stderr)
        return 2
    rx = Receiver(secrets, tolerance_s=args.tolerance_s, seen_file=args.seen_file)
    srv = make_server(args.host, args.port, rx)
    print(f"正在监听 http://{args.host}:{srv.server_port}/", file=sys.stderr, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
