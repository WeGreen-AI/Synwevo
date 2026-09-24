#!/usr/bin/env python3
"""Take and restore a consistent snapshot of a DocWeave instance.

    python scripts/snapshot.py create --data data --out ./backups
    python scripts/platform.py verify-restore ./backups/docweave-2026-09-23T18-40.tar.gz
    python scripts/snapshot.py restore --archive <file> --into /tmp/restored

**`verify` is the point.** A backup that has never been restored is a file, not a backup. It
restores into a throwaway directory, opens the result, and compares it row by row against what
was captured — rather than checking that tar exited 0.

Three things this handles that a `cp -r data/` does not:

1. **WAL.** The database runs in write-ahead-log mode, so the `.sqlite3` file alone can be hours
   behind — measured on the real store, 4.2 MB of log against a 4.4 MB main file. This uses
   SQLite's own backup API, which reads a consistent snapshot of a live database including
   everything still in the log.
2. **Blob content lives outside the database.** `data/blobs/` holds the actual file bytes. A
   database-only backup restores a catalogue of documents whose contents are gone.
3. **Key material.** Signing keys and MFA seeds are *in the database*. That makes the archive a
   secret, which the tooling has to say out loud rather than leave for someone to discover.

`restore` deliberately refuses to write into a directory that already holds a database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from platform_core.http import repo_root

# 相对的 --data 以**仓库根目录**为准，而不是脚本所在的模块目录：脚本搬了家，
# `--data data` 指的还得是同一个 data/，否则备份的是一个空实例。
ROOT = repo_root() or Path(__file__).resolve().parents[3]


def _blob_audit():
    """The inventory script, loaded by path.

    A plain `import blob_audit` works only when this file is being run as a script, because then
    its own directory is first on the import path. Loaded any other way — from a test, from a
    wrapper — the import vanishes. Loading the file it means is the version that works both ways.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_docweave_blob_audit", Path(__file__).resolve().parent / "blob_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

PASS, FAIL, WARN = "✓", "✗", "!"

# Tables whose row counts and content are compared after a restore. Chosen because losing any of
# them silently is the failure a backup exists to prevent.
CENSUS = ["projects", "templates", "template_versions", "source_files", "jobs", "artifacts",
          "users", "workspaces", "memberships", "audit_events"]


def _snapshot_db(src: Path, dst: Path) -> None:
    """A consistent copy of a live WAL database, without disturbing the writer."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        d = sqlite3.connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _census(db: Path) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {}
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in CENSUS:
            if t in have:
                out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        # A digest over the rows that carry user work, so a restore that has the right *number*
        # of different rows still fails.
        for t, cols in (("projects", "id,name,tenant"), ("templates", "id,name,tenant"),
                        ("artifacts", "id,job_id,blob")):
            if t not in have:
                continue
            present = {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
            use = [x for x in cols.split(",") if x in present]
            if not use:
                continue
            h = hashlib.sha256()
            for row in c.execute(f"SELECT {','.join(use)} FROM {t} ORDER BY id"):
                h.update(repr(row).encode())
            out[f"digest:{t}"] = h.hexdigest()[:16]
        row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        out["schema_version"] = row[0] if row else "?"
    finally:
        c.close()
    return out


def _secrets_present(db: Path) -> list:
    """Which secret-bearing tables have rows. Reported as a count, never as content."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    found = []
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, what in (("oidc_keys", "OIDC 签名私钥"),
                            ("totp_secrets", "TOTP 种子（密文）"),
                            ("oauth_clients", "客户端密钥哈希"),
                            ("api_keys", "API 密钥哈希"),
                            ("user_passwords", "口令哈希")):
            if table in have:
                n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n:
                    found.append((table, what, n))
        row = c.execute("SELECT COUNT(*) FROM meta WHERE key='server_secret'").fetchone()
        if row and row[0]:
            found.append(("meta.server_secret", "MFA 种子的加密密钥（与密文同库）", 1))
    finally:
        c.close()
    return found


