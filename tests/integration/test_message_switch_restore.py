"""B3: Synwevo Message switched off for one organization survives a restore — from real archives of
synthetic state, never production data. A switch-off is a withdrawal written ahead
(`organization_app_disabled`, generic receipt), so:

* a backup from before it comes back with the organization off — and only that organization;
* a backup that holds its receipt and a later switch-on stays on (the switch-on is not journalled,
  and the line is skipped by its receipt);
* a backup taken between the line and the COMMIT holds neither the change nor the receipt, and is
  given the switch-off;
* an attempt whose journal append failed is voided: refused live, never replayed;
* the replay needs no organization row (the table has no foreign key), so a backup that predates
  the organization still restores.

The journal failure at the console (503, nothing changed) is
services/identity/tests/test_admin_apps_organizations.py; every generic case of the kind is
test_journal_intents.py."""
from __future__ import annotations

import pytest

from synwevo_core import journal
from synwevo_core.apps import MESSAGES_APP_ID, Apps
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.messaging import Messaging
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, now
from tests.integration.test_journal_intents import _copy_between_the_line_and_its_commit, _directory_fsync_fails_once, _lines
from tests.integration.test_security_journal import _backup, _restore, recovery  # noqa: F401


def _two_organizations(w):
    """A (owned by `a`, `b` a member) and B (owned by `b`), each with one group and one message."""
    orgs = Organizations(w.store, w.ident)
    oa = orgs.create(w.a["id"], "Synthetic switch organization A", "A space", "b3-restore-organization-a")["organization"]["id"]
    ob = orgs.create(w.b["id"], "Synthetic switch organization B", "B space", "b3-restore-organization-b")["organization"]["id"]
    w.store.insert("organization_memberships", {"organization_id": oa, "user_id": w.b["id"], "role": "member",
                                                "created_at": now(), "updated_at": now(), "created_by": w.a["id"]})
    svc = Messaging(w.store, w.ident)
    ga = svc.create_group(oa, w.a["id"], name="Synthetic A group", member_ids=[w.b["id"]])["id"]
    gb = svc.create_group(ob, w.b["id"], name="Synthetic B group", member_ids=[])["id"]
    svc.send(ga, w.a["id"], body="synthetic A message", client_id="b3-restore-a")
    svc.send(gb, w.b["id"], body="synthetic B message", client_id="b3-restore-b")
    return oa, ob, ga, gb


def _switch(store, oid):
    row = store.one("SELECT status FROM organization_apps WHERE organization_id=? AND app_id=?", (oid, MESSAGES_APP_ID))
    return row and row["status"]


def _refused(store, cid, uid):
    with pytest.raises(ServiceError) as e:
        Messaging(store, Identity(store)).get(cid, uid)
    return e.value.status, e.value.code, (e.value.detail or {}).get("scope")


def _history(store, cid):
    return store.all("SELECT id, seq, sender_id, body, deleted_at FROM messages WHERE conversation_id=? ORDER BY seq", (cid,))


def test_a_backup_from_before_the_switch_off_comes_back_with_that_organization_off_and_the_other_on(recovery):
    w = recovery
    oa, ob, ga, gb = _two_organizations(w)
    archive = _backup(w)
    assert Apps(w.store, w.ident).disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"]) is True
    (line,) = _lines(w, "organization_app_disabled")
    assert (line["organization_id"], line["app_id"]) == (oa, MESSAGES_APP_ID)
    restored = _restore(w, archive)
    try:
        assert _switch(restored, oa) == "disabled" and _switch(restored, ob) is None
        assert restored.one("SELECT updated_by FROM organization_apps WHERE organization_id=?", (oa,))["updated_by"] == "journal"
        assert restored.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],)), "replayed once, with its receipt"
        assert _refused(restored, ga, w.b["id"]) == (403, "messages_disabled", "organization")
        svc = Messaging(restored, Identity(restored))
        assert svc.get(gb, w.b["id"])["id"] == gb, "organization B is unaffected"
        assert [c["id"] for c in svc.list_conversations(w.b["id"])] == [gb]
        assert _history(restored, ga) == _history(w.store, ga), "nothing deleted"
        assert journal.apply(restored, journal.read(w.data)).get("organization_app_disabled", 0) == 0, "a second replay changes nothing"
    finally:
        w.snapshot._close(restored)


