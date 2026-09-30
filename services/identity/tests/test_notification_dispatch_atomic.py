"""Isolated Store/real outbox regressions for the notification dispatch boundary.

The only test interventions are explicit fixture data, deterministic thread scheduling,
SQLite fault triggers, and a simulated process interruption. Mail stays in local file mode.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest

from synwevo_core.identity import Identity
from synwevo_core.messaging import Messaging
from synwevo_core.notifications import Notifier, CONVERSATION_DIGEST_DAILY_CAP, RETRY_DELAYS_S
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, now
from synwevo_identity.mail import Mailer
from synwevo_identity.notify import NotificationDispatcher


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "fixture-data"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "fixture-mail"))
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://accounts.example.test")
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    store = Store(tmp_path / "fixture-data")
    ident = Identity(store)
    users = {name: ident.create_user(f"{name}@example.test", None, status="active", display_name=name,
                                     with_personal_workspace=False)
             for name in ("owner", "reader", "sender")}
    for user in users.values():
        store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    org = Organizations(store, ident).create(users["owner"]["id"], "Fixture org", "Fixture space", "fixture-notify-atomic")
    oid = org["organization"]["id"]
    for name in ("reader", "sender"):
        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) "
                      "VALUES (?,?,'member',1,1,?)", (oid, users[name]["id"], users["owner"]["id"]))
    notifier = Notifier(store)
    notifier.set_preferences(users["reader"]["id"], {"categories": {"conversation": {"email": "digest"}}})
    mailer = Mailer(store, start=False)
    dispatcher = NotificationDispatcher(store, ident, mailer, start=False)
    yield {"store": store, "ident": ident, "users": users, "oid": oid, "notifier": notifier,
           "mailer": mailer, "dispatcher": dispatcher, "maildir": tmp_path / "fixture-mail"}
    mailer.stop()
    dispatcher.stop()


def conversation_item(env, sender="owner"):
    store, users = env["store"], env["users"]
    messaging = Messaging(store, env["ident"])
    cid = messaging.open_dm(env["oid"], users[sender]["id"], users["reader"]["id"])["id"]
    message, _ = messaging.send(cid, users[sender]["id"], body="Isolated fixture private message")
    row = env["notifier"].emit(users["reader"]["id"], "conversation.dm", params={"sender": sender},
                                organization_id=env["oid"], object_kind="conversation", object_id=cid)
    store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (row["id"],))
    return row["id"], cid, message["id"]


def deliveries(env):
    return env["store"].all("SELECT notification_id,status,mail_id,attempts,not_before,last_error FROM notification_deliveries ORDER BY notification_id")


def mails(env):
    return env["store"].all("SELECT id,ref_id,status FROM mail_outbox WHERE kind='notification'")


def second_dispatcher(env):
    store = Store(env["store"].root)
    return NotificationDispatcher(store, Identity(store), Mailer(store, start=False), start=False)


@pytest.mark.parametrize("same_item", [False, True])
def test_two_stores_share_the_digest_budget_and_never_duplicate_a_delivery(env, monkeypatch, same_item):
    first = conversation_item(env, "owner")[0]
    second = first if same_item else conversation_item(env, "sender")[0]
    dispatchers = [env["dispatcher"], second_dispatcher(env)]
    barrier = Barrier(2)
    # Two real candidate queries see the same initial state before either starts its write.
    # Disjoint picks model batches split across two instances; no service response is faked.
    for dispatcher, target in zip(dispatchers, [first, second]):
        query = dispatcher.notifier.due_deliveries
        def selected(limit, query=query, target=target):
            rows = [r for r in query(limit) if r["notification_id"] == target]
            assert len(rows) == 1
            barrier.wait(timeout=10)
            return rows
        monkeypatch.setattr(dispatcher.notifier, "due_deliveries", selected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda d: d.run_once(), dispatchers))
    assert sum(r["queued"] for r in results) == 1
    assert len(mails(env)) == 1
    rows = deliveries(env)
    assert len([d for d in rows if d["status"] == "queued" and d["mail_id"]]) == 1
    if not same_item:
        assert sum(r["deferred"] for r in results) == 1
        pending = next(d for d in rows if d["status"] == "pending")
        assert pending["not_before"] > now() + 25 * 60
    assert not env["maildir"].exists(), "enqueue performs no transport/file delivery"


def test_two_stores_cannot_both_take_the_final_daily_digest_slot(env, monkeypatch):
    uid = env["users"]["reader"]["id"]
    # Explicit already-queued fixture mails, spaced far enough apart that only the daily cap
    # applies. The history contains real outbox rows, not an overridden budget calculation.
    for index in range(CONVERSATION_DIGEST_DAILY_CAP - 1):
        row = env["notifier"].emit(uid, "conversation.added", params={"conversation": "Fixture history"})
        mail = env["mailer"].enqueue("reader@example.test", "Fixture digest", "Fixture only", kind="notification", ref_id=row["id"])
        env["notifier"].finish_delivery(row["id"], "queued", mail_id=mail["id"])
        env["store"].execute("UPDATE notification_deliveries SET updated_at=? WHERE notification_id=?",
                             (now() - 4000 - index * 3600, row["id"]))
    first = conversation_item(env, "owner")[0]
    second = conversation_item(env, "sender")[0]
    dispatchers = [env["dispatcher"], second_dispatcher(env)]
    barrier = Barrier(2)
    for dispatcher, target in zip(dispatchers, [first, second]):
        query = dispatcher.notifier.due_deliveries
        def selected(limit, query=query, target=target):
            rows = [r for r in query(limit) if r["notification_id"] == target]
            barrier.wait(timeout=10)
            return rows
        monkeypatch.setattr(dispatcher.notifier, "due_deliveries", selected)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda d: d.run_once(), dispatchers))
    assert sum(r["queued"] for r in results) == 1
    assert len(mails(env)) == CONVERSATION_DIGEST_DAILY_CAP
    pending = next(d for d in deliveries(env) if d["status"] == "pending")
    assert pending["not_before"] > now()


def test_a_full_daily_budget_defers_even_after_the_minimum_interval_has_passed(env):
    uid = env["users"]["reader"]["id"]
    oldest = now() - 36000
    for index in range(CONVERSATION_DIGEST_DAILY_CAP):
        row = env["notifier"].emit(uid, "conversation.added", params={"conversation": "Fixture history"})
        mail = env["mailer"].enqueue("reader@example.test", "Fixture digest", "Fixture only", kind="notification", ref_id=row["id"])
        env["notifier"].finish_delivery(row["id"], "queued", mail_id=mail["id"])
        env["store"].execute("UPDATE notification_deliveries SET updated_at=? WHERE notification_id=?",
                             (oldest + index * 3600, row["id"]))
    nid = conversation_item(env)[0]
    result = second_dispatcher(env).run_once()
    assert result["queued"] == 0 and result["deferred"] == 1
    assert len(mails(env)) == CONVERSATION_DIGEST_DAILY_CAP
    row = next(d for d in deliveries(env) if d["notification_id"] == nid)
    assert row["not_before"] == oldest + 86400


@pytest.mark.parametrize("change,expected", [
    ("suspend", "reader inactive"), ("off", "channel off"), ("read", "read in app"),
    ("left", "no longer a member"), ("organization_member_removed", "no longer a member"),
    ("muted", "conversation muted"), ("recalled", "nothing unread"), ("postponed", None),
])
def test_current_state_is_rechecked_after_waiting_for_the_writer_lock(env, monkeypatch, change, expected):
    nid, cid, mid = conversation_item(env)
    dispatcher = second_dispatcher(env)
    reached, release = Event(), Event()
    original = dispatcher.store.tx
    announced = False
    @contextmanager
    def gated_transaction():
        nonlocal announced
        if not announced:
            announced = True
            reached.set()
            assert release.wait(10)
        with original() as tx:
            yield tx
    monkeypatch.setattr(dispatcher.store, "tx", gated_transaction)
    store, uid = env["store"], env["users"]["reader"]["id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(dispatcher.run_once)
        try:
            assert reached.wait(10)
            if change == "suspend":
                env["ident"].set_status(uid, "suspended")
            elif change == "off":
                env["notifier"].set_preferences(uid, {"categories": {"conversation": {"email": "off"}}})
            elif change == "read":
                env["notifier"].mark(uid, [nid], read=True)
            elif change == "left":
                store.execute("UPDATE conversation_members SET left_at=? WHERE conversation_id=? AND user_id=?", (now(), cid, uid))
            elif change == "organization_member_removed":
                store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (env["oid"], uid))
            elif change == "muted":
                store.execute("UPDATE conversation_members SET muted=1 WHERE conversation_id=? AND user_id=?", (cid, uid))
            elif change == "recalled":
                store.execute("UPDATE messages SET deleted_at=?,body='' WHERE id=?", (now(), mid))
            else:
                store.execute("UPDATE notification_deliveries SET not_before=? WHERE notification_id=?", (now() + 3600, nid))
        finally:
            release.set()
        result = future.result(timeout=10)
    assert result["queued"] == 0 and mails(env) == []
    row = next(d for d in deliveries(env) if d["notification_id"] == nid)
    assert row["mail_id"] is None
    assert row["status"] == ("pending" if expected is None else "skipped")
    assert row["last_error"] == expected


@pytest.mark.parametrize("stage", ["before_insert", "after_insert", "link"])
def test_outbox_or_link_failure_rolls_back_the_whole_mail_and_retries_once(env, stage):
    first = conversation_item(env, "owner")[0]
    second = conversation_item(env, "sender")[0]
    if stage == "link":
        # The second link fails after the first delivery was already associated. Both links
        # and the shared outbox mail must still disappear together.
        second_link = max(first, second)
        env["store"].execute("CREATE TRIGGER fixture_enqueue_failure BEFORE UPDATE OF mail_id ON notification_deliveries "
                              f"WHEN NEW.mail_id IS NOT NULL AND NEW.notification_id='{second_link}' "
                              "BEGIN SELECT RAISE(FAIL,'fixture link failure'); END")
    else:
        timing = "BEFORE" if stage == "before_insert" else "AFTER"
        env["store"].execute(f"CREATE TRIGGER fixture_enqueue_failure {timing} INSERT ON mail_outbox "
                              "BEGIN SELECT RAISE(FAIL,'fixture outbox failure'); END")
    result = env["dispatcher"].run_once()
    assert result["queued"] == 0 and result["failed"] == 2 and result["digested"] == 0
    assert mails(env) == []
    rows = deliveries(env)
    assert {d["notification_id"] for d in rows} == {first, second}
    assert all(d["status"] == "pending" and d["mail_id"] is None and d["attempts"] == 1 for d in rows)
    assert all(d["last_error"] == "notification_enqueue_failed" and d["not_before"] > now() for d in rows)
    env["store"].execute("DROP TRIGGER fixture_enqueue_failure")
    env["store"].execute("UPDATE notification_deliveries SET not_before=0 WHERE status='pending'")
    retried = second_dispatcher(env).run_once()
    assert retried["queued"] == 2 and retried["digested"] == 1 and len(mails(env)) == 1
    assert len({d["mail_id"] for d in deliveries(env)}) == 1
    assert env["dispatcher"].run_once()["queued"] == 0


def test_a_process_interruption_after_insert_cannot_commit_orphaned_mail_or_claim(env, monkeypatch):
    nid = conversation_item(env)[0]
    enqueue = env["mailer"].enqueue
    class FixtureProcessExit(BaseException):
        pass
    def interrupted(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise FixtureProcessExit()
    monkeypatch.setattr(env["mailer"], "enqueue", interrupted)
    with pytest.raises(FixtureProcessExit):
        env["dispatcher"].run_once()
    row = deliveries(env)[0]
    assert row["notification_id"] == nid and row["status"] == "pending" and row["mail_id"] is None
    assert mails(env) == []
    assert second_dispatcher(env).run_once()["queued"] == 1
    assert len(mails(env)) == 1


def test_persistent_enqueue_failure_uses_existing_bounded_retry_schedule(env):
    conversation_item(env)
    env["store"].execute("CREATE TRIGGER fixture_enqueue_failure BEFORE INSERT ON mail_outbox "
                          "BEGIN SELECT RAISE(FAIL,'fixture failure'); END")
    for attempt in range(1, len(RETRY_DELAYS_S) + 2):
        env["store"].execute("UPDATE notification_deliveries SET not_before=0 WHERE status='pending'")
        result = env["dispatcher"].run_once()
        row = deliveries(env)[0]
        assert result["queued"] == 0 and result["failed"] == 1 and mails(env) == []
        assert row["attempts"] == attempt and row["mail_id"] is None
        if attempt <= len(RETRY_DELAYS_S):
            assert row["status"] == "pending"
            assert now() + RETRY_DELAYS_S[attempt - 1] - 5 <= row["not_before"] <= now() + RETRY_DELAYS_S[attempt - 1]
        else:
            assert row["status"] == "failed"
    assert env["dispatcher"].run_once()["failed"] == 0


def test_one_failed_security_notice_does_not_undo_another_and_disabled_mail_is_not_queued(env, monkeypatch):
    uid = env["users"]["reader"]["id"]
    good = env["notifier"].security_change(uid, "password_changed")
    bad = env["notifier"].security_change(uid, "email_changed")
    # One real insert fails; the unrelated critical notice must still commit.
    env["store"].execute(f"CREATE TRIGGER fixture_enqueue_failure BEFORE INSERT ON mail_outbox WHEN NEW.ref_id='{bad['id']}' "
                          "BEGIN SELECT RAISE(FAIL,'fixture failure'); END")
    result = env["dispatcher"].run_once()
    assert result["queued"] == 1 and result["failed"] == 1
    assert [m["ref_id"] for m in mails(env)] == [good["id"]]
    env["store"].execute("DROP TRIGGER fixture_enqueue_failure")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    env["store"].execute("UPDATE notification_deliveries SET not_before=0 WHERE status='pending'")
    result = env["dispatcher"].run_once()
    assert result["queued"] == 0 and result["skipped"] == 1
    assert next(d for d in deliveries(env) if d["notification_id"] == bad["id"])["last_error"] == "mail not configured"
