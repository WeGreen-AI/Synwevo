#!/usr/bin/env python3
"""Take and restore a consistent snapshot of a Synwevo instance.

    python scripts/platform.py backup [--out DIR]
    python scripts/platform.py verify-restore .local/backups/synwevo-2026-09-24T09-00-00.tar.gz
    python scripts/platform.py restore <archive> --into /srv/restored
    python scripts/platform.py restore <archive> --into data --force     # over the live directory

or directly: `python apps/docs/scripts/snapshot.py create|verify|restore …`.

**`verify` is the point.** A backup that has never been restored is a file, not a backup. It
restores into a throwaway directory, opens the result, and compares it row by row against what
was captured — rather than checking that tar exited 0 — and reads every referenced blob from the
path the application reads it from, checking its SHA-256.

Things this handles that a `cp -r data/` does not:

1. **WAL.** The database runs in write-ahead-log mode, so the `.sqlite3` file alone can be hours
   behind. This uses SQLite's own backup API, which reads a consistent snapshot of a live
   database including everything still in the log.
2. **Blob content lives outside the database.** `data/blobs/` holds the actual file bytes. A
   database-only backup restores a catalogue of documents whose contents are gone.
3. **Key material.** Signing keys and MFA seeds are *in the database*. The archive is a secret:
   it is created 0600 from the first byte, in a 0700 directory, and never exists half-written
   under its final name.
4. **Withdrawn access.** Restoring puts back the world as it was — including keys revoked,
   people removed and accounts suspended since. So a restore:
   * re-applies every entry of the live security journal (`security-journal.jsonl`, next to the
     live database, never overwritten by the archive) recorded after the snapshot was taken;
   * ends every session and every OIDC token, so everyone signs in again;
   * with explicit `--no-journal`, revokes every restored API key and disables every confidential
     or service client; public interactive clients remain available for fresh sign-in. The
     restored copy then starts a new journal history (`journal.EPOCH_KEY`): a later restore of its
     backups does not replay the written-ahead lines from before it;
   * drops every webhook delivery still pending in the restored database, so nothing is re-sent;
   * drops every mail still queued in the restored database and clears its body: those carry
     live invitation, reset and verification links, and the mail worker would send them the
     moment the restored instance starts;
   * does **not** resume queued or running jobs: they are marked `cancelled` with
     `halt_reason='restored_from_backup'`, and whoever owns them decides with a retry. A job
     resumed automatically would publish results and fire webhooks for work that, from the
     outside world's point of view, already happened or was already abandoned.
   All of this is done to the restored copy *before* it is moved to where a server can open it.

`restore` refuses to write into a directory that already holds a database unless `--force`; with
`--force` it refuses while any process has that database open, and moves the previous database
aside (never deletes it) before putting the restored one in place. Everything that can fail on
input it does not control — the archive, the live journal — is read and checked before the
target directory changes. The restored database is 0600 and a directory it creates is 0700,
because the database holds the signing keys. Only files that are exactly what a blob is (named by
their SHA-256, at `blobs/<h[:2]>/<h>`, content matching the name) are merged into the blob store:
the store never overwrites a blob it already has, so one wrong file would be served for that
content from then on.

The quarantine (`<data>/quarantine/`, CI-90: bytes a rescan judged malicious, their index, and
regression cases moved aside) travels with the database: the rows marked rejected and the index
that can release them are one state, and a backup holding only half of it could neither restore
a reviewed false positive nor tell a withdrawn blob from a lost one. A restore merges it — an
entry already present in the target is kept — and **a rescan is run again after any restore**
(docs/operations.md §2): a database older than a verdict has rows that do not know it yet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from synwevo_core import env
from synwevo_core.http import repo_root

# 相对的 --data 以**仓库根目录**为准，而不是脚本所在的模块目录：脚本搬了家，
# `--data data` 指的还得是同一个 data/，否则备份的是一个空实例。
ROOT = repo_root() or Path(__file__).resolve().parents[3]

DB_NAME = "docweave.sqlite3"
JOURNAL_NAME = "security-journal.jsonl"
# Where the archive keeps its copy of the journal. Deliberately not the live file name, so no
# extraction can ever land on top of a live journal.
ARCHIVED_JOURNAL = "journal/" + JOURNAL_NAME
HALT_REASON = "restored_from_backup"
# What a queued mail's body becomes: the original carries a live link, and the row is kept (its
# status is what the invitation list shows) but will never be sent.
MAIL_BODY_DROPPED = "（恢复自备份：这封邮件没有发送，正文已清除）"
_BLOB_NAME = re.compile(r"[0-9a-f]{64}")


def _blob_audit():
    """The inventory script, loaded by path.

    A plain `import blob_audit` works only when this file is being run as a script, because then
    its own directory is first on the import path. Loaded any other way — from a test, from a
    wrapper — the import vanishes. Loading the file it means is the version that works both ways.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_synwevo_blob_audit", Path(__file__).resolve().parent / "blob_audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PASS, FAIL, WARN = "✓", "✗", "!"

# Tables whose row counts and content are compared after a restore. Chosen because losing any of
# them silently is the failure a backup exists to prevent.
CENSUS = ["projects", "templates", "template_versions", "source_files", "jobs", "artifacts",
          "users", "workspaces", "memberships", "audit_events"]


def _resolve(p: str | Path) -> Path:
    p = Path(p).expanduser()
    return p if p.is_absolute() else ROOT / p


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
                            ("webhook_subscriptions", "Webhook 签名密钥（密文）"),
                            ("oauth_clients", "客户端密钥哈希"),
                            ("api_keys", "API 密钥哈希"),
                            ("user_passwords", "口令哈希")):
            if table in have:
                n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if n:
                    found.append((table, what, n))
        row = c.execute("SELECT COUNT(*) FROM meta WHERE key='server_secret'").fetchone()
        if row and row[0]:
            found.append(("meta.server_secret", "加密种子与 Webhook 密钥的根密钥（与密文同库）", 1))
    finally:
        c.close()
    return found


