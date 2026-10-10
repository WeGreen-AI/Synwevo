"""Killable pure parsing jobs. Children receive bytes and a reader, never a Store or credentials."""
from __future__ import annotations

import json
import multiprocessing
import os
import signal
import sys
import time

from synwevo_core.errors import ServiceError

TIMEOUT = 90
MAX_RESULT_BYTES = 2 * 1024 * 1024


def _run(pipe, fn, args):
    try:
        if os.name == 'posix':
            os.setsid()
            import resource
            resource.setrlimit(resource.RLIMIT_CPU, (TIMEOUT, TIMEOUT))
            if sys.platform.startswith('linux'):
                resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        result = fn(*args)
        payload = json.dumps({'parsed': result}, ensure_ascii=False).encode()
        if len(payload) > MAX_RESULT_BYTES:
            raise ServiceError(413, 'parse_limit', '解析结果过大')
        pipe.send_bytes(payload)
    except ServiceError as exc:
        pipe.send_bytes(json.dumps({'error': exc.code}).encode())
    except Exception:
        pipe.send_bytes(b'{"error":"parse_failed"}')
    finally:
        pipe.close()


def run_bounded(fn, args, check, *, timeout=TIMEOUT):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_run, args=(child, fn, args), daemon=True)
    started = False
    try:
        process.start()
        started = True
        child.close()
        deadline = time.monotonic() + timeout
        while True:
            check()
            if time.monotonic() > deadline:
                raise ServiceError(504, 'parse_timeout', '解析超时，请拆分文件后重试')
            if parent.poll(.1):
                try:
                    payload = json.loads(parent.recv_bytes(MAX_RESULT_BYTES))
                except (EOFError, OSError, ValueError):
                    raise ServiceError(422, 'parse_failed', '解析进程未返回完整结果') from None
                if 'error' in payload:
                    raise ServiceError(422, payload['error'], '文件解析未完成')
                check()
                return payload['parsed']
            if not process.is_alive():
                raise ServiceError(422, 'parse_failed', '解析进程意外退出')
    finally:
        child.close()
        parent.close()
        if started:
            if os.name == 'posix':
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.is_alive():
                process.kill()
            process.join(timeout=2)
