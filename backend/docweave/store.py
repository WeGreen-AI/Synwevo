"""Persistence: SQLite metadata + content-addressed blob storage on local disk.

Blobs are immutable and named by SHA-256, so source files, template versions and
outputs are never overwritten in place.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL, updated_at REAL,
  template_id TEXT, record_rule TEXT, output TEXT, mapping_rev INTEGER DEFAULT 0,
  rev INTEGER DEFAULT 0, step TEXT, deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS source_files (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL, ord REAL, blob TEXT, size INTEGER,
  format TEXT, family TEXT, status TEXT, error TEXT, notes TEXT, structure TEXT, signature TEXT,
  parsed_blob TEXT, working_blob TEXT, options TEXT, version INTEGER DEFAULT 1, representative INTEGER DEFAULT 0,
  ocr TEXT, corrections TEXT, created_at REAL, updated_at REAL, removed INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_src_project ON source_files(project_id);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT, format TEXT, original_blob TEXT, original_name TEXT,
  head INTEGER DEFAULT 1, created_at REAL, updated_at REAL, deleted INTEGER DEFAULT 0, info TEXT
);
CREATE TABLE IF NOT EXISTS template_versions (
  template_id TEXT NOT NULL, version_no INTEGER NOT NULL, parent_no INTEGER, working_blob TEXT, slots TEXT,
  editor TEXT, note TEXT, created_at REAL, PRIMARY KEY (template_id, version_no)
);
CREATE TABLE IF NOT EXISTS mapping_revs (
  project_id TEXT NOT NULL, rev INTEGER NOT NULL, parent_rev INTEGER, data TEXT, note TEXT, created_at REAL,
  PRIMARY KEY (project_id, rev)
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, project_id TEXT, status TEXT, created_at REAL, started_at REAL, finished_at REAL,
  snapshot TEXT, options TEXT, counts TEXT, request_id TEXT UNIQUE, error TEXT, cancel_requested INTEGER DEFAULT 0,
  summary TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON jobs(project_id);
CREATE TABLE IF NOT EXISTS job_items (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, seq INTEGER, record_key TEXT, record TEXT, status TEXT,
  issues TEXT, attempts INTEGER DEFAULT 0, started_at REAL, finished_at REAL, file_base TEXT, label TEXT
);
CREATE INDEX IF NOT EXISTS ix_items_job ON job_items(job_id, seq);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, job_id TEXT NOT NULL, item_id TEXT, format TEXT, filename TEXT, blob TEXT, size INTEGER,
  trace TEXT, verification TEXT, created_at REAL, attempt INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_art_job ON artifacts(job_id);
"""

