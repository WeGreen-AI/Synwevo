"""Isolated real dispatcher/outbox recovery; no SMTP or customer data."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.notifications import Notifier, RETRY_DELAYS_S
from synwevo_core.store import Store, now
from synwevo_identity.mail import Mailer
from synwevo_identity.notify import NotificationDispatcher


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://accounts.example.test")
    store = Store(tmp_path / "fixture")
    ident = Identity(store)
    user = ident.create_user("recovery@example.test", None, status="active", with_personal_workspace=False)
    store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    notifier = Notifier(store)
    mailer = Mailer(store, start=False)
    dispatcher = NotificationDispatcher(store, ident, mailer, start=False)
    yield store, notifier, dispatcher, user["id"]
    dispatcher.stop()
    mailer.stop()


def emitted(fixture):
    _, notifier, _, uid = fixture
    return notifier.emit(uid, "wallet.low_balance", params={"available": "fixture-private", "threshold": "1", "unit": "CNY"})["id"]


def exhaust(fixture, nid):
    store, _, dispatcher, _ = fixture
    store.execute("CREATE TRIGGER fixture_failure AFTER INSERT ON mail_outbox BEGIN SELECT RAISE(FAIL,'synthetic enqueue fault'); END")
    for _ in range(len(RETRY_DELAYS_S) + 1):
        store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (nid,))
        assert dispatcher.run_once()["failed"] == 1
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox")["n"] == 0


def test_recovery_does_not_reset_attempts_or_enqueue_itself(fixture):
    store, notifier, dispatcher, _ = fixture
    nid = emitted(fixture)
    exhaust(fixture, nid)
    row = notifier.email_recovery_row(nid)
    assert row["recoverable"] and row["attempts"] == 5
    receipt = notifier.recover_email_enqueue(nid, row["version"])
    assert receipt == {"notification_id": nid, "status": "pending", "attempts": 5, "recovery_scheduled": True}
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox")["n"] == 0
    assert dispatcher.run_once()["failed"] == 1
    again = notifier.email_recovery_row(nid)
    assert again["status"] == "failed" and again["attempts"] == 6
    assert again["version"] != row["version"]
    store.execute("DROP TRIGGER fixture_failure")
    notifier.recover_email_enqueue(nid, again["version"])
    assert dispatcher.run_once()["queued"] == 1
    assert dispatcher.run_once()["queued"] == 0
    done = store.one("SELECT status,attempts,mail_id FROM notification_deliveries WHERE notification_id=?", (nid,))
    assert done["status"] == "queued" and done["attempts"] == 6 and done["mail_id"]
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox")["n"] == 1


@pytest.mark.parametrize("change", ["off", "read", "suspended", "quiet"])
def test_recovery_rechecks_reader_and_preserves_existing_waits(fixture, monkeypatch, change):
    store, notifier, dispatcher, uid = fixture
    nid = emitted(fixture)
    exhaust(fixture, nid)
    store.execute("DROP TRIGGER fixture_failure")
    notifier.recover_email_enqueue(nid, notifier.email_recovery_row(nid)["version"])
    if change == "off":
        notifier.set_preferences(uid, {"categories": {"platform": {"email": "off"}}})
    elif change == "read":
        notifier.mark(uid, [nid], read=True)
    elif change == "suspended":
        Identity(store).set_status(uid, "suspended")
    else:
        # Fixed instant 00:30 UTC inside the actual configured quiet window.
        monkeypatch.setattr("synwevo_core.notifications.now", lambda: 1791419400.0)
        notifier.set_preferences(uid, {"quiet_start": "00:00", "quiet_end": "01:00", "timezone": "UTC"})
        store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (nid,))
    result = dispatcher.run_once()
    assert result["queued"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM mail_outbox")["n"] == 0
    row = store.one("SELECT status,attempts,defer_reason FROM notification_deliveries WHERE notification_id=?", (nid,))
    assert row["attempts"] == 5
    assert row["status"] == ("pending" if change == "quiet" else "skipped")
    if change == "quiet":
        assert row["defer_reason"] == "quiet"


def test_two_operators_cannot_schedule_the_same_snapshot(fixture):
    store, notifier, _, _ = fixture
    nid = emitted(fixture)
    exhaust(fixture, nid)
    version = notifier.email_recovery_row(nid)["version"]
    other = Notifier(Store(store.root))
    barrier = Barrier(2)
    def recover(service):
        barrier.wait(timeout=10)
        try:
            return service.recover_email_enqueue(nid, version)["status"]
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(recover, [notifier, other])) == ["notification_delivery_changed", "pending"]


def test_legacy_migration_never_invents_provenance_and_old_writers_stay_compatible(fixture):
    store, _, _, uid = fixture
    nid = emitted(fixture)
    exhaust(fixture, nid)
    store.execute("ALTER TABLE notification_deliveries DROP COLUMN enqueue_protocol")
    legacy = store.one("SELECT * FROM notification_deliveries WHERE notification_id=?", (nid,))
    reopened = Store(store.root)
    assert reopened.one("SELECT * FROM notification_deliveries WHERE notification_id=?", (nid,)) == legacy | {"enqueue_protocol": None}
    assert Store(store.root).one("SELECT enqueue_protocol FROM notification_deliveries WHERE notification_id=?", (nid,))["enqueue_protocol"] is None
    n = Notifier(reopened)
    row = n.email_recovery_row(nid)
    assert row["blocked_reason"] == "historical_delivery" and not row["recoverable"]
    with pytest.raises(ServiceError, match="安全恢复"):
        n.recover_email_enqueue(nid, row["version"])
    # Even an exact error from the current atomic dispatcher cannot upgrade old lineage.
    reopened.execute("UPDATE notification_deliveries SET status='pending',not_before=0 WHERE notification_id=?", (nid,))
    NotificationDispatcher(reopened, Identity(reopened), Mailer(reopened, start=False), start=False).run_once()
    assert n.email_recovery_row(nid)["blocked_reason"] == "historical_delivery"
    new = n.emit(uid, "wallet.low_balance")["id"]
    assert reopened.one("SELECT enqueue_protocol FROM notification_deliveries WHERE notification_id=?", (new,))["enqueue_protocol"] == "atomic-v1"
    reopened.execute("DELETE FROM notification_deliveries WHERE notification_id=?", (new,))
    reopened.execute("INSERT INTO notification_deliveries(notification_id,channel,status,updated_at) VALUES (?,'email','pending',?)", (new, now()))
    assert reopened.one("SELECT enqueue_protocol FROM notification_deliveries WHERE notification_id=?", (new,))["enqueue_protocol"] is None


@pytest.mark.parametrize("change,reason", [
    ("error", "unverified_failure"), ("attempts", "retry_not_exhausted"), ("mail_id", "mail_already_queued"), ("outbox", "mail_already_queued"),
])
def test_uncertain_or_not_exhausted_deliveries_cannot_be_requeued(fixture, change, reason):
    store, notifier, dispatcher, _ = fixture
    nid = emitted(fixture)
    exhaust(fixture, nid)
    store.execute("DROP TRIGGER fixture_failure")
    if change == "error":
        store.execute("UPDATE notification_deliveries SET last_error=? WHERE notification_id=?", ("fixture-private secret transport body", nid))
    elif change == "attempts":
        store.execute("UPDATE notification_deliveries SET attempts=1 WHERE notification_id=?", (nid,))
    elif change == "mail_id":
        store.execute("UPDATE notification_deliveries SET mail_id='fixture-existing-mail' WHERE notification_id=?", (nid,))
    else:
        dispatcher.mailer.enqueue("recovery@example.test", "synthetic", "synthetic", kind="notification", ref_id=nid)
    row = notifier.email_recovery_row(nid)
    assert row["blocked_reason"] == reason
    with pytest.raises(ServiceError):
        notifier.recover_email_enqueue(nid, row["version"])
    page = notifier.email_recovery_page(q=nid)
    assert page["total"] == 1 and page["items"][0]["blocked_reason"] == reason
    assert "fixture-private" not in json.dumps(page)
    assert not {"params", "target", "last_error", "version", "mail_id"} & page["items"][0].keys()


def test_metadata_pagination_does_not_include_later_notifications(fixture):
    store, notifier, _, uid = fixture
    ids = [emitted(fixture) for _ in range(4)]
    # Explicit metadata-only terminal fixtures; no real transport is represented.
    store.execute("UPDATE notification_deliveries SET status='failed',attempts=5,last_error='notification_enqueue_failed'")
    first = notifier.email_recovery_page(q=uid, limit=2)
    later = emitted(fixture)
    store.execute("UPDATE notification_deliveries SET status='failed' WHERE notification_id=?", (later,))
    second = notifier.email_recovery_page(q=uid, limit=2, offset=first["next_offset"], snapshot=first["snapshot"])
    assert first["total"] == second["total"] == 4
    assert second["next_offset"] is None
    assert {row["notification_id"] for row in first["items"] + second["items"]} == set(ids)