# ---------------------------------------------------------------- create
def _quarantine_files(data: Path) -> list[Path]:
    """What of `<data>/quarantine/` a backup carries: the held bytes (`<digest>`), their index
    (`<digest>.json`) and copies moved aside (`<digest>.d/...`) — not locks or half-written files."""
    q = data / "quarantine"
    if not q.is_dir():
        return []
    out = []
    for f in sorted(q.rglob("*")):
        if not f.is_file() or f.is_symlink():
            continue
        rel = f.relative_to(q).parts
        if len(rel) == 1 and (_BLOB_NAME.fullmatch(rel[0]) or (rel[0].endswith(".json") and _BLOB_NAME.fullmatch(rel[0][:-5]))):
            out.append(f)
        elif len(rel) > 1 and rel[0].endswith(".d") and _BLOB_NAME.fullmatch(rel[0][:-2]):
            out.append(f)                   # regression cases moved aside, whatever their file names
    return out


def _quarantine_member_ok(parts: tuple) -> bool:
    if len(parts) == 1:
        return True                                     # the directory itself
    head = parts[1]
    if len(parts) == 2 and (_BLOB_NAME.fullmatch(head) or (head.endswith(".json") and _BLOB_NAME.fullmatch(head[:-5]))):
        return True
    return head.endswith(".d") and bool(_BLOB_NAME.fullmatch(head[:-2]))


def _is_blob_file(p: Path) -> bool:
    # Content-addressed files only: put_blob writes `<digest>.tmp<hex>` and renames it, so a
    # partial write in progress is not a blob and does not belong in a backup.
    return p.is_file() and not p.is_symlink() and bool(_BLOB_NAME.fullmatch(p.name))


def create(args) -> int:
    data = _resolve(args.data)
    db = data / DB_NAME
    if not db.exists():
        print(f"找不到 {db}", file=sys.stderr)
        return 2
    out_dir = _resolve(args.out)
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if out_dir.stat().st_mode & 0o077:
        print(f"{WARN} 备份目录 {out_dir} 对其他用户可见；归档本身是 600，但建议把目录也设为 700。")
    stamp = time.strftime("%Y-%m-%dT%H-%M-%S", time.localtime())
    archive = out_dir / f"synwevo-{stamp}.tar.gz"
    if archive.exists():
        print(f"{archive} 已存在，拒绝覆盖。", file=sys.stderr)
        return 2
    partial = archive.with_name(archive.name + ".partial")

    work = Path(tempfile.mkdtemp(prefix="synwevo-snapshot-"))
    try:
        print(f"来源：{data}（只读打开）")
        wal = db.with_name(db.name + "-wal")
        print(f"  主库 {db.stat().st_size / 1e6:.1f} MB"
              + (f"，WAL {wal.stat().st_size / 1e6:.1f} MB —— 单独复制主库会丢掉这部分"
                 if wal.exists() else "，无 WAL"))

        # Taken *before* the snapshot starts. A restore re-applies journal entries newer than
        # this; a withdrawal made while the snapshot was being read is then re-applied (harmless,
        # entries are idempotent) instead of being missed. Entries older than this are never
        # replayed: they are already in the snapshot, and replaying one could undo a later grant
        # (a member removed and re-added before the backup would be removed again).
        snapshot_at = time.time()
        _snapshot_db(db, work / DB_NAME)
        census = _census(work / DB_NAME)
        print(f"  schema v{census.get('schema_version')}；"
              + "，".join(f"{t} {census[t]}" for t in CENSUS if t in census))

        blobs = sorted(p for p in (data / "blobs").rglob("*") if _is_blob_file(p)) \
            if (data / "blobs").is_dir() else []
        total = sum(p.stat().st_size for p in blobs)
        print(f"  blob {len(blobs)} 个文件，{total / 1e6:.1f} MB")

        live_journal = data / JOURNAL_NAME
        if live_journal.is_file():
            shutil.copyfile(live_journal, work / "journal.jsonl")

        qfiles = _quarantine_files(data)
        if qfiles:
            print(f"  隔离区 {len(qfiles)} 个文件，{sum(p.stat().st_size for p in qfiles) / 1e6:.1f} MB（字节、索引与移在一旁的回归用例）")

        secrets = _secrets_present(work / DB_NAME)
        manifest = {
            "created_at": time.time(),
            "snapshot_at": snapshot_at,
            "source": str(data),
            "census": census,
            "blobs": {"files": len(blobs), "bytes": total},
            "quarantine": {"files": len(qfiles), "bytes": sum(p.stat().st_size for p in qfiles)},
            "journal_entries": sum(1 for line in (work / "journal.jsonl").read_text("utf-8").splitlines()
                                   if line.strip()) if (work / "journal.jsonl").exists() else 0,
            "secret_bearing_tables": [{"table": t, "what": w, "rows": n} for t, w, n in secrets],
            "external_secret_configured": bool(env.get("SECRET")),
        }
        (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                            encoding="utf-8")

        # 0600 from creation, not chmod afterwards: there is no window in which another local
        # user can open the key material. Written under a `.partial` name and renamed, so an
        # interrupted run never leaves something that looks like a finished backup.
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
                tar.add(work / DB_NAME, arcname=DB_NAME)
                tar.add(work / "manifest.json", arcname="manifest.json")
                if (work / "journal.jsonl").exists():
                    tar.add(work / "journal.jsonl", arcname=ARCHIVED_JOURNAL)
                for p in blobs:
                    tar.add(p, arcname="blobs/" + p.relative_to(data / "blobs").as_posix())
                for p in qfiles:
                    tar.add(p, arcname="quarantine/" + p.relative_to(data / "quarantine").as_posix())
            os.replace(partial, archive)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        print(f"\n已写出 {archive}（{archive.stat().st_size / 1e6:.1f} MB，权限 600）")

        if secrets:
            print("\n!! 这个归档是密钥材料，按密钥保护，不要提交进仓库：")
            for t, w, n in secrets:
                print(f"   · {t}：{w}（{n} 行）")
            if not manifest["external_secret_configured"]:
                print("   · 未设置 SYNWEVO_SECRET，因此解密这些密文的根密钥就在这个库里——")
                print("     拿到这个归档即可解出 TOTP 种子与 Webhook 签名密钥。这不叫加密。")
        print("\n下一步：先验证，再依赖它。")
        print(f"  python scripts/platform.py verify-restore {archive}")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- extraction
