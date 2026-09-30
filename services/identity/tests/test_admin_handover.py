"""Real UV signatures for two-party handover; all accounts and keys are disposable fixtures."""
from __future__ import annotations

import concurrent.futures
import json
import secrets
import threading

import pytest

from synwevo_core import journal
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import PlatformAccess, SUPER_ADMIN
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, now
from synwevo_identity import admin_handover
from synwevo_identity.passkeys import Passkeys

from .platform_fixture import make_platform_admin
from .test_passkey_reauth import world, post, Authenticator
from .test_passkeys import ORIGIN, PW
from .test_platform_grant_restore import backup

REASON = "Fixture: recipient deliberately accepts administrator authority"
ADMIN = "/api/admin/super-admin-offers"
ACCOUNT = "/api/auth/super-admin-offers"


@pytest.fixture()
def handover(world):
    root = make_platform_admin(world["store"], world["identity"].create_user("root@example.test", PW, status="active"))
    client = world["login"](root)
    assert post(client, "/api/auth/reauth", {"scope": "admin", "password": PW}).status_code == 200
    return {**world, "root": root, "root_client": client}


def offer(w, target=None):
    body = {"target_user_id": (target or w["user"])["id"]}
    response = post(w["root_client"], ADMIN + "/preview", body)
    assert response.status_code == 200, response.text
    response = post(w["root_client"], ADMIN, {**body, "preview_token": response.json()["preview_token"],
        "idempotency_key": secrets.token_hex(16), "reason": REASON})
    assert response.status_code == 200, response.text
    return response.json()["offer"]


def begin(w, row, stage="accept", client=None):
    path = ACCOUNT if stage == "accept" else ADMIN
    response = post(client or w["client"], f"{path}/{row['id']}/{stage}/begin")
    assert response.status_code == 200, response.text
    assert response.json()["options"]["userVerification"] == "required"
    return response.json()["options"]


def finish(w, row, stage="accept", options=None, body=None, client=None):
    path = ACCOUNT if stage == "accept" else ADMIN
    if body is None:
        options = options or begin(w, row, stage, client)
        body = {"credential": w["auth"].assertion(options["challenge"]), "idempotency_key": secrets.token_hex(16)}
    return post(client or w["client"], f"{path}/{row['id']}/{stage}/finish", body)


def accepted(w):
    row = offer(w)
    response = finish(w, row)
    assert response.status_code == 200, response.text
    return response.json()["offer"]


def test_full_chain_real_uv_without_generic_admin_scope_or_session_extension(handover):
    w = handover
    row = offer(w)
    store, access = w["store"], PlatformAccess(w["store"])
    assert not access.live_grants(w["user"]["id"])
    assert [u["id"] for u in access.usable_super_admins()] == [w["root"]["id"]]
    before = store.one("SELECT * FROM sessions WHERE id=?", (w["sid"],))
    result = finish(w, row)
    assert result.status_code == 200, result.text
    after = store.one("SELECT * FROM sessions WHERE id=?", (w["sid"],))
    assert after["reauth_at"] == before["reauth_at"] and after["reauth_scope"] == before["reauth_scope"]
    assert after["absolute_expires_at"] == before["absolute_expires_at"]
    assert after["idle_expires_at"] <= before["idle_expires_at"]
    assert "passkey_uv" in after["amr"]
    assert access.effective_permissions(w["user"]["id"]) and not access.is_usable_super_admin(w["user"]["id"])
    readiness = w["client"].get(f"{ADMIN}/{row['id']}/readiness")
    assert readiness.status_code == 200 and readiness.json()["can_confirm"]
    old = access.live_grants(w["root"]["id"])[0]
    denied = post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON})
    assert denied.json()["error"] == "reauth_required"
    assert post(w["client"], "/api/auth/reauth", {"scope": "admin", "password": PW}).status_code == 200
    denied = post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON})
    assert denied.json()["error"] == "last_super_admin"
    store.execute("UPDATE sessions SET reauth_scope='mfa',reauth_at=0 WHERE id=?", (w["sid"],))
    assert finish(w, row, "ready").status_code == 200
    assert access.is_usable_super_admin(w["user"]["id"])
    assert store.one("SELECT reauth_scope,reauth_at FROM sessions WHERE id=?", (w["sid"],)) == {"reauth_scope": "mfa", "reauth_at": 0}
    denied = post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON})
    assert denied.json()["error"] == "reauth_required"
    assert post(w["client"], "/api/auth/reauth", {"scope": "admin", "password": PW}).status_code == 200
    assert post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON}).status_code == 200
    assert [u["id"] for u in access.usable_super_admins()] == [w["user"]["id"]]
    assert journal.read(store.root, since=0)


