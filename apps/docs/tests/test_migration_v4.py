"""v3 → v4 adds identity. The migration must not move, rewrite or hand away existing data.

The property that matters most is negative: after an upgrade, the historical projects belong to
a workspace that has **no members**. Access is granted by membership, so "the first person to
register inherits everything" is impossible by construction rather than by a check that some
later code path could forget.
"""
from __future__ import annotations

import sqlite3

from platform_core.store import SCHEMA_VERSION, Store, new_id, now


def _v3_with_data(tmp_path):
    """A data directory that looks like a pre-identity install: real rows, schema stamped v3."""
    d = tmp_path / "old"
    s = Store(d)
    ts = now()
    pid, tid = new_id("p"), new_id("t")
    with s.tx() as t:
        t.execute("INSERT INTO projects (id, name, created_at, updated_at, tenant) VALUES (?,?,?,?,'default')",
                  (pid, "历史项目", ts, ts))
        t.execute("INSERT INTO templates (id, name, kind, format, created_at, updated_at, tenant) "
                  "VALUES (?,?,'uploaded','docx',?,?,'default')", (tid, "历史模板", ts, ts))
    con = sqlite3.connect(d / "docweave.sqlite3")
    con.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
    con.execute("DROP TABLE IF EXISTS workspaces")
    con.execute("DROP TABLE IF EXISTS users")
    con.execute("DROP TABLE IF EXISTS memberships")
    con.commit()
    con.close()
    return d, pid, tid


def test_upgrade_keeps_every_row_and_rewrites_none(tmp_path):
    d, pid, tid = _v3_with_data(tmp_path)
    before = sqlite3.connect(f"file:{d / 'docweave.sqlite3'}?mode=ro", uri=True)
    rows_before = before.execute("SELECT id, name, tenant FROM projects ORDER BY id").fetchall()
    before.close()

    s = Store(d)
    assert s.schema_version() == SCHEMA_VERSION

    rows_after = [(r["id"], r["name"], r["tenant"]) for r in s.all("SELECT id, name, tenant FROM projects ORDER BY id")]
    assert rows_after == [tuple(r) for r in rows_before], "迁移不应改写任何既有行"
    assert s.one("SELECT tenant FROM templates WHERE id=?", (tid,))["tenant"] == "default"


def test_legacy_workspace_exists_and_has_no_members(tmp_path):
    """The load-bearing assertion: the historical data is adopted, and nobody is in it."""
    d, pid, _ = _v3_with_data(tmp_path)
    s = Store(d)

    ws = s.one("SELECT * FROM workspaces WHERE id='default'")
    assert ws and ws["kind"] == "legacy" and ws["status"] == "pending_claim"
    assert s.one("SELECT COUNT(*) AS n FROM memberships")["n"] == 0, "历史工作空间不能预先属于任何人"
    assert s.one("SELECT COUNT(*) AS n FROM users")["n"] == 0, "迁移不应凭空创建账户"

    # the project is in that workspace, so reaching it requires a membership that does not exist
    assert s.one("SELECT tenant FROM projects WHERE id=?", (pid,))["tenant"] == ws["id"]


def test_fresh_install_gets_no_phantom_legacy_workspace(tmp_path):
    s = Store(tmp_path / "new")
    assert s.schema_version() == SCHEMA_VERSION
    assert s.one("SELECT COUNT(*) AS n FROM workspaces")["n"] == 0, "空库不应出现「历史数据」空间"


def test_migration_is_idempotent(tmp_path):
    d, _, _ = _v3_with_data(tmp_path)
    Store(d)
    first = Store(d).one("SELECT COUNT(*) AS n FROM workspaces")["n"]
    Store(d)
    assert Store(d).one("SELECT COUNT(*) AS n FROM workspaces")["n"] == first == 1


def test_status_and_role_columns_reject_unknown_values(tmp_path):
    """DW-049/050/051 were silent enum fallbacks. Here the database refuses the write."""
    import pytest
    s = Store(tmp_path / "chk")
    ts = now()
    with pytest.raises(sqlite3.IntegrityError):
        with s.tx() as t:
            t.execute("INSERT INTO users (id, email, email_key, status, platform_role, created_at, updated_at) "
                      "VALUES ('u1','a@b','a@b','wat','none',?,?)", (ts, ts))
    with pytest.raises(sqlite3.IntegrityError):
        with s.tx() as t:
            t.execute("INSERT INTO users (id, email, email_key, status, platform_role, created_at, updated_at) "
                      "VALUES ('u2','a@b','a@b','active','superuser',?,?)", (ts, ts))
    with pytest.raises(sqlite3.IntegrityError):
        with s.tx() as t:
            t.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                      "VALUES ('w1','x','personal','whatever',?,?)", (ts, ts))
    with pytest.raises(sqlite3.IntegrityError):
        with s.tx() as t:
            t.execute("INSERT INTO memberships (workspace_id,user_id,role,created_at,updated_at) "
                      "VALUES ('w1','u1','superadmin',?,?)", (ts, ts))


def test_email_uniqueness_is_on_the_normalised_key(tmp_path):
    """Alice@x and alice@x must not become two accounts that share a mailbox."""
    import pytest
    s = Store(tmp_path / "em")
    ts = now()
    with s.tx() as t:
        t.execute("INSERT INTO users (id,email,email_key,status,created_at,updated_at) "
                  "VALUES ('u1','Alice@Example.com','alice@example.com','active',?,?)", (ts, ts))
    with pytest.raises(sqlite3.IntegrityError):
        with s.tx() as t:
            t.execute("INSERT INTO users (id,email,email_key,status,created_at,updated_at) "
                      "VALUES ('u2','alice@example.com','alice@example.com','active',?,?)", (ts, ts))
