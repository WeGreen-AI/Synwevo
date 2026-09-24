#!/usr/bin/env python3
"""Rehearse a schema migration on an isolated copy of a real database.

    python scripts/migration_rehearsal.py --from data
    python scripts/migration_rehearsal.py --from data --keep     # leave the copy for inspection

Takes a consistent snapshot, migrates the copy, and checks that everything that was there is
still there. **The source is opened read-only and is never written to.**

Why a snapshot and not `cp`: the database runs in WAL mode, so the `.sqlite3` file on its own
can be hours behind — here the write-ahead log was 4 MB against a 4 MB main file. Copying just
the one file and declaring the migration safe would be checking a database that does not exist.
This uses SQLite's own backup API, which takes a consistent snapshot of a live database
including everything still in the log.

Exit code 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from synwevo_core.http import repo_root

ROOT = repo_root() or Path(__file__).resolve().parents[2]

PASS, FAIL = "✓", "✗"
results: list[tuple[bool, str]] = []


def check(ok: bool, title: str, detail: str = "") -> bool:
    results.append((bool(ok), title))
    print(f"  {PASS if ok else FAIL} {title}" + (f"\n      {detail}" if detail else ""))
    return bool(ok)


def snapshot(src_db: Path, dst_db: Path) -> None:
    """A consistent copy of a live WAL database, without disturbing the writer."""
    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dst_db)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def census(db: Path) -> dict:
    """What must survive. Counts plus content fingerprints — a count alone would not notice
    rows being replaced by different rows."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    out: dict = {}
    try:
        tables = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in sorted(tables):
            out[f"count:{t}"] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        # Columns are intersected with what the table actually has rather than assumed: this
        # runs against real databases of different ages, and a KeyError here would look like a
        # migration failure when it is only this script being out of date.
        for t, wanted in (("projects", ("id", "name", "tenant", "template_id", "rev")),
                          ("templates", ("id", "name", "tenant", "head")),
                          ("jobs", ("id", "project_id", "status", "tenant")),
                          ("artifacts", ("id", "job_id", "blob", "size"))):
            if t not in tables:
                continue
            have = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            cols = [col for col in wanted if col in have]
            if not cols:
                continue
            rows = c.execute(f"SELECT {','.join(cols)} FROM {t} ORDER BY id").fetchall()
            out[f"rows:{t}"] = [tuple(r) for r in rows]
        if "blobs" in tables and {r[1] for r in c.execute("PRAGMA table_info(blobs)")}:
            key = "digest" if "digest" in {r[1] for r in c.execute("PRAGMA table_info(blobs)")} else "id"
            out["blob_digests"] = sorted(r[0] for r in c.execute(f"SELECT {key} FROM blobs"))
        out["schema_version"] = (c.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone() or ["?"])[0]
    finally:
        c.close()
    return out


