#!/usr/bin/env python3
"""Inventory every reference to every blob. **Reads only — this script cannot delete.**

    .venv/bin/python apps/docs/scripts/blob_audit.py --data data
    .venv/bin/python apps/docs/scripts/blob_audit.py --data data --json      # machine-readable

Exit status 1 when a referenced blob is missing, or when this inventory does not cover every table
the authorization layer would serve bytes from (or that comparison could not be made).

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

from synwevo_core.http import repo_root

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
    # The same store serves Synwevo Message and resumable uploads and organization publications,
    # each behind its own read gate that `authz._REACHABLE` does not describe. Missing them made
    # every message attachment whose bytes are its own look like an orphan (docs/platform/
    # blob-reclamation.md §1.2).
    ("message_attachments", "blob"),
    ("uploads", "blob"),                       # a completed upload not used yet: its only reference
    ("organization_templates", "working_blob"),  # outlives the source template by design
    ("organization_template_releases", "working_blob"),
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
    ("organization_templates", "info", "$.image.original_blob"),
    ("organization_template_releases", "info", "$.image.original_blob"),
]

# Files on disk (outside the database) that name blobs: a captured regression case keeps its
# template's fill options, image original included (regression.py). Walked like a JSON column.
FILE_ROOTS = [("regression", "*/*/case.json", "回归用例的 case.json（模板图片原图）")]

# Columns that hold the bytes themselves (SQLite BLOBs), not a digest: guessing "a column named
# *blob* holds an id" would hand these to `blob_path()`.
INLINE_BYTES = [
    ("users", "avatar_blob"), ("organization_avatars", "image"), ("conversation_avatars", "image"), ("collab_docs", "state"),
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
    ("uploads", "sha256", "客户端声明的摘要；完成后才等于 uploads.blob"),
    ("member_imports", "sha256", "导入原文件在 member_imports/，不在 blob 存储里"),
    ("project_copies", "digest", "复制申请的清单指纹；执行时重读活行，不钉住字节"),
]

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _cross_check_against_quarantine() -> list | None:
    """The second read gate that names referencing tables: the quarantine's own list (it tells an
    operator which application still points at withdrawn bytes). Tables it names and this file
    does not cover are an under-report, like `_cross_check_against_authz`."""
    try:
        from synwevo_core.quarantine import REFERENCING_TABLES
    except ImportError:
        return None
    covered = {t for t, _ in DIRECT} | {t for t, _, _ in JSON_PATHS}
    return sorted({t for tables in REFERENCING_TABLES.values() for t in tables} - covered)


def _cross_check_against_authz() -> list | None:
    """Refuse to under-report. Returns the tables `authz._REACHABLE` consults that this file does not,
    or None when the authorization layer cannot be imported (so the comparison was not made).

    The authorization layer decides what is *readable*; this file decides what is *referenced*.
    If the second is narrower than the first, then something the server will happily serve looks
    like an orphan here — and a collector built on this list deletes it. So the two are compared
    rather than assumed to agree, and a mismatch is a hard failure rather than a quieter report.
    """
    try:
        from synwevo_docs import authz
    except ImportError:
        return None                            # not importable: "not checked", never "no gaps"
    covered = {t for t, _ in DIRECT} | {t for t, _, _ in JSON_PATHS}
    missing = []
    for clause in authz._REACHABLE:
        tables = set(re.findall(r"\bFROM\s+([a-z_]+)", clause))
        tables |= set(re.findall(r"\bJOIN\s+([a-z_]+)", clause))
        for t in tables:
            if t not in covered and t not in ("projects", "jobs", "templates", "json_each"):
                # projects/jobs/templates appear only as the join that supplies a workspace (or the
                # verdict a row is checked against); json_each is SQLite's JSON table function.
                missing.append(t)
    return sorted(set(missing))


def _file_root(data_dir: Path, root: str) -> Path | None:
    if root == "regression":
        try:
            from synwevo_docs.regression import cases_root
            return cases_root(data_dir)       # SYNWEVO_DOCS_REGRESSION_DIR may move it out of the data directory
        except ImportError:
            return data_dir / "regression"
    return data_dir / root


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

    for root, pattern, _why in FILE_ROOTS:
        base = _file_root(data_dir, root)
        found = []
        if base is not None and base.is_dir():
            for f in base.glob(pattern):
                try:
                    _walk(json.loads(f.read_text(encoding="utf-8")), found)
                except (OSError, ValueError):
                    continue
        embedded[f"file:{root}"] = set(found)
        embedded_refs += len(found)

    referenced = set().union(*direct.values()) if direct else set()
    embedded_all = set().union(*embedded.values()) if embedded else set()
    only_json = embedded_all - referenced
    all_refs = referenced | embedded_all

    on_disk = set()
    blobs = data_dir / "blobs"
    total_bytes = 0
    partial: list[str] = []                   # put_blob's temporary files left by a crash
    misplaced: list[str] = []                 # a digest outside its <h[:2]>/ directory
    if blobs.is_dir():
        for p in blobs.rglob("*"):
            if not p.is_file():
                continue
            if HEX64.match(p.name):
                on_disk.add(p.name)
                total_bytes += p.stat().st_size
                if p.parent.name != p.name[:2]:
                    misplaced.append(p.name)
            else:
                partial.append(str(p.relative_to(blobs)))

    # Bytes withdrawn on purpose (CI-90). Their rows still name the digest — that is the record of
    # the verdict — so "referenced and absent" is expected for them and must not read as data loss,
    # or make a backup that contains the verdict unrestorable. Excused only reference by reference:
    # a missing digest is withdrawn when every row naming it is itself rejected (a source's upload)
    # or infected (a template, any of its contents), and — when the quarantine index says the
    # bytes are held — the held copy actually exists. A clean row naming it makes it missing.
    rejected_refs: set[str] = set()
    rejected_uploads: set[str] = set()           # a rejected source's own bytes (the upload gate unlinks them)
    live_refs: set[str] = set()
    if "source_files" in have and "status" in _columns(c, "source_files"):
        for blob, status, parsed, working in c.execute("SELECT blob, status, parsed_blob, working_blob FROM source_files"):
            if status == "rejected":
                if blob:
                    rejected_refs.add(blob)
                    rejected_uploads.add(blob)
            else:
                live_refs.update(b for b in (blob, parsed, working) if b)
    if "templates" in have and "scan" in _columns(c, "templates"):
        infected = set()
        for tid, orig, info, scan in c.execute("SELECT id, original_blob, info, scan FROM templates"):
            try:
                is_infected = (json.loads(scan) if scan else {}).get("status") == "infected"
            except (ValueError, AttributeError):
                is_infected = False
            try:
                img = ((json.loads(info) if info else {}) or {}).get("image") or {}
            except (ValueError, AttributeError):
                img = {}
            refs = {b for b in (orig, img.get("original_blob")) if b}
            (rejected_refs if is_infected else live_refs).update(refs)
            if is_infected:
                infected.add(tid)
        for tid, working in c.execute("SELECT template_id, working_blob FROM template_versions WHERE working_blob IS NOT NULL"):
            (rejected_refs if tid in infected else live_refs).add(working)
    if "message_attachments" in have and "status" in _columns(c, "message_attachments"):
        for blob, status in c.execute("SELECT blob, status FROM message_attachments WHERE blob IS NOT NULL"):
            (rejected_refs if status == "rejected" else live_refs).add(blob)
    for table in ("organization_templates", "organization_template_releases"):
        if table in have and {"working_blob", "info"} <= _columns(c, table):
            for working, info in c.execute(f"SELECT working_blob, info FROM {table}"):
                try:
                    doc = json.loads(info) if info else {}
                except ValueError:
                    doc = {}
                infected = ((doc.get("scan") or {}).get("status") == "infected") if isinstance(doc, dict) else False
                img = ((doc.get("image") or {}).get("original_blob")) if isinstance(doc, dict) else None
                (rejected_refs if infected else live_refs).update(b for b in (working, img) if b)
    for table, col in DIRECT:
        if table in ("source_files", "templates", "template_versions", "message_attachments",
                     "organization_templates", "organization_template_releases") or table not in have or col not in _columns(c, table):
            continue
        live_refs |= direct.get(f"{table}.{col}", set())
    index: dict[str, str] = {}
    qdir = data_dir / "quarantine"
    if qdir.is_dir():
        for q in qdir.glob("*.json"):
            if HEX64.match(q.stem):
                try:
                    index[q.stem] = json.loads(q.read_text(encoding="utf-8")).get("state") or ""
                except (OSError, ValueError):
                    index[q.stem] = ""
    quarantine_copy_missing = sorted(d for d, st in index.items() if st == "quarantined" and not (qdir / d).is_file())

    def excused(d: str) -> bool:
        if d not in rejected_refs or d in live_refs:
            return False
        st = index.get(d)
        if st == "quarantined":
            return (qdir / d).is_file()
        if st == "purged":
            return True
        # No index: only the upload gate's own rejection (a rejected source, bytes unlinked).
        return st is None and d in rejected_uploads
    withdrawn = {d for d in (set().union(*direct.values()) if direct else set()) if excused(d)}

    lookalike_hits = []
    for table, col, why in LOOKALIKES:
        if table not in have or col not in _columns(c, table):
            continue
        n = c.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} IS NOT NULL AND length({col})=64"
        ).fetchone()[0]
        if n:
            lookalike_hits.append({"column": f"{table}.{col}", "rows": n, "why": why})
    inline = [f"{t}.{col}" for t, col in INLINE_BYTES if t in have and col in _columns(c, t)]
    c.close()

    return {
        "authz_tables_not_covered": _cross_check_against_authz(),
        "quarantine_tables_not_covered": _cross_check_against_quarantine(),
        "partial_writes": sorted(partial),
        "misplaced": sorted(misplaced),
        "inline_byte_columns": inline,
        "blobs_on_disk": len(on_disk),
        "bytes_on_disk": total_bytes,
        "direct": {k: len(v) for k, v in direct.items()},
        "direct_refs": direct_refs,
        "embedded": {k: len(v) for k, v in embedded.items()},
        "embedded_refs": embedded_refs,
        "referenced_total": len(all_refs),
        "reachable_only_from_json": sorted(only_json),
        "unreferenced_on_disk": sorted(on_disk - all_refs),
        "referenced_but_missing": sorted(all_refs - on_disk - withdrawn),
        # Referenced, absent, and withdrawn on purpose (rejected / quarantined): not a loss.
        "withdrawn_absent": sorted((all_refs - on_disk) & withdrawn),
        # The index says the bytes are held, and the held copy is gone: that is a loss.
        "quarantine_copy_missing": quarantine_copy_missing,
        # The full reference set, so a caller can check each blob where the application reads it
        # (snapshot.py verify) instead of trusting a file-name match anywhere under blobs/.
        "referenced_all": sorted(all_refs),
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
    # Exit status carries the two findings that make this inventory unsafe to act on: bytes that
    # are referenced but gone, and an inventory narrower than what the server serves (or one that
    # could not be compared at all). Automation reads the status, not the prose.
    failed = (bool(r["referenced_but_missing"]) or bool(r["quarantine_copy_missing"]) or r["authz_tables_not_covered"] != []
              or r["quarantine_tables_not_covered"] != [])

    if args.json:
        out = {k: v for k, v in r.items() if k != "referenced_all"}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1 if failed else 0

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
    if r["withdrawn_absent"]:
        print(f"安全检查拒绝或隔离、按设计不在存储中：{len(r['withdrawn_absent'])} 个（不是缺失）")
    if r["quarantine_copy_missing"]:
        print(f"隔离索引说在隔离区、副本却不见了：{len(r['quarantine_copy_missing'])} 个  ← 数据已缺失")
    orphans = r["unreferenced_on_disk"]
    print(f"磁盘上但无人引用：{len(orphans)} 个")
    if orphans:
        print("     注意：这些**不能直接当作可删除**。写入与插行之间、内容去重命中时，引用只在进程内存里")
        print("     （见 docs/platform/blob-reclamation.md §1.5）；回收需要宽限期、写入方锁与两阶段移除。")
    if r["partial_writes"]:
        print(f"blobs/ 里的非 blob 文件（写入中断留下的临时文件等）：{len(r['partial_writes'])} 个")
    if r["misplaced"]:
        print(f"不在自己目录（<前两位>/）里的 blob：{len(r['misplaced'])} 个")
    print()

    gaps = r["authz_tables_not_covered"]
    if gaps is None:
        print("!! 无法导入 synwevo_docs.authz，没有核对清点是否覆盖授权层会提供的全部表。")
        print("   在装好各模块的环境里运行（python scripts/platform.py setup）。")
        print()
    elif gaps:
        print("!! 授权层会认为可读、但本清点没有覆盖的表：" + "、".join(gaps))
        print("   这意味着这份清点比服务器实际会提供的内容更窄——基于它做回收会删掉正在被服务的字节。")
        print("   请把这些表加进 DIRECT 或 JSON_PATHS 之后再使用本工具。")
        print()

    qgaps = r["quarantine_tables_not_covered"]
    if qgaps is None:
        print("!! 无法导入 synwevo_core.quarantine，没有核对清点是否覆盖隔离模块列出的引用表。")
        print()
    elif qgaps:
        print("!! 隔离模块列出、但本清点没有覆盖的引用表：" + "、".join(qgaps))
        print()

    if r["inline_byte_columns"]:
        print("存字节本身（不是摘要）的列，不属于 blob 存储：" + "、".join(r["inline_byte_columns"]))
        print()

    if r["lookalike_columns"]:
        print("形似 blob 但不是 blob 的列（正则全表扫描会误判）：")
        for h in r["lookalike_columns"]:
            print(f"  {h['column']:<34} {h['rows']} 行   {h['why']}")
        print()

    print("本脚本不会删除任何东西，也没有删除模式。要实现回收，先读")
    print("docs/quality/progress.md 里的陷阱清单，并在 BEGIN IMMEDIATE 内复核候选集、")
    print("用改名隔离而不是直接删除。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
