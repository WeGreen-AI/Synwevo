"""The list of places a stored blob can be referenced (`synwevo_core.blob_refs`) stays complete.

A collector is only as safe as that list: the first inventory missed Synwevo Message
attachments, resumable uploads and organization publications (CI-106), and would have deleted
every message attachment whose bytes are its own. These tests make the next such gap fail here
instead of in someone's data (docs/platform/blob-reclamation.md §1.9):

* every column of a freshly migrated database that could hold a digest is classified;
* every read gate that names referencing tables is covered by the list;
* every place in the code that writes, reads or moves blob bytes is registered, with where its
  reference lands — a new `put_blob` call must say which row will name the bytes.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

from synwevo_core import blob_refs
from synwevo_core.store import Store

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = re.compile(r"blob|sha|digest|hash", re.I)

#: Every function that stores new bytes, and the row (or column) that will name them.
WRITERS = {
    ("apps/docs/synwevo_docs/services.py", "add_source"): "source_files.blob",
    ("apps/docs/synwevo_docs/services.py", "_parse_source"): "source_files.parsed_blob / working_blob",
    ("apps/docs/synwevo_docs/services.py", "desensitize_sources"): "blob_grants (desensitized:<sid>)",
    ("apps/docs/synwevo_docs/services.py", "upload_template"): "templates.original_blob, templates.info.$.image.original_blob",
    ("apps/docs/synwevo_docs/services.py", "new_version"): "template_versions.working_blob",
    ("apps/docs/synwevo_docs/builtin_service.py", "create_builtin"): "templates.original_blob",
    ("apps/docs/synwevo_docs/builtin_service.py", "save_builtin"): "templates.original_blob / template_versions.working_blob",
    ("apps/docs/synwevo_docs/builtin_service.py", "save_live_sheet"): "templates.original_blob / template_versions.working_blob (a live table room's save)",
    ("apps/docs/synwevo_docs/jobs.py", "_process_item"): "artifacts.blob",
    ("apps/docs/synwevo_docs/views.py", "preview"): "blob_grants (preview)",
    ("apps/docs/synwevo_docs/views.py", "preview_layout"): "blob_grants (layout)",
    ("apps/docs/synwevo_docs/api/v1_uploads.py", "_copy_completed_blob"): "uploads.blob (bypasses put_blob: writes blob_path directly)",
    ("packages/core/synwevo_core/messaging.py", "attach"): "message_attachments.blob",
    ("apps/suite/synwevo_suite/forms/filling.py", "upload"): "form_files.blob",
    ("apps/suite/synwevo_suite/dataprep/service.py", "upload"): "dataprep_files.blob",
    ("apps/suite/synwevo_suite/dataprep/service.py", "save_table"): "dataprep_datasets.rows_blob",
    ("apps/suite/synwevo_suite/extract/service.py", "upload"): "extract_files.blob",
    ("packages/core/synwevo_core/store.py", "put_blob"): "(the store itself)",
    ("packages/core/synwevo_core/store.py", "put_json_blob"): "(the store itself)",
}

#: Functions that only locate, read or move existing bytes (no new reference).
ACCESSORS = {
    ("packages/core/synwevo_core/store.py", "get_blob"): "read",
    ("packages/core/synwevo_core/store.py", "has_blob"): "read",
    ("packages/core/synwevo_core/rescan.py", "read_verified"): "read for a rescan",
    ("packages/core/synwevo_core/rescan.py", "_examine"): "read for a rescan",
    ("packages/core/synwevo_core/quarantine.py", "read_any"): "read held bytes",
    ("packages/core/synwevo_core/quarantine.py", "remove_from_store"): "move into quarantine",
    ("packages/core/synwevo_core/quarantine.py", "put_back"): "move back from quarantine",
    ("packages/core/synwevo_core/quarantine.py", "settle"): "move into quarantine",
    ("apps/docs/synwevo_docs/services.py", "_quarantine_blob"): "the upload gate's own hold / unlink",
    ("apps/docs/synwevo_docs/jobs.py", "zip_file"): "read outputs into a ZIP",
    ("apps/docs/scripts/snapshot.py", "_check_blobs"): "backup verification (read)",
}

BLOB_CALLS = ("put_blob", "put_json_blob", "blob_path")


def _call_sites() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for top in ("apps", "packages", "services"):
        for f in (ROOT / top).rglob("*.py"):
            rel = f.relative_to(ROOT).as_posix()
            if "/tests/" in rel or "node_modules" in rel or "/web/" in rel:
                continue
            tree = ast.parse(f.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in BLOB_CALLS:
                        sites.add((rel, fn.name))
    return sites


def test_every_place_that_writes_or_moves_blob_bytes_is_registered():
    sites = _call_sites()
    unregistered = sorted(sites - set(WRITERS) - set(ACCESSORS))
    assert not unregistered, (
        "新的 blob 写入/读取位置没有登记：请在 WRITERS（写在哪一行/列）或 ACCESSORS 里说明，并确认 "
        f"synwevo_core.blob_refs 覆盖它写下的引用：{unregistered}")
    stale = sorted((set(WRITERS) | set(ACCESSORS)) - sites)
    assert not stale, f"登记了但代码里已不存在的位置：{stale}"


def test_every_column_that_could_hold_a_digest_is_classified(tmp_path):
    Store(tmp_path)
    db = next(tmp_path.glob("*.sqlite3"))
    c = sqlite3.connect(db)
    classified = ({f"{t}.{col}" for t, col in blob_refs.DIRECT} | {f"{t}.{col}" for t, col, _ in blob_refs.LOOKALIKES}
                  | {f"{t}.{col}" for t, col in blob_refs.INLINE_BYTES} | {f"{t}.{col}" for t, col, _ in blob_refs.JSON_PATHS})
    tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    candidates = {f"{t}.{r[1]}" for t in tables for r in c.execute(f"PRAGMA table_info({t})") if CANDIDATE.search(r[1])}
    missing = sorted(candidates - classified)
    assert not missing, ("这些列可能存着 blob 摘要，却没有归类（引用 / 形似 / 内联字节）：" + "、".join(missing))
    # and everything listed exists: a renamed column must not leave the list pointing at nothing
    have = {f"{t}.{r[1]}" for t in tables for r in c.execute(f"PRAGMA table_info({t})")}
    listed = ({f"{t}.{col}" for t, col in blob_refs.DIRECT} | {f"{t}.{col}" for t, col in blob_refs.INLINE_BYTES}
              | {f"{t}.{col}" for t, col, _ in blob_refs.JSON_PATHS})
    assert not sorted(listed - have), f"清单里的列在新库中不存在：{sorted(listed - have)}"


def test_the_list_covers_every_read_gate_that_names_referencing_tables():
    from synwevo_core.quarantine import REFERENCING_TABLES
    from synwevo_docs import authz
    covered = blob_refs.referencing_tables()
    gates = {t for ts in REFERENCING_TABLES.values() for t in ts}
    for clause in authz._REACHABLE:
        gates |= set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", clause))
    gates -= {"projects", "jobs", "json_each"}        # joins that supply a workspace or a verdict
    assert gates <= covered, sorted(gates - covered)
