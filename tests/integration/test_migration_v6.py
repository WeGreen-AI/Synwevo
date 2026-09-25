"""Synthetic v5-shaped database: additive upgrade must preserve IDs and secrets.

Only the temporary fixture is downgraded structurally; never a user database.
Assertions compare actual stored rows and blob bytes, not just a version number.
"""
import hashlib

import pytest

from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.oidc import Keys
from synwevo_core.store import SCHEMA_VERSION, Store, V6_COLUMNS, now


@pytest.mark.parametrize("claimed", [False, True])
def test_v5_upgrade_preserves_identity_business_and_legacy_claim(tmp_path, claimed):
    root = tmp_path / "synthetic-v5"
    store = Store(root)
    ident = Identity(store)
    user = ident.create_user("migration.person+tag@example.test", "Synthetic migration password phrase!",
                             status="active")
    uid = user["id"]
    personal = ident.workspaces_for(uid)[0]["id"]
    root_secret = Auth(store).secret()
    signing = Keys(store).active()
    ts = now()
    store.execute("INSERT INTO workspaces(id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','Historical','legacy',?,?,?)",
                  ("active" if claimed else "pending_claim", ts, ts))
    if claimed:
        store.execute("INSERT INTO memberships(workspace_id,user_id,role,created_at,updated_at) "
                      "VALUES ('default',?,'owner',?,?)", (uid, ts, ts))
    digest = store.put_blob(b"Synthetic v5 source content, not a user document")
    store.execute("INSERT INTO templates(id,name,kind,format,tenant,original_blob,created_at,updated_at) "
                  "VALUES ('t_migration','Existing template','uploaded','docx','default',?,?,?)", (digest, ts, ts))
    store.execute("INSERT INTO projects(id,name,tenant,created_at,updated_at) "
                  "VALUES ('p_migration','Existing project','default',?,?)", (ts, ts))
    comparisons = {
        "users": "id,email,email_key,status,platform_role,created_at",
        "workspaces": "id,name,kind,status,created_at",
        "memberships": "workspace_id,user_id,role,created_at",
        "user_passwords": "user_id,phc",
        "projects": "id,name,tenant,created_at",
        "templates": "id,name,tenant,original_blob,created_at",
        "oidc_keys": "*",
    }
    before = {table: store.all(f"SELECT {cols} FROM {table}") for table, cols in comparisons.items()}
    # Remove the new, currently unused features to represent a v5 install with real identity/data.
    for table in ("organization_idempotency", "organization_avatars", "organization_memberships",
                  "api_platform_bindings", "api_platform_approvals", "api_platform_calls", "account_recoveries"):
        store.execute(f"DROP TABLE {table}")
    for table, col, _ in V6_COLUMNS + [("workspaces", "organization_id", "")]:
        store.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
    store.execute("DROP TABLE organizations")
    store.execute("UPDATE meta SET value='5' WHERE key='schema_version'")
    store._local.c.close()
    store._local.c = None

    for _ in range(2):
        migrated = Store(root)
        assert migrated.schema_version() == SCHEMA_VERSION
        assert {table: migrated.all(f"SELECT {cols} FROM {table}")
                for table, cols in comparisons.items()} == before
        assert migrated.get_blob(digest) == b"Synthetic v5 source content, not a user document"
        assert hashlib.sha256(migrated.get_blob(digest)).hexdigest() == digest
        assert Auth(migrated).secret() == root_secret
        assert Keys(migrated).active() == signing
        assert migrated.all("SELECT id FROM organizations") == []
        assert migrated.one("SELECT locale,timezone FROM users WHERE id=?", (uid,)) == {
            "locale": "zh-Hans", "timezone": "UTC"}
        assert Identity(migrated).ensure_personal_workspace(uid)["id"] == personal
        migrated._local.c.close()
        migrated._local.c = None
