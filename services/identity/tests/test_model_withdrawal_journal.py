"""A restored backup must not bring back a withdrawn platform model key or a switched-off provider.

Isolated fixture prices, keys and accounts through the real admin routes (test_admin_model_provider
helpers); no model or payment request. A "restore" is simulated as the other restore tests do:
the database rows are put back the way an older backup holds them, then the journal is applied.
"""
from __future__ import annotations

import time

from synwevo_core import journal
from synwevo_core.billing.provider_settings import ProviderSettings

from .test_admin_model_provider import (  # noqa: F401 — fixtures
    DISABLED, KEY, REPLACEMENT, billing_environment, command, preview, provider_world, publish, world,
)


def _publish(w, revision, spec, credential):
    plan = preview(w, revision=revision, spec=spec, credential=credential)
    response = publish(w, command(plan, credential))
    assert response.status_code == 200, response.text
    return response.json()["state"]


def _entries(w, kind, since):
    return [e for e in journal.read(w["store"].root, since=since) if e["kind"] == kind]


def test_a_replaced_or_revoked_key_stays_withdrawn_after_a_restore(provider_world, tmp_path):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    assert _entries(w, "model_credential_withdrawn", started) == []          # nothing was withdrawn yet
    _backup_now(w, tmp_path / "before-rotation.sqlite3")
    _publish(w, 1, w["spec"], {"action": "replace", "api_key": KEY + "-rotated"})
    entries = _entries(w, "model_credential_withdrawn", started)
    assert [(e["environment"], e["credential_revision"]) for e in entries] == [("test", 1)]
    assert KEY not in journal.path(w["store"].root).read_text()
    # Restore a backup taken before the rotation: the old key's ciphertext is back, revision 1.
    _copy(tmp_path / "before-rotation.sqlite3", w["store"].db_path)
    assert w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")["secret_enc"]
    counts = journal.apply(w["store"], entries)
    assert counts["model_credential_withdrawn"] == 1
    assert w["store"].one("SELECT secret_enc FROM model_provider_credentials")["secret_enc"] is None
    assert journal.apply(w["store"], entries)["model_credential_withdrawn"] == 0      # idempotent


def test_a_newer_key_in_the_restored_database_is_left_alone(provider_world):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    _publish(w, 1, w["spec"], {"action": "replace", "api_key": KEY + "-rotated"})
    entries = _entries(w, "model_credential_withdrawn", started)
    journal.apply(w["store"], entries)          # the database already holds revision 2: unchanged
    assert w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")["secret_enc"]


def test_a_switched_off_provider_stays_off_after_a_restore(provider_world):
    w = provider_world
    started = time.time() - 1
    _publish(w, 0, w["spec"], REPLACEMENT)
    _publish(w, 1, DISABLED, {"action": "keep"})
    entries = _entries(w, "model_provider_disabled", started)
    assert len(entries) == 1
    settings = ProviderSettings(w["store"], environment="test")
    # A backup from before the switch-off: its newest version is the enabled one, and it holds no
    # receipt of the switch-off's journal line (journal._intent commits one with it).
    w["store"].execute("DROP TRIGGER model_provider_no_delete")
    w["store"].execute("DELETE FROM model_provider_versions WHERE revision=2")
    w["store"].execute("DELETE FROM meta WHERE key=?", ("journal_receipt:" + entries[0]["attempt_id"],))
    assert settings.read()["spec"]["enabled"] is True
    assert journal.apply(w["store"], entries)["model_provider_disabled"] == 1
    state = settings.read()
    assert state["revision"] == 2 and state["spec"]["enabled"] is False and "Journal replay" in state["reason"]
    assert settings.resolve() is None
    assert journal.apply(w["store"], entries)["model_provider_disabled"] == 0         # once only


