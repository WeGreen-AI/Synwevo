"""Real Store configuration snapshots and races; every database is a disposable fixture."""
from __future__ import annotations

import concurrent.futures
import json
import threading

import pytest

from synwevo_core.errors import ServiceError
from synwevo_core.platform_settings import DAILY_CAP_KEY, INTERVAL_KEY, PlatformSettings
from synwevo_core.store import Store, now

REASON = "Fixture: adjust the conversation digest budget"


@pytest.fixture()
def settings(tmp_path):
    return PlatformSettings(Store(tmp_path / "fixture"))


def append(settings, changes=None, *, revision=None, operation=None, rollback=None):
    current = settings.read()["revision"] if revision is None else revision
    with settings.store.tx():
        return settings.append(changes, expected_revision=current, actor_id="fixture-actor",
                               reason=REASON, operation_id=operation or f"fixture-op-{current + 1}",
                               rollback_revision=rollback)


def assert_error(code, callback, status=None):
    with pytest.raises(ServiceError) as error:
        callback()
    assert error.value.code == code
    if status is not None:
        assert error.value.status == status


def test_defaults_are_read_only_and_ignore_unregistered_environment_overrides(settings, monkeypatch):
    monkeypatch.setenv("SYNWEVO_CONVERSATION_DIGEST_INTERVAL_S", "77")
    monkeypatch.setenv("SYNWEVO_CONVERSATION_DIGEST_DAILY_CAP", "99")
    assert settings.read() == {"revision": 0, "overrides": {},
                               "values": {INTERVAL_KEY: 1800, DAILY_CAP_KEY: 8},
                               "sources": {INTERVAL_KEY: "builtin_default", DAILY_CAP_KEY: "builtin_default"}}
    assert settings.history() == {"items": [], "next_before_revision": None}
    catalogue = {item["key"]: item for item in settings.catalogue()}
    assert set(catalogue) == {INTERVAL_KEY, DAILY_CAP_KEY}
    assert catalogue[INTERVAL_KEY] == {"key": INTERVAL_KEY, "type": "integer", "unit": "seconds", "default": 1800,
                                      "min": 60, "max": 86400, "scope": "global", "default_source": "builtin_default",
                                      "description": "同一收件人的会话未读摘要邮件最小间隔；不影响安全通知或其他类别。"}
    assert (catalogue[DAILY_CAP_KEY]["default"], catalogue[DAILY_CAP_KEY]["min"], catalogue[DAILY_CAP_KEY]["max"]) == (8, 1, 100)
    catalogue[INTERVAL_KEY]["default"] = 999
    assert settings.catalogue()[0]["default"] == 1800
    assert settings.store.one("SELECT COUNT(*) n FROM platform_settings_versions")["n"] == 0


def test_preview_normalizes_changes_without_writing_or_notifying(settings):
    before = settings.store.one("SELECT COUNT(*) n FROM notifications")["n"]
    plan = settings.preview({INTERVAL_KEY: 60, DAILY_CAP_KEY: None})
    assert plan["expected_revision"] == 0 and plan["before"]["revision"] == 0
    assert plan["changes"] == {INTERVAL_KEY: 60}
    assert plan["after"]["values"] == {INTERVAL_KEY: 60, DAILY_CAP_KEY: 8}
    assert plan["after"]["sources"][INTERVAL_KEY] == "platform_override"
    assert "revision" not in plan["after"]
    assert settings.read()["revision"] == 0 and settings.history()["items"] == []
    assert settings.store.one("SELECT COUNT(*) n FROM notifications")["n"] == before


def test_complete_overrides_snapshots_reset_and_rollback_always_append(settings):
    one = append(settings, {INTERVAL_KEY: 60})
    two = append(settings, {DAILY_CAP_KEY: 100})
    three = append(settings, {INTERVAL_KEY: None})
    assert one["revision"] == 1 and two["overrides"] == {INTERVAL_KEY: 60, DAILY_CAP_KEY: 100}
    assert three["revision"] == 3 and three["overrides"] == {DAILY_CAP_KEY: 100}
    assert three["values"][INTERVAL_KEY] == 1800
    assert three["sources"][INTERVAL_KEY] == "builtin_default"
    restored = append(settings, rollback=1)
    assert restored["revision"] == 4 and restored["overrides"] == one["overrides"]
    assert restored["rollback_revision"] == 1
    defaults = append(settings, rollback=0)
    assert defaults["revision"] == 5 and defaults["overrides"] == {}
    assert defaults["values"] == settings.revision(0)["values"]
    assert settings.revision(2) == two
    assert settings.history()["items"][0] == defaults
    assert settings.revision(0)["actor_id"] is None
    assert all(row["actor_id"] == "fixture-actor" and row["reason"] == REASON for row in settings.history()["items"])
    raw = settings.store.all("SELECT revision,overrides FROM platform_settings_versions ORDER BY revision")
    assert json.loads(raw[1]["overrides"]) == {INTERVAL_KEY: 60, DAILY_CAP_KEY: 100}
    assert [row["revision"] for row in raw] == [1, 2, 3, 4, 5]


