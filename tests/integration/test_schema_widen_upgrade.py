"""The first start of this release on a database the previous release built (release safety).

Isolated fixture stores only (tmp_path, SYNWEVO_TEST_INSTANCE); never the real data directory.
The database is built by the current code with real rows (accounts, an organization, platform
grants, ledger facts); then the previous release's exact CHECK clauses and wallet trigger are put
back on the three tables this release rebuilds, so every other table, index and trigger is the
real one. Finally foreign-key orphans are added with foreign keys off — rows the platform never
writes itself, but an out-of-band fix made with the sqlite3 CLI or a piped script (foreign keys
off by default) can leave behind. Production has run long enough that this cannot be ruled out.

The business rule: opening the store widens the tables, keeps every row (with its rowid: the
journal rowid is the wallet statement's stable cursor), index and trigger, leaves the
pre-existing orphans exactly as they were, and does not stop the platform over them. A rebuild
that would itself orphan rows still refuses, rolls back and names the table.
"""
from __future__ import annotations

import sqlite3

import pytest

from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import ROLE_CHECK_WIDENED
from synwevo_core.schema_widen import widen_checks
from synwevo_core.store import Store
from synwevo_core.suite_schema.billing import JOURNAL_APPLY, WIDENED

from tests.integration.platform_fixture import make_platform_admin

#: billing_chargebacks joined the widened tables with the dispute bounds (review money-4); it
#: is new in this release, so production rebuilds only the other three.
REBUILT = ("billing_accounts", "billing_chargebacks", "billing_journal", "platform_role_grants")
#: The previous release's wallet trigger (a.kind='wallet'), which this release replaces.
OLD_JOURNAL_APPLY = JOURNAL_APPLY.replace("a.kind IN ('wallet','org_wallet')", "a.kind='wallet'")
assert OLD_JOURNAL_APPLY != JOURNAL_APPLY


def _narrow_to_previous_release(c: sqlite3.Connection) -> None:
    """Fixture: give the three tables the previous release's exact clauses back (the same helper,
    with each (old, new) pair reversed), and the previous wallet trigger."""
    narrow = {table: tuple((new, old) for old, new in pairs)
              for table, pairs in {**WIDENED, **ROLE_CHECK_WIDENED}.items()}

    def old_trigger(t):
        t.execute("DROP TRIGGER billing_journal_apply")
        t.execute(OLD_JOURNAL_APPLY)

    assert sorted(widen_checks(c, narrow, extra=old_trigger)) == sorted(REBUILT)


def _schema(c) -> dict:
    return {r[0]: (r[1], r[2], r[3]) for r in c.execute("SELECT name,type,tbl_name,sql FROM sqlite_master")}


def _rows(c) -> dict:
    return {t: c.execute(f"SELECT rowid,* FROM {t} ORDER BY rowid").fetchall() for t in REBUILT}


def _violations(c) -> list:
    return sorted(c.execute("PRAGMA foreign_key_check").fetchall())


@pytest.fixture
def previous_release_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    root = tmp_path / "isolated-previous-release"
    store = Store(root)
    identity = Identity(store)
    alice = identity.create_user("upgrade-alice@example.test", None, status="active", with_personal_workspace=False)
    bob = identity.create_user("upgrade-bob@example.test", None, status="active", with_personal_workspace=False)
    make_platform_admin(store, alice)
    make_platform_admin(store, bob, role="support")
    Organizations(store).create(alice["id"], "Upgrade fixture org", "Upgrade fixture space", "fixture-upgrade-0001")
    with store.tx() as t:
        t.execute("INSERT INTO billing_units VALUES ('FIXTURE-CNY',2)")
        t.execute("INSERT INTO billing_accounts VALUES ('acc-wallet','test','FIXTURE-CNY',?,'wallet')", (alice["id"],))
        t.execute("INSERT INTO billing_accounts VALUES ('acc-src','test','FIXTURE-CNY','platform','test_payment')")
        t.executemany("INSERT INTO billing_balances VALUES (?,0)", [("acc-wallet",), ("acc-src",)])
        # Explicit rowids with gaps: the rebuild must not renumber the statement cursor.
        for rowid, (jid, amount) in ((7, ("j-1", 40)), (19, ("j-2", 2))):
            t.execute("INSERT INTO billing_journal(rowid,id,environment,namespace,voucher,unit,debit_account,"
                      "credit_account,amount,kind,reference,created_at) VALUES (?,?,'test','agent',?,'FIXTURE-CNY',"
                      "'acc-src','acc-wallet',?,'test_payment','fixture',1)", (rowid, jid, f"payment:{jid}", amount))
        t.execute("UPDATE platform_role_grants SET rowid=rowid+30")
    raw = sqlite3.connect(store.db_path, isolation_level=None)
    raw.execute("PRAGMA busy_timeout=30000")
    assert _violations(raw) == []
    _narrow_to_previous_release(raw)
    # Orphans written with foreign keys off (sqlite3's default): in an unrelated table, in a table
    # that references a rebuilt one, and in a rebuilt table itself.
    assert raw.execute("PRAGMA foreign_keys").fetchone()[0] == 0
    raw.execute("INSERT INTO organization_memberships VALUES ('org-gone','user-gone','member',1,1,NULL)")
    raw.execute("INSERT INTO billing_balances VALUES ('acc-gone',0)")
    raw.execute("INSERT INTO platform_role_grants(id,user_id,role,granted_by,granted_at,reason) "
                "VALUES ('grant-orphan','user-gone','support','fixture:tests',1,'fixture orphan')")
    yield root, raw
    raw.close()


