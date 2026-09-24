#!/usr/bin/env python3
"""Measure Argon2id cost on THIS host, so password parameters are chosen from numbers.

    .venv/bin/python apps/docs/scripts/bench_argon2.py

Run it on the machine that will actually serve logins — a laptop and a shared VPS differ by
3-4x. The parameters in packages/core/synwevo_core/passwords.py carry the measurements they came from;
re-run this and update both when the host changes.
"""
import time, statistics
from argon2 import PasswordHasher
from argon2.low_level import Type

CANDIDATES = [
    ("OWASP 基线",      19456, 2, 1),
    ("OWASP 加倍内存",  39936, 2, 1),
    ("库默认",          65536, 3, 4),
    ("64MiB p=1",       65536, 3, 1),
    ("46MiB t=2 p=1",   47104, 2, 1),
]
print(f"{'方案':16} {'内存':>9} {'t':>2} {'p':>2} {'中位耗时':>9} {'p95':>8}")
for name, m, t, p in CANDIDATES:
    ph = PasswordHasher(time_cost=t, memory_cost=m, parallelism=p, hash_len=32, salt_len=16, type=Type.ID)
    ts = []
    for _ in range(7):
        s = time.perf_counter(); ph.hash("correct horse battery staple 中文口令"); ts.append((time.perf_counter()-s)*1000)
    print(f"{name:16} {m//1024:6d}MiB {t:2d} {p:2d} {statistics.median(ts):7.1f}ms {max(ts):6.1f}ms")
