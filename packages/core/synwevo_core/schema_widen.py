"""Widen a CHECK constraint of an existing SQLite table, as one migration step.

SQLite cannot ALTER a CHECK. The documented procedure (sqlite.org/lang_altertable.html, "Making
Other Kinds Of Table Schema Changes", read 2026-10-05) is: foreign keys off, one transaction,
create the new table, copy the rows, drop the old table, rename the new one, recreate the
indexes, triggers and views, `PRAGMA foreign_key_check`, commit, foreign keys back on.

This helper is that procedure and nothing else:

* It is driven by the table's **stored** CREATE text (`sqlite_master.sql`), so columns added
  later by ``ALTER TABLE ... ADD COLUMN`` come along unchanged and in place. Each change is an
  exact text replacement of the old clause by the new one; when the old clause is not there
  (an unexpected schema), it refuses instead of guessing, and nothing is changed.
* It is idempotent: when every new clause is already present it does nothing, so every process
  that opens the store may run it; the check is repeated under the write lock.
* Rows are copied column by column, unchanged. Immutability triggers on the table stop UPDATE /
  DELETE, not this copy; they are recreated from their stored text afterwards.
* Indexes and triggers on the table, and every trigger or view elsewhere whose text names it,
  are recreated from their stored text after the rename (dependent ones are dropped first, so
  the rename cannot fail on a dangling reference).
* ``PRAGMA foreign_keys`` cannot change inside a transaction, so the caller's connection must
  not be in one; the previous setting is restored afterwards, also on failure.
"""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence


def _stored(c: sqlite3.Connection, table: str) -> str | None:
    row = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row[0] if row else None


def _pending(c: sqlite3.Connection, changes: Mapping[str, Sequence[tuple[str, str]]]) -> dict[str, str]:
    """table -> stored SQL, for every table that still lacks one of its new clauses."""
    out = {}
    for table, pairs in changes.items():
        sql = _stored(c, table)
        if sql is None:
            continue                         # created later by its own CREATE TABLE, already wide
        if all(new in sql for _, new in pairs):
            continue
        out[table] = sql
    return out


def pending(c: sqlite3.Connection, changes: Mapping[str, Sequence[tuple[str, str]]]) -> list[str]:
    """The tables of ``changes`` that still need widening (read without a lock)."""
    return list(_pending(c, changes))


def widen_checks(c: sqlite3.Connection, changes: Mapping[str, Sequence[tuple[str, str]]], *,
                 extra: Callable[[sqlite3.Connection], None] | None = None, force: bool = False) -> list[str]:
    """Rebuild each table in ``changes`` with its (old, new) clause replacements applied.

    ``extra(c)`` runs inside the same transaction after the rebuild (e.g. to replace a trigger
    whose body must change too; it must be idempotent). Without ``force`` nothing runs when no
    table needs widening. Returns the tables rebuilt.
    """
    if c.in_transaction:
        raise RuntimeError("schema widening needs a connection outside a transaction")
    if not force and not _pending(c, changes):
        return []
    previous = c.execute("PRAGMA foreign_keys").fetchone()[0]
    c.execute("PRAGMA foreign_keys=OFF")
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            todo = _pending(c, changes)                       # again, under the write lock
            saved: dict[str, tuple[str, str]] = {}
            for table in todo:
                pattern = re.compile(r"\b" + re.escape(table) + r"\b")
                for name, kind, owner, sql in c.execute(
                        "SELECT name,type,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL "
                        "AND type IN ('index','trigger','view') ORDER BY rowid").fetchall():
                    if owner == table or (kind in ("trigger", "view") and pattern.search(sql)):
                        saved.setdefault(name, (kind, sql))
            # Dependents that live on other tables (or are views) would dangle during the rename.
            for name, (kind, _) in saved.items():
                if kind == "view":
                    c.execute(f'DROP VIEW IF EXISTS "{name}"')
                elif kind == "trigger":
                    c.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            rebuilt = []
            for table, sql in todo.items():
                for old, new in changes[table]:
                    if new in sql:
                        continue
                    if sql.count(old) != 1:
                        raise RuntimeError(f"unexpected schema for {table}: cannot widen")
                    sql = sql.replace(old, new)
                head = re.match(r'CREATE TABLE\s+"?' + re.escape(table) + r'"?\s*\(', sql)
                if not head:
                    raise RuntimeError(f"unexpected schema for {table}: cannot widen")
                staging = f"{table}__widen"
                c.execute(f'DROP TABLE IF EXISTS "{staging}"')
                c.execute(f'CREATE TABLE "{staging}" (' + sql[head.end():])
                columns = ",".join(f'"{r[1]}"' for r in c.execute(f'PRAGMA table_info("{table}")').fetchall())
                c.execute(f'INSERT INTO "{staging}" ({columns}) SELECT {columns} FROM "{table}"')
                c.execute(f'DROP TABLE "{table}"')
                # Legacy rename: no re-parse of every other trigger and view in the schema. A
                # trigger of another module may name a column its own migration adds only later
                # in this start-up; the modern rename refuses the whole schema over it. Nothing
                # here needs rewriting: dependents were dropped above and are recreated below.
                legacy = c.execute("PRAGMA legacy_alter_table").fetchone()[0]
                c.execute("PRAGMA legacy_alter_table=ON")
                try:
                    c.execute(f'ALTER TABLE "{staging}" RENAME TO "{table}"')
                finally:
                    c.execute(f"PRAGMA legacy_alter_table={'ON' if legacy else 'OFF'}")
                rebuilt.append(table)
            order = {"index": 0, "view": 1, "trigger": 2}
            for name, (kind, sql) in sorted(saved.items(), key=lambda item: order[item[1][0]]):
                exists = c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone()
                if not exists:
                    c.execute(sql)
            if extra is not None:
                extra(c)
            problems = c.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise RuntimeError("schema widening left foreign key violations")
            c.execute("COMMIT")
            return rebuilt
        except BaseException:
            c.execute("ROLLBACK")
            raise
    finally:
        c.execute(f"PRAGMA foreign_keys={'ON' if previous else 'OFF'}")


def statements(script: str):
    """The complete statements of an SQL script, for running one inside a transaction
    (``executescript`` would commit the transaction first)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""
    if buf.strip():
        raise ValueError("incomplete SQL statement")
