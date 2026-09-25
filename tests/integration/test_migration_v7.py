"""Synthetic v6-shaped database: the v7 upgrade is additive and preserves every row it touches.

Only the temporary fixture is downgraded structurally; never a user database. v7 adds the
remembered-session and provider columns, the sign-in code table and the external identity
tables; an existing session must keep working, as a non-remembered one, after the upgrade.
"""
from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, V7_COLUMNS, now, SCHEMA_VERSION


def test_v6_upgrade_adds_columns_and_tables_without_touching_existing_rows(tmp_path):
    root = tmp_path / "synthetic-v6"
    store = Store(root)
    ident = Identity(store)
    user = ident.create_user("v7.person@example.test", "Synthetic migration password phrase!", status="active")
    raw, row = Sessions(store).create(user["id"], workspace_id=None, amr="password")
    root_secret = Auth(store).secret()
    ts = now()
    store.execute("INSERT INTO mfa_tickets (id,user_id,created_at,expires_at) VALUES ('t1',?,?,?)",
                  (user["id"], ts, ts + 300))
    comparisons = {
        "users": "id,email,email_key,status,platform_role,created_at",
        "sessions": "id,user_id,created_at,idle_expires_at,absolute_expires_at,amr",
        "mfa_tickets": "id,user_id,created_at,expires_at",
    }
    before = {table: store.all(f"SELECT {cols} FROM {table}") for table, cols in comparisons.items()}
    for table in ("login_codes", "external_identities", "external_auth_requests", "external_link_offers"):
        store.execute(f"DROP TABLE {table}")
    for table, col, _ in V7_COLUMNS:
        store.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    store.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    store._local.c.close()
    store._local.c = None

    for _ in range(2):                                   # idempotent: a second open changes nothing
        migrated = Store(root)
        assert migrated.schema_version() == SCHEMA_VERSION
        # v8 (CI-20): the scan verdict columns exist and rows written before scanning existed read
        # as "no verdict" (NULL → `unscanned` at the API), never as clean.
        assert all(migrated.one(f"SELECT COUNT(*) AS n FROM pragma_table_info('{t}') WHERE name='scan'")["n"] == 1
                   for t in ("source_files", "templates"))
        assert {table: migrated.all(f"SELECT {cols} FROM {table}")
                for table, cols in comparisons.items()} == before
        assert Auth(migrated).secret() == root_secret
        loaded = Sessions(migrated).load(raw)
        assert loaded and loaded["id"] == row["id"] and loaded["persistent"] == 0 and loaded["auth_provider"] is None
        assert migrated.one("SELECT first_factor FROM mfa_tickets WHERE id='t1'") == {"first_factor": "password"}
        for table in ("login_codes", "external_identities", "external_auth_requests", "external_link_offers"):
            assert migrated.all(f"SELECT * FROM {table}") == []
        migrated._local.c.close()
        migrated._local.c = None