# ---------------------------------------------------------------- create
def create(args) -> int:
    data = Path(args.data) if Path(args.data).is_absolute() else ROOT / args.data
    db = data / "docweave.sqlite3"
    if not db.exists():
        print(f"找不到 {db}", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H-%M", time.localtime())
    archive = out_dir / f"docweave-{stamp}.tar.gz"

    work = Path(tempfile.mkdtemp(prefix="docweave-snapshot-"))
    try:
        print(f"来源：{data}（只读打开）")
        wal = db.with_name(db.name + "-wal")
        print(f"  主库 {db.stat().st_size / 1e6:.1f} MB"
              + (f"，WAL {wal.stat().st_size / 1e6:.1f} MB —— 单独复制主库会丢掉这部分"
                 if wal.exists() else "，无 WAL"))

        _snapshot_db(db, work / "docweave.sqlite3")
        census = _census(work / "docweave.sqlite3")
        print(f"  schema v{census.get('schema_version')}；"
              + "，".join(f"{t} {census[t]}" for t in CENSUS if t in census))

        blobs = data / "blobs"
        n_blobs = 0
        total = 0
        if blobs.is_dir():
            shutil.copytree(blobs, work / "blobs")
            for p in (work / "blobs").rglob("*"):
                if p.is_file():
                    n_blobs += 1
                    total += p.stat().st_size
        print(f"  blob {n_blobs} 个文件，{total / 1e6:.1f} MB")

        secrets = _secrets_present(work / "docweave.sqlite3")
        manifest = {
            "created_at": time.time(),
            "source": str(data),
            "census": census,
            "blobs": {"files": n_blobs, "bytes": total},
            "secret_bearing_tables": [{"table": t, "what": w, "rows": n} for t, w, n in secrets],
            "external_secret_configured": bool(os.environ.get("DOCWEAVE_SECRET")),
        }
        (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

        with tarfile.open(archive, "w:gz") as tar:
            for item in sorted(work.iterdir()):
                tar.add(item, arcname=item.name)
        os.chmod(archive, 0o600)
        print(f"\n已写出 {archive}（{archive.stat().st_size / 1e6:.1f} MB，权限 600）")

        if secrets:
            print("\n!! 这个归档是密钥材料，按密钥保护，不要提交进仓库：")
            for t, w, n in secrets:
                print(f"   · {t}：{w}（{n} 行）")
            if not manifest["external_secret_configured"]:
                print("   · 未设置 DOCWEAVE_SECRET，因此 MFA 种子的密钥就在这个库里——")
                print("     拿到这个归档即可解出 TOTP 种子。这不叫加密。")
        print("\n下一步：先验证，再依赖它。")
        print(f"  python scripts/platform.py verify-restore {archive}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- restore / verify
def _extract(archive: Path, into: Path) -> dict:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            # A tar can name paths outside the target. Refuse rather than trust the archive.
            target = (into / member.name).resolve()
            if into.resolve() not in target.parents and target != into.resolve():
                raise SystemExit(f"归档里有越界路径，拒绝解开：{member.name}")
        tar.extractall(into)
    manifest = into / "manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}


def restore(args) -> int:
    archive = Path(args.archive)
    into = Path(args.into)
    if (into / "docweave.sqlite3").exists() and not args.force:
        print(f"拒绝覆盖：{into} 里已经有一个数据库。换一个空目录，或显式加 --force。",
              file=sys.stderr)
        return 2
    into.mkdir(parents=True, exist_ok=True)
    manifest = _extract(archive, into)
    print(f"已解开到 {into}")
    _restore_warnings(into / "docweave.sqlite3")
    return 0


def _restore_warnings(db: Path) -> None:
    """What a restored copy brings back that you probably did not mean to bring back.

    Restoring an old backup is not "going back in time" — it is bringing back the state of the
    world as it was, including things that were deliberately taken away since.
    """
    if not db.exists():
        return
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        notes = []
        if "sessions" in have:
            n = c.execute("SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0]
            if n:
                notes.append(f"{n} 个未撤销的会话会复活——包括备份之后才被撤销的那些")
        if "oauth_tokens" in have:
            n = c.execute("SELECT COUNT(*) FROM oauth_tokens WHERE revoked_at IS NULL").fetchone()[0]
            if n:
                notes.append(f"{n} 个未撤销的访问/刷新令牌会复活")
        if "api_keys" in have:
            n = c.execute("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL").fetchone()[0]
            if n:
                notes.append(f"{n} 个有效的 API 密钥会复活")
        if "jobs" in have:
            n = c.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
            if n:
                notes.append(f"{n} 个处于 queued/running 的任务会被 worker 接手并重新执行")
        if notes:
            print("\n!! 恢复之后必须处理：")
            for x in notes:
                print(f"   · {x}")
            print("   建议：轮换 OIDC 签名密钥（使全部旧令牌失效）、撤销全部会话、")
            print("         把未完成任务标为需要人工裁决后再启动 worker。")
        print("\n!! 认证模式来自环境变量，不在这个库里。恢复数据库不会改变它；")
        print("   恢复整台机器会——确认 DOCWEAVE_UI_AUTH 不会退回 none。")
    finally:
        c.close()


def verify(args) -> int:
    """Restore into a throwaway directory and check the result actually matches."""
    archive = Path(args.archive)
    if not archive.exists():
        print(f"找不到 {archive}", file=sys.stderr)
        return 2
    results = []

    def check(ok, title, detail=""):
        results.append(bool(ok))
        print(f"  {PASS if ok else FAIL} {title}" + (f"\n      {detail}" if detail else ""))

    work = Path(tempfile.mkdtemp(prefix="docweave-verify-"))
    try:
        print(f"归档：{archive}（{archive.stat().st_size / 1e6:.1f} MB）")
        print(f"恢复到隔离目录：{work}\n")
        manifest = _extract(archive, work)
        db = work / "docweave.sqlite3"
        check(db.exists(), "归档里有数据库")
        if not db.exists():
            return 1

        print("\n【1】数据库能打开，且内容与捕获时一致")
        actual = _census(db)
        expected = manifest.get("census") or {}
        check(bool(expected), "归档带有 manifest（记录了捕获时的清点结果）")
        for key in sorted(expected):
            check(actual.get(key) == expected[key], f"{key} = {expected[key]}",
                  "" if actual.get(key) == expected[key] else f"恢复后是 {actual.get(key)}")

        print("\n【2】blob 内容也在（数据库只是目录，正文在文件里）")
        n = sum(1 for p in (work / "blobs").rglob("*") if p.is_file()) if (work / "blobs").is_dir() else 0
        want = (manifest.get("blobs") or {}).get("files", 0)
        check(n == want, f"blob 文件 {want} 个", "" if n == want else f"恢复后是 {n}")

        print("\n【3】引用与内容对得上（恢复出一个内容缺失的目录是最糟的情况）")
        audit = _blob_audit().audit
        r = audit(work)
        check(not r["referenced_but_missing"],
              f"被引用的 blob 全都在：{r['referenced_total']} 个",
              "" if not r["referenced_but_missing"]
              else f"缺失 {len(r['referenced_but_missing'])} 个")

        print("\n【4】应用能真的打开它")
        from platform_core.store import Store
        s = Store(work)
        check(s.schema_version() >= 4, f"schema v{s.schema_version()}")
        n_proj = s.one("SELECT COUNT(*) AS n FROM projects")["n"]
        check(n_proj == actual.get("projects"), f"通过应用读到 {n_proj} 个项目")

        print("\n【5】恢复之后要处理的东西")
        _restore_warnings(db)

        bad = sum(1 for ok in results if not ok)
        print(f"\n{'─' * 60}\n{len(results) - bad}/{len(results)} 项通过"
              + (f"，{bad} 项失败" if bad else "。这份备份是可恢复的。"))
        return 1 if bad else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="取一致快照")
    c.add_argument("--data", default="data")
    c.add_argument("--out", default="backups")
    c.set_defaults(func=create)
    v = sub.add_parser("verify", help="恢复到隔离目录并逐项核对")
    v.add_argument("--archive", required=True)
    v.set_defaults(func=verify)
    r = sub.add_parser("restore", help="解开到指定目录")
    r.add_argument("--archive", required=True)
    r.add_argument("--into", required=True)
    r.add_argument("--force", action="store_true", help="允许写入已有数据库的目录")
    r.set_defaults(func=restore)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