def test_a_backup_holding_the_receipt_and_a_later_switch_on_stays_on(recovery):
    w = recovery
    oa, ob, ga, gb = _two_organizations(w)
    apps = Apps(w.store, w.ident)
    apps.disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"])
    assert apps.enable_for_organization(oa, MESSAGES_APP_ID, w.a["id"]) is True
    restored = _restore(w, _backup(w))
    try:
        assert _switch(restored, oa) == "active", "the switch-on stands: the line is skipped by its receipt"
        assert Messaging(restored, Identity(restored)).get(ga, w.b["id"])["id"] == ga
        assert _history(restored, ga) == _history(w.store, ga), "history is as it was"
    finally:
        w.snapshot._close(restored)


def test_a_backup_taken_between_the_line_and_the_commit_is_given_the_switch_off(recovery, monkeypatch):
    w = recovery
    oa, ob, ga, gb = _two_organizations(w)
    mid = Store(w.tmp / "mid")
    try:
        with _copy_between_the_line_and_its_commit(w, monkeypatch, mid) as taken:
            Apps(w.store, w.ident).disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"])
        assert taken, "the line was appended inside the switch-off's transaction"
        (line,) = _lines(w, "organization_app_disabled")
        assert _switch(mid, oa) is None and not mid.one("SELECT 1 FROM meta WHERE key=?", ("journal_receipt:" + line["attempt_id"],))
        assert journal.apply(mid, journal.read(w.data, since=now() + 3600)).get("organization_app_disabled") == 1
        assert _switch(mid, oa) == "disabled" and _switch(mid, ob) is None
        assert _refused(mid, ga, w.b["id"]) == (403, "messages_disabled", "organization")
    finally:
        w.snapshot._close(mid)


def test_a_switch_off_whose_journal_append_failed_is_voided_and_never_replayed(recovery, monkeypatch):
    w = recovery
    oa, ob, ga, gb = _two_organizations(w)
    with _directory_fsync_fails_once(monkeypatch):
        with pytest.raises(OSError):
            Apps(w.store, w.ident).disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"])
    assert _switch(w.store, oa) is None, "rolled back: Message stays on"
    (line,) = _lines(w, "organization_app_disabled")
    (void,) = _lines(w, journal.VOID_KIND)
    assert (void["attempt_id"], void["voided_kind"]) == (line["attempt_id"], "organization_app_disabled")
    restored = _restore(w, _backup(w))
    try:
        assert _switch(restored, oa) is None, "the void keeps the failed attempt out of the restore"
        assert Messaging(restored, Identity(restored)).get(ga, w.b["id"])["id"] == ga
    finally:
        w.snapshot._close(restored)


def test_the_replay_needs_no_organization_row(recovery):
    """A backup that predates the organization: the switch-off is replayed into it all the same
    (no foreign key), so the restore neither fails nor reopens anything."""
    w = recovery
    archive = _backup(w)
    oa, ob, ga, gb = _two_organizations(w)
    Apps(w.store, w.ident).disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"])
    restored = _restore(w, archive)
    try:
        assert restored.one("SELECT 1 FROM organizations WHERE id=?", (oa,)) is None
        assert _switch(restored, oa) == "disabled"
    finally:
        w.snapshot._close(restored)


def test_an_older_restore_refuses_the_new_kind_rather_than_skipping_it(recovery):
    """Release note: restore code that predates B3 does not know `organization_app_disabled`. It
    must fail closed (refuse the journal) — never apply it as a silent no-op, which is why the kind
    is not `workspace_app_disabled`. Simulated by the current code's own rule for an unknown kind."""
    w = recovery
    oa, *_ = _two_organizations(w)
    Apps(w.store, w.ident).disable_for_organization(oa, MESSAGES_APP_ID, w.a["id"])
    (line,) = _lines(w, "organization_app_disabled")
    unknown = {**line, "kind": "organization_app_disabled_from_a_later_release"}
    copy = Store(w.tmp / "older")
    try:
        with pytest.raises(Exception):
            journal.apply(copy, [unknown])
        assert _switch(copy, oa) is None
    finally:
        w.snapshot._close(copy)
