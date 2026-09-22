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

SCHEMA_VERSION = 1

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

JSON_COLS = {"record_rule", "output", "notes", "structure", "options", "ocr", "corrections", "slots", "editor", "data",
             "snapshot", "counts", "record", "issues", "trace", "verification", "info", "summary"}


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def now() -> float:
    return time.time()


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "blobs").mkdir(exist_ok=True)
        self.db_path = self.root / "docweave.sqlite3"
        self._local = threading.local()
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    # ---------------------------------------------------------------- connection
    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    @contextmanager
    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = self._connect()
            self._local.c = c
        with self._lock:
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise

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
        with self.conn() as c:
            c.execute(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                      tuple(self._encode(row[k]) for k in cols))

    def update(self, table: str, key: dict, values: dict) -> None:
        if not values:
            return
        sets = ",".join(f"{k}=?" for k in values)
        where = " AND ".join(f"{k}=?" for k in key)
        with self.conn() as c:
            c.execute(f"UPDATE {table} SET {sets} WHERE {where}",
                      tuple(self._encode(v) for v in values.values()) + tuple(key.values()))

    def execute(self, sql: str, args: tuple = ()) -> int:
        with self.conn() as c:
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
        with self.conn() as c:
            c.execute("UPDATE projects SET rev = rev + 1, updated_at = ? WHERE id = ?", (now(), pid))
            r = c.execute("SELECT rev FROM projects WHERE id = ?", (pid,)).fetchone()
            return r["rev"] if r else 0
