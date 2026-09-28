"""TEST DOUBLE: a clamd protocol server for the Synwevo Docs tests (not a scanner).

A real socket server speaking the subset of the clamd protocol the adapter uses (zINSTREAM with
length-prefixed chunks, zPING, zVERSION). It judges by the EICAR marker at offset 0 — as the real
engine does — and can be told to go down, to enforce a stream limit, to flag particular contents
(by SHA-256, standing in for a signature match on a file that cannot carry the marker, such as a
DOCX template), to answer ERROR for particular contents (the engine failing on one file while
it keeps answering PING), or to stop flagging EICAR (a signature withdrawn after a false-positive
report). It proves the integration — framing, verdict handling, quarantine, retries, gating —
not the engine; the real engine's verdicts are recorded in docs/quality/verification.md.

Shared by test_malware_scan.py (the upload gate) and test_rescan.py (CI-90).
"""
from __future__ import annotations

import hashlib
import socket
import struct
import threading

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class ClamdDouble:
    """Speaks the subset of the clamd protocol the adapter uses."""

    def __init__(self):
        self.down = False
        self.stream_limit: int | None = None
        self.scans: list[int] = []
        #: sha256 → signature: contents this double reports as infected besides EICAR
        self.infected: dict[str, str] = {}
        #: sha256 of contents the engine answers ERROR for (it stays reachable)
        self.errors: set[str] = set()
        #: True after a false-positive report was accepted: EICAR no longer matches
        self.eicar_withdrawn = False
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.address = f"tcp://127.0.0.1:{self.port}"
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def close(self):
        self._stop = True
        try:
            self.srv.close()
        except OSError:
            pass

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket):
        with conn:
            if self.down:
                return                      # connection dropped without a reply
            cmd = b""
            while not cmd.endswith(b"\0"):
                chunk = conn.recv(1024)
                if not chunk:
                    return
                cmd += chunk
                if cmd.startswith(b"zINSTREAM\0"):
                    break
            if cmd.startswith(b"zPING"):
                conn.sendall(b"PONG\0")
            elif cmd.startswith(b"zVERSION"):
                conn.sendall(b"ClamAV double/1.0/Fri Sep 25 00:00:00 2026\0")
            elif cmd.startswith(b"zINSTREAM"):
                data = bytearray(cmd[len(b"zINSTREAM\0"):])
                # read length-prefixed chunks until the zero chunk
                payload = bytearray()
                buf = bytes(data)
                while True:
                    while len(buf) < 4:
                        more = conn.recv(65536)
                        if not more:
                            return
                        buf += more
                    n = struct.unpack(">I", buf[:4])[0]
                    buf = buf[4:]
                    if n == 0:
                        break
                    while len(buf) < n:
                        more = conn.recv(65536)
                        if not more:
                            return
                        buf += more
                    payload += buf[:n]
                    buf = buf[n:]
                    if self.stream_limit is not None and len(payload) > self.stream_limit:
                        conn.sendall(b"INSTREAM size limit exceeded. ERROR\0")
                        return
                self.scans.append(len(payload))
                digest = hashlib.sha256(bytes(payload)).hexdigest()
                # As the real engine behaves (verified against ClamAV 1.5.4): the EICAR string is
                # a signature only at offset 0 of a file (or of an archive member); embedded in the
                # middle of a CSV it is plain text. The double mirrors that so a test cannot pass
                # here with a payload the real engine would call clean.
                if digest in self.errors:
                    conn.sendall(b"stream: Can't allocate memory ERROR\0")
                elif digest in self.infected:
                    conn.sendall(b"stream: " + self.infected[digest].encode() + b" FOUND\0")
                elif payload.startswith(EICAR) and not self.eicar_withdrawn:
                    conn.sendall(b"stream: Win.Test.EICAR_HDB-1 FOUND\0")
                else:
                    conn.sendall(b"stream: OK\0")