def test_proposal_allows_no_uv_but_both_old_immediate_grant_routes_refuse(handover):
    w = handover
    target = w["identity"].create_user("not-ready@example.test", PW, status="active")
    w["store"].execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), target["id"]))
    row = offer(w, target)
    c = w["login"](target)
    listed = c.get(ACCOUNT).json()["items"]
    assert len(listed) == 1 and not listed[0]["can_accept"] and listed[0]["unavailable_reason"] == "passkey_required"
    assert post(c, f"{ACCOUNT}/{row['id']}/accept/begin").json()["error"] == "super_offer_passkey_required"
    details = w["root_client"].get(f"/api/admin/users/{target['id']}/platform-access").json()
    assert details["can_offer_super_admin"] and SUPER_ADMIN not in details["assignable_roles"]
    for suffix, role in (("platform-grants", "super_admin"), ("platform-role", "admin")):
        response = post(w["root_client"], f"/api/admin/users/{target['id']}/{suffix}", {"role": role, "reason": REASON})
        assert response.status_code == 409 and response.json()["error"] == "super_admin_acceptance_required"
    with pytest.raises(ServiceError) as exc:
        PlatformAccess(w["store"]).grant(w["root"]["id"], target["id"], SUPER_ADMIN, REASON)
    assert exc.value.code == "super_admin_acceptance_required"
    assert not PlatformAccess(w["store"]).live_grants(target["id"])


@pytest.mark.parametrize("bad", ["ordinary", "other_session", "other_user", "no_uv", "no_up", "origin", "handle", "expired", "revoked_session", "deleted_key"])
def test_accept_requires_exact_current_dedicated_uv(handover, bad):
    w, row = handover, offer(handover)
    options = begin(w, row)
    credential = w["auth"].assertion(options["challenge"], **{
        "uv": bad != "no_uv", "up": bad != "no_up", "origin": "https://other.example.test" if bad == "origin" else ORIGIN,
        "handle": "different-user" if bad == "handle" else None})
    client = w["client"]
    if bad == "ordinary":
        ordinary = post(client, "/api/auth/reauth/passkey/begin", {"scope": "admin"}).json()
        credential = w["auth"].assertion(ordinary["challenge"])
    elif bad == "other_session":
        client = w["login"]()
    elif bad == "other_user":
        client = w["root_client"]
    elif bad == "expired":
        w["store"].execute("UPDATE reauth_challenges SET expires_at=0")
    elif bad == "revoked_session":
        Sessions(w["store"]).revoke(w["sid"], reason="fixture_logout")
    elif bad == "deleted_key":
        w["store"].execute("UPDATE passkeys SET deleted_at=? WHERE id=?", (now(), w["key_id"]))
    result = finish(w, row, body={"credential": credential, "idempotency_key": secrets.token_hex(16)}, client=client)
    expected = {"ordinary": "no_challenge", "other_session": "no_challenge", "other_user": "super_offer_not_found",
                "no_uv": "bad_assertion", "no_up": "bad_assertion", "origin": "bad_assertion", "handle": "bad_assertion",
                "expired": "no_challenge", "revoked_session": "unauthorized", "deleted_key": "bad_assertion"}
    assert result.json()["error"] == expected[bad], result.text
    assert not PlatformAccess(w["store"]).live_grants(w["user"]["id"])
    assert w["store"].one("SELECT state FROM platform_super_admin_offers WHERE id=?", (row["id"],))["state"] == "pending"