def test_an_explicit_default_and_the_builtin_source_are_distinct(settings):
    explicit = append(settings, {INTERVAL_KEY: 1800})
    assert explicit["sources"][INTERVAL_KEY] == "platform_override"
    reset = append(settings, {INTERVAL_KEY: None})
    assert reset["values"] == explicit["values"] and reset["sources"][INTERVAL_KEY] == "builtin_default"
    assert reset["revision"] > explicit["revision"]


@pytest.mark.parametrize("value", [True, False, 60.0, "60", [], {}, -1, 0, 59, 86401, 1 << 100])
def test_interval_rejects_nonintegers_and_out_of_range_values(settings, value):
    assert_error("settings_invalid_value", lambda: settings.preview({INTERVAL_KEY: value}), 400)
    assert settings.read()["revision"] == 0


@pytest.mark.parametrize("value", [True, 1.0, "8", 0, 101])
def test_daily_cap_rejects_nonintegers_and_out_of_range_values(settings, value):
    assert_error("settings_invalid_value", lambda: settings.preview({DAILY_CAP_KEY: value}), 400)


@pytest.mark.parametrize("changes", [None, [], "secret", {}, {"unknown": 1}, {INTERVAL_KEY: 60, "unknown": 1}])
def test_empty_invalid_and_unknown_changes_write_nothing(settings, changes):
    code = "settings_unknown_key" if isinstance(changes, dict) and "unknown" in changes else "settings_bad_changes"
    assert_error(code, lambda: append(settings, changes), 400)
    assert settings.history()["items"] == []


def test_no_effective_edit_and_ambiguous_rollback_are_rejected(settings):
    assert_error("settings_no_change", lambda: settings.preview({INTERVAL_KEY: None}), 409)
    append(settings, {INTERVAL_KEY: 60})
    assert_error("settings_no_change", lambda: append(settings, {INTERVAL_KEY: 60}), 409)
    assert_error("settings_no_change", lambda: append(settings, rollback=1), 409)
    assert_error("settings_bad_changes", lambda: append(settings, {INTERVAL_KEY: 120}, rollback=0), 400)
    assert_error("settings_revision_not_found", lambda: append(settings, rollback=8), 404)
    assert len(settings.history()["items"]) == 1


def test_a_b_a_values_never_make_an_old_preview_current_again(settings):
    original = settings.read()
    append(settings, {INTERVAL_KEY: 60})
    append(settings, {INTERVAL_KEY: None})
    assert settings.read()["values"] == original["values"]
    assert_error("settings_revision_conflict", lambda: append(settings, {DAILY_CAP_KEY: 9}, revision=0), 409)
    assert settings.read()["revision"] == 2


def test_two_independent_stores_cannot_both_commit_the_same_expected_revision(settings):
    second = PlatformSettings(Store(settings.store.root))
    barrier = threading.Barrier(2, timeout=10)
    def write(pair):
        service, value = pair
        barrier.wait()
        try:
            return append(service, {INTERVAL_KEY: value}, revision=0, operation=f"fixture-race-{value}")
        except ServiceError as exc:
            return exc.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [(settings, 60), (second, 120)]))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert "settings_revision_conflict" in results
    assert settings.read() == second.read()
    assert settings.read()["revision"] == 1 and len(settings.history()["items"]) == 1


def test_append_requires_the_existing_store_writer_transaction(settings):
    kwargs = {"expected_revision": 0, "actor_id": "fixture-actor", "reason": REASON, "operation_id": "fixture-op"}
    with pytest.raises(RuntimeError, match="existing Store.tx"):
        settings.append({INTERVAL_KEY: 60}, **kwargs)
    with settings.store.conn() as connection:
        connection.execute("BEGIN")
        try:
            with pytest.raises(RuntimeError, match="existing Store.tx"):
                settings.append({INTERVAL_KEY: 60}, **kwargs)
        finally:
            connection.execute("ROLLBACK")
    assert settings.read()["revision"] == 0


