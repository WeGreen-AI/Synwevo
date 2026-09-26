"""A tiny HTTP forward proxy that resolves every `*.localhost` name to the loopback address.

Why: Playwright's WebKit resolves hostnames through the operating system, which has no entries
for `accounts.localhost`, `portal.localhost` or `docweave.localhost` (Chromium resolves them
itself, per RFC 6761). Pointing WebKit at this proxy keeps the browser side untouched — each
origin still gets its own host-only cookie jar and the cross-origin single sign-on hops run for
real — while the socket goes to 127.0.0.1 on the port the URL names.

Plain HTTP targets; CONNECT is tunnelled to the loopback address (Playwright's API request context uses it). Test infrastructure, never
part of a deployment.

    from localhost_proxy import LocalhostProxy
    with LocalhostProxy() as proxy:
        browser.new_context(proxy={"server": proxy.url})
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
from urllib.parse import urlsplit


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    """Copy bytes one way until the source ends; used for CONNECT tunnels and WebSocket upgrades."""
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


class LocalhostProxy:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host, self.port = host, port
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: asyncio.AbstractServer | None = None
        self._ready = threading.Event()

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, name="localhost-proxy", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("localhost proxy did not start")
        return self

    def __exit__(self, *exc):
        if self._loop and self._server:
            self._loop.call_soon_threadsafe(self._server.close)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(3)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._server = self._loop.run_until_complete(asyncio.start_server(self._handle, self.host, self.port))
        self.port = self._server.sockets[0].getsockname()[1]
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                request_line, _, header_block = head.partition(b"\r\n")
                method, target, version = request_line.decode("latin-1").split(" ", 2)
                if os.environ.get("SYNWEVO_E2E_DEBUG"):
                    print(f"[proxy] {method} {target}", file=sys.stderr, flush=True)
                if method == "CONNECT":
                    # Playwright's API request context tunnels even plain-HTTP targets through
                    # CONNECT: open the TCP connection to the loopback address and pipe bytes.
                    host, _, port_s = target.rpartition(":")
                    addr = "127.0.0.1" if host == "localhost" or host.endswith(".localhost") else host
                    try:
                        up_r, up_w = await asyncio.open_connection(addr, int(port_s or 80))
                    except OSError:
                        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                        await writer.drain()
                        return
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                    await writer.drain()

                    async def pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
                        try:
                            while True:
                                chunk = await src.read(65536)
                                if not chunk:
                                    break
                                dst.write(chunk)
                                await dst.drain()
                        except (ConnectionError, asyncio.CancelledError):
                            pass
                        finally:
                            try:
                                dst.close()
                            except Exception:
                                pass
                    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))
                    return
                parts = urlsplit(target)
                # WebKit sends a WebSocket handshake to a proxy as an absolute ws:// request line.
                if parts.scheme not in ("http", "ws") or not parts.hostname:
                    writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                    return
                host = parts.hostname
                port = parts.port or 80
                addr = "127.0.0.1" if host == "localhost" or host.endswith(".localhost") else host
                headers = [h for h in header_block.split(b"\r\n") if h]
                length = 0
                out_headers = []
                # A WebSocket handshake (ws:// through a proxy is a plain GET with Upgrade) must keep
                # its Connection/Upgrade pair and, once answered, becomes a two-way byte pipe.
                upgrade = any(h.split(b":", 1)[0].strip().lower() == b"upgrade" for h in headers)
                for h in headers:
                    name, _, value = h.partition(b":")
                    lname = name.strip().lower()
                    if lname == b"content-length":
                        length = int(value.strip() or 0)
                    if lname in (b"proxy-connection", b"keep-alive") or (lname == b"connection" and not upgrade):
                        continue
                    out_headers.append(h)
                body = await reader.readexactly(length) if length else b""
                path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
                upstream_r, upstream_w = await asyncio.open_connection(addr, port)
                tail = b"\r\n\r\n" if upgrade else b"\r\nConnection: close\r\n\r\n"
                upstream_w.write(f"{method} {path} HTTP/1.1\r\n".encode("latin-1") + b"\r\n".join(out_headers) + tail + body)
                await upstream_w.drain()
                if upgrade:
                    await asyncio.gather(_pipe(upstream_r, writer), _pipe(reader, upstream_w))
                    return
                # relay the response as-is; the upstream closes, so `Connection: close` tells the
                # browser to open a fresh connection for the next request
                resp_head = await upstream_r.readuntil(b"\r\n\r\n")
                writer.write(resp_head)
                await writer.drain()
                while True:
                    chunk = await upstream_r.read(65536)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                upstream_w.close()
                writer.close()
                return
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            try:
                writer.close()
            except Exception:
                pass


if __name__ == "__main__":  # manual check: python localhost_proxy.py → prints the proxy URL and waits
    import time
    with LocalhostProxy(port=31799) as p:
        print(p.url, flush=True)
        while True:
            time.sleep(3600)