# --------------------------------------------------------------- a backup between line and COMMIT
# `platform.py backup` notes snapshot_at, then copies the database through a reader that does not
# wait for a writer. A withdrawal journals ahead of its SQL change, so a backup can land after the
# line and before the COMMIT. The fixture below takes the backup at exactly that moment (the
# journal is wrapped; the committed database is copied right after the line), restores it in
# place and replays the journal with that cutoff, as snapshot.fence does.

def _copy(src_path, dest_path):
    import sqlite3
    src, dst = sqlite3.connect(src_path, timeout=30), sqlite3.connect(dest_path, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _backup_inside(w, kind, dest, monkeypatch):
    taken, original = {}, journal.record

    def record(store, k, **fields):
        original(store, k, **fields)
        if k == kind and not taken:
            taken["at"] = time.time()
            _copy(w["store"].db_path, dest)

    monkeypatch.setattr(journal, "record", record)
    return taken


def _backup_now(w, dest):
    at = time.time()
    _copy(w["store"].db_path, dest)
    return at


def _restore(w, backup, since):
    _copy(backup, w["store"].db_path)
    return journal.apply(w["store"], journal.read(w["store"].root, since=since))


def test_a_key_withdrawal_a_backup_caught_before_its_commit_stays_withdrawn(provider_world, tmp_path, monkeypatch):
    w = provider_world
    _publish(w, 0, w["spec"], REPLACEMENT)
    taken = _backup_inside(w, "model_credential_withdrawn", tmp_path / "backup.sqlite3", monkeypatch)
    _publish(w, 1, w["spec"], {"action": "replace", "api_key": KEY + "-rotated"})
    _restore(w, tmp_path / "backup.sqlite3", taken["at"])
    row = w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")
    assert row["revision"] == 1 and row["secret_enc"] is None, "the replaced key must not come back"


def test_a_switch_off_a_backup_caught_before_its_commit_stays_off(provider_world, tmp_path, monkeypatch):
    w = provider_world
    _publish(w, 0, w["spec"], REPLACEMENT)
    taken = _backup_inside(w, "model_provider_disabled", tmp_path / "backup.sqlite3", monkeypatch)
    _publish(w, 1, DISABLED, {"action": "keep"})
    _restore(w, tmp_path / "backup.sqlite3", taken["at"])
    assert ProviderSettings(w["store"], environment="test").read()["spec"]["enabled"] is False


def test_replaying_whatever_the_backup_time_keeps_a_later_re_enable_and_a_reused_key_revision(provider_world, tmp_path):
    w = provider_world
    settings = ProviderSettings(w["store"], environment="test")
    _publish(w, 0, w["spec"], REPLACEMENT)                               # credential revision 1
    _publish(w, 1, DISABLED, {"action": "keep"})                         # switched off
    _publish(w, 2, w["spec"], {"action": "keep"})                        # and on again
    at = _backup_now(w, tmp_path / "later.sqlite3")
    _restore(w, tmp_path / "later.sqlite3", at)
    assert settings.read()["spec"]["enabled"] is True, "a re-enabled provider stays on"
    # Restore a backup from before two rotations: both withdrawals apply (revision 1 loses its
    # secret). Publishing a key afterwards reuses revision 2, which a withdrawal named once; the
    # next restore must keep that new key.
    early_at = _backup_now(w, tmp_path / "early.sqlite3")
    _publish(w, 3, w["spec"], {"action": "replace", "api_key": KEY + "-two"})       # withdraws 1
    _publish(w, 4, w["spec"], {"action": "replace", "api_key": KEY + "-three"})     # withdraws 2
    _restore(w, tmp_path / "early.sqlite3", early_at)
    assert w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")["secret_enc"] is None
    state = _publish(w, 3, w["spec"], {"action": "replace", "api_key": KEY + "-after-restore"})
    assert state["credential_revision"] == 2
    again = _backup_now(w, tmp_path / "again.sqlite3")
    _restore(w, tmp_path / "again.sqlite3", again)
    row = w["store"].one("SELECT revision, secret_enc FROM model_provider_credentials")
    assert row["revision"] == 2 and row["secret_enc"], "a key published after the restore stays usable"
