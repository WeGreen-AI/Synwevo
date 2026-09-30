"""Real versioned settings consumed by independent notification dispatchers.

All accounts, messages, queued-mail history and clock adjustments are isolated fixtures.
No SMTP or external provider is used; enqueue and Store decisions remain real.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Event

import pytest

from synwevo_core.notifications import Notifier
from synwevo_core.platform_settings import PlatformSettings, INTERVAL_KEY, DAILY_CAP_KEY
from synwevo_core.store import Store, new_id, now
from .test_notification_dispatch_atomic import env, conversation_item, second_dispatcher, mails  # noqa: F401
from .test_notifications import acct, login  # noqa: F401


def publish(store, changes=None, rollback_revision=None):
    """Explicit fixture authority uses the same storage service as governed publication."""
    settings = PlatformSettings(store)
    with store.tx():
        return settings.append(changes, expected_revision=settings.read()["revision"],
                               actor_id="fixture-owner", reason="Isolated configuration acceptance fixture",
                               operation_id=new_id("fixture"), rollback_revision=rollback_revision)


def queued_history(env, *, ages):
    uid = env["users"]["reader"]["id"]
    timestamps = []
    for index, age in enumerate(ages):
        row = env["notifier"].emit(uid, "conversation.added", params={"conversation": f"Fixture history {index}"})
        mail = env["mailer"].enqueue("reader@example.test", "Fixture digest", "Fixture only", kind="notification", ref_id=row["id"])
        env["notifier"].finish_delivery(row["id"], "queued", mail_id=mail["id"])
        ts = now() - age
        env["store"].execute("UPDATE notification_deliveries SET updated_at=? WHERE notification_id=?", (ts, row["id"]))
        timestamps.append(ts)
    return timestamps


def due_item(env, sender="owner"):
    nid, _, _ = conversation_item(env, sender)
    # The helper makes the initial window due; its fixture timestamp also describes a
    # notification older than that window. It is not a bypass in the running application.
    env["store"].execute("UPDATE notifications SET created_at=? WHERE id=?", (now() - 3600, nid))
    return nid


def delivery(env, nid):
    return env["store"].one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'", (nid,))


def enable_current_quiet_hours(env):
    # Exercise the real preference/time-zone calculation, choosing a zone away from the
    # one-minute gap so this test does not depend on its wall-clock execution hour.
    zone = "UTC" if datetime.now(timezone.utc).hour != 23 else "Asia/Hong_Kong"
    prefs = env["notifier"].set_preferences(env["users"]["reader"]["id"],
        {"quiet_start": "00:00", "quiet_end": "23:59", "timezone": zone})
    until = env["notifier"].quiet_until(prefs)
    assert until and until > now()
    return until


def test_lower_interval_rechecks_an_old_budget_wait_without_restarting_its_dispatcher(env):
    queued_history(env, ages=[600])
    nid = due_item(env)
    old_worker = env["dispatcher"]
    assert old_worker.run_once()["deferred"] == 1
    before = delivery(env, nid)
    assert before["defer_reason"] == "budget" and before["policy_revision"] == 0 and before["not_before"] > now()
    writer = Store(env["store"].root)
    version = publish(writer, {INTERVAL_KEY: 300})["revision"]
    # Publishing does not mutate the old schedule or manufacture a worker acknowledgement.
    assert delivery(env, nid) == before
    result = old_worker.run_once()
    assert result["queued"] == 1 and len(mails(env)) == 2
    after = delivery(env, nid)
    assert after["status"] == "queued" and after["mail_id"] and after["defer_reason"] is None
    assert after["policy_revision"] == version
    assert old_worker.run_once()["queued"] == 0


def test_raised_daily_cap_releases_old_budget_wait_on_a_restarted_instance(env):
    writer = Store(env["store"].root)
    first = publish(writer, {DAILY_CAP_KEY: 1})["revision"]
    queued_history(env, ages=[4000])
    nid = due_item(env)
    assert env["dispatcher"].run_once()["deferred"] == 1
    waiting = delivery(env, nid)
    assert waiting["policy_revision"] == first and waiting["not_before"] > now() + 20 * 3600
    next_version = publish(writer, {DAILY_CAP_KEY: 2})["revision"]
    restarted = second_dispatcher(env)
    assert restarted.run_once()["queued"] == 1
    assert delivery(env, nid)["policy_revision"] == next_version
    assert PlatformSettings(restarted.store).read()["values"][DAILY_CAP_KEY] == 2


@pytest.mark.parametrize("change", [{INTERVAL_KEY: 7200}, {DAILY_CAP_KEY: 1}])
def test_tightening_is_used_by_existing_due_deliveries(env, change):
    timestamp = queued_history(env, ages=[4000])[0]
    nid = due_item(env)
    version = publish(Store(env["store"].root), change)["revision"]
    result = env["dispatcher"].run_once()
    assert result["queued"] == 0 and result["deferred"] == 1 and len(mails(env)) == 1
    row = delivery(env, nid)
    assert row["defer_reason"] == "budget" and row["policy_revision"] == version
    assert row["not_before"] == timestamp + (7200 if INTERVAL_KEY in change else 86400)


def test_tightening_recalculates_an_already_deferred_budget_wait(env):
    timestamp = queued_history(env, ages=[600])[0]
    nid = due_item(env)
    assert env["dispatcher"].run_once()["deferred"] == 1
    before = delivery(env, nid)
    assert before["not_before"] == timestamp + 1800
    version = publish(Store(env["store"].root), {INTERVAL_KEY: 7200})["revision"]
    assert second_dispatcher(env).run_once()["deferred"] == 1
    after = delivery(env, nid)
    assert after["not_before"] == timestamp + 7200 and after["policy_revision"] == version
    assert len(mails(env)) == 1


def test_lowered_cap_waits_until_enough_prior_mails_leave_the_rolling_window(env):
    timestamps = queued_history(env, ages=[20000, 10000, 4000])
    nid = due_item(env)
    publish(Store(env["store"].root), {DAILY_CAP_KEY: 1})
    assert env["dispatcher"].run_once()["deferred"] == 1
    assert delivery(env, nid)["not_before"] == max(timestamps) + 86400


def test_rollback_is_a_new_version_and_releases_only_the_old_budget_wait(env):
    queued_history(env, ages=[4000])
    nid = due_item(env)
    writer = Store(env["store"].root)
    changed = publish(writer, {INTERVAL_KEY: 7200})["revision"]
    assert env["dispatcher"].run_once()["deferred"] == 1
    restored = publish(writer, rollback_revision=0)
    assert restored["revision"] > changed and restored["values"][INTERVAL_KEY] == 1800
    assert env["dispatcher"].run_once()["queued"] == 1
    assert delivery(env, nid)["policy_revision"] == restored["revision"]
    assert len(PlatformSettings(writer).history()["items"]) == 2


@pytest.mark.parametrize("wait", ["aggregation", "quiet", "retry", "legacy"])
def test_relaxing_budget_never_clears_unrelated_waits(env, wait):
    nid, _, _ = conversation_item(env)
    if wait == "aggregation":
        env["store"].execute("UPDATE notification_deliveries SET not_before=? WHERE notification_id=?", (now() + 1800, nid))
    elif wait == "quiet":
        enable_current_quiet_hours(env)
        assert env["dispatcher"].run_once()["deferred"] == 1
    elif wait == "retry":
        env["store"].execute("CREATE TRIGGER fixture_dynamic_failure BEFORE INSERT ON mail_outbox "
                             "BEGIN SELECT RAISE(FAIL,'isolated fixture failure'); END")
        assert env["dispatcher"].run_once()["failed"] == 1
        env["store"].execute("DROP TRIGGER fixture_dynamic_failure")
    else:
        env["store"].execute("UPDATE notification_deliveries SET not_before=?,defer_reason=NULL,policy_revision=NULL WHERE notification_id=?",
                             (now() + 3600, nid))
    before = delivery(env, nid)
    assert before["defer_reason"] == (None if wait == "legacy" else wait)
    publish(Store(env["store"].root), {INTERVAL_KEY: 60, DAILY_CAP_KEY: 100})
    assert second_dispatcher(env).run_once()["queued"] == 0
    assert delivery(env, nid) == before and mails(env) == []


def test_a_budget_wait_rechecks_new_quiet_hours_before_using_a_relaxed_policy(env):
    queued_history(env, ages=[600])
    nid = due_item(env)
    assert env["dispatcher"].run_once()["deferred"] == 1
    publish(Store(env["store"].root), {INTERVAL_KEY: 60})
    quiet_until = enable_current_quiet_hours(env)
    result = env["dispatcher"].run_once()
    row = delivery(env, nid)
    assert result["queued"] == 0 and result["deferred"] == 1
    assert row["defer_reason"] == "quiet" and row["not_before"] == quiet_until and row["policy_revision"] is None


def test_publication_while_dispatcher_waits_for_the_lock_is_not_missed(env, monkeypatch):
    queued_history(env, ages=[4000])
    nid = due_item(env)
    worker = second_dispatcher(env)
    reached, release = Event(), Event()
    original = worker.store.tx
    first = True
    @contextmanager
    def gated():
        nonlocal first
        if first:
            first = False
            reached.set()
            assert release.wait(10)
        with original() as tx:
            yield tx
    monkeypatch.setattr(worker.store, "tx", gated)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(worker.run_once)
        try:
            assert reached.wait(10)
            version = publish(env["store"], {INTERVAL_KEY: 7200})["revision"]
        finally:
            release.set()
        result = pending.result(timeout=10)
    assert result["queued"] == 0 and result["deferred"] == 1
    assert delivery(env, nid)["policy_revision"] == version


def test_ordinary_preferences_show_only_effective_limits_and_cannot_publish_them(acct):
    headers = login(acct, "member@example.test")
    expected = {"revision": 0, "interval_s": 1800, "daily_cap": 8, "aggregation_window_s": 1800, "window_s": 86400}
    response = acct.get("/api/auth/notifications/preferences")
    assert response.status_code == 200 and response.json()["conversation_digest_policy"] == expected
    version = publish(Store(acct.ident.store.root), {INTERVAL_KEY: 600, DAILY_CAP_KEY: 3})["revision"]
    response = acct.get("/api/auth/notifications/preferences")
    policy = response.json()["conversation_digest_policy"]
    assert policy == {**expected, "revision": version, "interval_s": 600, "daily_cap": 3}
    assert not ({"reason", "actor_id", "operation_id", "overrides", "sources", "history"} & set(policy))
    denied = acct.put("/api/auth/notifications/preferences", headers=headers,
                      json={"conversation_digest_policy": {"interval_s": 60}})
    assert denied.status_code == 400 and PlatformSettings(acct.ident.store).read()["revision"] == version
    changed = acct.put("/api/auth/notifications/preferences", headers=headers, json={"preview": "minimal"})
    assert changed.status_code == 200 and changed.json()["conversation_digest_policy"] == policy
