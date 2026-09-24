"""Terminal deletion replay on a real isolated archive; no user instances."""
import pytest

from synwevo_core import journal
from tests.integration.test_security_journal import recovery, _backup, _restore


@pytest.mark.parametrize("withdrawal_order", [("user_suspended", "user_deleted"), ("user_deleted", "user_suspended")])
def test_deleted_account_stays_erased_and_unusable_after_archive_restore(recovery, withdrawal_order):
    w = recovery
    uid = w.a["id"]
    other = w.store.one("SELECT * FROM users WHERE id=?", (w.b["id"],))
    blob_rows = w.store.all("SELECT id,tenant,original_blob FROM templates ORDER BY id")
    w.store.execute("UPDATE users SET display_name='Private fixture name',avatar_blob=?,locale='en' WHERE id=?",
                    (b"synthetic private avatar", uid))
    archive = _backup(w)
    for event in withdrawal_order:
        journal.record(w.store, event, user_id=uid)
    restored = _restore(w, archive)
    try:
        row = restored.one("SELECT * FROM users WHERE id=?", (uid,))
        assert row["status"] == "deleted" and row["deleted_at"]
        assert row["email"] == "" and row["email_key"] == w.a["email_key"]
        assert row["display_name"] is None and row["avatar_blob"] is None
        assert row["platform_role"] == "none" and row["email_verified_at"] is None
        for table in ("user_passwords", "passkeys", "webauthn_credentials", "totp_secrets", "recovery_codes", "account_recoveries"):
            assert restored.one(f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?", (uid,))["n"] == 0
        assert restored.one("SELECT * FROM users WHERE id=?", (w.b["id"],)) == other
        assert restored.all("SELECT id,tenant,original_blob FROM templates ORDER BY id") == blob_rows
        journal.apply(restored, journal.read(w.store.root))
        assert restored.one("SELECT deleted_at FROM users WHERE id=?", (uid,))["deleted_at"] == row["deleted_at"]
    finally:
        w.snapshot._close(restored)
