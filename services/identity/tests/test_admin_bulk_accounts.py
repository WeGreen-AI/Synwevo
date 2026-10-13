"""Bulk account administration (A18) through the real isolated identity application.

Every identity, grant, workspace, session and token below is a synthetic fixture in a data
directory under tmp_path; no mail worker runs and no production configuration or data is used.
The one stand-in is the crash simulation, which replaces one account's apply step with a raising
wrapper for one request and puts the real one back before the retry.
"""
from __future__ import annotations

import json
import types
import uuid

import pytest

from synwevo_core import journal, oidc, platform_access
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import now
from synwevo_identity import admin_bulk
from synwevo_identity.admin_bulk import REFUSAL_MESSAGES, BulkAccountOperations

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, REASON, post, world  # noqa: F401 -- shared isolated fixture

PREVIEW, BULK = "/api/admin/users/bulk-preview", "/api/admin/users/bulk"
MISSING = "u_fixture_missing"


def account(w, email, *, status="active", login=False):
    """A verified fixture account, optionally signed in (one live session)."""
    user = w["ident"].create_user(email, PW, status="active")
    w["store"].execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
    client = w["login"](user) if login else None
    if status != "active":
        w["store"].execute("UPDATE users SET status=? WHERE id=?", (status, user["id"]))
    return {**w["ident"].get_user(user["id"]), "client": client}


def fixture_token(w, uid, ref):
    """A synthetic, never-issued OAuth token row for the account."""
    raw, ts = f"fixture-bulk-token-{ref}", now()
    w["store"].execute("INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,issued_at,"
                       "access_expires_at,refresh_expires_at,family_id) VALUES (?,?,?,?,?,'openid',?,?,?,?)",
                       (f"fixture-token-{ref}", "docweave-web", uid, oidc._sha(raw), oidc._sha(raw + "-refresh"),
                        ts, ts + 900, ts + 3600, f"fixture-token-{ref}"))
    return raw


def sole_owner(w, email):
    """An account that is the only owner of a shared (org-kind) workspace with another member."""
    owner = account(w, email)
    member = account(w, "member-of-" + email)
    wid = w["ident"].create_workspace("Fixture shared space", owner_id=owner["id"], kind="org")["id"]
    w["ident"].add_member(wid, member["id"], "viewer")
    return owner


def preview(w, operation, ids, *, client=None):
    response = post(client or w["admin"], PREVIEW, {"operation": operation, "user_ids": ids})
    assert response.status_code == 200, response.text
    return response.json()


def command(plan, *, key=None, reason=REASON):
    return {"operation": plan["operation"], "reason": reason, "preview_token": plan["preview_token"],
            "idempotency_key": key or uuid.uuid4().hex}


def execute(w, body, *, client=None):
    return post(client or w["admin"], BULK, body)


