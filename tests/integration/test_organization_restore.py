"""Real archives of synthetic governance state; no production data or mail."""
import hashlib
from types import SimpleNamespace

import pytest

from synwevo_core import journal
from synwevo_core.organizations import Organizations, accept_workspace_invitation, workspace_access_failure
from synwevo_core.store import Store
from tests.integration.test_security_journal import recovery, _backup, _restore, REASON


def setup_org(w):
    orgs = Organizations(w.store)
    result = orgs.create(w.a["id"], "Synthetic restored organization", "Owned workspace", "restore-org-fixture")
    oid, wid = result["organization"]["id"], result["workspace"]["id"]
    accept_workspace_invitation(w.store, wid, w.b["id"], w.a["id"])
    w.ident.add_member(wid, w.b["id"], "editor")
    return orgs, oid, wid


@pytest.mark.parametrize("action", ["freeze", "delete", "remove", "transfer", "policy"])
def test_real_archive_replays_org_withdrawals_and_keeps_ids(recovery, action):
    w = recovery
    orgs, oid, wid = setup_org(w)
    if action == "policy":
        orgs.change_role(oid, w.a["id"], w.b["id"], "admin", reason=REASON)
    original = b"Synthetic organization recovery fixture, not customer content."
    bid = w.store.put_blob(original)
    archive = _backup(w)
    if action in ("freeze", "delete"):
        orgs.transition(oid, w.a["id"], action, reason=REASON)
    elif action == "remove":
        orgs.remove_member(oid, w.a["id"], w.b["id"], reason=REASON)
    elif action == "transfer":
        orgs.transfer(oid, w.a["id"], w.b["id"], reason=REASON)
    else:
        orgs.update(oid, w.a["id"], invitation_policy="owners", reason=REASON)
    entries = journal.read(w.data)
    current = w.store.all("SELECT * FROM organizations")
    restored = _restore(w, archive)
    try:
        org = restored.one("SELECT * FROM organizations WHERE id=?", (oid,))
        assert restored.one("SELECT organization_id FROM workspaces WHERE id=?", (wid,))["organization_id"] == oid
        assert hashlib.sha256(restored.blob_path(bid).read_bytes()).hexdigest() == bid
        if action == "freeze":
            assert org["status"] == "frozen"
        elif action == "delete":
            live = w.store.one("SELECT * FROM organizations WHERE id=?", (oid,))
            assert org["status"] == "deleted"
            assert org["recover_until"] == live["recover_until"]
        elif action == "remove":
            assert restored.one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (wid,w.b["id"])) is None
            assert workspace_access_failure(restored, wid, w.b["id"]) == "not_an_organization_member"
        elif action == "transfer":
            assert org["status"] == "frozen"  # the backup never held the new owner's grant
            assert restored.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid,w.a["id"]))["role"] == "admin"
            assert restored.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid,w.b["id"]))["role"] == "member"
        else:
            assert org["invitation_policy"] == "owners"
        version = org["version"]
        journal.apply(restored, entries)
        assert restored.one("SELECT version FROM organizations WHERE id=?",(oid,))["version"] == version
        assert w.store.all("SELECT * FROM organizations") == current
        assert not restored.one("SELECT 1 FROM mail_outbox WHERE status IN ('pending','sending')")
    finally:
        w.snapshot._close(restored)


def test_backup_after_explicit_recovery_keeps_receipt_and_does_not_refreeze(recovery):
    w = recovery
    orgs, oid, wid = setup_org(w)
    orgs.transition(oid,w.a["id"],"freeze",reason=REASON)
    orgs.transition(oid,w.a["id"],"unfreeze",reason=REASON)
    restored = _restore(w, _backup(w))
    try:
        assert restored.one("SELECT status FROM organizations WHERE id=?", (oid,))["status"] == "active"
        assert workspace_access_failure(restored,wid,w.a["id"]) is None
    finally:
        w.snapshot._close(restored)


def test_journal_fsync_failure_aborts_live_org_withdrawal(recovery, monkeypatch):
    w = recovery
    orgs, oid, _ = setup_org(w)
    def fail(*args,**kwargs):
        raise OSError("synthetic journal unavailable")
    monkeypatch.setattr(journal,"record",fail)
    with pytest.raises(OSError):
        orgs.transition(oid,w.a["id"],"freeze",reason=REASON)
    assert orgs.get(oid,w.a["id"])["status"] == "active"
    assert not w.store.one("SELECT 1 FROM audit_events WHERE action='organization.freeze'")


def test_write_ahead_intent_before_backup_cutoff_still_applies_without_receipt(recovery):
    w = recovery
    orgs, oid, wid = setup_org(w)
    with pytest.raises(RuntimeError):
        with w.store.tx():
            journal.record_organization(w.store,"organization_frozen",organization_id=oid)
            raise RuntimeError("synthetic rollback after durable append")
    restored = _restore(w,_backup(w))
    try:
        assert restored.one("SELECT status FROM organizations WHERE id=?",(oid,))["status"] == "frozen"
    finally:
        w.snapshot._close(restored)


def test_backup_before_adoption_does_not_resurrect_old_workspace(recovery):
    w = recovery
    orgs, oid, _ = setup_org(w)
    archive = _backup(w)
    orgs.adopt_workspace(oid,w.a["id"],w.wid,reason=REASON)
    orgs.transition(oid,w.a["id"],"freeze",reason=REASON)
    restored = _restore(w,archive)
    try:
        ws = restored.one("SELECT organization_id,status FROM workspaces WHERE id=?",(w.wid,))
        assert ws == {"organization_id":None,"status":"frozen"}
    finally:
        w.snapshot._close(restored)
