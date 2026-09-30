"""Killable parsing process. The injected reader is trusted code; file bytes are untrusted.

Readers must be spawn-pickleable and have describe()/extract(bytes, filename, ocr=...).
No database, session, or credentials are passed to the child. No unbounded thread timeout.
"""
from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sys
import time
from typing import Protocol

from synwevo_core.errors import ServiceError
from .definition import normalize

TIMEOUT = 90
MAX_RESULT_BYTES = 2 * 1024 * 1024


class Reader(Protocol):
    def describe(self) -> dict: ...
    def extract(self, data: bytes, filename: str, *, ocr: bool = False) -> dict: ...


def _parse(pipe, reader: Reader, data: bytes, filename: str, ocr: bool):
    try:
        if os.name == "posix":
            os.setsid()  # OCR subprocesses share this group; timeout kills descendants too.
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        description = reader.describe()
        if ocr and not description.get("ocr"):
            raise ServiceError(503, "ocr_not_configured", "文字识别服务未配置")
        parsed = normalize(reader.extract(data, filename, ocr=ocr), description, ocr=ocr)
        payload = json.dumps({"parsed": parsed}, ensure_ascii=False).encode()
        if len(payload) > MAX_RESULT_BYTES:
            raise ServiceError(413, "parse_limit", "解析结果过大")
        pipe.send_bytes(payload)
    except ServiceError as exc:
        pipe.send_bytes(json.dumps({"error": exc.code}).encode())
    except Exception:
        # Parser exceptions may contain file contents, filenames or provider secrets.
        pipe.send_bytes(b'{"error":"parse_failed"}')
    finally:
        pipe.close()


def parse_bounded(reader: Reader, data: bytes, filename: str, ocr: bool, check, *, timeout=TIMEOUT) -> dict:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_parse, args=(child, reader, data, filename, ocr), daemon=True)
    started = False
    try:
        process.start()
        started = True
        child.close()
        deadline = time.monotonic() + timeout
        while True:
            check()  # cancellation, current membership, application access and lease
            if time.monotonic() > deadline:
                raise ServiceError(504, "parse_timeout", "解析超时，请拆分文件后重试")
            if parent.poll(0.1):
                try:
                    payload = json.loads(parent.recv_bytes(MAX_RESULT_BYTES))
                except (EOFError, OSError, ValueError):
                    raise ServiceError(422, "parse_failed", "解析进程未返回完整结果") from None
                if "error" in payload:
                    raise ServiceError(422, payload["error"], "文件解析未完成")
                check()
                return payload["parsed"]
            if not process.is_alive():
                raise ServiceError(422, "parse_failed", "解析进程意外退出")
    finally:
        child.close()
        parent.close()
        if started:
            # Even after the reader returns, remove any orphan OCR child processes.
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.is_alive():
                process.kill()
            process.join(timeout=2)