def test_dedicated_proofs_do_not_cross_generic_or_stage_and_are_idempotent(handover):
    w, row = handover, offer(handover)
    options = begin(w, row)
    credential = w["auth"].assertion(options["challenge"])
    assert post(w["client"], "/api/auth/reauth/passkey/finish", {"scope": "admin", "credential": credential}).json()["error"] == "no_challenge"
    body = {"credential": credential, "idempotency_key": secrets.token_hex(16)}
    one = finish(w, row, body=body)
    assert one.status_code == 200, one.text
    assert finish(w, row, body=body).json() == one.json()
    assert finish(w, row, "ready", body={**body, "idempotency_key": secrets.token_hex(16)}).json()["error"] == "no_challenge"
    ready_options = begin(w, row, "ready")
    proof = w["auth"].assertion(ready_options["challenge"])
    assert post(w["client"], "/api/auth/reauth/passkey/finish", {"scope": "admin", "credential": proof}).json()["error"] == "no_challenge"
    done = {"credential": proof, "idempotency_key": secrets.token_hex(16)}
    result = finish(w, row, "ready", body=done)
    assert result.status_code == 200 and result.json()["usable_super_admin"]
    assert finish(w, row, "ready", body=done).json() == result.json()
    assert finish(w, row, "ready", body={**done, "credential": credential}).json()["error"] == "idempotency_conflict"
    assert len(PlatformAccess(w["store"]).live_grants(w["user"]["id"])) == 1


def test_original_grant_revoke_and_regrant_cannot_revive_offer(handover):
    w, row = handover, offer(handover)
    spare = make_platform_admin(w["store"], w["identity"].create_user("spare@example.test", PW, status="active"))
    access = PlatformAccess(w["store"])
    access.revoke(spare["id"], access.live_grants(w["root"]["id"])[0]["id"], REASON)
    make_platform_admin(w["store"], w["root"])
    response = post(w["client"], f"{ACCOUNT}/{row['id']}/accept/begin")
    assert response.json()["error"] == "super_offer_authority_changed"
    assert w["client"].get(ACCOUNT).json()["items"][0]["status"] == "authority_changed"


@pytest.mark.parametrize("stage", ["accept", "ready"])
def test_audit_failure_rolls_back_proof_role_factor_and_result_but_spends_challenge(handover, monkeypatch, stage):
    w = handover
    row = accepted(w) if stage == "ready" else offer(w)
    before = w["store"].one("SELECT * FROM sessions WHERE id=?", (w["sid"],))
    options = begin(w, row, stage)
    previous = admin_handover.HandoverOperations._record
    def fail(self, *args, **kwargs):
        previous(self, *args, **kwargs)
        raise RuntimeError("fixture-sensitive-audit-error")
    monkeypatch.setattr(admin_handover.HandoverOperations, "_record", fail)
    response = finish(w, row, stage, options=options)
    assert response.status_code == 500
    saved = w["store"].one("SELECT * FROM platform_super_admin_offers WHERE id=?", (row["id"],))
    assert saved["state"] == ("accepted" if stage == "ready" else "pending")
    assert saved["ready_at" if stage == "ready" else "accepted_at"] is None
    after = w["store"].one("SELECT * FROM sessions WHERE id=?", (w["sid"],))
    assert (after["amr"], after["reauth_at"], after["reauth_scope"]) == (before["amr"], before["reauth_at"], before["reauth_scope"])
    assert w["store"].one("SELECT used_at FROM reauth_challenges WHERE challenge=?", (options["challenge"],))["used_at"] is not None
    action = "auth.super_admin.accept" if stage == "accept" else "admin.super_admin.ready"
    assert not w["store"].one("SELECT 1 FROM admin_operations WHERE action=?", (action,))
    audits = w["store"].all("SELECT * FROM audit_events WHERE action=?", (action,))
    assert not any(a["result"] == "ok" for a in audits)
    assert "fixture-sensitive-audit-error" not in json.dumps(audits)


def test_cancel_and_decline_are_bounded_terminal_history_not_hidden(handover):
    w, row = handover, offer(handover)
    before = w["store"].one("SELECT COUNT(*) n FROM platform_super_admin_offers")["n"]
    assert w["client"].get(ACCOUNT + "?limit=51").status_code == 422
    response = post(w["client"], f"{ACCOUNT}/{row['id']}/decline", {"idempotency_key": secrets.token_hex(16)})
    assert response.status_code == 200 and response.json()["offer"]["status"] == "declined"
    next_row = offer(w)
    w["store"].execute("UPDATE platform_super_admin_offers SET expires_at=0 WHERE id=?", (next_row["id"],))
    first = w["client"].get(ACCOUNT + "?limit=1").json()
    assert first["items"][0]["status"] == "expired" and first["next_cursor"]
    second = w["client"].get(ACCOUNT + "?before=" + first["next_cursor"]).json()
    assert second["items"][0]["status"] == "declined"
    assert w["store"].one("SELECT COUNT(*) n FROM platform_super_admin_offers")["n"] == before + 1