class UnsafeArchive(SystemExit):
    pass


def _member_ok(m: tarfile.TarInfo) -> str | None:
    """Why this member must not be extracted, or None.

    Checked on every member before anything is written. Name checks alone are not enough on
    Python 3.12/3.13 (the container's interpreter), where extraction without a filter follows a
    symlink member written earlier in the same archive: `blobs -> /app` followed by
    `blobs/x` writes outside the target. So links of either kind are refused outright — a real
    backup contains none — and only the names a backup actually contains are accepted.
    """
    name = m.name
    if m.issym() or m.islnk():
        return "链接"
    if not (m.isfile() or m.isdir()):
        return "非普通文件"
    parts = PurePosixPath(name).parts
    if not parts:
        return "空路径"
    if name.startswith("/") or "\\" in name or ":" in parts[0]:
        return "绝对路径"
    if any(p == ".." for p in parts):
        return "越界路径"
    top = parts[0]
    if name in (DB_NAME, "manifest.json", ARCHIVED_JOURNAL) or top == "blobs" \
            or (top == "journal" and m.isdir() and len(parts) == 1):
        return None
    if top == "quarantine" and _quarantine_member_ok(parts):
        return None
    return "不属于备份的条目"


def _extract(archive: Path, into: Path) -> dict:
    into.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        for m in members:
            why = _member_ok(m)
            if why:
                raise UnsafeArchive(f"归档里有{why}（{m.name}），拒绝解开：{archive}")
            target = (into / m.name).resolve()
            if not target.is_relative_to(into.resolve()):
                raise UnsafeArchive(f"归档里有越界路径，拒绝解开：{m.name}")
        # The "data" filter is the second lock: it also refuses links, device files and absolute
        # or escaping paths, and strips permission bits no backup needs.
        tar.extractall(into, members=members, filter="data")
    manifest = into / "manifest.json"
    return json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}


# ---------------------------------------------------------------- blob verification
def _check_blobs(data_dir: Path) -> dict:
    """Read every referenced blob from where the application reads it, and hash it.

    Matching file names anywhere under `blobs/` is not the same thing: a blob stored in the
    wrong directory, or whose bytes changed, looks present to a name match and is unreadable
    (or wrong) to the application.
    """
    from synwevo_core.store import Store
    audit = _blob_audit().audit(data_dir)
    # Bytes a security verdict withdrew (rejected / quarantined, CI-90) are absent by design.
    withdrawn = set(audit.get("withdrawn_absent") or [])
    referenced = sorted(set(audit["referenced_all"]) - withdrawn)
    ro = Store(data_dir, migrate=False)
    missing, misplaced, corrupt, verified = [], [], [], []
    on_disk = {p.name: p for p in (data_dir / "blobs").rglob("*") if p.is_file()} \
        if (data_dir / "blobs").is_dir() else {}
    for h in referenced:
        try:
            path = ro.blob_path(h)
        except ValueError:
            continue
        if not path.is_file():
            (misplaced if h in on_disk else missing).append(h)
            continue
        if _sha256(path) != h:
            corrupt.append(h)
        else:
            verified.append(h)
    return {"referenced": len(referenced), "missing": missing, "misplaced": misplaced,
            "corrupt": corrupt, "verified": verified, "withdrawn": sorted(withdrawn),
            "quarantine_copy_missing": audit.get("quarantine_copy_missing") or [],
            "authz_tables_not_covered": audit["authz_tables_not_covered"]}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blob_moves(src_blobs: Path, dst_blobs: Path, verified=()) -> tuple[list, list]:
    """Which files under an extracted `blobs/` may enter a blob store, and which may not.

    A file qualifies only if it is exactly what `Store.put_blob` would have written: named by the
    SHA-256 of its content, at `<h[:2]>/<h>`. Anything else is reported and left behind. This
    matters because the store is content-addressed and never overwrites: `put_blob` skips a
    digest whose file exists, so a wrong file merged under a valid name is served for that
    content from then on, to whoever uploads it next. `verified` are digests already hashed at
    exactly that path (by `_check_blobs`), so a restore reads each blob once, not twice.

    Returns ([(src, dst)] still to move, [why each rejected file was rejected]).
    """
    moves, rejected = [], []
    if not src_blobs.is_dir():
        return moves, rejected
    verified = set(verified)
    for p in sorted(src_blobs.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src_blobs).as_posix()
        h = p.name
        if not _BLOB_NAME.fullmatch(h) or rel != f"{h[:2]}/{h}":
            rejected.append(f"blobs/{rel}：不是 blobs/<前两位>/<SHA-256> 形式的 blob")
            continue
        if h not in verified and _sha256(p) != h:
            rejected.append(f"blobs/{rel}：内容的 SHA-256 与名字不符")
            continue
        dst = dst_blobs / h[:2] / h
        if not dst.exists():
            moves.append((p, dst))
    return moves, rejected


# ---------------------------------------------------------------- the restore fence
def _default_live_dir() -> Path | None:
    raw = env.get("DATA_DIR")
    if raw:
        return _resolve(raw)
    try:
        from synwevo_core.http import data_dir_default
        return data_dir_default()
    except RuntimeError:
        return None


def _journal_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text("utf-8").splitlines() if line.strip()]


