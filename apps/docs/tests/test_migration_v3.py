"""Additive migrations must reach a database at ANY older version.

DW-058: the gate was `if ver < 2`, so a database already stamped v2 never received v3's
columns and the app then failed at runtime with "no such column: origin".  The deployed
instance is exactly such a database, so this would have broken it on upgrade.
"""
from __future__ import annotations

import json

import sqlite3

import pytest

from synwevo_core.store import (INDEXES, SCHEMA, SCHEMA_VERSION, V2_COLUMNS, V3_COLUMNS, V4_COLUMNS,
                                 V5_COLUMNS, Store)

TABLES = ("projects", "templates", "jobs", "source_files", "artifacts", "job_items")


def _rows(store_or_conn, table):
    if isinstance(store_or_conn, Store):
        return store_or_conn.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    return store_or_conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()[0]


def _stamp(path, version: int):
    c = sqlite3.connect(path / "docweave.sqlite3")
    c.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
    c.commit()
    c.close()


@pytest.mark.parametrize("from_version", [1, 2])
def test_columns_are_added_from_any_older_version(tmp_path, from_version):
    d = tmp_path / f"v{from_version}"
    s = Store(d)
    s.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p_old','旧项目',1,1)")
    # simulate a database created by an older release
    for table, col, _ in V3_COLUMNS:
        try:
            s.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        except Exception:  # noqa: BLE001 - older SQLite without DROP COLUMN
            pytest.skip("此 SQLite 不支持 DROP COLUMN，无法构造旧库")
    _stamp(d, from_version)

    s2 = Store(d)
    assert s2.schema_version() == SCHEMA_VERSION
    cols = {r["name"] for r in s2.all("PRAGMA table_info(projects)")}
    for table, col, _ in V3_COLUMNS:
        if table == "projects":
            assert col in cols, f"从 v{from_version} 升级后仍缺少 {col}"
    assert s2.one("SELECT origin FROM projects WHERE id='p_old'")["origin"] is None, "旧行的来源必须是未知，而不是被当成测试数据"
    assert _rows(s2, "projects") == 1, "迁移不得丢行"


def test_migration_is_idempotent(tmp_path):
    d = tmp_path / "idem"
    Store(d).execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p1','x',1,1)")
    for _ in range(3):
        s = Store(d)
        assert s.schema_version() == SCHEMA_VERSION
        assert _rows(s, "projects") == 1


ALL_COLUMNS = V2_COLUMNS + V3_COLUMNS + V4_COLUMNS + V5_COLUMNS


def _assert_current_schema(s: Store) -> None:
    for table, col, _ in ALL_COLUMNS:
        cols = {r["name"] for r in s.all(f"PRAGMA table_info({table})")}
        assert col in cols, f"{table}.{col} 缺失"
    have = {r["name"] for r in s.all("SELECT name FROM sqlite_master WHERE type='index'")}
    missing = [name for name, _, _ in INDEXES if name not in have]
    assert not missing, f"缺少索引：{missing}"


def test_all_declared_columns_exist_after_migration(tmp_path):
    """Every declared column list, not a hand-picked subset: V4_COLUMNS are applied on a path
    of their own (unconditionally), which is exactly the path that is easy to break unnoticed."""
    _assert_current_schema(Store(tmp_path / "fresh"))


def test_a_real_v1_database_reaches_the_current_schema(tmp_path):
    """A database built from the v1 schema alone — no v2 tables, no tenant columns, no identity
    tables — not a current database with a few columns dropped."""
    d = tmp_path / "v1"
    d.mkdir()
    con = sqlite3.connect(d / "docweave.sqlite3")
    con.executescript(SCHEMA)
    con.execute("INSERT INTO meta (key, value) VALUES ('schema_version', '1')")
    con.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p_v1','一代项目',1,1)")
    con.commit()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "users" not in tables and "idempotency" not in tables, "夹具应当是真正的 v1 库"

    s = Store(d)
    assert s.schema_version() == SCHEMA_VERSION
    _assert_current_schema(s)
    row = s.one("SELECT name, tenant, origin FROM projects WHERE id='p_v1'")
    assert row == {"name": "一代项目", "tenant": "default", "origin": None}, "旧行必须原样保留并归入 default"
    ws = s.one("SELECT kind, status FROM workspaces WHERE id='default'")
    assert ws == {"kind": "legacy", "status": "pending_claim"}
    assert s.one("SELECT COUNT(*) AS n FROM memberships")["n"] == 0


def test_a_database_stamped_current_still_receives_a_missing_v4_column(tmp_path):
    """V4 columns are applied on every start precisely because a database already stamped with
    the current version never re-enters the version-gated branch."""
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("此 SQLite 不支持 DROP COLUMN，无法构造缺列的库")
    d = tmp_path / "v4gap"
    s = Store(d)
    s.execute("DROP INDEX ix_sess_oidc_sid")            # an indexed column cannot be dropped
    s.execute("ALTER TABLE sessions DROP COLUMN oidc_sid")
    s.execute("DROP INDEX ix_tok_family")
    assert "oidc_sid" not in {r["name"] for r in s.all("PRAGMA table_info(sessions)")}
    assert s.schema_version() == SCHEMA_VERSION

    s2 = Store(d)
    _assert_current_schema(s2)


