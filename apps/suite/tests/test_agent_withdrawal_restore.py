"""A backup taken between an Agent withdrawal's journal line and its COMMIT (security journal).

`platform.py backup` notes ``snapshot_at`` and then copies the database through a reader, which
does not wait for a writer. A withdrawal is journalled ahead of its SQL change, so a backup can
land after the line and before the COMMIT: it holds neither the change nor, being newer than the
line, a reason to replay it. These tests take the backup at exactly that moment — the journal is
wrapped so that right after the line is written the committed database is copied, as the snapshot
does — then restore it in place and replay the journal with that cutoff, as ``snapshot.fence``
does. Fixture accounts and workspaces only; no model is called.

The same replay must never undo what a backup already holds: a suspension lifted later, a consent
given again after a withdrawal, or a workspace AI policy opened again after it was narrowed.
"""
from __future__ import annotations

import sqlite3
import time

from synwevo_core import journal
from synwevo_core.agent_safety import AgentAccess, ProviderConsents, provider_terms

from .test_agent_provider_policy import listed, set_policy


def _copy(src_path, dest_path) -> None:
    src, dst = sqlite3.connect(src_path, timeout=30), sqlite3.connect(dest_path, timeout=30)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def backup_inside(store, kind, dest, monkeypatch) -> dict:
    """Fixture: copy the committed database right after the first ``kind`` line is written —
    before the withdrawal's own transaction commits — and note that moment as ``snapshot_at``."""
    taken: dict = {}
    original = journal.record

    def record(s, k, **fields):
        original(s, k, **fields)
        if k == kind and not taken:
            taken["at"] = time.time()
            _copy(store.db_path, dest)

    monkeypatch.setattr(journal, "record", record)
    return taken


def backup_now(store, dest) -> float:
    at = time.time()
    _copy(store.db_path, dest)
    return at


def restore(store, backup_file, since) -> dict:
    """Put the backup's database in place of the live one, then replay the journal from its
    cutoff, as a restore does."""
    _copy(backup_file, store.db_path)
    return journal.apply(store, journal.read(store.root, since=since))


def test_a_suspension_a_backup_caught_before_its_commit_is_restored(suite, tmp_path, monkeypatch):
    p = suite.people
    alice = p.uid("alice")
    taken = backup_inside(p.store, "agent_access_suspended", tmp_path / "backup.sqlite3", monkeypatch)
    suspension = AgentAccess(p.store).suspend(alice, p.uid("oscar"), reason="fixture: reported answers")
    assert "at" in taken
    restore(p.store, tmp_path / "backup.sqlite3", taken["at"])
    active = AgentAccess(p.store).active(alice)
    assert active is not None and active["id"] == suspension["id"], "the restore must not give the Agent back"


def test_a_consent_withdrawal_a_backup_caught_before_its_commit_is_restored(suite, tmp_path, monkeypatch):
    p = suite.people
    alice = p.uid("alice")
    consents = ProviderConsents(p.store)
    assert consents.status(alice, "deepseek")["accepted"] is True
    taken = backup_inside(p.store, "agent_provider_consent_withdrawn", tmp_path / "backup.sqlite3", monkeypatch)
    consents.withdraw(alice, "deepseek")
    restore(p.store, tmp_path / "backup.sqlite3", taken["at"])
    assert consents.status(alice, "deepseek")["accepted"] is False, "the restore must not give the consent back"


def test_a_narrowed_ai_policy_a_backup_caught_before_its_commit_is_restored(suite, tmp_path, monkeypatch):
    p = suite.people
    set_policy(suite, "any", ["anthropic", "deepseek"])
    taken = backup_inside(p.store, "ai_policy_narrowed", tmp_path / "backup.sqlite3", monkeypatch)
    set_policy(suite, "platform", ["anthropic"])
    restore(p.store, tmp_path / "backup.sqlite3", taken["at"])
    now = listed(suite, p.a)
    assert (now["processing"], now["providers"]) == ("platform", ["anthropic"]), \
        "the restore must not reopen the workspace's content to DeepSeek"


def test_replaying_whatever_the_backup_time_keeps_a_later_lift_consent_and_widening(suite, tmp_path):
    p = suite.people
    alice = p.uid("alice")
    access, consents = AgentAccess(p.store), ProviderConsents(p.store)
    version = provider_terms("deepseek").version
    access.suspend(alice, p.uid("oscar"), reason="fixture: reported answers")
    consents.withdraw(alice, "deepseek")
    set_policy(suite, "any", ["anthropic", "deepseek"])
    set_policy(suite, "none", [])
    # Every withdrawal is undone afterwards, and the backup holds that.
    access.lift(alice, p.uid("oscar"), reason="fixture: review found nothing")
    consents.accept(alice, "deepseek", version)
    set_policy(suite, "any", ["anthropic", "deepseek"])
    at = backup_now(p.store, tmp_path / "later.sqlite3")
    restore(p.store, tmp_path / "later.sqlite3", at)
    assert access.active(alice) is None, "a lifted suspension stays lifted"
    assert consents.status(alice, "deepseek")["accepted"] is True, "a consent given again stands"
    now = listed(suite, p.a)
    assert (now["processing"], now["providers"]) == ("any", ["anthropic", "deepseek"]), "a re-widened policy stands"
    # A restore from before the narrowing applies it once; reopened after that restore, the policy
    # stays open through the next restore (the replay left its receipt in the restored database).
    early = tmp_path / "early.sqlite3"
    set_policy(suite, "any", ["anthropic", "deepseek"])
    early_at = backup_now(p.store, early)
    set_policy(suite, "none", [])
    restore(p.store, early, early_at)
    assert listed(suite, p.a)["processing"] == "none"
    set_policy(suite, "any", ["anthropic", "deepseek"])
    again = backup_now(p.store, tmp_path / "again.sqlite3")
    restore(p.store, tmp_path / "again.sqlite3", again)
    assert listed(suite, p.a)["processing"] == "any"