def _close(store) -> None:
    """Close the connection a Store holds so SQLite checkpoints the WAL into the main file and
    removes -wal/-shm: the file about to be moved must be complete on its own."""
    c = getattr(store._local, "c", None)
    if c is not None:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.close()
        store._local.c = None


def _job_counts(c, jid: str) -> dict:
    """A job's `counts`, in the shape every other job has (JobService._update_counts).

    Kept identical on purpose, and tested against that method: a job cancelled mid-planning has
    fewer items than records, so `total` is the record count, not the number of items planned.
    """
    rows = c.execute("SELECT status, COUNT(*) AS n FROM job_items WHERE job_id=? GROUP BY status",
                     (jid,)).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    planned = sum(counts.values())
    srow = c.execute("SELECT summary FROM jobs WHERE id=?", (jid,)).fetchone()
    summary = srow["summary"] if srow else None
    if isinstance(summary, (str, bytes)):            # the store's row factory usually decodes it
        try:
            summary = json.loads(summary)
        except ValueError:
            summary = None
    records = int((summary if isinstance(summary, dict) else {}).get("records") or 0)
    counts["planned"] = planned
    counts["total"] = max(planned, records)
    if records > planned:
        counts["unplanned"] = records - planned
    counts["files"] = c.execute("SELECT COUNT(*) AS n FROM artifacts WHERE job_id=? AND deleted_at IS NULL",
                                (jid,)).fetchone()["n"]
    counts["documents"] = counts.get("success", 0)
    return counts