def test_backup_without_ready_successor_refuses_old_root_withdrawal(handover, tmp_path):
    w, row = handover, accepted(handover)
    path = backup(w["store"], tmp_path, "accepted-not-ready")
    assert finish(w, row, "ready").status_code == 200
    access = PlatformAccess(w["store"])
    access.revoke(w["user"]["id"], access.live_grants(w["root"]["id"])[0]["id"], REASON)
    restored = Store(path)
    with pytest.raises(ValueError, match="no usable super administrator"):
        journal.apply(restored, journal.read(w["store"].root, since=0))
    assert PlatformAccess(restored).is_usable_super_admin(w["root"]["id"])
    assert not PlatformAccess(restored).is_usable_super_admin(w["user"]["id"])


def test_unready_successor_does_not_unlock_any_last_admin_exit(handover):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    w, row = handover, accepted(handover)
    uid = w["root"]["id"]
    assert post(w["root_client"], "/api/auth/deletion", {"password": PW}).json()["error"] == "last_super_admin"
    assert post(w["client"], "/api/auth/reauth", {"scope": "admin", "password": PW}).status_code == 200
    blocked = post(w["client"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"})
    assert blocked.json()["error"] == "last_super_admin"
    # Register a genuine UV key on the predecessor, then make it passkey-only in this fixture.
    assert post(w["root_client"], "/api/auth/reauth", {"scope": "mfa", "password": PW}).status_code == 200
    auth = Authenticator()
    options = post(w["root_client"], "/api/auth/passkeys/register/begin").json()
    registered = post(w["root_client"], "/api/auth/passkeys/register/finish", {"credential": auth.register(options["challenge"])})
    assert registered.status_code == 200
    w["store"].execute("DELETE FROM user_passwords WHERE user_id=?", (uid,))
    response = w["root_client"].delete("/api/auth/passkeys/" + registered.json()["id"],
        headers={CSRF_HEADER: w["root_client"].cookies.get(CSRF_COOKIE)})
    assert response.status_code == 409 and response.json()["error"] == "last_super_admin"
    assert PlatformAccess(w["store"]).is_usable_super_admin(uid)
    assert not PlatformAccess(w["store"]).is_usable_super_admin(w["user"]["id"])
    assert finish(w, row, "ready").status_code == 200


@pytest.mark.parametrize("withdrawal", ["session", "key", "grantor", "cancel"])
def test_withdrawal_committed_by_second_store_after_proof_spend_wins(handover, monkeypatch, withdrawal):
    w, row = handover, offer(handover)
    other = Store(w["store"].root)
    spare = make_platform_admin(other, w["identity"].create_user("race-spare@example.test", PW, status="active"))
    original = Passkeys.spend_reauthentication
    def withdraw(self, *args, **kwargs):
        proof = original(self, *args, **kwargs)
        if withdrawal == "session":
            Sessions(other).revoke(w["sid"], reason="fixture_concurrent_logout")
        elif withdrawal == "key":
            Passkeys(other).remove(w["user"]["id"], w["key_id"])
        elif withdrawal == "grantor":
            access = PlatformAccess(other)
            access.revoke(spare["id"], access.live_grants(w["root"]["id"])[0]["id"], REASON)
        else:
            response = post(w["root_client"], f"{ADMIN}/{row['id']}/cancel", {"reason": REASON, "idempotency_key": secrets.token_hex(16)})
            assert response.status_code == 200, response.text
        return proof
    monkeypatch.setattr(Passkeys, "spend_reauthentication", withdraw)
    response = finish(w, row)
    expected = {"session": "unauthorized", "key": "bad_assertion", "grantor": "super_offer_authority_changed", "cancel": "super_offer_state_conflict"}
    assert response.json()["error"] == expected[withdrawal], response.text
    assert not PlatformAccess(other).live_grants(w["user"]["id"])


def test_two_service_instances_accepting_the_same_offer_commit_one_grant(handover, monkeypatch):
    from fastapi.testclient import TestClient
    from synwevo_identity.app import create_app
    w, row = handover, offer(handover)
    second_api = create_app(str(w["store"].root), start_jobs=False)
    with TestClient(second_api, base_url=ORIGIN, raise_server_exceptions=False) as second:
        assert post(second, "/api/auth/login", {"email": w["user"]["email"], "password": PW}).status_code == 200
        one_options, two_options = begin(w, row), begin(w, row, client=second)
        one = {"credential": w["auth"].assertion(one_options["challenge"]), "idempotency_key": secrets.token_hex(16)}
        two = {"credential": w["auth"].assertion(two_options["challenge"]), "idempotency_key": secrets.token_hex(16)}
        barrier, original = threading.Barrier(2, timeout=10), Passkeys.spend_reauthentication
        def synchronized(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            barrier.wait()
            return value
        monkeypatch.setattr(Passkeys, "spend_reauthentication", synchronized)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(finish, w, row, body=body, client=client) for client, body in ((w["client"], one), (second, two))]
            results = [f.result(timeout=20) for f in futures]
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        assert len(PlatformAccess(w["store"]).live_grants(w["user"]["id"])) == 1
        assert w["store"].one("SELECT COUNT(*) n FROM admin_operations WHERE action='auth.super_admin.accept'")["n"] == 1


def test_passkey_only_recipient_can_complete_both_proofs_and_independent_admin_reauth(handover):
    w = handover
    w["store"].execute("DELETE FROM user_passwords WHERE user_id=?", (w["user"]["id"],))
    w["store"].execute("UPDATE sessions SET amr='email',reauth_at=0,reauth_scope='' WHERE id=?", (w["sid"],))
    row = accepted(w)
    assert finish(w, row, "ready").status_code == 200
    old = PlatformAccess(w["store"]).live_grants(w["root"]["id"])[0]
    denied = post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON})
    assert denied.json()["error"] == "reauth_required"
    options = post(w["client"], "/api/auth/reauth/passkey/begin", {"scope": "admin"}).json()
    assert post(w["client"], "/api/auth/reauth/passkey/finish", {"scope": "admin", "credential": w["auth"].assertion(options["challenge"])}).status_code == 200
    assert post(w["client"], f"/api/admin/platform-grants/{old['id']}/revoke", {"reason": REASON}).status_code == 200


