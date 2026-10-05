"""FIXTURE — a DeepSeek-compatible test double for end-to-end tests. NOT a model and NOT a proxy.

It answers on a loopback address only and never forwards anything anywhere. A Suite started with
``SYNWEVO_BILLING_ENVIRONMENT=test`` and ``SYNWEVO_DEEPSEEK_UPSTREAM=http://127.0.0.1:<port>``
sends its (fixture) platform key here instead of to the official origin; any other environment
or a non-loopback address is refused at startup (agent/deepseek.py ``upstream_base``), so the
platform key can never be pointed elsewhere.

What it reproduces, from the provider's published contract (docs/deepseek-provider.md):

* ``POST /chat/completions`` with ``stream: true``: SSE with ``: keep-alive`` comments, a role
  chunk, ``reasoning_content`` pieces while thinking is on, visible text or tool-call argument
  fragments split across chunks, and usage ONLY on the final chunk (``finish_reason`` set), then
  ``data: [DONE]``. Usage obeys the provider's identities (hit + miss = prompt, prompt + output =
  total, reasoning tokens counted inside output).
* Thinking mode with tools: a request whose earlier assistant message lacks ``reasoning_content``
  is refused with HTTP 400, as the provider does.
* ``GET /models`` (the configured ids) and ``GET /user/balance`` (a fixture balance).
* Error bodies chosen by a marker in the last user message: ``[fixture:402]`` (operator balance
  empty), ``[fixture:429]`` (with Retry-After), ``[fixture:503]``, ``[fixture:500]``,
  ``[fixture:400]``; endings ``[fixture:length]``, ``[fixture:content_filter]``,
  ``[fixture:aborted]``; ``[fixture:cut]`` drops the connection before usage; ``[fixture:tool]``
  calls the first offered tool with ``{}``.

    .venv/bin/python apps/suite/scripts/deepseek_test_double.py --port 31790 --models fixture-flash
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
ERRORS = {
    "400": (400, "invalid_request_error", "Invalid request (FIXTURE)"),
    "402": (402, "unknown_error", "Insufficient Balance (FIXTURE)"),
    "429": (429, "rate_limit_error", "Rate limit reached (FIXTURE)"),
    "500": (500, "server_error", "Server error (FIXTURE)"),
    "503": (503, "server_error", "Server overloaded (FIXTURE)"),
}
MARKER = re.compile(r"\[fixture:([a-z_0-9]+)\]")


def _bytes_tokens(value: object) -> int:
    # A deterministic stand-in for a tokenizer: about four UTF-8 bytes per token.
    return max(1, len(json.dumps(value, ensure_ascii=False).encode()) // 4)


class Double(BaseHTTPRequestHandler):
    server_version = "deepseek-test-double/1 (FIXTURE)"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # Quiet; a FIXTURE must not print request contents.
        pass

    # ------------------------------------------------------------------ helpers
    def _json(self, status: int, value: dict, headers: dict | None = None) -> None:
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, header in (headers or {}).items():
            self.send_header(key, header)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, code: str) -> None:
        status, kind, message = ERRORS[code]
        self._json(status, {"error": {"message": message, "type": kind, "param": None, "code": kind}},
                   {"Retry-After": "1"} if status == 429 else None)

    def _authorized(self) -> bool:
        if not self.headers.get("Authorization", "").startswith("Bearer ") or len(self.headers["Authorization"]) < 12:
            self._json(401, {"error": {"message": "Authentication Fails (FIXTURE)", "type": "authentication_error"}})
            return False
        return True

    def _event(self, value) -> None:
        self.wfile.write(b"data: " + (value if isinstance(value, bytes) else json.dumps(value).encode()) + b"\n\n")
        self.wfile.flush()

    # ------------------------------------------------------------------ routes
    def do_GET(self):  # noqa: N802 - http.server naming
        if not self._authorized():
            return
        if self.path == "/models":
            self._json(200, {"object": "list", "data": [
                {"id": model, "object": "model", "owned_by": "fixture"} for model in self.server.models]})
        elif self.path == "/user/balance":
            self._json(200, {"is_available": True, "balance_infos": [
                {"currency": "CNY", "total_balance": "10.00", "granted_balance": "0.00", "topped_up_balance": "10.00"}]})
        else:
            self._json(404, {"error": {"message": "Not found (FIXTURE)", "type": "invalid_request_error"}})

    def do_POST(self):  # noqa: N802
        if self.path != "/chat/completions":
            return self._json(404, {"error": {"message": "Not found (FIXTURE)", "type": "invalid_request_error"}})
        if not self._authorized():
            return
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            messages = request["messages"]
        except (ValueError, KeyError, TypeError):
            return self._error("400")
        last = next((m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"), "")
        scenario = (MARKER.search(last if isinstance(last, str) else "") or [None, self.server.scenario])[1]
        if scenario in ERRORS:
            return self._error(scenario)
        if request.get("model") not in self.server.models:
            return self._json(400, {"error": {"message": "Model Not Exist (FIXTURE)", "type": "invalid_request_error"}})
        thinking = (request.get("thinking") or {}).get("type") != "disabled"
        if thinking and request.get("tools") and any(
                m.get("role") == "assistant" and "reasoning_content" not in m for m in messages):
            # The provider's contract: thinking + tools needs every earlier turn's reasoning back.
            return self._json(400, {"error": {"message": "Missing reasoning_content (FIXTURE)",
                                              "type": "invalid_request_error"}})
        self._stream(request, scenario, thinking)

    def _stream(self, request: dict, scenario: str | None, thinking: bool) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        cid = "fixture-" + format(self.server.next_id(), "06d")
        model = request["model"]

        def chunk(delta, finish=None, usage=None):
            return {"id": cid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish, "logprobs": None}], "usage": usage}

        self.wfile.write(b": keep-alive\n\n")
        self._event(chunk({"role": "assistant", "content": ""}))
        reasoning = ["Considering the request", " (FIXTURE reasoning)."] if thinking else []
        for piece in reasoning:
            self._event(chunk({"reasoning_content": piece}))
        self.wfile.write(b": keep-alive\n\n")
        tools = request.get("tools") or []
        if scenario == "tool" and tools:
            name = tools[0]["function"]["name"]
            self._event(chunk({"tool_calls": [{"index": 0, "id": "call_fixture_" + cid[-6:], "type": "function",
                                               "function": {"name": name, "arguments": ""}}]}))
            for fragment in ("{", "}"):
                self._event(chunk({"tool_calls": [{"index": 0, "function": {"arguments": fragment}}]}))
            finish, output = "tool_calls", ["{}"]
        else:
            output = ["This is a ", "FIXTURE answer."]
            for piece in output:
                self._event(chunk({"content": piece}))
            finish = {"length": "length", "content_filter": "content_filter", "aborted": "aborted"}.get(scenario, "stop")
        if scenario == "cut":
            return  # The connection closes before usage and [DONE]: usage stays unknown.
        prompt = _bytes_tokens(request.get("messages")) + (_bytes_tokens(tools) if tools else 0)
        hit = prompt // 2 if self.server.cache_hits else 0
        reasoning_tokens = sum(_bytes_tokens(p) for p in reasoning)
        completion = reasoning_tokens + sum(_bytes_tokens(p) for p in output)
        usage = {"prompt_tokens": prompt, "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": prompt - hit,
                 "completion_tokens": completion, "total_tokens": prompt + completion,
                 "prompt_tokens_details": {"cached_tokens": hit},
                 "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}
        self._event(chunk({}, finish, usage))
        self._event(b"[DONE]")


class DoubleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int, *, models=("fixture-model",), scenario=None, cache_hits=False):
        try:
            loopback = host in LOOPBACK_HOSTS or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("the DeepSeek test double only listens on a loopback address")
        if ":" in host:
            self.address_family = socket.AF_INET6
        super().__init__((host, port), Double)
        self.models, self.scenario, self.cache_hits = tuple(models), scenario, cache_hits
        self._ids, self._lock = 0, threading.Lock()

    def next_id(self) -> int:
        with self._lock:
            self._ids += 1
            return self._ids

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"


def serve(host="127.0.0.1", port=0, **options) -> DoubleServer:
    """Start the double in a daemon thread (for tests); call ``shutdown()`` to stop it."""
    server = DoubleServer(host, port, **options)
    threading.Thread(target=server.serve_forever, name="deepseek-test-double", daemon=True).start()
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="FIXTURE DeepSeek-compatible test double (loopback only)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=31790)
    parser.add_argument("--models", nargs="+", default=["fixture-model"])
    parser.add_argument("--scenario", choices=sorted({*ERRORS, "tool", "length", "content_filter", "aborted", "cut"}))
    parser.add_argument("--cache-hits", action="store_true", help="report half of each prompt as a cache hit")
    args = parser.parse_args()
    server = DoubleServer(args.host, args.port, models=args.models, scenario=args.scenario, cache_hits=args.cache_hits)
    print(f"FIXTURE DeepSeek test double on {server.base_url} (loopback only; not a model)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