def fence(data_dir: Path, *, since: float | None, journal_dir: Path | None,
          archive_name: str = "", no_journal: bool = False) -> dict:
    """Make a restored copy safe to start, in place. Returns what was done.

    Runs against the restored copy only. Every step is expressed as "make this true", so running
    it twice (a verify, then a restore) changes nothing the second time.
    """
    from synwevo_core import journal
    from synwevo_core.store import Store
    if no_journal:
        journal_dir = None
    store = Store(data_dir)                      # brings an older archive up to the current schema
    report: dict = {"journal": {}, "journal_source": str(journal_dir) if journal_dir else None,
                    "no_journal": no_journal}
    try:
        entries = journal.read(journal_dir, since=since) if (journal_dir and since is not None) else []
        # What read() returns is wider than "after the backup": the written-ahead lines (and the
        # voids of attempts that never committed) come whatever their time, for apply() to check
        # against the receipts the restored copy holds — and, given the backup's time, against
        # what the backup shows was committed after them. The report counts what apply() did with
        # each line (journal.apply `outcome`): the lines the backup did not hold and that were
        # replayed, apart from the ones it skipped by receipt, by void, as never committed or as
        # an abandoned history's.
        outcome: dict = {}
        report["journal"] = journal.apply(store, entries, since=since, outcome=outcome) if entries else {}
        report["journal_entries"] = outcome.get("replayed", 0)
        report["journal_lines_read"] = len(entries)
        report["journal_receipted"] = outcome.get("receipted", 0)
        report["journal_uncommitted"] = outcome.get("uncommitted", 0)
        report["journal_superseded"] = outcome.get("superseded_history", 0)
        report["journal_before_backup"] = outcome.get("before_backup", {})
        t = time.time()
        with store.tx() as c:
            def run(sql, args=()):
                return c.execute(sql, args).rowcount
            if no_journal:
                # Nothing was replayed, by choice: this copy starts a new journal history. The live
                # journal may stay beside it (an in-place restore only appends to it), so a later
                # restore of this history's backups must not replay the lines from before now — an
                # abandoned history's rotated secret would authenticate again, a withdrawal the
                # operator chose not to replay would land (journal.EPOCH_KEY).
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (journal.EPOCH_KEY, repr(t)))
            # With no withdrawal history, any restored machine credential may have been
            # revoked or replaced. An acknowledgement cannot make an old secret safe. Keep
            # hashes/key material intact, but require explicit operator regrant before use.
            # Public user clients have no secret and still need a fresh interactive login.
            if no_journal:
                report["api_keys_revoked_without_journal"] = run(
                    "UPDATE api_keys SET revoked_at=?, revoke_reason=? WHERE revoked_at IS NULL",
                    (t, "restored_without_security_journal"))
                report["clients_disabled_without_journal"] = run(
                    "UPDATE oauth_clients SET disabled_at=?, updated_at=? WHERE disabled_at IS NULL "
                    "AND (kind='service' OR token_endpoint_auth_method<>'none' OR secret_hash IS NOT NULL)",
                    (t, t))
            # Everyone signs in again. Sessions and tokens are the credentials a restore brings
            # back most often, and the journal only knows about the ones ended deliberately —
            # not about a token that simply leaked in the meantime.
            report["sessions_revoked"] = run(
                "UPDATE sessions SET revoked_at=?, revoked_reason=? WHERE revoked_at IS NULL",
                (t, HALT_REASON))
            report["tokens_revoked"] = run(
                "UPDATE oauth_tokens SET revoked_at=?, revoked_reason=? WHERE revoked_at IS NULL",
                (t, HALT_REASON))
            report["client_sessions_ended"] = run(
                "UPDATE client_sessions SET logged_out_at=?, logout_error=? WHERE logged_out_at IS NULL",
                (t, HALT_REASON))
            # Single-use proofs in flight at backup time. They may have been spent since; a
            # restore would make them spendable again.
            report["single_use_expired"] = sum(run(sql, (t,)) for sql in (
                "UPDATE oauth_codes SET used_at=? WHERE used_at IS NULL",
                "UPDATE mfa_tickets SET used_at=? WHERE used_at IS NULL",
                "UPDATE email_tokens SET used_at=? WHERE used_at IS NULL",
                "UPDATE bootstrap_tokens SET used_at=? WHERE used_at IS NULL",
                "UPDATE webauthn_challenges SET used_at=? WHERE used_at IS NULL",
                "UPDATE reauth_challenges SET used_at=? WHERE used_at IS NULL",
                "UPDATE rp_auth_requests SET used_at=? WHERE used_at IS NULL",
            ))
            report["super_admin_offers_invalidated"] = run(
                "UPDATE platform_super_admin_offers SET state='invalidated',closed_at=?,closed_by='system:restore', "
                "closed_reason='restored_invalidated' WHERE state='pending'", (t,))
            # A pending second-factor reset (A26) in the backup may have been cancelled since —
            # by the person, through the mailed link — and its announcement may be among the mail
            # dropped below. Completing it from the restored copy would remove factors the person
            # refused, or never heard about; it is voided, and an administrator asks again.
            # The fence can tell nobody, so the review below lists them for a person to follow up.
            voided_resets = [r["id"] for r in c.execute(
                "SELECT id FROM second_factor_resets WHERE status='pending' ORDER BY created_at, id").fetchall()]
            run("UPDATE second_factor_resets SET status='void', void_reason='restored_invalidated' WHERE status='pending'")
            report["second_factor_resets_voided"] = len(voided_resets)
            # Nothing is re-sent. A delivery pending in the backup was either delivered by the
            # live system afterwards or given up on; sending it again from a restored copy is a
            # duplicate the receiver never asked for.
            report["webhook_deliveries_dropped"] = run(
                "UPDATE webhook_deliveries SET status='dropped', last_error=? WHERE status='pending'",
                ("恢复自备份：未重新发送",))
            # The same for mail, with a sharper edge: a queued mail carries a live invitation,
            # reset or verification link, and the mail worker sends every queued row as soon as
            # the restored instance starts — before anyone has read the review list below. The
            # body is cleared rather than kept: a dropped row is never sent, and keeping the
            # link would leave working tokens sitting in the database. The row stays, so the
            # invitation list shows the mail as not sent and the inviter can send it again.
            report["mail_dropped"] = run(
                "UPDATE mail_outbox SET status='dropped', last_error=?, body=?, next_attempt_at=NULL "
                "WHERE status='queued'", (HALT_REASON, MAIL_BODY_DROPPED))
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) AS s FROM job_events").fetchone()["s"]
            for key in ("webhook_last_seq", "restore_event_fence"):
                c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(seq)))
            # Not auto-resumed. Cancelled with a reason the owner can see, and retryable through
            # the normal retry path when someone decides the work is still wanted.
            jids = [r["id"] for r in c.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running','cancelling')").fetchall()]
            for jid in jids:
                c.execute("UPDATE job_items SET status='cancelled' WHERE job_id=? AND status IN ('pending','running')",
                          (jid,))
                c.execute("UPDATE job_runs SET status='cancelled', finished_at=? WHERE job_id=? "
                          "AND status IN ('queued','running')", (t, jid))
                c.execute("UPDATE jobs SET status='cancelled', halt_reason=?, finished_at=?, lease_owner=NULL, "
                          "lease_until=NULL, counts=? WHERE id=?", (HALT_REASON, t, json.dumps(_job_counts(c, jid)), jid))
            report["jobs_halted"] = len(jids)
            # Upload parts live in <data>/tmp, which a backup does not carry.
            report["uploads_aborted"] = run(
                "UPDATE uploads SET status='aborted', updated_at=? WHERE status='open'", (t,))
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('restored_at', ?)", (str(t),))
            c.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('restored_from', ?)",
                      (json.dumps({"archive": archive_name, "snapshot_at": since}, ensure_ascii=False),))
        # Left for a person: things a restore cannot decide on its own.
        report["review"] = {
            "api_keys_active": store.one("SELECT COUNT(*) AS n FROM api_keys WHERE revoked_at IS NULL")["n"],
            "invitations_pending": store.one(
                "SELECT COUNT(*) AS n FROM invitations WHERE accepted_at IS NULL AND revoked_at IS NULL "
                "AND expires_at > ?", (t,))["n"],
            "service_accounts_active": store.one(
                "SELECT COUNT(*) AS n FROM oauth_clients WHERE kind='service' AND disabled_at IS NULL")["n"],
            "webhook_subscriptions_active": store.one(
                "SELECT COUNT(*) AS n FROM webhook_subscriptions WHERE status='active' AND deleted_at IS NULL")["n"],
            # A26: the pending second-factor resets the fence voided (ids only; nobody was told).
            "second_factor_resets_voided": voided_resets,
        }
    finally:
        _close(store)
    return report