@pytest.mark.parametrize("tamper", ["signature", "target", "actor", "origin", "expired", "grant_id"])
def test_offer_preview_cannot_be_rebound(handover, monkeypatch, tamper):
    w = handover
    target = w["user"]["id"]
    response = post(w["root_client"], ADMIN + "/preview", {"target_user_id": target})
    plan = response.json()
    ops = admin_handover.HandoverOperations(w["identity"], Sessions(w["store"]), Passkeys(w["store"]), None)
    value = ops._verify(plan["preview_token"])
    if tamper == "signature":
        token = plan["preview_token"] + "bad"
    else:
        field = "proposer_grant_id" if tamper == "grant_id" else "expires_at" if tamper == "expired" else tamper
        value[field] = 1 if tamper == "expired" else "unrelated"
        token = ops._sign(value)
    response = post(w["root_client"], ADMIN, {"target_user_id": target, "preview_token": token,
        "idempotency_key": secrets.token_hex(16), "reason": REASON})
    assert response.status_code == 409 and response.json()["error"] == ("admin_preview_expired" if tamper == "expired" else "admin_preview_invalid")
    assert not w["store"].all("SELECT * FROM platform_super_admin_offers")


def test_declined_proof_cannot_authorize_replacement_offer(handover):
    w, row = handover, offer(handover)
    opts = begin(w, row)
    proof = w["auth"].assertion(opts["challenge"])
    assert post(w["client"], f"{ACCOUNT}/{row['id']}/decline", {"idempotency_key": secrets.token_hex(16)}).status_code == 200
    replacement = offer(w)
    response = finish(w, replacement, body={"credential": proof, "idempotency_key": secrets.token_hex(16)})
    assert response.status_code == 400 and response.json()["error"] == "no_challenge"
    assert not PlatformAccess(w["store"]).live_grants(w["user"]["id"])