def test_the_first_start_widens_the_tables_despite_unrelated_orphans(previous_release_db):
    root, raw = previous_release_db
    schema, rows, orphans = _schema(raw), _rows(raw), _violations(raw)
    assert "'org_wallet'" not in schema["billing_accounts"][2] and "'finance'" not in schema["platform_role_grants"][2]
    assert {o[0] for o in orphans} == {"organization_memberships", "billing_balances", "platform_role_grants"}

    store = Store(root)                                   # the release's first start: must not stop

    after = _schema(raw)
    assert "'org_wallet'" in after["billing_accounts"][2] and "'chargeback'" in after["billing_journal"][2]
    assert "'finance'" in after["platform_role_grants"][2]
    assert "org_wallet" in after["billing_journal_apply"][2]
    changed = set(REBUILT) | {"billing_journal_apply"}
    assert {k: v for k, v in after.items() if k not in changed} == {k: v for k, v in schema.items() if k not in changed}, \
        "every other table, index, trigger and view is kept exactly"
    assert _rows(raw) == rows, "every row of the rebuilt tables is kept, with its rowid"
    assert raw.execute("SELECT rowid FROM billing_journal ORDER BY rowid").fetchall() == [(7,), (19,)]
    assert _violations(raw) == orphans, "pre-existing orphans are left as they were, none added"
    assert raw.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert store.one("SELECT amount FROM billing_balances WHERE account_id='acc-wallet'")["amount"] == 42
    with store.conn() as c:
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1, "foreign keys are back on after the rebuild"
    with pytest.raises(sqlite3.IntegrityError, match="immutable_billing"):
        store.execute("UPDATE billing_journal SET amount=1 WHERE id='j-1'")

    Store(root)                                           # a second start changes nothing
    assert _schema(raw) == after and _rows(raw) == rows


def test_a_rebuild_that_would_orphan_rows_refuses_names_the_table_and_rolls_back(tmp_path):
    c = sqlite3.connect(tmp_path / "isolated-widen.sqlite3", isolation_level=None)
    c.executescript("""
        CREATE TABLE parent(id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('a')));
        CREATE TABLE child(id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES parent(id));
        CREATE TABLE unrelated(id TEXT PRIMARY KEY);
        CREATE TABLE elsewhere(id TEXT PRIMARY KEY, other TEXT REFERENCES unrelated(id));
        INSERT INTO parent VALUES ('p1','a'),('p2','a');
        INSERT INTO child VALUES ('c1','p1');
        INSERT INTO elsewhere VALUES ('e1','gone');
    """)
    widen = {"parent": (("CHECK(kind IN ('a'))", "CHECK(kind IN ('a','b'))"),)}
    with pytest.raises(RuntimeError, match="child"):
        widen_checks(c, widen, extra=lambda t: t.execute("DELETE FROM parent WHERE id='p1'"))
    assert not c.in_transaction
    assert "'b'" not in c.execute("SELECT sql FROM sqlite_master WHERE name='parent'").fetchone()[0]
    assert c.execute("SELECT id FROM parent ORDER BY id").fetchall() == [("p1",), ("p2",)], "rolled back"
    # The same widening without the damaging step succeeds; the orphan elsewhere is not its business.
    assert widen_checks(c, widen) == ["parent"]
    assert c.execute("PRAGMA foreign_key_check").fetchall() == [("elsewhere", 1, "unrelated", 0)]