def columns(db: Path, table: str) -> set:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    finally:
        c.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default="data", help="来源数据目录（只读打开）")
    ap.add_argument("--keep", action="store_true", help="保留副本以便检查")
    args = ap.parse_args()

    src_dir = (ROOT / args.src).resolve() if not Path(args.src).is_absolute() else Path(args.src)
    src_db = src_dir / "docweave.sqlite3"
    if not src_db.exists():
        print(f"找不到 {src_db}", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="docweave-rehearsal-"))
    dst_db = work / "docweave.sqlite3"
    print(f"来源：{src_db}  （只读）")
    print(f"副本：{dst_db}\n")

    wal = src_db.with_name(src_db.name + "-wal")
    print("【1】取一致快照")
    print(f"      主库 {src_db.stat().st_size / 1e6:.1f} MB"
          + (f"，WAL {wal.stat().st_size / 1e6:.1f} MB" if wal.exists() else "，无 WAL"))
    snapshot(src_db, dst_db)
    before = census(dst_db)
    check(before["count:projects"] > 0, f"快照里有 {before['count:projects']} 个项目")
    print(f"      schema v{before['schema_version']}，"
          f"模板 {before.get('count:templates', 0)}，任务 {before.get('count:jobs', 0)}，"
          f"产物 {before.get('count:artifacts', 0)}")
    # Blob bytes live in the content-addressed directory, not in the database, so a schema
    # migration cannot touch them. Said out loud rather than printed as "blob 0", which would
    # read as "the blobs are gone".
    blob_dir = src_dir / "blobs"
    if blob_dir.is_dir():
        n_blobs = sum(1 for p in blob_dir.rglob("*") if p.is_file())
        print(f"      blob 内容在 {blob_dir}（{n_blobs} 个文件），不在库里，迁移碰不到")
    src_mtime = src_db.stat().st_mtime

    print("\n【2】在副本上跑迁移")
    from synwevo_core.store import Store
    store = Store(work)                       # opening it runs _migrate
    after = census(dst_db)

    print("\n【3】原有数据一个不少")
    for key in sorted(k for k in before if k.startswith(("count:", "rows:")) or k == "blob_digests"):
        if key.startswith("count:") and key.split(":", 1)[1] in (
                "workspace_apps", "app_entitlements", "apps"):
            continue                          # these are what the migration adds; checked below
        ok = after.get(key) == before[key]
        if not ok or key.startswith("rows:") or key == "blob_digests":
            n = len(before[key]) if isinstance(before[key], list) else before[key]
            check(ok, f"{key} 未改变（{n}）",
                  "" if ok else f"迁移前 {before[key]!r:.120}\n      迁移后 {after.get(key)!r:.120}")
    counts_ok = all(after.get(k) == v for k, v in before.items()
                    if k.startswith("count:") and k.split(":", 1)[1] not in
                    ("workspace_apps", "app_entitlements", "apps"))
    check(counts_ok, "所有既有表的行数都没有变化")

    print("\n【4】新列到位（无条件迁移，不靠版本号）")
    check({"family_id", "parent_id"} <= columns(dst_db, "oauth_tokens"),
          "oauth_tokens 有 family_id / parent_id")
    check({"oidc_sid", "central_session_id"} <= columns(dst_db, "sessions"),
          "sessions 有 oidc_sid / central_session_id")
    check(all(t in after for t in ("count:apps", "count:oauth_clients", "count:oidc_keys",
                                  "count:rp_auth_requests")),
          "平台表已创建")

    print("\n【5】历史数据空间保持未认领")
    legacy = store.one("SELECT * FROM workspaces WHERE id='default'")
    if legacy:
        check(legacy["kind"] == "legacy" and legacy["status"] == "pending_claim",
              f"default 仍是 {legacy['kind']}/{legacy['status']}")
        for t in ("memberships", "workspace_apps", "app_entitlements"):
            n = store.one(f"SELECT COUNT(*) AS n FROM {t} WHERE workspace_id='default'")["n"]
            check(n == 0, f"default 空间的 {t} 仍是 0 行")
    else:
        check(True, "这份库没有 default 空间（迁移前已是 v4），跳过")
    n_legacy_projects = store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant='default'")["n"]
    print(f"      tenant='default' 的项目 {n_legacy_projects} 个，仍然只能由认领流程指派")

    print("\n【6】重复执行是幂等的")
    snap2 = census(dst_db)
    Store(work)                               # migrate again
    snap3 = census(dst_db)
    diffs = [k for k in snap2 if snap2[k] != snap3.get(k)]
    check(not diffs, "再跑一次迁移，什么都没变", ", ".join(diffs[:5]))

    print("\n【7】来源没有被碰过")
    check(src_db.stat().st_mtime == src_mtime, "来源数据库的修改时间未变")

    if args.keep:
        print(f"\n副本保留在 {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)

    bad = sum(1 for ok, _ in results if not ok)
    print(f"\n{'─' * 60}\n{len(results) - bad}/{len(results)} 项通过"
          + (f"，{bad} 项失败" if bad else "。真实库的隔离副本，来源只读，未部署。"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