# v2: tenancy, job leases / runs / events, persistent idempotency, API keys, webhook deliveries
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS idempotency (
  tenant TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL,
  job_id TEXT, created_at REAL NOT NULL, PRIMARY KEY (tenant, scope, key)
);
CREATE TABLE IF NOT EXISTS job_runs (
  job_id TEXT NOT NULL, run_no INTEGER NOT NULL, trigger TEXT NOT NULL, requested_by TEXT, items INTEGER,
  created_at REAL, started_at REAL, finished_at REAL, status TEXT, PRIMARY KEY (job_id, run_no)
);
CREATE TABLE IF NOT EXISTS job_events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, job_id TEXT NOT NULL, tenant TEXT,
  type TEXT NOT NULL, version INTEGER NOT NULL, occurred_at REAL NOT NULL, data TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_job ON job_events(job_id, seq);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, tenant TEXT NOT NULL, scopes TEXT NOT NULL, secret_hash TEXT NOT NULL,
  created_at REAL NOT NULL, expires_at REAL, last_used_at REAL, revoked_at REAL
);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
  id TEXT PRIMARY KEY, event_id TEXT NOT NULL, event_seq INTEGER, endpoint TEXT NOT NULL, status TEXT NOT NULL,
  attempts INTEGER DEFAULT 0, next_attempt_at REAL, last_code INTEGER, last_error TEXT, created_at REAL, delivered_at REAL,
  UNIQUE (event_id, endpoint)
);
CREATE INDEX IF NOT EXISTS ix_wh_due ON webhook_deliveries(status, next_attempt_at);
"""

# columns added to v1 tables (name, table, definition)
V3_COLUMNS = [
    # Provenance: which run created this project.  Without it a cleanup tool cannot prove a
    # row is test data, and deleting by name prefix is not evidence.  Pre-v3 rows keep NULL,
    # which means "unknown origin" and is deliberately NOT treated as test data.
    ("projects", "origin", "TEXT"),
    # Archive is separate from `deleted`: deleted is the soft delete (the project 404s), an
    # archived project is still fully usable, just out of the default list.
    ("projects", "archived_at", "REAL"),
]

V2_COLUMNS = [
    ("projects", "tenant", "TEXT DEFAULT 'default'"),
    ("projects", "template_version", "INTEGER"),
    ("templates", "tenant", "TEXT DEFAULT 'default'"),
    ("jobs", "tenant", "TEXT DEFAULT 'default'"),
    ("jobs", "lease_owner", "TEXT"),
    ("jobs", "lease_until", "REAL"),
    ("jobs", "run_no", "INTEGER DEFAULT 1"),
    ("jobs", "cancel_requested_at", "REAL"),
    ("jobs", "origin", "TEXT DEFAULT 'ui'"),
    ("job_items", "run_no", "INTEGER DEFAULT 1"),
    ("artifacts", "run_no", "INTEGER DEFAULT 1"),
    ("artifacts", "deleted_at", "REAL"),
]

JSON_COLS = {"record_rule", "output", "notes", "structure", "options", "ocr", "corrections", "slots", "editor", "data",
             "snapshot", "counts", "record", "issues", "trace", "verification", "info", "summary"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now() -> float:
    return time.time()


class Store:
    def __init__(self, root: str | Path, migrate: bool = True):
        """Open the store, migrating the schema forward unless told not to.

        ``migrate=False`` is for looking at a data directory without changing it — checking a
        backup's schema version, or reading counts before an upgrade. Opening used to migrate
        unconditionally, which made `manage.py info` (the command the upgrade docs tell you to
        run to *check* the version) silently rewrite a v2 database to v3 in place. Pointed at a
        backup, an inspection command that mutates its subject defeats the backup.
        """
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        self.db_path = self.root / "docweave.sqlite3"
        self._local = threading.local()
        self._write_lock = threading.RLock()     # serialises writers inside this process; SQLite locks across processes
        c = self._c()
        if not migrate:
            return
        c.executescript(SCHEMA)
        self._migrate(c)
        self._stamp_instance_kind(c)

    # ---------------------------------------------------------------- instance identity
    def _stamp_instance_kind(self, c: sqlite3.Connection) -> None:
        """Record once, at creation, whether this data directory belongs to a test run.

        Destructive test tooling refuses to touch an instance that is not marked ``test``.
        The mark is written only while the directory is still empty, so setting
        DOCWEAVE_TEST_INSTANCE against a directory that already holds real projects can
        never re-label it - it stays ``normal`` and stays protected."""
        row = c.execute("SELECT value FROM meta WHERE key='instance_kind'").fetchone()
        if row:
            return
        empty = c.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0
        want_test = (os.environ.get("DOCWEAVE_TEST_INSTANCE") or "").strip().lower() in ("1", "true", "yes", "on")
        kind = "test" if (empty and want_test) else "normal"
        c.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('instance_kind', ?)", (kind,))

    def instance_kind(self) -> str:
        row = self.one("SELECT value FROM meta WHERE key='instance_kind'")
        return (row or {}).get("value") or "normal"

    # ---------------------------------------------------------------- schema migration
    def _migrate(self, c: sqlite3.Connection) -> None:
        """Forward-only, idempotent migrations.  Existing rows stay readable: new columns have
        defaults, and pre-v2 rows belong to the default tenant."""
        c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1')")
        ver = int(c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"])
        if ver < SCHEMA_VERSION:
            # Every migration so far is additive: new tables (idempotent CREATE IF NOT EXISTS)
            # and new columns (guarded by PRAGMA).  Running the whole set on any older version
            # is therefore safe AND necessary — gating on `ver < 2` meant a v2 database never
            # received v3's columns and the app then failed with "no such column".
            c.executescript(SCHEMA_V2)
            with self.tx() as t:
                for table, col, decl in V2_COLUMNS + V3_COLUMNS:
                    have = {r["name"] for r in t.execute(f"PRAGMA table_info({table})").fetchall()}
                    if col not in have:
                        t.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                t.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))
        c.executescript(SCHEMA_V2)                   # new tables of the current version (idempotent)

    def schema_version(self) -> int:
        """0 means "nothing here yet" — only reachable via ``Store(..., migrate=False)``, which
        may be pointed at an empty or not-yet-initialised directory."""
        try:
            row = self.one("SELECT value FROM meta WHERE key='schema_version'")
        except sqlite3.OperationalError:
            return 0
        return int(row["value"]) if row else 0

    # ---------------------------------------------------------------- connection
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False, isolation_level=None)  # explicit transactions
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    def _c(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._connect()
            self._local.c = c
            self._local.depth = 0
        return c

    @contextmanager
    def tx(self):
        """A write transaction (``BEGIN IMMEDIATE``): all statements inside commit together or
        not at all.  Re-entrant within a thread; the outermost block commits."""
        c = self._c()
        if self._local.depth:
            self._local.depth += 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        with self._write_lock:
            c.execute("BEGIN IMMEDIATE")
            self._local.depth = 1
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
            finally:
                self._local.depth = 0

    @contextmanager
    def conn(self):
        """Single statements: autocommit (atomic on their own); inside ``tx()`` they join it."""
        yield self._c()

    # ---------------------------------------------------------------- rows
    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in list(d):
            if k in JSON_COLS and isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except json.JSONDecodeError:
                    pass
        return d

    @staticmethod
    def _encode(v):
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return v

    def one(self, sql: str, args: tuple = ()) -> dict | None:
        with self.conn() as c:
            return self._decode(c.execute(sql, args).fetchone())

    def all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.conn() as c:
            return [self._decode(r) for r in c.execute(sql, args).fetchall()]

    def insert(self, table: str, row: dict) -> None:
        cols = list(row)
        with self.tx() as c:
            c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      tuple(self._encode(row[k]) for k in cols))

    def update(self, table: str, key: dict, values: dict) -> None:
        if not values:
            return
        sets = ",".join(f"{k}=?" for k in values)
        where = " AND ".join(f"{k}=?" for k in key)
        with self.tx() as c:
            c.execute(f"UPDATE {table} SET {sets} WHERE {where}",
                      tuple(self._encode(v) for v in values.values()) + tuple(key.values()))

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.tx() as c:
            return c.execute(sql, args).rowcount

    # ---------------------------------------------------------------- blobs
    def put_blob(self, data: bytes) -> str:
        h = hashlib.sha256(data).hexdigest()
        p = self.blob_path(h)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp" + secrets.token_hex(3))
            tmp.write_bytes(data)
            os.replace(tmp, p)
        return h

    def put_json_blob(self, obj) -> str:
        return self.put_blob(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def blob_path(self, h: str) -> Path:
        if not h or not all(ch in "0123456789abcdef" for ch in h) or len(h) != 64:
            raise ValueError("invalid blob id")
        return self.root / "blobs" / h[:2] / h

    def get_blob(self, h: str) -> bytes:
        return self.blob_path(h).read_bytes()

    def get_json_blob(self, h: str):
        return json.loads(self.get_blob(h).decode("utf-8"))

    def has_blob(self, h: str | None) -> bool:
        return bool(h) and self.blob_path(h).exists()

    # ---------------------------------------------------------------- project rev
    def bump_project(self, pid: str) -> int:
        with self.tx() as c:
            c.execute("UPDATE projects SET rev = rev + 1, updated_at = ? WHERE id = ?", (now(), pid))
            r = c.execute("SELECT rev FROM projects WHERE id = ?", (pid,)).fetchone()
            return r["rev"] if r else 0