def _print_fence(report: dict, *, rehearsal: bool) -> None:
    verb = "恢复时会" if rehearsal else "已"
    src = report.get("journal_source")
    n = report.get("journal_entries", 0)
    read = report.get("journal_lines_read", n)
    counts = report.get("journal") or {}
    voided = counts.get("intent_voided", 0)                       # journal.VOID_KIND
    # Only what changed the restored copy: a kind every line of which the copy already held reads 0.
    applied = {k: v for k, v in counts.items() if k != "intent_voided" and v > 0}
    receipted = report.get("journal_receipted", 0)
    uncommitted = report.get("journal_uncommitted", 0)
    superseded = report.get("journal_superseded", 0)
    if src is None:
        print(f"  {WARN} 没有可读的安全日志：无法重放备份之后的成员移除、角色变更、账户停用等撤销。")
    else:
        print(f"  {PASS} 安全日志 {src}：备份未包含的撤销记录 {n} 条"
              + (f"（共读取 {read} 条）" if read != n else "")
              + (f"，{verb}重放：" + "，".join(f"{k} {v}" for k, v in sorted(applied.items())) if applied
                 else "，恢复副本无需改动"))
        if receipted:
            print(f"  {PASS} 备份已包含的预写记录 {receipted} 条：按回执核对，{verb}跳过")
        if voided:
            print(f"  {PASS} 已作废的尝试 {voided} 条：其事务从未提交，{verb}跳过，不重放")
        if uncommitted:
            print(f"  {PASS} 未提交的尝试 {uncommitted} 条：备份里有其后提交的变更，证明它从未提交，{verb}跳过并作废")
        if superseded:
            print(f"  {PASS} 无日志恢复之前的记录 {superseded} 条：属于已放弃的历史，{verb}跳过，不重放")
    if report.get("no_journal"):
        print(f"  {PASS} {verb}撤销全部旧 API 密钥（本次 {report['api_keys_revoked_without_journal']} 个），"
              f"停用私密及服务客户端（本次 {report['clients_disabled_without_journal']} 个）；"
              "保留公共交互客户端，不改动密钥材料。由授权操作员核实并重新授权机器访问。")
    print(f"  {PASS} {verb}结束会话 {report['sessions_revoked']} 个、OIDC 令牌 {report['tokens_revoked']} 个"
          f"（所有人需要重新登录）")
    print(f"  {PASS} {verb}作废未用的一次性凭据 {report['single_use_expired']} 个（授权码、验证票据、邮件链接）")
    print(f"  {PASS} {verb}丢弃待发送的 Webhook {report['webhook_deliveries_dropped']} 条（不会重新发送）")
    print(f"  {PASS} {verb}丢弃排队中的邮件 {report['mail_dropped']} 封并清除正文"
          "（邀请、找回、验证链接不会自动发出；需要的邀请请重新发送）")
    print(f"  {PASS} {verb}停止未完成的任务 {report['jobs_halted']} 个"
          f"（cancelled，halt_reason={HALT_REASON}；需要时由所有者重试，不会自动续跑）")
    rv = report["review"]
    print("\n!! 仍需人工确认（恢复无法替你判断）：")
    print(f"   · 有效的 API 密钥 {rv['api_keys_active']} 个、服务账户 {rv['service_accounts_active']} 个："
          + ("没有安全日志，旧机器凭据已统一停用" if report.get("no_journal") else
             "备份之后撤销的已按安全日志重放；备份之后泄露但未撤销的，请现在轮换" if src else
             "没有安全日志，无法确认备份之后的撤销"))
    print(f"   · 未接受的邀请 {rv['invitations_pending']} 个：备份之后撤回并记入安全日志的已按日志重放；"
          "其余的请逐个确认——已经发出的邀请链接仍然有效")
    print(f"   · 启用的 Webhook 订阅 {rv['webhook_subscriptions_active']} 个："
          "备份之后轮换过的签名密钥会回到旧值")
    before = report.get("journal_before_backup") or {}
    if before:
        print(f"   · 早于备份时间、备份却没有回执的预写撤销 {sum(before.values())} 条"
              f"（{'，'.join(f'{k} {v}' for k, v in sorted(before.items()))}）：{verb}重放。"
              "多数是备份恰好在其提交前拍下；也可能是进程在提交前停止、从未生效的尝试——"
              "请核对这些对象在备份之后没有被合法地重新授予")
    voided = rv.get("second_factor_resets_voided") or []
    if voided:
        print(f"   · {verb}作废等待中的第二因素重置 {len(voided)} 项（{', '.join(voided)}）：恢复不会通知本人或申请的管理员；"
              "仍需要的，请由管理员重新核实本人身份后再申请")
    print("   · 认证模式来自环境变量，不在这个库里。恢复数据库不会改变它；")
    print("     恢复整台机器会——确认 SYNWEVO_UI_AUTH 不会退回 none。")


# ---------------------------------------------------------------- restore
def _in_use(db: Path) -> bool:
    """Does any process have this database open?

    Leaving WAL mode needs the only connection to the file, so SQLite refuses it with "database
    is locked" while a server — even an idle one — holds the database open. That makes it a real
    test rather than a pid-file guess. When it succeeds it also checkpoints the log into the main
    file, so the copy set aside next is complete on its own.
    """
    c = sqlite3.connect(db, timeout=0, isolation_level=None)
    try:
        mode = c.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        return mode.lower() != "delete"
    except sqlite3.OperationalError:
        return True
    finally:
        c.close()


def _journal_additions(live: Path | None, archived: Path, target: Path) -> list[str]:
    """Validated withdrawals absent from the target, oldest first. Corruption is fatal."""
    from synwevo_core import journal
    have = {line for _, line in journal.validated_lines(target)}
    found: list[tuple[float, str]] = []
    entries = journal.validated_lines(archived) + (journal.validated_lines(live / JOURNAL_NAME) if live else [])
    for entry, line in entries:
        if line in have:
            continue
        have.add(line)
        found.append((entry["at"], line))
    found.sort(key=lambda pair: pair[0])
    return [line for _, line in found]


def _write_journal(target: Path, lines: list[str], staging: Path) -> None:
    """Put `lines` into `target`, append-only.

    A target that exists (the live journal itself, on an in-place restore) is only appended to,
    after validating every existing entry. A target that does not exist yet is written complete under a
    temporary name in `staging` (same directory tree, same file system) and renamed into place.
    """
    if not lines:
        return
    from synwevo_core import journal
    journal.validated_lines(target)
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    if target.exists():
        fd = os.open(target, os.O_RDWR | os.O_APPEND)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                payload = b"\n" + payload
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        return
    tmp = staging / (JOURNAL_NAME + ".new")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)