def test_other_begin_cannot_collect_a_spent_dedicated_receipt_before_commit(handover, monkeypatch):
    w, row = handover, offer(handover)
    original = Passkeys.spend_reauthentication
    def interleave(self, *args, **kwargs):
        proof = original(self, *args, **kwargs)
        result = post(w["client"], "/api/auth/reauth/passkey/begin", {"scope": "mfa"})
        assert result.status_code == 200
        return proof
    monkeypatch.setattr(Passkeys, "spend_reauthentication", interleave)
    response = finish(w, row)
    assert response.status_code == 200, response.text


def test_revoked_and_regranted_recipient_cannot_ready_or_retry_old_acceptance(handover):
    w, row = handover, offer(handover)
    opts = begin(w, row)
    body = {"credential": w["auth"].assertion(opts["challenge"]), "idempotency_key": secrets.token_hex(16)}
    result = finish(w, row, body=body)
    assert result.status_code == 200, result.text
    ready = begin(w, row, "ready")
    access = PlatformAccess(w["store"])
    old = access.live_grants(w["user"]["id"])[0]
    access.revoke(w["root"]["id"], old["id"], REASON)
    make_platform_admin(w["store"], w["user"])  # Explicit independent host regrant fixture; not acceptance proof.
    assert finish(w, row, "ready", options=ready).json()["error"] == "super_offer_state_conflict"
    assert finish(w, row, body=body).json()["error"] == "super_offer_state_conflict"
    assert w["store"].one("SELECT ready_at FROM platform_super_admin_offers WHERE id=?", (row["id"],))["ready_at"] is None


def test_ready_availability_persists_after_proposal_expiry_and_reopen(handover):
    w, row = handover, accepted(handover)
    assert finish(w, row, "ready").status_code == 200
    w["store"].execute("UPDATE platform_super_admin_offers SET expires_at=0 WHERE id=?", (row["id"],))
    assert PlatformAccess(Store(w["store"].root)).is_usable_super_admin(w["user"]["id"])
    listed = w["client"].get(ACCOUNT).json()["items"][0]
    assert listed["status"] == "ready"


def test_pending_expiry_never_accepts_and_listing_is_read_only(handover):
    w, row = handover, offer(handover)
    w["store"].execute("UPDATE platform_super_admin_offers SET expires_at=0 WHERE id=?", (row["id"],))
    before = w["store"].all("SELECT * FROM platform_super_admin_offers")
    assert w["client"].get(ACCOUNT).json()["items"][0]["status"] == "expired"
    assert w["store"].all("SELECT * FROM platform_super_admin_offers") == before
    assert post(w["client"], f"{ACCOUNT}/{row['id']}/accept/begin").json()["error"] == "super_offer_expired"


def test_accepted_recipient_can_ready_after_proposer_is_legitimately_revoked(handover):
    w, row = handover, accepted(handover)
    third = make_platform_admin(w["store"], w["identity"].create_user("other-root@example.test", PW, status="active"))
    access = PlatformAccess(w["store"])
    former = access.live_grants(w["root"]["id"])[0]
    access.revoke(third["id"], former["id"], REASON)
    assert not access.effective_permissions(w["root"]["id"])
    assert finish(w, row, "ready").status_code == 200
    assert access.is_usable_super_admin(w["user"]["id"])


def test_dedicated_failure_audit_names_offer_without_recording_assertion(handover):
    w, row = handover, offer(handover)
    response = finish(w, row, body={"credential": {"id": "fixture-sensitive-assertion"}, "idempotency_key": secrets.token_hex(16)})
    assert response.status_code == 400 and response.json()["error"] == "no_challenge"
    entries = w["store"].all("SELECT * FROM audit_events WHERE action='auth.super_admin.accept'")
    assert len(entries) == 1
    assert entries[0]["object_kind"] == "super_admin_offer" and entries[0]["object_id"] == row["id"]
    assert json.loads(entries[0]["meta"]) == {"code": "no_challenge"}
    assert "fixture-sensitive-assertion" not in json.dumps(entries)
