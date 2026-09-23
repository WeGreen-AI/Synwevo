"""Additive migrations must reach a database at ANY older version.

DW-058: the gate was `if ver < 2`, so a database already stamped v2 never received v3's
columns and the app then failed at runtime with "no such column: origin".  The deployed
instance is exactly such a database, so this would have broken it on upgrade.
"""
from __future__ import annotations

import sqlite3

import pytest

from platform_core.store import SCHEMA_VERSION, V2_COLUMNS, V3_COLUMNS, Store

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


def test_all_declared_columns_exist_after_migration(tmp_path):
    s = Store(tmp_path / "fresh")
    for table, col, _ in V2_COLUMNS + V3_COLUMNS:
        cols = {r["name"] for r in s.all(f"PRAGMA table_info({table})")}
        assert col in cols, f"{table}.{col} 缺失"


def test_info_style_open_does_not_upgrade_the_directory(tmp_path):
    """Store(migrate=False) must leave an old data directory exactly as it found it.

    `manage.py info` is what the upgrade docs tell you to run to check a schema version, and it
    used to migrate v2 -> v3 in place just by opening. Pointed at a backup, that rewrites the
    backup you were about to verify.
    """
    import sqlite3
    from platform_core.store import SCHEMA_VERSION, Store

    d = tmp_path / "old"
    Store(d)                                             # create at the current version
    db = d / "docweave.sqlite3"
    con = sqlite3.connect(db)
    con.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    con.commit()
    con.close()

    before = (db.read_bytes(), sqlite3.connect(db).execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    assert before[1] == "2"

    s = Store(d, migrate=False)
    assert s.schema_version() == 2, "只读打开不应升级"
    after = sqlite3.connect(db).execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]
    assert after == "2", "目录被 info 式打开改写了"

    # and a normal open still upgrades, so the real startup path is unaffected
    assert Store(d).schema_version() == SCHEMA_VERSION


def test_info_style_open_on_an_empty_directory_reports_zero(tmp_path):
    """migrate=False may be pointed at a directory with no database at all."""
    from platform_core.store import Store
    assert Store(tmp_path / "nothing", migrate=False).schema_version() == 0