def restore(args) -> int:
    archive = Path(args.archive)
    into = _resolve(args.into)
    force = bool(getattr(args, "force", False))
    no_journal = bool(getattr(args, "no_journal", False))
    if not archive.is_file():
        print(f"找不到 {archive}", file=sys.stderr)
        return 2
    db = into / DB_NAME
    if db.exists() and not force:
        print(f"拒绝覆盖：{into} 里已经有一个数据库。换一个空目录，或显式加 --force"
              f"（原库会被移到隔离目录，不会被删除）。", file=sys.stderr)
        return 2

    # Whose journal decides what was withdrawn after the backup: the live instance's.
    jf = getattr(args, "journal_from", None)
    live = _resolve(jf) if jf else (into if db.exists() else _default_live_dir())
    live = live.resolve() if live is not None else None
    in_place = live is not None and live == into.resolve()
    if not no_journal and (live is None or not (live / DB_NAME).exists() and not (live / JOURNAL_NAME).exists()):
        print(f"找不到正在使用的数据目录（{live or '未配置'}），也就无法知道备份之后撤销过哪些访问。\n"
              f"用 --journal-from 指向它；如果原数据目录已经整体丢失，显式加 --no-journal"
              f"（会话、令牌和旧 API 密钥全部作废，私密及服务客户端停用；"
              f"成员及账户撤销无法重放，须人工核实）。",
              file=sys.stderr)
        return 2
    if db.exists() and _in_use(db):
        print(f"拒绝恢复：{db} 正被某个进程打开（服务还在运行？）。先停止服务："
              f"python scripts/platform.py down", file=sys.stderr)
        return 2

    # 0700 when this creates it: the directory is about to hold the signing keys. One that
    # already exists keeps its mode — it may be the live data directory — but is named if others
    # can read it.
    into.mkdir(parents=True, exist_ok=True, mode=0o700)
    if into.stat().st_mode & 0o077:
        print(f"{WARN} {into} 对其他用户可见；恢复出的数据库是 600，但建议把目录也设为 700。")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    staging = Path(tempfile.mkdtemp(prefix=f".restore-{stamp}-", dir=into))
    try:
        manifest = _extract(archive, staging)
        if not (staging / DB_NAME).exists():
            print("归档里没有数据库。", file=sys.stderr)
            return 1
        since = manifest.get("snapshot_at") or manifest.get("created_at")
        if since is None and not no_journal:
            print("归档没有记录拍摄时间，无法判断哪些撤销发生在备份之后；加 --no-journal 才能继续。",
                  file=sys.stderr)
            return 2
        print(f"已解开到临时目录 {staging}")

        blobs = _check_blobs(staging)
        bad = blobs["missing"] + blobs["misplaced"] + blobs["corrupt"] + blobs["quarantine_copy_missing"]
        if bad:
            print(f"{FAIL} 归档里的 blob 不完整：缺失 {len(blobs['missing'])}、位置不对 {len(blobs['misplaced'])}、"
                  f"内容不符 {len(blobs['corrupt'])}。没有改动 {into}。先运行 verify-restore 查看。",
                  file=sys.stderr)
            return 1

        print("\n恢复前先处理恢复出来的副本（服务还看不到它）：")
        report = fence(staging, since=None if no_journal else since,
                       journal_dir=None if no_journal else live, archive_name=archive.name,
                       no_journal=no_journal)
        _print_fence(report, rehearsal=False)

        # Everything read from input this restore does not control is read and checked here,
        # before the target changes: which blob files may enter the store, and what the journal
        # needs. A failure up to this point leaves `into` exactly as it was.
        moves, rejected = _blob_moves(staging / "blobs", into / "blobs", verified=blobs["verified"])
        if rejected:
            print(f"\n{WARN} 归档里有 {len(rejected)} 个文件不是完好的 blob，不会放进 blob 存储"
                  f"（没有记录引用它们；放进去会让以后同样内容的上传读到错误的字节）：")
            for why in rejected[:20]:
                print(f"   · {why}")
            if len(rejected) > 20:
                print(f"   · …另有 {len(rejected) - 20} 个")
        journal_lines = _journal_additions(
            None if no_journal or in_place else live, staging / ARCHIVED_JOURNAL, into / JOURNAL_NAME)

        # The journal first: append-only and harmless on its own, and the one step left that
        # writes a file other than the ones being swapped.
        _write_journal(into / JOURNAL_NAME, journal_lines, staging)

        moved = []
        if db.exists():
            quarantine = into / f".pre-restore-{stamp}"
            quarantine.mkdir(mode=0o700)
            for suffix in ("", "-wal", "-shm"):
                p = into / (DB_NAME + suffix)
                if p.exists():
                    os.replace(p, quarantine / p.name)
                    moved.append(p.name)
            print(f"\n原数据库已移到 {quarantine}（{', '.join(moved)}），没有删除。")
        # The database holds the OIDC signing keys, the server secret and the sealed MFA seeds.
        # The tar member keeps whatever mode it was archived with (the data filter keeps read
        # bits), so the mode is set here, before the file appears under its final name.
        os.chmod(staging / DB_NAME, 0o600)
        os.replace(staging / DB_NAME, db)
        # _close() checkpointed and closed the only connection, so these should not exist; if
        # SQLite kept them anyway they belong to the restored database and travel with it.
        for suffix in ("-wal", "-shm"):
            if (staging / (DB_NAME + suffix)).exists():
                os.chmod(staging / (DB_NAME + suffix), 0o600)
                os.replace(staging / (DB_NAME + suffix), into / (DB_NAME + suffix))
        (into / "blobs").mkdir(exist_ok=True, mode=0o700)
        for src, dst in moves:
            dst.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(src, dst)
        qmoved = _merge_quarantine(staging / "quarantine", into / "quarantine")
        print(f"\n已恢复到 {into}：数据库 1 个，blob 新增 {len(moves)} 个，隔离区文件新增 {qmoved} 个"
              + (f"（{len(rejected)} 个不合格文件未放入）" if rejected else "")
              + f"，安全日志补入 {len(journal_lines)} 条。")
        return 0
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _merge_quarantine(src: Path, dst: Path) -> int:
    """Put the archive's quarantine next to the restored database. An entry the target already
    has is kept (it may be newer than the backup); held bytes that do not match their name are
    left out, as blobs are."""
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True, mode=0o700)

    def purged(digest: str) -> bool:
        """The target's own entry decides when it has one (it may be newer than the archive)."""
        for d in (dst, src):
            try:
                return json.loads((d / f"{digest}.json").read_text(encoding="utf-8")).get("state") == "purged"
            except (OSError, ValueError):
                continue
        return False

    n = 0
    for f in sorted(src.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        if len(rel.parts) == 1 and _BLOB_NAME.fullmatch(rel.name):
            if _sha256(f) != rel.name:
                print(f"{WARN} 归档里隔离区的 {rel.name} 与摘要不符，没有放回")
                continue
            if purged(rel.name):            # deleted for good after review: never brought back
                continue
        target = dst / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(f, 0o600)
        os.replace(f, target)
        n += 1
    return n


# ---------------------------------------------------------------- verify
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

    work = Path(tempfile.mkdtemp(prefix="synwevo-verify-"))
    try:
        print(f"归档：{archive}（{archive.stat().st_size / 1e6:.1f} MB）")
        print(f"恢复到隔离目录：{work}\n")
        try:
            manifest = _extract(archive, work)
        except UnsafeArchive as e:
            check(False, "归档可以安全解开", str(e))
            return 1
        db = work / DB_NAME
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

        print("\n【3】每个被引用的 blob 都能从应用读取的位置读出，且内容与名字相符")
        r = _check_blobs(work)
        check(not r["missing"], f"被引用的 blob 全都在：{r['referenced']} 个",
              "" if not r["missing"] else f"缺失 {len(r['missing'])} 个，例如 {r['missing'][0]}")
        check(not r["misplaced"], "都在应用读取的位置（blobs/<前两位>/<摘要>）",
              "" if not r["misplaced"] else f"{len(r['misplaced'])} 个文件在别处，应用读不到")
        check(not r["corrupt"], "内容的 SHA-256 与名字一致",
              "" if not r["corrupt"] else f"{len(r['corrupt'])} 个内容已损坏")
        check(not r["quarantine_copy_missing"], "隔离索引记为在隔离区的内容都在"
              + (f"（按设计不在存储中的被拒绝内容 {len(r['withdrawn'])} 个）" if r["withdrawn"] else ""),
              "" if not r["quarantine_copy_missing"] else f"缺 {len(r['quarantine_copy_missing'])} 个隔离副本")
        _, rejected = _blob_moves(work / "blobs", work / ".nowhere", verified=r["verified"])
        if rejected:
            # Not referenced by anything, so the restore is still complete; but these are the
            # files a restore leaves out, and a backup that carries them came from a store that
            # already held a damaged file.
            print(f"  {WARN} 另有 {len(rejected)} 个未被引用的文件不是完好的 blob，恢复时不会放进 blob 存储"
                  f"\n      例如 {rejected[0]}")
        gaps = r["authz_tables_not_covered"]
        check(gaps == [], "清点覆盖授权层会提供的全部表",
              "" if gaps == [] else ("无法导入授权层，未核对" if gaps is None else "未覆盖：" + "、".join(gaps)))

        print("\n【4】应用能真的打开它")
        from synwevo_core.store import SCHEMA_VERSION, Store
        s = Store(work)
        check(s.schema_version() == SCHEMA_VERSION, f"schema v{s.schema_version()}（升级到当前版本）")
        n_proj = s.one("SELECT COUNT(*) AS n FROM projects")["n"]
        check(n_proj == actual.get("projects"), f"通过应用读到 {n_proj} 个项目")
        # The exact cost rollup and the per-wallet spend rollup are derived from the cost facts and the
        # settled calls by triggers; a backup carries both in one snapshot. Recompute every day from the
        # facts (verify --all): a difference fails the rehearsal, because the restored copy would refuse
        # that provider account's (or wallet's) paid calls.
        from synwevo_core.billing.cost_rollup import CostRollup
        rollup = CostRollup.check(s, all=True, mode="restore")
        check(rollup.ok, f"成本汇总与成本事实一致：{len(rollup.scopes)} 个提供方账户，{rollup.facts} 条成本事实；"
              f"{len(rollup.wallets)} 个钱包，{rollup.wallet_facts} 条已结算调用",
              "" if rollup.ok else json.dumps(rollup.summary(), ensure_ascii=False, default=str)[:2000])
        _close(s)

        print("\n【5】恢复时会做的处理（在这份隔离副本上演练）")
        jf = getattr(args, "journal_from", None)
        live = _resolve(jf) if jf else _default_live_dir()
        if live is not None and not (live / DB_NAME).exists() and not (live / JOURNAL_NAME).exists():
            live = None
        since = manifest.get("snapshot_at") or manifest.get("created_at")
        report = fence(work, since=since, journal_dir=live, archive_name=archive.name,
                       no_journal=live is None or since is None)
        _print_fence(report, rehearsal=True)

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
    c.add_argument("--out", default=".local/backups")
    c.set_defaults(func=create)
    v = sub.add_parser("verify", help="恢复到隔离目录并逐项核对")
    v.add_argument("--archive", required=True)
    v.add_argument("--journal-from", help="正在使用的数据目录（只读其安全日志；默认 SYNWEVO_DATA_DIR）")
    v.set_defaults(func=verify)
    r = sub.add_parser("restore", help="恢复到指定目录，并重放备份之后的撤销")
    r.add_argument("--archive", required=True)
    r.add_argument("--into", required=True)
    r.add_argument("--force", action="store_true",
                   help="目录里已有数据库时先把它移到隔离目录（服务必须已停止）")
    r.add_argument("--journal-from",
                   help="正在使用的数据目录：从它的安全日志读取备份之后的撤销（默认：--into 已有数据库时就是它，"
                        "否则 SYNWEVO_DATA_DIR）")
    r.add_argument("--no-journal", action="store_true",
                   help="原数据目录已整体丢失时使用：不重放撤销记录；会话、令牌、旧 API 密钥全部作废，"
                        "私密及服务客户端停用，公共交互客户端保留；成员及账户撤销须人工核实")
    r.set_defaults(func=restore)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