def _listing(d):
    """Every file under d with its bytes: the directory exactly as it is."""
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def _release(store: Store) -> None:
    """Close the thread-local connection a Store holds, so SQLite checkpoints and removes the
    -wal/-shm files the way it does when a real process exits."""
    import gc
    c = getattr(store._local, "c", None)
    if c is not None:
        c.close()
        store._local.c = None
    gc.collect()


def test_info_style_open_does_not_upgrade_the_directory(tmp_path):
    """Store(migrate=False) must leave an old data directory exactly as it found it.

    `manage.py info` is what the upgrade docs tell you to run to check a schema version, and it
    used to migrate v2 -> v3 in place just by opening. Pointed at a backup, that rewrites the
    backup you were about to verify. "Exactly" is checked literally: the same files, byte for
    byte — not just the version number, and no -wal/-shm left behind.
    """
    d = tmp_path / "old"
    s = Store(d)                                         # create at the current version
    s.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p1','x',1,1)")
    _release(s)
    db = d / "docweave.sqlite3"
    con = sqlite3.connect(db)
    con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    con.commit()
    con.close()

    before = _listing(d)
    assert "docweave.sqlite3-wal" not in before

    ro = Store(d, migrate=False)
    assert ro.schema_version() == 2, "只读打开不应升级"
    assert ro.one("SELECT COUNT(*) AS n FROM projects")["n"] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM projects")               # and it cannot be made to write
    _release(ro)
    assert _listing(d) == before, "只读打开改动了目录（多出文件或字节变化）"

    # and a normal open still upgrades, so the real startup path is unaffected
    assert Store(d).schema_version() == SCHEMA_VERSION


def test_info_style_open_reads_through_a_live_write_ahead_log(tmp_path):
    """While a server has the database open, the newest rows may exist only in the -wal file.
    The read-only open must see them, and still add nothing to the directory."""
    d = tmp_path / "live"
    writer = Store(d)
    writer.execute("INSERT INTO projects (id, name, created_at, updated_at) VALUES ('p_wal','只在日志里',1,1)")
    assert (d / "docweave.sqlite3-wal").exists()
    names_before = sorted(p.name for p in d.iterdir())

    ro = Store(d, migrate=False)
    assert ro.one("SELECT name FROM projects WHERE id='p_wal'")["name"] == "只在日志里"
    _release(ro)
    assert sorted(p.name for p in d.iterdir()) == names_before


def test_info_style_open_on_an_empty_directory_reports_zero(tmp_path):
    """migrate=False may be pointed at a directory with no database at all — and a mistyped path
    must not be turned into an empty instance by looking at it."""
    target = tmp_path / "nothing"
    assert Store(target, migrate=False).schema_version() == 0
    assert not target.exists(), "只读打开不应创建目录"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert Store(empty, migrate=False).schema_version() == 0
    assert list(empty.iterdir()) == [], "只读打开不应创建数据库或 blobs/"


def test_blob_reachability_and_revocation_lookups_use_an_index(tmp_path):
    """Every preview/blob read asks authz whether the blob is reachable from this workspace, and
    every back-channel logout and refresh-family revocation filters tokens and sessions by one
    column. Without these indexes each of those is a full scan of the largest tables."""
    from synwevo_docs import authz
    s = Store(tmp_path / "plan")

    def plan(sql, args):
        return " | ".join(r["detail"] for r in s.all("EXPLAIN QUERY PLAN " + sql, args))

    # `q` carries the digests under a standing quarantine verdict (CI-90). Walking that small JSON
    # parameter — or a job's snapshot — with json_each is a virtual-table scan, not a table scan.
    args = {"b": "0" * 64, "t": "w1", "q": json.dumps(["1" * 64])}
    for clause in authz._REACHABLE:
        detail = " | ".join(d for d in plan(clause, args).split(" | ") if "VIRTUAL TABLE" not in d)
        assert "SCAN" not in detail.replace("SCAN CONSTANT ROW", ""), f"全表扫描：{clause}\n{detail}"
    for sql, arg in (("SELECT id FROM oauth_tokens WHERE family_id=?", ("f",)),
                     ("SELECT id FROM oauth_tokens WHERE client_id=? AND revoked_at IS NULL", ("c",)),
                     ("SELECT id FROM sessions WHERE oidc_sid=?", ("s",)),
                     ("SELECT id FROM api_keys WHERE lineage=?", ("l",))):
        assert "USING INDEX" in plan(sql, arg) or "USING COVERING INDEX" in plan(sql, arg), sql


def test_the_store_is_the_only_place_the_schema_comes_from(tmp_path):
    """ui_users used to exist only after something constructed Auth(store): a second migrator,
    and a table whose presence depended on which code path happened to run first."""
    s = Store(tmp_path / "only")
    tables = {r["name"] for r in s.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ui_users" in tables