def audits(w, action, **where):
    rows = w["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (action,))
    return [row for row in rows if all(row[k] == v for k, v in where.items())]


def journal_events(w, kind):
    return [entry for entry in journal.read(w["store"].root) if entry["kind"] == kind]


def single_refusal(w, uid, client=None):
    """What the single suspension preview answers for this account (drift guard for messages)."""
    return post(client or w["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"}).json()


@pytest.fixture()
def mixed(world):
    """Two ordinary accounts, a platform-grant holder, the actor itself, the sole owner of a shared
    workspace, an unknown id, an already suspended account and a duplicate — in that order."""
    w = world
    second = account(w, "second@example.test", login=True)
    fixture_token(w, second["id"], "second")
    granted = make_platform_admin(w["store"], account(w, "granted@example.test")["id"], role="support")
    owner = sole_owner(w, "owner@example.test")
    suspended = account(w, "suspended@example.test", status="suspended")
    ids = [w["target"]["id"], second["id"], granted["id"], w["root"]["id"], owner["id"], MISSING,
           suspended["id"], w["target"]["id"]]
    expected = [(w["target"]["id"], None), (second["id"], None), (granted["id"], "demote_first"),
                (w["root"]["id"], "self_suspend"), (owner["id"], "ownership_required"), (MISSING, "no_user"),
                (suspended["id"], "account_state_conflict")]
    return {**w, "second": second, "granted": granted, "owner": owner, "suspended": suspended,
            "ids": ids, "expected": expected}


# ---------------------------------------------------------------- (a) the preview
def test_suspend_preview_reports_every_account_in_order_with_the_single_refusal(mixed):
    w = mixed
    plan = preview(w, "suspend", w["ids"])
    assert plan["operation"] == "suspend" and plan["scope"] == "platform" and plan["risk"] == "high"
    assert [(item["id"], item["code"]) for item in plan["items"]] == w["expected"], "submitted order, duplicate collapsed"
    assert plan["total"] == 7 and plan["eligible"] == 2
    assert [item["eligible"] for item in plan["items"]] == [True, True, False, False, False, False, False]
    by_id = {item["id"]: item for item in plan["items"]}
    assert by_id[w["target"]["id"]] == {"id": w["target"]["id"], "email": "target@example.test", "status": "active",
                                        "eligible": True, "code": None, "sessions": 1, "tokens": 0}
    assert by_id[w["second"]["id"]]["sessions"] == 1 and by_id[w["second"]["id"]]["tokens"] == 1
    assert by_id[MISSING] == {"id": MISSING, "email": None, "status": None, "eligible": False, "code": "no_user",
                              "sessions": 0, "tokens": 0}
    assert by_id[w["suspended"]["id"]]["status"] == "suspended"
    assert plan["impact"] == {"sessions": 2, "tokens": 1}, "summed over the eligible accounts only"
    assert plan["preview_token"] and plan["expires_at"] > now()
    # A preview changes nothing and records nothing.
    store = w["store"]
    assert {u["status"] for u in (w["ident"].get_user(w["target"]["id"]), w["ident"].get_user(w["second"]["id"]))} == {"active"}
    assert w["ordinary"].get("/api/auth/me").status_code == 200
    assert not store.all("SELECT * FROM admin_operations")
    assert not audits(w, "admin.user.status") and not audits(w, "admin.user.bulk")
    assert not journal.read(store.root)
    # Each refusal is the one the single route gives the same account now.
    for uid, code in w["expected"][2:]:
        assert single_refusal(w, uid)["error"] == code, uid


# ---------------------------------------------------------------- (b) the confirmation
def test_execute_suspends_only_the_eligible_accounts_each_as_the_single_act(mixed):
    w, store = mixed, mixed["store"]
    plan = preview(w, "suspend", w["ids"])
    body = command(plan)
    response = execute(w, body)
    assert response.status_code == 200, response.text
    out = response.json()
    batch = out["operation_id"]
    assert out["operation"] == "suspend" and out["ok"] is False and out["succeeded"] == 2 and out["failed"] == 5
    assert [(r["id"], r["ok"], r["code"]) for r in out["results"]] == [
        (uid, code is None, code or "ok") for uid, code in w["expected"]]
    done = {r["id"]: r for r in out["results"] if r["ok"]}
    assert done[w["target"]["id"]]["sessions_revoked"] == 1 and done[w["target"]["id"]]["tokens_revoked"] == 0
    assert done[w["second"]["id"]]["sessions_revoked"] == 1 and done[w["second"]["id"]]["tokens_revoked"] == 1
    assert all(r["message"] == "账户已停用" for r in done.values())
    # Exactly the eligible accounts are suspended, their sessions end and the next request is refused.
    eligible = [w["target"]["id"], w["second"]["id"]]
    for uid in eligible:
        assert w["ident"].get_user(uid)["status"] == "suspended"
        assert not store.one("SELECT 1 FROM sessions WHERE user_id=? AND revoked_at IS NULL", (uid,))
        assert not store.one("SELECT 1 FROM oauth_tokens WHERE user_id=? AND revoked_at IS NULL", (uid,))
    assert w["ordinary"].get("/api/auth/me").status_code == 401
    assert w["second"]["client"].get("/api/auth/me").status_code == 401
    for uid in (w["granted"]["id"], w["owner"]["id"]):
        assert w["ident"].get_user(uid)["status"] == "active"
    assert w["ident"].get_user(w["suspended"]["id"])["status"] == "suspended"
    assert w["admin"].get("/api/admin/overview").status_code == 200
    # One admin.user.status row per suspended account, carrying the batch; nothing for the
    # accounts refused at preview (reported, never attempted).
    rows = audits(w, "admin.user.status")
    assert sorted(row["object_id"] for row in rows) == sorted(eligible) and {row["result"] for row in rows} == {"ok"}
    for row in rows:
        meta = json.loads(row["meta"])
        assert meta["batch_id"] == batch and meta["reason"] == REASON and meta["changed"] == ["status"]
        assert row["actor_id"] == w["root"]["id"] and row["workspace_id"] is None
    events = journal_events(w, "user_suspended")
    assert sorted(e["user_id"] for e in events) == sorted(eligible)
    # The per-account receipts and the batch's own result.
    receipts = store.all("SELECT * FROM admin_operations WHERE action='admin.user.status' ORDER BY target_id")
    assert sorted((r["target_id"], r["idempotency_key"]) for r in receipts) == sorted(
        (uid, f"{body['idempotency_key']}:{uid}") for uid in eligible)
    assert {e["account_event_id"] for e in events} == {r["id"] for r in receipts}
    record = store.one("SELECT * FROM admin_operations WHERE action='admin.user.bulk'")
    assert record["id"] == batch and record["idempotency_key"] == body["idempotency_key"]
    assert json.loads(record["result_json"]) == out
    (bulk_row,) = audits(w, "admin.user.bulk")
    meta = json.loads(bulk_row["meta"])
    assert bulk_row["result"] == "ok" and bulk_row["object_id"] == batch
    assert {k: meta[k] for k in ("operation", "total", "succeeded", "failed", "reason", "operation_id")} == {
        "operation": "suspend", "total": 7, "succeeded": 2, "failed": 5, "reason": REASON, "operation_id": batch}
    assert meta["codes"] == {"ok": 2, "demote_first": 1, "self_suspend": 1, "ownership_required": 1,
                             "no_user": 1, "account_state_conflict": 1}
    # Server sentences only: each refusal reads as the single route's answer, never exception text.
    for result in out["results"]:
        if not result["ok"]:
            assert result["message"] == REFUSAL_MESSAGES[result["code"]]
    for uid, code in w["expected"][2:]:
        refusal = single_refusal(w, uid)
        assert (refusal["error"], refusal["message"]) == (code, REFUSAL_MESSAGES[code]), uid
    assert "Traceback" not in response.text and "Error" not in response.text


# ---------------------------------------------------------------- (c) changes between preview and confirmation
def test_an_account_that_changed_after_preview_is_refused_alone(world):
    w, store = world, world["store"]
    renewed, promoted, steady = (account(w, f"{name}@example.test", login=True) for name in ("renewed", "promoted", "steady"))
    plan = preview(w, "suspend", [renewed["id"], promoted["id"], steady["id"]])
    assert plan["eligible"] == 3
    Sessions(store).create(renewed["id"], workspace_id=None, amr="password")
    make_platform_admin(store, promoted["id"], role="auditor")
    out = execute(w, command(plan)).json()
    assert [(r["id"], r["code"]) for r in out["results"]] == [
        (renewed["id"], "admin_preview_stale"), (promoted["id"], "demote_first"), (steady["id"], "ok")]
    assert out["succeeded"] == 1 and out["failed"] == 2
    assert [w["ident"].get_user(u["id"])["status"] for u in (renewed, promoted, steady)] == ["active", "active", "suspended"]
    assert renewed["client"].get("/api/auth/me").status_code == 200
    assert steady["client"].get("/api/auth/me").status_code == 401
    # Each refusal is recorded the way the single confirmation records it, with the batch.
    denied = {row["object_id"]: json.loads(row["meta"]) for row in audits(w, "admin.user.status", result="denied")}
    assert {uid: meta["code"] for uid, meta in denied.items()} == {renewed["id"]: "admin_preview_stale",
                                                                   promoted["id"]: "demote_first"}
    assert all(meta["phase"] == "execute" and meta["batch_id"] == out["operation_id"] and meta["reason"] == REASON
               for meta in denied.values())
    assert [e["user_id"] for e in journal_events(w, "user_suspended")] == [steady["id"]]


def test_an_account_refused_at_preview_is_never_acted_on_even_once_eligible(world):
    w = world
    later = account(w, "later@example.test", status="suspended")
    plan = preview(w, "suspend", [later["id"], w["target"]["id"]])
    assert [item["code"] for item in plan["items"]] == ["account_state_conflict", None]
    w["store"].execute("UPDATE users SET status='active' WHERE id=?", (later["id"],))
    out = execute(w, command(plan)).json()
    assert [(r["id"], r["ok"], r["code"]) for r in out["results"]] == [
        (later["id"], False, "account_state_conflict"), (w["target"]["id"], True, "ok")]
    assert w["ident"].get_user(later["id"])["status"] == "active"


def co_owners(w, scope, n, *, staying=True):
    """`n` fixture accounts that own one workspace (or organization) together, plus — when
    `staying` — one more owner who is not in the batch. For an organization the staying owner (or,
    without one, the first co-owner) creates it and so also owns its initial workspace."""
    owners = [account(w, f"{scope}-owner-{i}@example.test") for i in range(n)]
    stay = account(w, f"{scope}-staying-owner@example.test") if staying else None
    creator = stay or owners[0]
    if scope == "workspace":
        wid = w["ident"].create_workspace("Fixture shared space", owner_id=creator["id"], kind="org")["id"]
        for owner in owners:
            if owner is not creator:
                w["ident"].add_member(wid, owner["id"], "owner")
        return owners, stay, ("memberships", "workspace_id", wid)
    from synwevo_core.organizations import Organizations
    created = Organizations(w["store"]).create(creator["id"], "Fixture organization", "Fixture org space",
                                              f"bulk-fixture-{n}-{int(staying)}")
    oid = created["organization"]["id"]
    for owner in owners:
        if owner is not creator:
            w["store"].insert("organization_memberships", {"organization_id": oid, "user_id": owner["id"],
                                                           "role": "owner", "created_by": creator["id"],
                                                           "created_at": now(), "updated_at": now()})
            w["ident"].add_member(created["workspace"]["id"], owner["id"], "owner")
    return owners, stay, ("organization_memberships", "organization_id", oid)


@pytest.mark.parametrize("scope", ["workspace", "organization"])
def test_co_owners_leaving_together_are_judged_in_order_and_go_in_one_round(world, scope):
    """The preview judges each owner after the earlier ones are suspended (a rolled-back savepoint),
    which is what the confirmation meets: every digest matches, nobody is stale."""
    w = world
    owners, stay, _ = co_owners(w, scope, 3)
    plan = preview(w, "suspend", [owner["id"] for owner in owners])
    assert plan["eligible"] == 3 and [item["code"] for item in plan["items"]] == [None, None, None]
    assert [item["status"] for item in plan["items"]] == ["active"] * 3, "the preview reports the accounts as they are"
    assert [w["ident"].get_user(owner["id"])["status"] for owner in owners] == ["active"] * 3, "the simulation rolled back"
    out = execute(w, command(plan)).json()
    assert [r["code"] for r in out["results"]] == ["ok", "ok", "ok"], out
    assert [w["ident"].get_user(owner["id"])["status"] for owner in owners] == ["suspended"] * 3
    assert w["ident"].get_user(stay["id"])["status"] == "active"


def test_a_member_and_an_owner_go_together_while_another_owner_stays(world):
    w = world
    (owner,), stay, (_, _, wid) = co_owners(w, "workspace", 1)
    viewer = account(w, "viewer@example.test")
    w["ident"].add_member(wid, viewer["id"], "viewer")
    plan = preview(w, "suspend", [viewer["id"], owner["id"]])
    assert plan["eligible"] == 2
    out = execute(w, command(plan)).json()
    assert [r["code"] for r in out["results"]] == ["ok", "ok"]
    assert w["ident"].get_user(stay["id"])["status"] == "active"


@pytest.mark.parametrize("scope", ["workspace", "organization"])
def test_the_last_two_owners_cannot_both_go_in_one_batch(world, scope):
    w = world
    (first, second), _, _ = co_owners(w, scope, 2, staying=False)
    error = "ownership_required" if scope == "workspace" else "organization_ownership_required"
    plan = preview(w, "suspend", [first["id"], second["id"]])
    assert [item["code"] for item in plan["items"]] == [None, error], "the second is judged after the first is gone"
    assert plan["eligible"] == 1
    out = execute(w, command(plan)).json()
    assert [(r["id"], r["code"]) for r in out["results"]] == [(first["id"], "ok"), (second["id"], error)]
    assert out["results"][1]["message"] == REFUSAL_MESSAGES[error]
    assert w["ident"].get_user(second["id"])["status"] == "active"
    assert not audits(w, "admin.user.status", object_id=second["id"]), "refused at preview: never attempted"


def test_the_preview_savepoint_needs_the_callers_transaction(world):
    from synwevo_identity.admin_bulk import _discarded
    with world["store"].conn() as connection, pytest.raises(RuntimeError):
        with _discarded(connection):
            pass


# ---------------------------------------------------------------- (d) idempotency and crash safety
def test_the_same_key_returns_the_stored_batch_and_another_request_conflicts(world, monkeypatch):
    w, store = world, world["store"]
    other = account(w, "other@example.test")
    plan = preview(w, "suspend", [w["target"]["id"], other["id"]])
    body = command(plan)
    first = execute(w, body)
    assert first.status_code == 200, first.text
    counts = (len(store.all("SELECT * FROM audit_events")), len(journal.read(store.root)),
              len(store.all("SELECT * FROM admin_operations")))
    # Past the preview's expiry too: the stored result is the answer, nothing is re-applied.
    monkeypatch.setattr(admin_bulk, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    again = execute(w, {**body, "reason": "  " + REASON + "  "})
    assert again.status_code == 200 and again.json() == first.json()
    assert (len(store.all("SELECT * FROM audit_events")), len(journal.read(store.root)),
            len(store.all("SELECT * FROM admin_operations"))) == counts
    for change in ("reason", "preview_token", "operation"):
        altered = {**body, change: {"reason": REASON + " changed", "preview_token": "another-preview",
                                    "operation": "revoke_sessions"}[change]}
        response = execute(w, altered)
        assert response.status_code == 409 and response.json()["error"] == "idempotency_conflict", (change, response.text)
    assert len(audits(w, "admin.user.status", result="ok")) == 2 and len(audits(w, "admin.user.bulk", result="ok")) == 1


def crash_on(monkeypatch, uid):
    """For the next requests: the batch's apply step raises at `uid` — a stand-in for the process
    stopping there. Returns the real step, which the test puts back with setattr."""
    original = BulkAccountOperations._apply_one

    def crashing(self, request, operation, target, *args, **kwargs):
        if target == uid:
            raise RuntimeError("fixture: the process stopped here")
        return original(self, request, operation, target, *args, **kwargs)

    monkeypatch.setattr(BulkAccountOperations, "_apply_one", crashing)
    return original


def batch_row(w):
    row = w["store"].one("SELECT * FROM admin_operations WHERE action='admin.user.bulk'")
    return row and {**row, "result": json.loads(row["result_json"])}


def test_a_retry_after_a_crash_resumes_the_running_batch_and_skips_the_accounts_already_done(world, monkeypatch):
    w, store = world, world["store"]
    second, third = account(w, "second@example.test", login=True), account(w, "third@example.test")
    ids = [w["target"]["id"], second["id"], third["id"]]
    body = command(preview(w, "suspend", ids))
    original = crash_on(monkeypatch, second["id"])
    crashed = execute(w, body)
    assert crashed.status_code == 500 and "fixture" not in crashed.text
    assert [w["ident"].get_user(uid)["status"] for uid in ids] == ["suspended", "active", "active"]
    assert [e["user_id"] for e in journal_events(w, "user_suspended")] == [w["target"]["id"]]
    # The key was reserved before the first account: the batch row is there, still running.
    running = batch_row(w)
    batch = running["id"]
    assert running["result"] == {"state": "running", "operation_id": batch, "operation": "suspend"}
    assert running["idempotency_key"] == body["idempotency_key"]
    (applied,) = audits(w, "admin.user.status", result="ok")
    assert json.loads(applied["meta"])["batch_id"] == batch
    (failed_item,) = audits(w, "admin.user.status", result="error")
    assert failed_item["object_id"] == second["id"] and json.loads(failed_item["meta"])["batch_id"] == batch
    (failed_batch,) = audits(w, "admin.user.bulk", result="error")
    assert failed_batch["object_id"] == batch
    assert {k: json.loads(failed_batch["meta"])[k] for k in ("code", "operation_id", "phase")} == {
        "code": "admin_operation_failed", "operation_id": batch, "phase": "execute"}

    monkeypatch.setattr(BulkAccountOperations, "_apply_one", original)
    retried = execute(w, body)
    assert retried.status_code == 200, retried.text
    out = retried.json()
    assert out["operation_id"] == batch, "the retry resumes the running batch under its id"
    assert [(r["id"], r["ok"], r["code"]) for r in out["results"]] == [
        (w["target"]["id"], True, "already_applied"), (second["id"], True, "ok"), (third["id"], True, "ok")]
    assert out["results"][0]["sessions_revoked"] == 1
    assert [w["ident"].get_user(uid)["status"] for uid in ids] == ["suspended"] * 3
    assert sorted(e["user_id"] for e in journal_events(w, "user_suspended")) == sorted(ids), "no second journal entry"
    assert len(audits(w, "admin.user.status", result="ok")) == 3
    assert second["client"].get("/api/auth/me").status_code == 401
    assert batch_row(w)["result"] == out and len(audits(w, "admin.user.bulk", result="ok")) == 1
    # And now the batch is finished: the same request returns it verbatim.
    assert execute(w, body).json() == out


def test_another_request_under_a_running_batchs_key_conflicts_before_doing_anything(world, monkeypatch):
    w, store = world, world["store"]
    first, second = account(w, "first@example.test", login=True), account(w, "second@example.test")
    ids = [first["id"], second["id"]]
    body = command(preview(w, "suspend", ids))
    original = crash_on(monkeypatch, first["id"])
    assert execute(w, body).status_code == 500
    monkeypatch.setattr(BulkAccountOperations, "_apply_one", original)
    batch = batch_row(w)["id"]
    assert batch_row(w)["result"]["state"] == "running"
    for altered in ({**body, "reason": REASON + " but different"}, {**body, "operation": "revoke_sessions"}):
        conflict = execute(w, altered)
        assert conflict.status_code == 409 and conflict.json()["error"] == "idempotency_conflict", conflict.text
    assert [w["ident"].get_user(uid)["status"] for uid in ids] == ["active", "active"]
    assert first["client"].get("/api/auth/me").status_code == 200
    assert not journal.read(store.root) and not audits(w, "admin.user.status", result="ok")
    assert not audits(w, "admin.user.revoke_sessions")
    # The request that reserved the key still resumes and finishes it.
    out = execute(w, body).json()
    assert out["operation_id"] == batch and [r["code"] for r in out["results"]] == ["ok", "ok"]


def test_a_batch_resumed_after_its_preview_expired_is_finished_without_acting(world, monkeypatch):
    w = world
    second, third = account(w, "second@example.test"), account(w, "third@example.test")
    ids = [w["target"]["id"], second["id"], third["id"]]
    plan = preview(w, "suspend", ids)
    body = command(plan)
    original = crash_on(monkeypatch, second["id"])
    assert execute(w, body).status_code == 500
    monkeypatch.setattr(BulkAccountOperations, "_apply_one", original)
    monkeypatch.setattr(admin_bulk, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    out = execute(w, body)
    assert out.status_code == 200, out.text
    out = out.json()
    assert [(r["id"], r["code"]) for r in out["results"]] == [
        (w["target"]["id"], "already_applied"), (second["id"], "admin_preview_expired"),
        (third["id"], "admin_preview_expired")]
    assert [w["ident"].get_user(uid)["status"] for uid in ids] == ["suspended", "active", "active"]
    assert batch_row(w)["result"] == out
    denied = {row["object_id"]: json.loads(row["meta"])["code"] for row in audits(w, "admin.user.status", result="denied")}
    assert denied == {second["id"]: "admin_preview_expired", third["id"]: "admin_preview_expired"}


def test_a_revocation_crash_and_retry_skips_the_accounts_already_done(world, monkeypatch):
    w, store = world, world["store"]
    people = [account(w, f"person-{i}@example.test", login=True) for i in range(3)]
    fixture_token(w, people[0]["id"], "person-0")
    ids = [person["id"] for person in people]
    body = command(preview(w, "revoke_sessions", ids))
    original = crash_on(monkeypatch, ids[1])
    assert execute(w, body).status_code == 500
    assert [e["user_id"] for e in journal_events(w, "sessions_revoked")] == [ids[0]]
    assert people[0]["client"].get("/api/auth/me").status_code == 401
    assert people[1]["client"].get("/api/auth/me").status_code == 200
    monkeypatch.setattr(BulkAccountOperations, "_apply_one", original)
    out = execute(w, body)
    assert out.status_code == 200, out.text
    out = out.json()
    assert [(r["id"], r["code"], r["sessions_revoked"], r["tokens_revoked"]) for r in out["results"]] == [
        (ids[0], "already_applied", 1, 1), (ids[1], "ok", 1, 0), (ids[2], "ok", 1, 0)]
    assert sorted(e["user_id"] for e in journal_events(w, "sessions_revoked")) == sorted(ids), "one entry per account"
    assert len(audits(w, "admin.user.revoke_sessions", result="ok")) == 3
    receipts = store.all("SELECT * FROM admin_operations WHERE action='admin.user.revoke_sessions'")
    assert sorted(r["idempotency_key"] for r in receipts) == sorted(f"{body['idempotency_key']}:{uid}" for uid in ids)
    assert all(json.loads(r["result_json"])["batch_id"] == out["operation_id"] for r in receipts)
    assert all(person["client"].get("/api/auth/me").status_code == 401 for person in people)


@pytest.mark.parametrize("left_with, code", [(None, "not_found"), ("auditor", "platform_permission_denied")])
def test_an_actor_who_loses_authority_mid_batch_stops_it_there(world, monkeypatch, left_with, code):
    """The scheduling hook changes the actor's grants (real writes) just before the second account's
    transaction; that transaction's own re-admission refuses, and nothing after it is attempted."""
    w, store = world, world["store"]
    actor = make_platform_admin(store, account(w, "access@example.test")["id"], role="access_admin")
    client = w["login"](actor)
    w["reauth"](client)
    first, second, third = (account(w, f"{name}@example.test") for name in ("first", "second", "third"))
    body = command(preview(w, "suspend", [first["id"], second["id"], third["id"]], client=client))
    original = BulkAccountOperations._apply_one

    def demoted_before_second(self, request, operation, uid, *args, **kwargs):
        if uid == second["id"]:
            end_platform_grants(store, actor)
            if left_with:
                make_platform_admin(store, actor["id"], role=left_with)
        return original(self, request, operation, uid, *args, **kwargs)

    monkeypatch.setattr(BulkAccountOperations, "_apply_one", demoted_before_second)
    response = execute(w, body, client=client)
    monkeypatch.setattr(BulkAccountOperations, "_apply_one", original)
    assert response.status_code == 200, response.text
    out = response.json()
    assert [(r["id"], r["code"]) for r in out["results"]] == [(first["id"], "ok"), (second["id"], code), (third["id"], code)]
    assert [w["ident"].get_user(u["id"])["status"] for u in (first, second, third)] == ["suspended", "active", "active"]
    assert [row["object_id"] for row in audits(w, "admin.user.status", result="ok")] == [first["id"]]
    # The account the batch stopped at is recorded as its single confirmation would record it.
    (stopped,) = audits(w, "admin.user.status", result="denied")
    assert stopped["object_id"] == second["id"]
    assert json.loads(stopped["meta"])["code"] == code and json.loads(stopped["meta"])["batch_id"] == out["operation_id"]
    # A missing permission keeps the platform's own denied row, which rolled back with the account.
    permission_rows = audits(w, "admin.access.denied", actor_id=actor["id"], object_id="platform.users.manage")
    assert bool(permission_rows) == (code == "platform_permission_denied")
    if permission_rows:
        assert json.loads(permission_rows[0]["meta"])["code"] == "platform_permission_denied"
    (row,) = audits(w, "admin.user.bulk")
    assert json.loads(row["meta"])["codes"] == {code: 2, "ok": 1}


# ---------------------------------------------------------------- (e) bulk session revocation
def test_support_revokes_sessions_in_bulk_and_cannot_reach_a_higher_account(world):
    w, store = world, world["store"]
    support_user = make_platform_admin(store, account(w, "support@example.test")["id"], role="support")
    support = w["login"](support_user)
    w["reauth"](support)
    first, second = account(w, "first@example.test", login=True), account(w, "second@example.test", login=True)
    raw = fixture_token(w, first["id"], "first")
    access_user = make_platform_admin(store, account(w, "access@example.test")["id"], role="access_admin")
    ids = [first["id"], w["root"]["id"], second["id"], access_user["id"]]
    plan = preview(w, "revoke_sessions", ids, client=support)
    assert [(item["id"], item["code"]) for item in plan["items"]] == [
        (first["id"], None), (w["root"]["id"], "target_outranks_actor"), (second["id"], None),
        (access_user["id"], "target_outranks_actor")]
    assert plan["eligible"] == 2 and plan["impact"] == {"sessions": 2, "tokens": 1}
    assert not audits(w, "admin.access.denied"), "a preview reports refusals; it does not attempt them"
    # After the preview, the second account is raised above the support holder.
    make_platform_admin(store, second["id"], role="access_admin")
    out = execute(w, command(plan), client=support)
    assert out.status_code == 200, out.text
    out = out.json()
    assert [(r["id"], r["ok"], r["code"]) for r in out["results"]] == [
        (first["id"], True, "ok"), (w["root"]["id"], False, "target_outranks_actor"),
        (second["id"], False, "target_outranks_actor"), (access_user["id"], False, "target_outranks_actor")]
    assert out["results"][0]["sessions_revoked"] == 1 and out["results"][0]["tokens_revoked"] == 1
    assert out["results"][0]["message"] == "已撤销该账户的全部会话"
    assert first["client"].get("/api/auth/me").status_code == 401
    assert oidc.Grants(store).load_access(raw) is None
    assert second["client"].get("/api/auth/me").status_code == 200
    assert w["admin"].get("/api/admin/overview").status_code == 200, "the super administrator is untouched"
    assert w["ident"].get_user(first["id"])["status"] == "active", "revocation ends sessions, not the account"
    (row,) = audits(w, "admin.user.revoke_sessions")
    meta = json.loads(row["meta"])
    assert row["object_id"] == first["id"] and meta["batch_id"] == out["operation_id"]
    assert meta["sessions"] == 1 and meta["tokens"] == 1 and meta["reason"] == REASON
    assert [e["user_id"] for e in journal_events(w, "sessions_revoked")] == [first["id"]]
    receipt = store.one("SELECT * FROM admin_operations WHERE action='admin.user.revoke_sessions'")
    assert receipt["target_id"] == first["id"] and receipt["actor_id"] == support_user["id"]
    assert journal_events(w, "sessions_revoked")[0]["account_event_id"] == receipt["id"]
    # The refusal at confirmation is recorded as the single route records it.
    denied = audits(w, "admin.access.denied", object_id=second["id"])
    assert denied and json.loads(denied[-1]["meta"])["code"] == "target_outranks_actor"
    # The sentence is the single route's for the same refusal.
    single = post(support, f"/api/admin/users/{w['root']['id']}/revoke-sessions", {"reason": REASON}).json()
    assert (single["error"], single["message"]) == ("target_outranks_actor", out["results"][1]["message"])


def test_revoking_the_actors_own_sessions_in_a_batch_comes_last(world):
    w = world
    other = account(w, "other@example.test", login=True)
    plan = preview(w, "revoke_sessions", [w["root"]["id"], other["id"]])
    assert plan["eligible"] == 2
    out = execute(w, command(plan))
    assert out.status_code == 200, out.text
    assert [(r["id"], r["code"]) for r in out.json()["results"]] == [(w["root"]["id"], "ok"), (other["id"], "ok")]
    assert other["client"].get("/api/auth/me").status_code == 401
    assert w["admin"].get("/api/auth/me").status_code == 401
    assert [e["user_id"] for e in journal_events(w, "sessions_revoked")] == [other["id"], w["root"]["id"]]


# ---------------------------------------------------------------- (f) permissions and re-authentication
def test_permission_is_the_operations_and_the_act_needs_a_strict_reauthentication(world):
    w, store = world, world["store"]
    support = w["login"](make_platform_admin(store, account(w, "support@example.test")["id"], role="support"))
    auditor = w["login"](make_platform_admin(store, account(w, "auditor@example.test")["id"], role="auditor"))
    w["reauth"](support)
    w["reauth"](auditor)
    ids = [w["target"]["id"]]
    for client, operation, permission in ((support, "suspend", "platform.users.manage"),
                                          (auditor, "suspend", "platform.users.manage"),
                                          (auditor, "revoke_sessions", "platform.users.support")):
        for path, body in ((PREVIEW, {"operation": operation, "user_ids": ids}),
                           (BULK, {"operation": operation, "reason": REASON, "preview_token": "fixture",
                                   "idempotency_key": uuid.uuid4().hex})):
            response = post(client, path, body)
            assert response.status_code == 403 and response.json()["error"] == "platform_permission_denied", response.text
            assert response.json()["detail"]["permission"] == permission
    # A support holder may preview a revocation, but not a suspension, and gets no suspend token.
    assert post(support, PREVIEW, {"operation": "revoke_sessions", "user_ids": ids}).status_code == 200
    # An account without any platform permission meets the missing page, whatever it sends.
    for body in ({"operation": "suspend", "user_ids": ids}, {"operation": "nonsense"}, {}):
        for path in (PREVIEW, BULK):
            response = post(w["ordinary"], path, body)
            assert response.status_code == 404 and response.json()["error"] == "not_found"
    # The act asks for the strict re-authentication before anything else in the body is judged.
    fresh = w["login"](w["root"])
    for body in ({"operation": "suspend"}, {"operation": "revoke_sessions", "reason": "short"},
                 {"operation": "unknown"}):
        response = post(fresh, BULK, body)
        assert response.status_code == 403 and response.json()["error"] == "reauth_required", (body, response.text)
    assert w["ident"].get_user(w["target"]["id"])["status"] == "active"


def test_a_preview_also_needs_the_account_list_permission(world, monkeypatch):
    """Every built-in role with `platform.users.support` also holds `platform.users.view`; this
    fixture takes view out of the support role for one test to show the preview asks for it (the
    act does not: it lists nothing it was not shown)."""
    w, store = world, world["store"]
    support = w["login"](make_platform_admin(store, account(w, "support@example.test")["id"], role="support"))
    w["reauth"](support)
    original = platform_access.ROLES["support"]
    monkeypatch.setitem(platform_access.ROLES, "support", original - {"platform.users.view"})
    response = post(support, PREVIEW, {"operation": "revoke_sessions", "user_ids": [w["target"]["id"]]})
    assert response.status_code == 403 and response.json()["error"] == "platform_permission_denied"
    assert response.json()["detail"]["permission"] == "platform.users.view"
    response = execute(w, {"operation": "revoke_sessions", "reason": REASON, "preview_token": "fixture.unsigned",
                           "idempotency_key": uuid.uuid4().hex}, client=support)
    assert response.status_code == 409 and response.json()["error"] == "admin_preview_invalid", response.text
    monkeypatch.setitem(platform_access.ROLES, "support", original)
    assert post(support, PREVIEW, {"operation": "revoke_sessions", "user_ids": [w["target"]["id"]]}).status_code == 200


def test_a_confirmation_is_bound_to_its_preview(world, monkeypatch):
    w = world
    plan = preview(w, "revoke_sessions", [w["target"]["id"]])
    for alteration, error in (("operation", "admin_preview_invalid"), ("single", "admin_preview_invalid"),
                              ("tampered", "admin_preview_invalid"), ("missing", "admin_confirmation_required"),
                              ("key", "admin_confirmation_required"), ("reason", "missing_reason")):
        body = command(plan)
        if alteration == "operation":
            body["operation"] = "suspend"
        elif alteration == "single":
            body["preview_token"] = post(w["admin"], f"/api/admin/users/{w['target']['id']}/status-preview",
                                         {"status": "suspended"}).json()["preview_token"]
            body["operation"] = "suspend"
        elif alteration == "tampered":
            body["preview_token"] += "x"
        elif alteration == "missing":
            del body["preview_token"]
        elif alteration == "key":
            body["idempotency_key"] = "short"
        else:
            body["reason"] = "short"
        response = execute(w, body)
        assert response.status_code in (400, 409) and response.json()["error"] == error, (alteration, response.text)
    monkeypatch.setattr(admin_bulk, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    response = execute(w, command(plan))
    assert response.status_code == 409 and response.json()["error"] == "admin_preview_expired"
    assert w["ordinary"].get("/api/auth/me").status_code == 200
    assert not w["store"].all("SELECT * FROM admin_operations") and not journal.read(w["store"].root)
    denied = [json.loads(row["meta"]) for row in audits(w, "admin.user.bulk", result="denied")]
    assert {meta["code"] for meta in denied} >= {"admin_preview_invalid", "admin_confirmation_required",
                                                  "missing_reason", "admin_preview_expired"}
    assert all(meta["phase"] == "execute" for meta in denied)


# ---------------------------------------------------------------- (g) limits
def test_request_limits(world):
    w = world
    cases = [({"operation": "suspend", "user_ids": []}, "missing_users"),
             ({"operation": "suspend"}, "missing_users"),
             ({"operation": "suspend", "user_ids": "u_one"}, "missing_users"),
             ({"operation": "suspend", "user_ids": [w["target"]["id"], 7]}, "missing_users"),
             ({"operation": "suspend", "user_ids": [""]}, "missing_users"),
             ({"operation": "suspend", "user_ids": [f"u_fixture_{i:03d}" for i in range(101)]}, "too_many"),
             ({"operation": "delete", "user_ids": [w["target"]["id"]]}, "bad_operation"),
             ({"user_ids": [w["target"]["id"]]}, "bad_operation")]
    for body, error in cases:
        response = post(w["admin"], PREVIEW, body)
        assert response.status_code == 400 and response.json()["error"] == error, (body, response.text)
        if error == "too_many":
            assert response.json()["detail"]["limit"] == 100
    # An id no account can have — a lone surrogate, which SQLite could not even bind, or a space —
    # is malformed input, refused before any query (sent as raw JSON: a client would not encode it).
    for raw in (b'{"operation":"suspend","user_ids":["\\ud800"]}', b'{"operation":"suspend","user_ids":["u fixture"]}'):
        response = w["admin"].post(PREVIEW, content=raw, headers={"content-type": "application/json",
                                                                  CSRF_HEADER: w["admin"].cookies.get(CSRF_COOKIE) or ""})
        assert response.status_code == 400 and response.json()["error"] == "missing_users", (raw, response.text)
    denied = [json.loads(row["meta"]) for row in audits(w, "admin.user.bulk", result="denied")]
    assert [meta["code"] for meta in denied] == [error for _, error in cases] + ["missing_users"] * 2
    assert all(meta["phase"] == "preview" for meta in denied)
    # A hundred distinct ids is the limit, not past it; duplicates do not count twice.
    hundred = [w["target"]["id"]] + [f"u_fixture_{i:03d}" for i in range(99)]
    plan = preview(w, "revoke_sessions", hundred)
    assert plan["total"] == 100 and plan["eligible"] == 1
    out = execute(w, command(plan)).json()
    assert out["succeeded"] == 1 and out["failed"] == 99 and len(out["results"]) == 100


# ---------------------------------------------------------------- routing
def test_bulk_paths_reach_the_bulk_handlers_not_an_account_id(world):
    w = world
    paths = {p: set(ops) for p, ops in w["api"].openapi()["paths"].items()}
    assert paths["/api/admin/users/bulk"] == {"post"} and paths["/api/admin/users/bulk-preview"] == {"post"}
    # POST: the bulk handlers' own answers, never an account route's.
    assert post(w["admin"], PREVIEW, {"operation": "revoke_sessions", "user_ids": [w["target"]["id"]]}).json()["total"] == 1
    response = post(w["admin"], BULK, {"operation": "suspend"})
    assert response.status_code == 400 and response.json()["error"] == "missing_reason"
    # GET: the account detail route reads "bulk" as an id and finds no such account.
    for literal in ("bulk", "bulk-preview"):
        response = w["admin"].get(f"/api/admin/users/{literal}")
        assert response.status_code == 404 and response.json()["error"] == "no_user"