def test_outer_rollback_reverts_history_operation_result_and_audit_together(settings):
    observer = PlatformSettings(Store(settings.store.root))
    with pytest.raises(OSError, match="fixture audit unavailable"):
        with settings.store.tx() as connection:
            saved = settings.append({INTERVAL_KEY: 60}, expected_revision=0, actor_id="fixture-actor", reason=REASON,
                                    operation_id="fixture-atomic")
            assert saved["revision"] == 1 and observer.read()["revision"] == 0
            connection.execute("INSERT INTO meta(key,value) VALUES ('fixture-operation-result','recorded')")
            connection.execute("INSERT INTO audit_events(id,at,actor_kind,actor_id,action,result) "
                               "VALUES ('fixture-settings-audit',?,'user','fixture-actor','settings.update','ok')", (now(),))
            raise OSError("fixture audit unavailable")
    assert observer.read()["revision"] == 0 and settings.history()["items"] == []
    assert not settings.store.one("SELECT 1 FROM meta WHERE key='fixture-operation-result'")
    assert not settings.store.one("SELECT 1 FROM audit_events WHERE id='fixture-settings-audit'")
    assert append(settings, {INTERVAL_KEY: 60}, operation="fixture-atomic")["revision"] == 1


def test_all_input_validation_precedes_any_insert(settings):
    valid = {"expected_revision": 0, "actor_id": "fixture-actor", "reason": REASON, "operation_id": "fixture-input"}
    for key, value, code in [("expected_revision", True, "settings_bad_revision"),
                             ("expected_revision", -1, "settings_bad_revision"),
                             ("expected_revision", 1 << 70, "settings_bad_revision"),
                             ("actor_id", "", "settings_bad_metadata"), ("operation_id", "", "settings_bad_metadata"),
                             ("actor_id", {"id": "forged"}, "settings_bad_metadata"),
                             ("reason", "short", "missing_reason"), ("reason", "x" * 501, "missing_reason")]:
        with settings.store.tx():
            assert_error(code, lambda: settings.append({INTERVAL_KEY: 60}, **{**valid, key: value}), 400)
    assert settings.history()["items"] == []


def test_operation_reference_is_unique_and_history_pages_remain_stable(settings):
    append(settings, {INTERVAL_KEY: 60}, operation="fixture-unique")
    assert_error("settings_operation_conflict", lambda: append(settings, {INTERVAL_KEY: 120}, operation="fixture-unique"), 409)
    for value in (120, 180, 240):
        append(settings, {INTERVAL_KEY: value})
    first = settings.history(limit=2)
    assert [item["revision"] for item in first["items"]] == [4, 3] and first["next_before_revision"] == 3
    append(settings, {INTERVAL_KEY: 300})
    second = settings.history(before_revision=first["next_before_revision"], limit=2)
    assert [item["revision"] for item in second["items"]] == [2, 1] and second["next_before_revision"] is None
    assert settings.history(before_revision=0)["items"] == []
    assert_error("settings_bad_page", lambda: settings.history(limit=True), 400)
    assert_error("settings_bad_page", lambda: settings.history(limit=101), 400)
    assert_error("settings_bad_revision", lambda: settings.history(before_revision=-1), 400)


def test_reopening_store_preserves_versions_and_existing_services_read_latest(settings):
    row = append(settings, {INTERVAL_KEY: 86400, DAILY_CAP_KEY: 1})
    reopened = PlatformSettings(Store(settings.store.root))
    assert reopened.revision(row["revision"]) == row
    append(reopened, {DAILY_CAP_KEY: 10})
    assert settings.read() == reopened.read() and settings.read()["values"][DAILY_CAP_KEY] == 10
    with settings.store.conn() as connection:
        columns = {r["name"] for r in connection.execute("PRAGMA table_info(notification_deliveries)")}
    assert {"defer_reason", "policy_revision"} <= columns


def test_corrupt_persisted_snapshot_is_not_silently_replaced_by_defaults(settings):
    append(settings, {INTERVAL_KEY: 60})
    settings.store.execute("UPDATE platform_settings_versions SET overrides=?", ('{"unknown":1}',))
    assert_error("settings_unavailable", settings.read, 503)


def test_additive_migration_preserves_pre_policy_delivery_schedule_and_unknown_revision(settings):
    store = settings.store
    store.execute("INSERT INTO notifications (id,user_id,kind,category,created_at) "
                  "VALUES ('fixture-notification','fixture-user','conversation.added','conversation',1)")
    store.execute("INSERT INTO notification_deliveries (notification_id,channel,status,not_before,updated_at) "
                  "VALUES ('fixture-notification','email','pending',123456,1)")
    # Disposable old-schema fixture: the previous release had no reason or observed version.
    with store.tx() as transaction:
        transaction.execute("ALTER TABLE notification_deliveries DROP COLUMN defer_reason")
        transaction.execute("ALTER TABLE notification_deliveries DROP COLUMN policy_revision")
    for _ in range(2):
        migrated = Store(store.root)
        row = migrated.one("SELECT status,not_before,defer_reason,policy_revision FROM notification_deliveries "
                           "WHERE notification_id='fixture-notification'")
        assert row == {"status": "pending", "not_before": 123456, "defer_reason": None, "policy_revision": None}
