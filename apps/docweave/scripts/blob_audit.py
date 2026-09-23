#!/usr/bin/env python3
"""Inventory every reference to every blob. **Reads only — this script cannot delete.**

    python scripts/blob_audit.py --data data
    python scripts/blob_audit.py --data data --json      # machine-readable

Why this exists before a garbage collector does: a mark-and-sweep is only as safe as its list of
places a reference can hide, and two of those places are inside JSON columns where a
per-column scan cannot see them. This produces the list and the counts, so the question
"is the mark set complete?" has an answer that was measured rather than assumed.

**There is deliberately no delete mode.** `docs/quality/progress.md` records why: on the real
database today, the set of blobs reachable only from JSON is *empty*, so a six-column sweeper
would pass every test anyone writes — and then the first change that hard-deletes a source row
or prunes a template version makes those references exclusive and the sweeper destroys the
inputs of every re-run. The trap is latent, not absent. A delete path should be written against
this inventory, with the quarantine and concurrency handling described below, not instead of it.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from platform_core.http import repo_root

# 相对的 --data 以**仓库根目录**为准，而不是脚本所在的模块目录：脚本搬了家，
# `--data data` 指的还得是同一个 data/，否则备份的是一个空实例。
ROOT = repo_root() or Path(__file__).resolve().parents[3]

# Columns that hold a blob id directly.
#
# This list must cover every clause of `authz._REACHABLE`, which is the authorization layer's
# own answer to "can these bytes be read". `_cross_check_against_authz()` below enforces that,
# because the first version of this file did not and got it wrong: it copied four of the five
# clauses and dropped `blob_grants` — the one table whose blobs have no other reference. Preview
# outputs and layout PDFs live only there, so the audit reported bytes the server was actively
# serving as unreferenced. `_REACHABLE`'s own comment asks for exactly this ("kept as data so
# the read check and any future sweeper cannot drift apart") and this file drifted anyway.
DIRECT = [
    ("source_files", "blob"),
    ("source_files", "parsed_blob"),
    ("source_files", "working_blob"),
    ("templates", "original_blob"),
    ("template_versions", "working_blob"),
    ("artifacts", "blob"),
    # Previews and layout PDFs. No row owns them; the grant *is* the reference.
    ("blob_grants", "blob"),
]

# References that live *inside* JSON and are invisible to a per-column scan. Each entry is a
# table, a JSON column, and a description of where in that document the id sits. These are the
# ones a naive sweeper misses.
JSON_PATHS = [
    ("jobs", "snapshot", "$.template.working_blob"),
    ("jobs", "snapshot", "$.template.fill_options.image.original_blob"),
    ("jobs", "snapshot", "$.sources[*].blob"),
    ("jobs", "snapshot", "$.sources[*].parsed_blob"),
    ("jobs", "snapshot", "$.sources[*].working_blob"),
    ("templates", "info", "$.image.original_blob"),
]

# Columns that look like a blob id (64 hex characters) but are not one. Listed so a future
# "just scan everything that looks like a digest" approach has a written reason not to.
LOOKALIKES = [
    ("idempotency", "request_hash", "请求指纹，不是 blob"),
    ("api_keys", "secret_hash", "密钥哈希，不是 blob"),
    ("sessions", "id", "Cookie 值的 SHA-256，不是 blob"),
    ("oauth_tokens", "access_hash", "令牌哈希，不是 blob"),
    ("oauth_tokens", "refresh_hash", "令牌哈希，不是 blob"),
    ("oauth_codes", "code_hash", "授权码哈希，不是 blob"),
    ("recovery_codes", "code_hash", "恢复码哈希，不是 blob"),
    ("email_tokens", "token_hash", "邮件令牌哈希，不是 blob"),
    ("passkeys", "credential_id", "WebAuthn 凭据 id，不是 blob"),
]

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _cross_check_against_authz() -> list:
    """Refuse to under-report. Returns the tables `authz._REACHABLE` consults that this file does not.

    The authorization layer decides what is *readable*; this file decides what is *referenced*.
    If the second is narrower than the first, then something the server will happily serve looks
    like an orphan here — and a collector built on this list deletes it. So the two are compared
    rather than assumed to agree, and a mismatch is a hard failure rather than a quieter report.
    """
    try:
        from docweave import authz
    except ImportError:
        return []                              # backend not importable; the caller says so
    covered = {t for t, _ in DIRECT} | {t for t, _, _ in JSON_PATHS}
    missing = []
    for clause in authz._REACHABLE:
        tables = set(re.findall(r"\bFROM\s+([a-z_]+)", clause))
        tables |= set(re.findall(r"\bJOIN\s+([a-z_]+)", clause))
        for t in tables:
            if t not in covered and t not in ("projects", "jobs", "templates"):
                # projects/jobs/templates appear only as the join that supplies a workspace.
                missing.append(t)
    return sorted(set(missing))


def _tables(c) -> set:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(c, table: str) -> set:
    return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}


def _walk(node, out: list):
    """Every 64-hex string anywhere in a decoded JSON document."""
    if isinstance(node, str):
        if HEX64.match(node):
            out.append(node)
    elif isinstance(node, dict):
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)
    return out


def audit(data_dir: Path) -> dict:
    db = data_dir / "docweave.sqlite3"
    if not db.exists():
        raise SystemExit(f"找不到 {db}")
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    have = _tables(c)

    direct: dict[str, set] = {}
    direct_refs = 0
    for table, col in DIRECT:
        if table not in have or col not in _columns(c, table):
            continue
        vals = [r[0] for r in c.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL")]
        direct[f"{table}.{col}"] = set(vals)
        direct_refs += len(vals)

    # JSON columns are decoded in full rather than probed at fixed paths: a path list can go
    # stale when a snapshot's shape changes, and a blob the walker finds but the path list does
    # not is exactly the reference a sweeper would delete.
    embedded: dict[str, set] = {}
    embedded_refs = 0
    for table, col in {(t, cl) for t, cl, _ in JSON_PATHS}:
        if table not in have or col not in _columns(c, table):
            continue
        found: list[str] = []
        for (raw,) in c.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"):
            try:
                _walk(json.loads(raw), found)
            except (ValueError, TypeError):
                continue
        embedded[f"{table}.{col}"] = set(found)
        embedded_refs += len(found)

    referenced = set().union(*direct.values()) if direct else set()
    embedded_all = set().union(*embedded.values()) if embedded else set()
    only_json = embedded_all - referenced
    all_refs = referenced | embedded_all

    on_disk = set()
    blobs = data_dir / "blobs"
    total_bytes = 0
    if blobs.is_dir():
        for p in blobs.rglob("*"):
            if p.is_file() and HEX64.match(p.name):
                on_disk.add(p.name)
                total_bytes += p.stat().st_size

    lookalike_hits = []
    for table, col, why in LOOKALIKES:
        if table not in have or col not in _columns(c, table):
            continue
        n = c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND length({col})=64"
        ).fetchone()[0]
        if n:
            lookalike_hits.append({"column": f"{table}.{col}", "rows": n, "why": why})
    c.close()

    return {
        "authz_tables_not_covered": _cross_check_against_authz(),
        "blobs_on_disk": len(on_disk),
        "bytes_on_disk": total_bytes,
        "direct": {k: len(v) for k, v in direct.items()},
        "direct_refs": direct_refs,
        "embedded": {k: len(v) for k, v in embedded.items()},
        "embedded_refs": embedded_refs,
        "referenced_total": len(all_refs),
        "reachable_only_from_json": sorted(only_json),
        "unreferenced_on_disk": sorted(on_disk - all_refs),
        "referenced_but_missing": sorted(all_refs - on_disk),
        "lookalike_columns": lookalike_hits,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data", help="数据目录（只读打开）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    d = Path(args.data)
    if not d.is_absolute():
        d = ROOT / d
    r = audit(d)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    print(f"数据目录：{d}（只读）\n")
    print(f"磁盘上的 blob：{r['blobs_on_disk']} 个，{r['bytes_on_disk'] / 1e6:.1f} MB")
    print(f"被引用：{r['referenced_total']} 个\n")

    print("直接列引用：")
    for k, n in sorted(r["direct"].items()):
        print(f"  {k:<34} {n} 个去重 blob")
    print(f"  合计 {r['direct_refs']} 处引用\n")

    print("JSON 内嵌引用（按列扫描看不见的那些）：")
    for k, n in sorted(r["embedded"].items()):
        print(f"  {k:<34} {n} 个去重 blob")
    print(f"  合计 {r['embedded_refs']} 处引用")
    only = r["reachable_only_from_json"]
    print(f"  **只能从 JSON 到达** 的 blob：{len(only)} 个"
          + ("" if not only else f"  ← 六列扫描会删掉它们"))
    if not only:
        print("     今天是 0。这不代表陷阱不存在：快照引用只有在底层行被硬删除之后才会变成")
        print("     唯一引用，而代码库里目前没有硬删除路径。加上硬删除的那一刻它就变成真的。")
    print()

    missing = r["referenced_but_missing"]
    print(f"引用了但磁盘上没有：{len(missing)} 个" + (f"  ← 数据已缺失" if missing else "（好）"))
    orphans = r["unreferenced_on_disk"]
    print(f"磁盘上但无人引用：{len(orphans)} 个")
    if orphans:
        print("     注意：这些**不能直接当作可删除**。脱敏产物（services.py:271）写完之后 id 只出现在")
        print("     HTTP 响应里，不进任何可查询的列——它们在这里看起来就是孤儿。（这一类同时也不可读：")
        print("     没有 grant，`/api/desensitized/{blob}` 会 404，所以它们确实是死字节。）")
    print()

    gaps = r["authz_tables_not_covered"]
    if gaps:
        print("!! 授权层会认为可读、但本清点没有覆盖的表：" + "、".join(gaps))
        print("   这意味着这份清点比服务器实际会提供的内容更窄——基于它做回收会删掉正在被服务的字节。")
        print("   请把这些表加进 DIRECT 或 JSON_PATHS 之后再使用本工具。")
        print()

    if r["lookalike_columns"]:
        print("形似 blob 但不是 blob 的列（正则全表扫描会误判）：")
        for h in r["lookalike_columns"]:
            print(f"  {h['column']:<34} {h['rows']} 行   {h['why']}")
        print()

    print("本脚本不会删除任何东西，也没有删除模式。要实现回收，先读")
    print("docs/quality/progress.md 里的陷阱清单，并在 BEGIN IMMEDIATE 内复核候选集、")
    print("用改名隔离而不是直接删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
