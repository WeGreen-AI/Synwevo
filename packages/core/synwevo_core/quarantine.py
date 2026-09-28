"""Quarantine for stored bytes that a later scan judged malicious (CI-90).

A new upload that the engine flags is refused, and when an asynchronous source scan flags it the
blob is unlinked (docs/security/file-acceptance.md §3): the uploader still has the file, nobody
has used it yet. Files stored *before* scanning was switched on are different. They have been
readable for weeks, the owner may no longer have a copy, and a false positive — which only the
signature publisher can withdraw (clamav.net/reports/fp) — must be recoverable. So a rescan that
finds one does not delete it: the bytes move out of the blob store into this directory, which no
route serves, and an index entry records what they were, which rows pointed at them and what
state each row was in, so a reviewed false positive can be put back exactly and a confirmed one
purged.

Layout under the data directory (the same volume as ``blobs/``)::

    quarantine/                 0700
        <sha256>                the bytes, 0600, verified against their name before every use
        <sha256>.json           the index entry (below)
        <sha256>.d/             other copies moved aside whole (Docs regression cases); a purge
                                never deletes these — they hold other files too
        .lock                   serialises writers across processes (the docs and messages
                                containers share the volume)
        .generation             a counter every index write moves forward: the servers' cached
                                set of standing verdicts is keyed on it (`standing`)

An entry::

    {"sha256", "size", "signature", "engine", "state": "quarantined" | "released" | "purged",
     "quarantined_at", "released_at", "purged_at",
     "rows": [{"kind": "source_files", "id": "s_…", "workspace": "…", "previous": {…},
               "held_at": t, "released_at": null}],
     "history": [{"at": t, "action": "held" | "rows_added" | "released" | "purged", "by": "…", …}]}

``previous`` holds only states (status and scan record) — never file names, error sentences or
other user text; the rows are identified by id. Each application restores only its own rows
(``kind``) and records exactly the rows it restored; the bytes leave this directory when no row is
still held.

**The verdict is the entry, not the absence of the bytes.** While an entry is ``quarantined`` or
``purged`` its digest is refused everywhere — read paths, new uploads, completed resumable uploads,
organization template copies — whether or not a copy of the bytes happens to be in the blob store
again (a restored backup, a crash between marking rows and removing the store copy). `standing()`
is the cheap set every such check asks; `settle()` takes any such store copy back out.

The index is files, not a table: the only reader is the operator's command line, the bytes sit
next to it, and adding a table would be a schema migration for a directory listing.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .store import Store

DIRNAME = "quarantine"
#: A verdict stands for these states: the bytes are malicious and not released.
STANDING = ("quarantined", "purged")


class QuarantineError(Exception):
    """The operation cannot be done as asked (unknown digest, wrong state, bytes that do not match)."""


def _valid(sha: str) -> str:
    sha = (sha or "").strip().lower()
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise QuarantineError(f"不是 SHA-256 摘要：{sha[:80]!r}")
    return sha


def _is_digest(name: str) -> bool:
    return len(name) == 64 and all(ch in "0123456789abcdef" for ch in name)


def directory(store: Store, create: bool = False) -> Path:
    d = Path(store.root) / DIRNAME
    if create and not d.is_dir():
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return d


def bytes_path(store: Store, sha: str) -> Path:
    return directory(store) / _valid(sha)


def aside_dir(store: Store, sha: str, create: bool = False) -> Path:
    """Where an application moves other copies of the same bytes (kept with the entry)."""
    d = directory(store, create=create) / f"{_valid(sha)}.d"
    if create:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _entry_path(store: Store, sha: str) -> Path:
    return directory(store) / f"{_valid(sha)}.json"


@contextmanager
def lock(store: Store):
    """Exclusive across processes: the docs and messages containers share the data volume."""
    d = directory(store, create=True)
    with open(d / ".lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _bump_generation(store: Store) -> None:
    """Every index write moves the counter forward (callers hold `lock`). Directory timestamps
    are too coarse to key a cache on (a Linux ext4 tick can hold both the temporary file and the
    rename), so readers key on this counter instead."""
    p = directory(store) / ".generation"
    try:
        n = int(p.read_text(encoding="ascii").strip() or "0")
    except (FileNotFoundError, ValueError):
        n = 0
    tmp = p.with_name(f".generation.tmp{os.getpid()}")
    tmp.write_text(str(n + 1), encoding="ascii")
    os.replace(tmp, p)


def _write_entry(store: Store, sha: str, entry: dict) -> None:
    _write_json(_entry_path(store, sha), entry)
    _bump_generation(store)


def _write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _write_bytes(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _digest_of(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None


def load(store: Store, sha: str) -> dict | None:
    p = _entry_path(store, sha)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def entries(store: Store) -> list[dict]:
    d = directory(store)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        if not _is_digest(p.stem):
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return sorted(out, key=lambda e: e.get("quarantined_at") or 0)


# ------------------------------------------------------------------ the standing verdicts
_standing_cache: dict[str, tuple[tuple, frozenset]] = {}
_standing_lock = threading.Lock()
#: A directory modified this recently is not cached on its timestamp alone (coarse clocks).
_FRESH_S = 2.0


def standing(store: Store) -> frozenset:
    """Every digest whose verdict stands (quarantined or purged). Read on hot paths — every source
    view, template read and blob-id check — so it is cached, keyed on the `.generation` counter
    every index write moves forward (written after the entry, so a reader that sees a counter
    also sees the entries it covers). The directory's identity and modification time are part of
    the key too, for an index written without the counter (a restore), and a directory modified
    within the last two seconds is read afresh every time."""
    d = directory(store)
    try:
        st = d.stat()
    except FileNotFoundError:
        return frozenset()
    try:
        gen = (d / ".generation").read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError):
        gen = ""
    stamp = (gen, st.st_mtime_ns, st.st_ino)
    fresh = (time.time() - st.st_mtime) < _FRESH_S
    key = str(d)
    if not fresh:
        with _standing_lock:
            hit = _standing_cache.get(key)
            if hit and hit[0] == stamp:
                return hit[1]
    out = set()
    for p in d.glob("*.json"):
        if not _is_digest(p.stem):
            continue
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("state") in STANDING:
                out.add(p.stem)
        except (OSError, ValueError):
            continue
    result = frozenset(out)
    if not fresh:
        with _standing_lock:
            _standing_cache[key] = (stamp, result)
    return result


def verdict(store: Store, sha: str | None) -> dict | None:
    """The standing verdict on these bytes, if any: quarantined (bytes here) or purged (gone)."""
    if not sha or sha not in standing(store):
        return None
    try:
        e = load(store, sha)
    except (QuarantineError, OSError, ValueError):
        return None
    return e if e and e.get("state") in STANDING else None


def read(store: Store, sha: str) -> bytes:
    """The quarantined bytes, verified against their name."""
    p = bytes_path(store, sha)
    if not p.is_file():
        raise QuarantineError(f"隔离区里没有 {sha} 的内容（已清除，或从未隔离）")
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise QuarantineError(f"隔离区里 {sha} 的内容与摘要不符，拒绝使用")
    return data


def read_any(store: Store, sha: str) -> bytes:
    """The bytes for a release: the quarantine copy, or — once an earlier release put them back and
    removed nothing yet for the other application — the blob store copy; either verified."""
    try:
        return read(store, sha)
    except QuarantineError:
        path = store.blob_path(_valid(sha))
        if _digest_of(path) == sha:
            return path.read_bytes()
        raise


def _merge_rows(entry: dict, rows: list[dict], at: float) -> int:
    have = {(r["kind"], r["id"]): r for r in entry.setdefault("rows", [])}
    added = 0
    for r in rows:
        key = (r["kind"], r["id"])
        cur = have.get(key)
        if cur is not None and not cur.get("released_at"):
            continue                                   # already held: keep the first "previous"
        rec = {"kind": r["kind"], "id": r["id"], "workspace": r.get("workspace"),
               "previous": r.get("previous"), "held_at": at, "released_at": None}
        if cur is not None:
            entry["rows"].remove(cur)
        entry["rows"].append(rec)
        have[key] = rec
        added += 1
    return added


def hold(store: Store, sha: str, data: bytes | None, *, signature: str | None, engine: str | None,
         rows: list[dict], by: str) -> dict:
    """Keep these bytes here and record the rows that pointed at them.

    ``data`` must hash to ``sha``; it may be None only when an entry whose verdict stands already
    exists (another application found the bytes first). The blob store copy is *not* removed here:
    callers mark their rows first and then call :func:`remove_from_store`, so a crash in between
    leaves the bytes in two places, never in none — and :func:`settle` finishes the move later.
    """
    sha = _valid(sha)
    if data is not None and hashlib.sha256(data).hexdigest() != sha:
        raise QuarantineError(f"内容的 SHA-256 与 {sha} 不符，拒绝隔离")
    at = time.time()
    with lock(store):
        entry = load(store, sha)
        target = bytes_path(store, sha)
        if data is not None and _digest_of(target) != sha:
            _write_bytes(target, data)
        if entry is None:
            if data is None:
                raise QuarantineError(f"{sha} 没有隔离记录，也没有给出内容")
            entry = {"sha256": sha, "size": len(data), "signature": signature, "engine": engine,
                     "state": "quarantined", "quarantined_at": at, "released_at": None, "purged_at": None,
                     "rows": [], "history": []}
            action = "held"
        else:
            action = "rows_added"
            if data is not None and entry.get("state") in ("released", "purged"):
                # Flagged again after a release (a signature that came back), or bytes that were
                # purged and have since been stored again: they are held here again.
                entry.update({"state": "quarantined", "signature": signature or entry.get("signature"),
                              "engine": engine or entry.get("engine"), "quarantined_at": at, "released_at": None,
                              "purged_at": None})
                action = "held"
        added = _merge_rows(entry, rows, at)
        entry["history"].append({"at": at, "action": action, "by": by, "rows": added,
                                 "signature": signature, "engine": engine})
        _write_entry(store, sha, entry)
    return entry


def remove_from_store(store: Store, sha: str) -> bool:
    """Take the bytes out of the blob store once they are safe here — only after the quarantine
    copy was checked against its digest. Returns whether a file went."""
    if _digest_of(bytes_path(store, sha)) != sha:
        raise QuarantineError(f"{sha} 在隔离区的副本不存在或与摘要不符，不能从存储中移走")
    try:
        store.blob_path(sha).unlink()
        return True
    except FileNotFoundError:
        return False


def put_back(store: Store, sha: str, data: bytes) -> None:
    """Return verified bytes to the blob store. A store file already there is kept only if it is
    exactly these bytes; a damaged one is replaced (`Store.put_blob` would keep it)."""
    if hashlib.sha256(data).hexdigest() != sha:
        raise QuarantineError(f"内容的 SHA-256 与 {sha} 不符，拒绝放回")
    dest = store.blob_path(sha)
    if _digest_of(dest) == sha:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(dest, data)


def settle(store: Store) -> list[dict]:
    """Finish what a crash may have left: every digest whose verdict stands must not also sit in
    the blob store. Run at the start of every rescan and before a purge.

    A store copy of a ``quarantined`` digest is removed once the quarantine copy is verified (and
    becomes the quarantine copy if that one is missing or damaged); a store copy of a ``purged``
    digest is removed once it is verified to be those bytes. A store file that does not match its
    own name is left alone and reported: it is not the judged content."""
    out = []
    for sha in sorted(standing(store)):
        path = store.blob_path(sha)
        if not path.exists():
            continue
        got = _digest_of(path)
        if got != sha:
            out.append({"sha256": sha, "action": "store_copy_damaged"})
            continue
        with lock(store):
            entry = load(store, sha) or {}
            if entry.get("state") == "quarantined" and _digest_of(bytes_path(store, sha)) != sha:
                _write_bytes(bytes_path(store, sha), path.read_bytes())
                action = "moved"
            else:
                action = "removed"
            try:
                path.unlink()
            except FileNotFoundError:
                continue
        out.append({"sha256": sha, "action": action})
    return out


def pending(entry: dict, kinds: set[str] | None = None) -> list[dict]:
    """Rows still held (optionally only of these kinds)."""
    return [r for r in entry.get("rows") or [] if not r.get("released_at") and (kinds is None or r["kind"] in kinds)]


def _prune_empty(d: Path) -> None:
    """Remove empty directories under (and including) ``d``; anything with a file in it stays."""
    if not d.is_dir():
        return
    for root, dirs, files in os.walk(d, topdown=False):
        if not files and not os.listdir(root):
            try:
                os.rmdir(root)
            except OSError:
                pass


def release(store: Store, sha: str, done: set[tuple[str, str]], *, by: str, engine: str | None) -> dict:
    """The verdict is withdrawn (the current engine found the bytes clean, which the caller
    checked) and the rows in ``done`` — ``(kind, id)`` — were restored by their application, or
    released from this verdict while another still holds them. Only those rows are marked.

    The entry becomes ``released`` at once: the bytes are back in the blob store and no read path
    refuses them for this digest any more. Rows of the other application stay marked rejected in
    their own table until its own ``quarantine release`` restores them; the quarantine copy is
    kept until no row is pending, then removed. The entry stays as the record of what happened."""
    sha = _valid(sha)
    at = time.time()
    with lock(store):
        entry = load(store, sha)
        if entry is None:
            raise QuarantineError(f"{sha} 没有隔离记录")
        if entry.get("state") == "purged":
            raise QuarantineError(f"{sha} 已清除，不能恢复")
        n = 0
        for r in pending(entry):
            if (r["kind"], r["id"]) in done:
                r["released_at"] = at
                n += 1
        if entry.get("state") != "released":
            entry["state"] = "released"
            entry["released_at"] = at
        left = pending(entry)
        if not left:
            try:
                bytes_path(store, sha).unlink()
            except FileNotFoundError:
                pass
            _prune_empty(aside_dir(store, sha))
        entry["history"].append({"at": at, "action": "released", "by": by, "rows": n, "engine": engine,
                                 "still_held": len(left)})
        _write_entry(store, sha, entry)
    return entry


def aside_copies(store: Store, sha: str) -> list[str]:
    """What an application moved aside for these bytes, relative to the aside directory."""
    d = aside_dir(store, sha)
    if not d.is_dir():
        return []
    return sorted(str(p.relative_to(d)) for p in d.glob("*/*/*") if p.is_dir())


def purge(store: Store, sha: str, *, by: str) -> dict:
    """Delete the quarantined bytes for good after a review confirmed the verdict. Rows stay
    rejected. Copies moved aside whole (regression cases) are **not** deleted — they hold other,
    clean files too — and are left for the operator, listed by `aside_copies`."""
    sha = _valid(sha)
    at = time.time()
    with lock(store):
        entry = load(store, sha)
        if entry is None:
            raise QuarantineError(f"{sha} 没有隔离记录")
        if entry.get("state") != "quarantined":
            raise QuarantineError(f"{sha} 的状态是 {entry.get('state')}，只有 quarantined 可以清除")
        try:
            bytes_path(store, sha).unlink()
        except FileNotFoundError:
            pass
        entry["state"] = "purged"
        entry["purged_at"] = at
        entry["history"].append({"at": at, "action": "purged", "by": by, "aside_kept": aside_copies(store, sha)})
        _write_entry(store, sha, entry)
    return entry


#: Every table whose rows point at stored bytes, per application — data, so the reference
#: inventory (apps/docs/scripts/blob_audit.py) can be checked against it rather than drift.
REFERENCING_TABLES = {
    "docs": ("source_files", "templates", "template_versions", "organization_templates", "organization_template_releases"),
    "messages": ("message_attachments",),
}


def unmarked_references(store: Store, sha: str) -> dict[str, int]:
    """Rows of each application that still point at these bytes without being marked rejected —
    read-only, for the operator summary ("run the other application's rescan too"). The tables
    are the ones `Store` creates (`REFERENCING_TABLES`); nothing here imports an application."""
    q = {
        "docs": [
            ("SELECT COUNT(*) AS n FROM source_files WHERE blob=? AND COALESCE(status,'')<>'rejected'", (sha,)),
            ("SELECT COUNT(*) AS n FROM templates WHERE COALESCE(json_extract(scan,'$.status'),'')<>'infected' AND "
             "(original_blob=? OR json_extract(info,'$.image.original_blob')=? OR id IN "
             "(SELECT template_id FROM template_versions WHERE working_blob=?))", (sha, sha, sha)),
            ("SELECT COUNT(*) AS n FROM organization_templates WHERE COALESCE(json_extract(info,'$.scan.status'),'')<>'infected' AND "
             "(working_blob=? OR json_extract(info,'$.image.original_blob')=? OR id IN "
             "(SELECT publication_id FROM organization_template_releases WHERE working_blob=?))", (sha, sha, sha)),
        ],
        "messages": [
            ("SELECT COUNT(*) AS n FROM message_attachments WHERE blob=? AND status<>'rejected'", (sha,)),
        ],
    }
    out = {}
    for app, queries in q.items():
        n = 0
        for sql, args in queries:
            try:
                n += (store.one(sql, args) or {}).get("n") or 0
            except Exception:  # noqa: BLE001 - a table this deployment does not have holds no reference
                continue
        out[app] = n
    return out
