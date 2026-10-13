"""The platform-controlled reset of a lost second factor (admin coverage A26), through the real
account service: preview, the delayed and announced request, the mailed cancel link, the
administrator's cancel, the sweeper's completion and its void, and the permission boundary.

Isolated data directory under tmp_path, file mail, no worker threads. Every account, passkey row,
token row and moved due date below is a labelled test fixture; the TOTP enrolments go through the
real `/api/auth/mfa` routes with codes computed by pyotp from the secret those routes return.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import types
import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_identity import admin_second_factor
from synwevo_identity.admin_second_factor import (ANNOUNCEMENT_GRACE_S, SECOND_FACTOR_RESET_DELAY_S,
                                                  complete_due_second_factor_resets, factors)

from .platform_fixture import end_platform_grants, make_platform_admin

PW = "isolated-second-factor-reset-fixture-58"
REASON = "Isolated fixture: identity confirmed on a video call"
REQUEST, CANCEL, COMPLETE = (admin_second_factor.REQUEST, admin_second_factor.CANCEL, admin_second_factor.COMPLETE)
TOKEN_IN_LINK = re.compile(r"/account/second-factor-reset\?lang=[A-Za-z-]+#token=([A-Za-z0-9_-]+)")


def post(client, path, body=None, **kwargs):
    headers = {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or "", **kwargs.pop("headers", {})}
    return client.post(path, json=body if body is not None else {}, headers=headers, **kwargs)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "data"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "http://testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "data"), start_jobs=False)
    store = Store(tmp_path / "data")
    ident = Identity(store)
    clients: list[TestClient] = []

    def verified(email):
        user = ident.create_user(email, PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        return ident.get_user(user["id"])

    def login(user):
        client = TestClient(api, raise_server_exceptions=False)
        clients.append(client)
        response = post(client, "/api/auth/login", {"email": user["email"], "password": PW})
        assert response.status_code == 200, response.text
        return client

    def reauth(client):
        response = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
        assert response.status_code == 200, response.text

    # FIXTURE accounts: a super administrator, the person who lost their factors, an access
    # administrator and a support agent (both without `platform.users.recover`), a plain account.
    root = make_platform_admin(store, verified("root@example.test"))
    target = verified("target@example.test")
    access = make_platform_admin(store, verified("access@example.test"), role="access_admin")
    support = make_platform_admin(store, verified("support@example.test"), role="support")
    plain = verified("plain@example.test")
    admin = login(root)
    reauth(admin)
    person = login(target)
    yield {"api": api, "store": store, "ident": ident, "root": root, "target": target, "access": access,
           "support": support, "plain": plain, "admin": admin, "person": person, "login": login,
           "reauth": reauth, "verified": verified}
    for client in clients:
        client.close()


def enrol_totp(client) -> tuple[str, list[str]]:
    """The real enrolment: re-authenticate, begin, confirm with a computed code. Returns the secret
    and the recovery codes, which only this test (the person) ever holds."""
    assert post(client, "/api/auth/mfa/reauth", {"password": PW}).status_code == 200
    secret = post(client, "/api/auth/mfa/totp/begin").json()["secret"]
    response = post(client, "/api/auth/mfa/totp/confirm", {"code": pyotp.TOTP(secret).now()})
    assert response.status_code == 200, response.text
    return secret, response.json()["recovery_codes"]


def fixture_passkey(store, user, n=1):
    """FIXTURE: registered passkey rows (public material only, never used to sign in)."""
    for i in range(n):
        store.execute("INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,uv_capable,name,created_at) "
                      "VALUES (?,?,?,?,'testserver',1,'fixture key',?)",
                      (f"pk_fixture_{user['id']}_{i}", user["id"], f"cred-fixture-{user['id']}-{i}", "fixture-public-key", now()))


def fixture_token(store, user):
    """FIXTURE: one live access/refresh token pair of the account (hashes of a value never issued)."""
    raw, ts = f"fixture-access-token-{user['id']}", now()
    store.execute("INSERT INTO oauth_tokens (id,client_id,user_id,access_hash,refresh_hash,scope,issued_at,"
                  "access_expires_at,refresh_expires_at,family_id) VALUES (?,?,?,?,?,'openid',?,?,?,?)",
                  (f"fixture-token-{user['id']}", "docweave-web", user["id"], oidc._sha(raw), oidc._sha(raw + "-refresh"),
                   ts, ts + 900, ts + 3600, f"fixture-token-{user['id']}"))
    return raw


def preview(w, *, include=False, client=None, uid=None):
    response = post(client or w["admin"], f"/api/admin/users/{uid or w['target']['id']}/second-factor-reset/preview",
                    {"include_passkeys": include})
    assert response.status_code == 200, response.text
    return response.json()


def body_for(plan, *, method="video_call", include=False, key=None, reason=REASON):
    return {"method": method, "reason": reason, "include_passkeys": include, "preview_token": plan["preview_token"],
            "idempotency_key": key or uuid.uuid4().hex}


def ask(w, body, *, client=None, uid=None):
    return post(client or w["admin"], f"/api/admin/users/{uid or w['target']['id']}/second-factor-reset", body)


def requested(w, *, include=False, uid=None):
    plan = preview(w, include=include, uid=uid)
    response = ask(w, body_for(plan, include=include), uid=uid)
    assert response.status_code == 200, response.text
    return response.json()


def mailed_token(w, uid=None) -> str:
    """The cancel token, read from the announcement while it is still queued (the worker redacts
    a sent mail's body)."""
    row = w["store"].one("SELECT body FROM mail_outbox WHERE kind='second_factor_reset_requested' AND ref_id=? "
                         "ORDER BY created_at DESC LIMIT 1", (uid or w["target"]["id"],))
    assert row, "the announcement is in the outbox"
    return TOKEN_IN_LINK.search(row["body"])[1]


def deliver(w):
    """The mail worker's own pass (file mode: each queued mail becomes an .eml under tmp_path)."""
    assert w["api"].state.mailer.drain_once(limit=100) >= 1


def announcement_of(w, rid):
    return w["store"].one("SELECT m.* FROM mail_outbox m JOIN second_factor_resets r ON r.announcement_mail_id=m.id "
                          "WHERE r.id=?", (rid,))


def fall_due(w, rid, *, delivered_ago=SECOND_FACTOR_RESET_DELAY_S + 1, delivered=True):
    """FIXTURE times: the reset's due time has passed and — unless `delivered=False` — its
    announcement, delivered by the real worker, was sent `delivered_ago` seconds ago. The clock is
    not moved; the rows' times are."""
    store = w["store"]
    if delivered:
        if announcement_of(w, rid)["status"] == "queued":
            deliver(w)
        store.execute("UPDATE mail_outbox SET sent_at=? WHERE id=(SELECT announcement_mail_id FROM second_factor_resets "
                      "WHERE id=?)", (now() - delivered_ago, rid))
    store.execute("UPDATE second_factor_resets SET due_at=? WHERE id=?", (now() - 1, rid))


def outcome(**counts):
    return {"completed": 0, "void": 0, "waiting": 0, "failed": 0, **counts}


def void_of(w, rid):
    return w["store"].one("SELECT status,void_reason FROM second_factor_resets WHERE id=?", (rid,))


def notices(w, uid, kind):
    return [json.loads(r["params"]) if isinstance(r["params"], str) else r["params"] for r in w["store"].all(
        "SELECT params FROM notifications WHERE user_id=? AND kind=? ORDER BY seq", (uid, kind))]


def audits(w, action, result=None):
    rows = w["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (action,))
    return [r for r in rows if result is None or r["result"] == result]


def needs_mfa(w, user) -> bool:
    fresh = TestClient(w["api"])
    response = post(fresh, "/api/auth/login", {"email": user["email"], "password": PW})
    assert response.status_code == 200, response.text
    return bool(response.json().get("mfa_required"))


# ---------------------------------------------------------------- (a) preview and request
def test_a_reset_is_previewed_requested_announced_and_audited_without_removing_anything_yet(world, capfd):
    w, store, uid = world, world["store"], world["target"]["id"]
    secret, codes = enrol_totp(w["person"])
    fixture_token(store, w["target"])
    started = time.time()
    plan = preview(w)
    assert plan["action"] == REQUEST and plan["scope"] == "platform" and plan["risk"] == "high"
    assert plan["target"] == {"id": uid, "email": "target@example.test", "status": "active"}
    assert plan["factors"] == {"totp": True, "recovery_remaining": 10, "passkeys": 0}
    assert plan["effect"] == {"removes_totp": True, "removes_recovery_codes": True, "removes_passkeys": 0,
                              "sessions": 1, "tokens": 1}
    assert started + SECOND_FACTOR_RESET_DELAY_S - 1 <= plan["due_at"] <= time.time() + SECOND_FACTOR_RESET_DELAY_S + 1
    assert SECOND_FACTOR_RESET_DELAY_S == 72 * 3600
    assert started + 299 <= plan["expires_at"] <= time.time() + 301
    assert not store.all("SELECT * FROM second_factor_resets"), "a preview changes nothing"

    response = ask(w, body_for(plan, method="video_call"))
    assert response.status_code == 200, response.text
    out = response.json()
    assert set(out) == {"ok", "operation_id", "reset"} and out["ok"] is True
    assert out["reset"]["status"] == "pending" and set(out["reset"]) == {"id", "due_at", "status"}
    assert started + SECOND_FACTOR_RESET_DELAY_S - 1 <= out["reset"]["due_at"] <= time.time() + SECOND_FACTOR_RESET_DELAY_S + 1
    row = store.one("SELECT * FROM second_factor_resets WHERE id=?", (out["reset"]["id"],))
    assert row["status"] == "pending" and row["user_id"] == uid and row["requested_by"] == w["root"]["id"]
    assert row["method"] == "video_call" and row["reason"] == REASON and row["include_passkeys"] == 0

    # The announcement: to the account's own address, with the cancel link carrying the token
    # (the token's SHA-256 is all the database keeps).
    mail = store.one("SELECT * FROM mail_outbox WHERE kind='second_factor_reset_requested'")
    assert mail["to_addr"] == "target@example.test" and mail["ref_id"] == uid and mail["status"] == "queued"
    token = mailed_token(w)
    assert "http://testserver/account/second-factor-reset?lang=zh-Hans#token=" + token in mail["body"]
    assert row["cancel_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert "平台管理员" in mail["body"] and "root@example.test" not in mail["body"], "the administrator is named by role"
    assert token not in json.dumps(store.all("SELECT * FROM audit_events"), default=str)
    assert token not in response.text and token not in json.dumps(store.all("SELECT * FROM admin_operations"))
    assert token not in json.dumps(store.all("SELECT * FROM second_factor_resets"), default=str)
    note = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='security.change' ORDER BY seq DESC LIMIT 1", (uid,))
    assert note and "second_factor_reset_requested" in json.dumps(dict(note), default=str)

    [audit] = audits(w, REQUEST, "ok")
    meta = json.loads(audit["meta"])
    assert audit["actor_id"] == w["root"]["id"] and audit["object_id"] == uid
    assert {k: meta[k] for k in ("method", "reason", "include_passkeys", "due_at", "operation_id", "reset_id")} == {
        "method": "video_call", "reason": REASON, "include_passkeys": False, "due_at": out["reset"]["due_at"],
        "operation_id": out["operation_id"], "reset_id": out["reset"]["id"]}
    assert meta["before"] == {"reset": None} and meta["after"] == {"reset": "pending"} and meta["changed"] == ["reset"]

    # Nothing is removed yet: the factors stand, the person still needs the authenticator to sign
    # in, and their session and token still work.
    assert factors(store, uid) == {"totp": True, "recovery_remaining": 10, "passkeys": 0}
    assert needs_mfa(w, w["target"])
    assert w["person"].get("/api/auth/me").status_code == 200
    assert store.one("SELECT 1 AS x FROM oauth_tokens WHERE user_id=? AND revoked_at IS NULL", (uid,))
    assert not journal.read(store.root)
    printed = capfd.readouterr()
    assert token not in printed.out + printed.err, "the token is never logged"
    assert secret not in response.text and not any(code in response.text for code in codes)


# ---------------------------------------------------------------- (b) the mailed cancel link
def test_the_mailed_link_shows_when_it_falls_due_cancels_once_and_the_sweep_then_does_nothing(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    out = requested(w)
    token = mailed_token(w)
    anonymous = TestClient(w["api"])
    shown = anonymous.post("/api/auth/second-factor-reset/preview", json={"token": token})
    assert shown.status_code == 200 and shown.json() == {"status": "pending", "due_at": out["reset"]["due_at"]}
    assert "target@example.test" not in shown.text and shown.headers["cache-control"] == "no-store"
    assert anonymous.post("/api/auth/second-factor-reset/preview", json={"token": "x" * 43}).status_code == 410
    assert anonymous.post("/api/auth/second-factor-reset/preview", json={"token": "short"}).json()["error"] == "bad_token"

    cancelled = anonymous.post("/api/auth/second-factor-reset/cancel", json={"token": token})
    assert cancelled.status_code == 200 and cancelled.json() == {"ok": True}
    row = store.one("SELECT * FROM second_factor_resets WHERE id=?", (out["reset"]["id"],))
    assert row["status"] == "cancelled" and row["cancelled_by"] == "account_holder" and row["cancelled_at"]
    [audit] = audits(w, "auth.second_factor_reset.cancelled")
    assert audit["actor_kind"] == "user" and audit["actor_id"] == uid and audit["result"] == "ok"
    assert token not in audit["meta"]
    # The person sees it in their own security activity, and is told.
    assert "auth.second_factor_reset.cancelled" in w["person"].get("/api/auth/security-activity").text
    assert store.one("SELECT 1 AS x FROM mail_outbox WHERE kind='second_factor_reset_cancelled' AND to_addr=?",
                     ("target@example.test",))
    note = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='security.change' ORDER BY seq DESC LIMIT 1", (uid,))
    assert "second_factor_reset_cancelled" in json.dumps(dict(note), default=str)

    # Single use: the link is spent, for preview and cancel alike.
    for path in ("/api/auth/second-factor-reset/preview", "/api/auth/second-factor-reset/cancel"):
        again = anonymous.post(path, json={"token": token})
        assert again.status_code == 410 and again.json()["error"] == "bad_token", path

    fall_due(w, out["reset"]["id"])
    assert complete_due_second_factor_resets(store) == outcome()
    assert factors(store, uid)["totp"] is True and needs_mfa(w, w["target"])
    assert store.one("SELECT status FROM second_factor_resets WHERE id=?", (out["reset"]["id"],))["status"] == "cancelled"
    assert not audits(w, COMPLETE)


def test_the_link_cancels_until_the_reset_takes_effect_and_shows_when_that_is(world):
    """Past its nominal due time the reset still waits the full delay after the announcement was
    delivered, and for all that time the link still cancels it; once it took effect, it no longer does."""
    w, store = world, world["store"]
    enrol_totp(w["person"])
    out = requested(w)
    token = mailed_token(w)
    fall_due(w, out["reset"]["id"], delivered_ago=10 * 3600)          # delivered 10 hours ago
    sent_at = announcement_of(w, out["reset"]["id"])["sent_at"]
    anonymous = TestClient(w["api"])
    shown = anonymous.post("/api/auth/second-factor-reset/preview", json={"token": token}).json()
    assert shown == {"status": "pending", "due_at": sent_at + SECOND_FACTOR_RESET_DELAY_S}
    assert complete_due_second_factor_resets(store) == outcome(waiting=1)
    assert anonymous.post("/api/auth/second-factor-reset/cancel", json={"token": token}).json() == {"ok": True}
    assert store.one("SELECT status FROM second_factor_resets")["status"] == "cancelled"

    # Another request, carried out: its link is spent.
    out = requested(w)
    token = mailed_token(w)
    fall_due(w, out["reset"]["id"])
    assert complete_due_second_factor_resets(store) == outcome(completed=1)
    for path in ("/api/auth/second-factor-reset/preview", "/api/auth/second-factor-reset/cancel"):
        response = anonymous.post(path, json={"token": token})
        assert response.status_code == 410 and response.json()["error"] == "bad_token", path


# ---------------------------------------------------------------- (c) completion
@pytest.mark.parametrize("include_passkeys", [False, True])
def test_completion_removes_the_factors_ends_every_session_and_the_person_enrols_again(world, include_passkeys):
    w, store, uid = world, world["store"], world["target"]["id"]
    secret, _ = enrol_totp(w["person"])
    fixture_passkey(store, w["target"], 2)
    token_raw = fixture_token(store, w["target"])
    out = requested(w, include=include_passkeys)
    rid = out["reset"]["id"]
    assert complete_due_second_factor_resets(store) == outcome(), "not due yet"
    assert factors(store, uid)["totp"] is True

    fall_due(w, rid)
    # Through the account service's own sweeper (its mailer and inbox).
    done = w["api"].state.auth_sweeper.run_once()["second_factor_resets"]
    assert done == outcome(completed=1)
    assert factors(store, uid) == {"totp": False, "recovery_remaining": 0,
                                   "passkeys": 0 if include_passkeys else 2}
    assert not store.one("SELECT 1 AS x FROM totp_secrets WHERE user_id=?", (uid,))
    assert not store.one("SELECT 1 AS x FROM recovery_codes WHERE user_id=?", (uid,))
    if include_passkeys:
        assert {r["deleted_at"] is not None for r in store.all("SELECT deleted_at FROM passkeys WHERE user_id=?", (uid,))} == {True}
    # Every session and token of the account ended, and the restore journal knows.
    assert not store.one("SELECT 1 AS x FROM sessions WHERE user_id=? AND revoked_at IS NULL", (uid,))
    assert not store.one("SELECT 1 AS x FROM oauth_tokens WHERE user_id=? AND revoked_at IS NULL", (uid,))
    assert not oidc.Grants(store).load_access(token_raw)
    assert w["person"].get("/api/auth/me").status_code == 401
    removed_ids = [f"pk_fixture_{uid}_{i}" for i in range(2)] if include_passkeys else []
    assert [(e["kind"], e.get("credential_id") or e.get("user_id")) for e in journal.read(store.root)] == [
        *(("passkey_removed", pid) for pid in removed_ids), ("sessions_revoked", uid)], \
        "each removed passkey is journalled as the person's own removal is, before the sessions"
    row = store.one("SELECT * FROM second_factor_resets WHERE id=?", (rid,))
    assert row["status"] == "completed" and row["completed_at"]

    [audit] = audits(w, COMPLETE, "ok")
    meta = json.loads(audit["meta"])
    assert audit["actor_kind"] == "system" and audit["actor_id"] == "second-factor-reset" and audit["object_id"] == uid
    assert meta["reset_id"] == rid and meta["requested_by"] == w["root"]["id"]
    assert meta["removed"] == {"totp": True, "recovery_codes": 10, "passkeys": 2 if include_passkeys else 0}
    assert meta["sessions"] == 1 and meta["tokens"] == 1
    assert meta["before"]["status"] == "pending" and meta["after"]["status"] == "completed"
    assert {"totp", "recovery_remaining", "sessions", "tokens", "status"} <= set(meta["changed"])
    assert secret not in audit["meta"]
    mail = store.one("SELECT * FROM mail_outbox WHERE kind='second_factor_reset_completed'")
    assert mail and mail["to_addr"] == "target@example.test" and "/login?lang=zh-Hans" in mail["body"]
    note = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='security.change' ORDER BY seq DESC LIMIT 1", (uid,))
    assert "second_factor_reset_completed" in json.dumps(dict(note), default=str)

    # The password alone signs in now, and the person sets the authenticator up again.
    assert not needs_mfa(w, w["target"])
    again = w["login"](w["target"])
    assert again.get("/api/auth/me").status_code == 200
    new_secret, new_codes = enrol_totp(again)
    assert new_secret != secret and len(new_codes) == 10 and needs_mfa(w, w["target"])

    # A second sweep finds nothing to do.
    assert complete_due_second_factor_resets(store) == outcome()
    assert len(audits(w, COMPLETE)) == 1 and len(journal.read(store.root)) == 1 + len(removed_ids)
    assert factors(store, uid)["totp"] is True


# ---------------------------------------------------------------- (d) void at completion
@pytest.mark.parametrize("change", ["suspended", "platform_grant", "deleted"])
def test_a_reset_is_voided_when_the_account_changed_before_it_fell_due(world, change):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    out = requested(w)
    if change == "suspended":
        w["ident"].set_status(uid, "suspended")                       # FIXTURE: suspended meanwhile
        code = "account_state_conflict"
    elif change == "platform_grant":
        make_platform_admin(store, w["target"], role="support")       # FIXTURE: granted a platform role meanwhile
        code = "target_is_platform_admin"
    else:
        store.execute("UPDATE users SET status='deleted',deleted_at=? WHERE id=?", (now(), uid))  # FIXTURE
        code = "account_deleted"
    fall_due(w, out["reset"]["id"])
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    row = store.one("SELECT * FROM second_factor_resets WHERE id=?", (out["reset"]["id"],))
    assert row["status"] == "void" and row["void_reason"] == code and row["completed_at"] is None
    assert store.one("SELECT 1 AS x FROM totp_secrets WHERE user_id=?", (uid,)), "nothing was touched"
    assert store.one("SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id=?", (uid,))["n"] == 10
    [audit] = audits(w, COMPLETE)
    assert audit["result"] == "denied" and audit["actor_kind"] == "system" and json.loads(audit["meta"])["code"] == code
    assert not journal.read(store.root)
    assert complete_due_second_factor_resets(store) == outcome()


# ---------------------------------------------------------------- (e) refusals
def test_the_request_refuses_what_it_must(world, monkeypatch):
    w, store, uid = world, world["store"], world["target"]["id"]
    admin = w["admin"]

    def refusal(response, status, code):
        assert response.status_code == status and response.json()["error"] == code, response.text

    base = "/api/admin/users/{}/second-factor-reset/preview"
    refusal(post(admin, base.format("u_fixture_missing"), {}), 404, "no_user")
    refusal(post(admin, base.format(w["root"]["id"]), {}), 409, "self_reset")
    refusal(post(admin, base.format(uid), {}), 409, "nothing_to_reset")
    fixture_passkey(store, w["target"])
    refusal(post(admin, base.format(uid), {"include_passkeys": False}), 409, "nothing_to_reset")
    assert preview(w, include=True)["effect"]["removes_passkeys"] == 1, "passkeys alone are something to reset when asked"
    assert post(admin, base.format(uid), {"include_passkeys": "yes"}).json()["error"] == "nothing_to_reset", \
        "anything but true is the less destructive choice"
    enrol_totp(w["person"])
    refusal(post(admin, base.format(w["support"]["id"]), {}), 409, "target_is_platform_admin")
    store.execute("INSERT INTO app_accounts (user_id,app,created_at) VALUES (?,?,?)", (w["plain"]["id"], "fixture-app", now()))  # FIXTURE
    refusal(post(admin, base.format(w["plain"]["id"]), {}), 409, "application_identity")
    w["ident"].set_status(uid, "suspended")                                    # FIXTURE
    refusal(post(admin, base.format(uid), {}), 409, "account_state_conflict")
    w["ident"].set_status(uid, "active")

    plan = preview(w)
    refusal(ask(w, {**body_for(plan), "reason": "short"}), 400, "missing_reason")
    refusal(ask(w, {**body_for(plan), "method": "phone_call"}), 400, "bad_method")
    refusal(ask(w, {"method": "in_person", "reason": REASON}), 400, "admin_confirmation_required")
    refusal(ask(w, {**body_for(plan), "include_passkeys": True}), 409, "admin_preview_invalid")
    refusal(ask(w, {**body_for(plan), "preview_token": plan["preview_token"] + "x"}), 409, "admin_preview_invalid")
    original_time = admin_second_factor.time
    monkeypatch.setattr(admin_second_factor, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    refusal(ask(w, body_for(plan)), 409, "admin_preview_expired")
    monkeypatch.setattr(admin_second_factor, "time", original_time)
    # A factor changes between the preview and the confirmation — through the person's own
    # routes — and the confirmed version is stale: the recovery codes are regenerated (the count
    # stays 10, the set does not), and, after a fresh preview, one of them is spent at sign-in.
    assert post(w["person"], "/api/auth/mfa/reauth", {"password": PW}).status_code == 200
    regenerated = post(w["person"], "/api/auth/mfa/recovery/regenerate")
    assert regenerated.status_code == 200, regenerated.text
    assert factors(store, uid)["recovery_remaining"] == 10
    refusal(ask(w, body_for(plan)), 409, "admin_preview_stale")
    plan = preview(w)
    fresh = TestClient(w["api"])
    ticket = post(fresh, "/api/auth/login", {"email": "target@example.test", "password": PW}).json()["ticket"]
    spent = post(fresh, "/api/auth/login/mfa", {"ticket": ticket, "recovery_code": regenerated.json()["recovery_codes"][0]})
    assert spent.status_code == 200, spent.text
    refusal(ask(w, body_for(plan)), 409, "admin_preview_stale")
    assert not store.all("SELECT * FROM second_factor_resets"), "no refused request left a row"
    assert not store.one("SELECT 1 AS x FROM mail_outbox WHERE kind='second_factor_reset_requested'")
    assert {json.loads(r["meta"])["code"] for r in audits(w, REQUEST, "denied")} >= {
        "missing_reason", "bad_method", "admin_confirmation_required", "admin_preview_invalid", "admin_preview_expired",
        "admin_preview_stale",
        "no_user", "self_reset", "nothing_to_reset", "target_is_platform_admin", "application_identity",
        "account_state_conflict"}

    # The same request again is answered from its stored result; the same key with another
    # request is a conflict; a second reset while one is pending is refused.
    plan = preview(w)
    body = body_for(plan)
    first = ask(w, body)
    assert first.status_code == 200, first.text
    assert ask(w, body).json() == first.json()
    refusal(ask(w, {**body, "reason": REASON + " (changed)"}), 409, "idempotency_conflict")
    assert len(store.all("SELECT * FROM second_factor_resets")) == 1 and len(audits(w, REQUEST, "ok")) == 1
    assert len(store.all("SELECT * FROM mail_outbox WHERE kind='second_factor_reset_requested'")) == 1
    refusal(post(admin, base.format(uid), {}), 409, "reset_pending")
    refusal(ask(w, body_for(plan)), 409, "reset_pending")


def test_mail_that_cannot_be_queued_leaves_no_pending_reset(world, monkeypatch):
    w = world
    enrol_totp(w["person"])
    plan = preview(w)
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    response = ask(w, body_for(plan))
    assert response.status_code == 503 and response.json()["error"] == "mail_not_configured"
    assert not w["store"].all("SELECT * FROM second_factor_resets"), "the request rolled back with its announcement"
    assert not audits(w, REQUEST, "ok") and not w["store"].all("SELECT * FROM admin_operations")


# ---------------------------------------------------------------- (f) permissions
def test_only_the_recover_permission_resets_support_reads_counts_and_others_see_nothing(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    secret, codes = enrol_totp(w["person"])
    out = requested(w)
    token = mailed_token(w)
    paths = (f"/api/admin/users/{uid}/second-factor-reset/preview", f"/api/admin/users/{uid}/second-factor-reset",
             f"/api/admin/users/{uid}/second-factor-reset/{out['reset']['id']}/cancel")
    access = w["login"](w["access"])
    w["reauth"](access)
    for path in paths:
        response = post(access, path, {"reason": REASON, "method": "in_person"})
        assert response.status_code == 403 and response.json()["error"] == "platform_permission_denied", (path, response.text)
        assert response.json()["detail"]["permission"] == "platform.users.recover"

    support = w["login"](w["support"])
    state = support.get(f"/api/admin/users/{uid}/second-factor")
    assert state.status_code == 200, state.text
    body = state.json()
    assert set(body) == {"totp", "recovery_remaining", "passkeys", "pending_reset", "history"}
    assert (body["totp"], body["recovery_remaining"], body["passkeys"]) == (True, 10, 0)
    assert body["pending_reset"] == {"id": out["reset"]["id"], "created_at": body["pending_reset"]["created_at"],
                                     "due_at": out["reset"]["due_at"], "method": "video_call",
                                     "requested_by_email": "root@example.test", "include_passkeys": False,
                                     "announcement_status": "queued", "announcement_sent_at": None,
                                     "effective_due_at": None}
    assert [h["id"] for h in body["history"]] == [out["reset"]["id"]] and body["history"][0]["status"] == "pending"
    for leaked in (secret, token, hashlib.sha256(token.encode()).hexdigest(), *codes):
        assert leaked not in state.text
    assert "secret" not in state.text and "hash" not in state.text and REASON not in state.text
    for path in paths:
        assert post(support, path, {"reason": REASON}).json()["detail"]["permission"] == "platform.users.recover"
    assert support.get("/api/admin/users/u_fixture_missing/second-factor").json()["error"] == "no_user"

    plain = w["login"](w["plain"])
    for path in paths:
        response = post(plain, path, {"reason": REASON})
        assert response.status_code == 404 and response.json()["error"] == "not_found", path
    assert plain.get(f"/api/admin/users/{uid}/second-factor").status_code == 404

    # Strict re-authentication comes before the body is read: a fresh sign-in without it is
    # refused even with an empty body.
    fresh_root = w["login"](w["root"])
    for path in paths[1:]:
        response = post(fresh_root, path, {})
        assert response.status_code == 403 and response.json()["error"] == "reauth_required", (path, response.text)
    assert store.one("SELECT status FROM second_factor_resets")["status"] == "pending"


# ---------------------------------------------------------------- (g) administrator cancel
def test_an_administrator_cancels_with_a_reason_and_the_person_is_told(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    out = requested(w)
    token = mailed_token(w)
    rid = out["reset"]["id"]
    path = f"/api/admin/users/{uid}/second-factor-reset/{rid}/cancel"
    missing = post(w["admin"], path, {"reason": "short"})
    assert missing.status_code == 400 and missing.json()["error"] == "missing_reason"
    unknown = post(w["admin"], f"/api/admin/users/{uid}/second-factor-reset/sfr_fixture_missing/cancel", {"reason": REASON})
    assert unknown.status_code == 404 and unknown.json()["error"] == "no_reset"
    other = post(w["admin"], f"/api/admin/users/{w['plain']['id']}/second-factor-reset/{rid}/cancel", {"reason": REASON})
    assert other.status_code == 404 and other.json()["error"] == "no_reset", "a reset is cancelled through its own account"

    response = post(w["admin"], path, {"reason": REASON})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True and response.json()["reset"]["status"] == "cancelled"
    row = store.one("SELECT * FROM second_factor_resets WHERE id=?", (rid,))
    assert row["status"] == "cancelled" and row["cancelled_by"] == w["root"]["id"]
    [audit] = audits(w, CANCEL, "ok")
    meta = json.loads(audit["meta"])
    assert audit["actor_id"] == w["root"]["id"] and meta["reason"] == REASON and meta["reset_id"] == rid
    assert meta["changed"] == ["status"] and meta["after"] == {"status": "cancelled"}
    mail = store.one("SELECT * FROM mail_outbox WHERE kind='second_factor_reset_cancelled'")
    assert mail["to_addr"] == "target@example.test" and "平台管理员" in mail["body"]
    note = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='security.change' ORDER BY seq DESC LIMIT 1", (uid,))
    assert "second_factor_reset_cancelled" in json.dumps(dict(note), default=str)

    again = post(w["admin"], path, {"reason": REASON})
    assert again.status_code == 409 and again.json()["error"] == "reset_not_pending"
    assert TestClient(w["api"]).post("/api/auth/second-factor-reset/cancel", json={"token": token}).status_code == 410
    fall_due(w, rid)
    assert complete_due_second_factor_resets(store)["completed"] == 0 and factors(store, uid)["totp"] is True
    # The history shows the closed reset; a new one may be asked for.
    history = w["admin"].get(f"/api/admin/users/{uid}/second-factor").json()
    assert history["pending_reset"] is None and history["history"][0]["status"] == "cancelled"
    assert history["history"][0]["cancelled_at"] == row["cancelled_at"]
    assert preview(w)["factors"]["totp"] is True


def change_email(w, client, address):
    """The person's own address change through the real routes (request, mailed token, confirm)."""
    response = post(client, "/api/auth/email-change", {"email": address, "password": PW})
    assert response.status_code == 200, response.text
    row = w["store"].one("SELECT body FROM mail_outbox WHERE kind='email_change' AND to_addr=? ORDER BY created_at DESC LIMIT 1",
                         (address,))
    token = re.search(r"#token=([A-Za-z0-9_-]+)", row["body"])[1]
    response = post(client, "/api/auth/email-change/confirm", {"token": token, "password": PW})
    assert response.status_code == 200, response.text


def person_told(w, uid):
    """The void notices the person got: (mail kinds, inbox `void_reason`s)."""
    mails = [r["to_addr"] for r in w["store"].all("SELECT to_addr FROM mail_outbox WHERE kind='second_factor_reset_void'")]
    inbox = [n["void_reason"] for n in notices(w, uid, "security.change") if n.get("what") == "second_factor_reset_void"]
    return mails, inbox


# ---------------------------------------------------------------- the announcement decides when the delay ends
@pytest.mark.parametrize("fate", ["failed", "dropped", "stuck"])
def test_an_undelivered_announcement_voids_the_reset_which_never_completes(world, fate):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    mail = announcement_of(w, rid)
    if fate == "failed":
        # FIXTURE: the worker's terminal record of an SMTP answer it cannot trust.
        store.execute("UPDATE mail_outbox SET status='failed',last_error='delivery_unknown',next_attempt_at=NULL WHERE id=?",
                      (mail["id"],))
        fall_due(w, rid, delivered=False)
    elif fate == "dropped":
        store.execute("UPDATE mail_outbox SET status='dropped' WHERE id=?", (mail["id"],))     # FIXTURE: a restore dropped it
        fall_due(w, rid, delivered=False)
    else:
        # Still queued: within a day of the due time it waits; a day past it, the queue is stuck.
        fall_due(w, rid, delivered=False)
        assert complete_due_second_factor_resets(store) == outcome(waiting=1)
        store.execute("UPDATE second_factor_resets SET due_at=? WHERE id=?", (now() - ANNOUNCEMENT_GRACE_S - 1, rid))  # FIXTURE
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    assert void_of(w, rid) == {"status": "void", "void_reason": "announcement_undelivered"}
    assert factors(store, uid) == {"totp": True, "recovery_remaining": 10, "passkeys": 0} and needs_mfa(w, w["target"])
    [audit] = audits(w, COMPLETE)
    assert audit["result"] == "denied" and json.loads(audit["meta"])["code"] == "announcement_undelivered"
    # About the person's own mailbox: nothing is sent to it; the administrator who asked is told.
    assert person_told(w, uid) == ([], [])
    assert notices(w, w["root"]["id"], "platform.second_factor_reset_void") == [
        {"email": "target@example.test", "void_reason": "announcement_undelivered"}]
    assert complete_due_second_factor_resets(store) == outcome()


def test_a_late_announcement_still_leaves_the_full_delay_after_delivery(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    fall_due(w, rid, delivered_ago=12 * 3600)        # delivered 60 hours after the request, 12 hours ago
    sent_at = announcement_of(w, rid)["sent_at"]
    assert complete_due_second_factor_resets(store) == outcome(waiting=1)
    pending = w["admin"].get(f"/api/admin/users/{uid}/second-factor").json()["pending_reset"]
    assert (pending["announcement_status"], pending["announcement_sent_at"], pending["effective_due_at"]) == (
        "sent", sent_at, sent_at + SECOND_FACTOR_RESET_DELAY_S)
    assert factors(store, uid)["totp"] is True
    store.execute("UPDATE mail_outbox SET sent_at=? WHERE id=?",                    # FIXTURE: 72 hours since delivery
                  (now() - SECOND_FACTOR_RESET_DELAY_S - 1, announcement_of(w, rid)["id"]))
    assert complete_due_second_factor_resets(store) == outcome(completed=1)
    assert factors(store, uid)["totp"] is False


# ---------------------------------------------------------------- everything the request relied on, again
@pytest.mark.parametrize("lost", ["revoked", "suspended"])
def test_a_reset_whose_requester_lost_the_authority_is_void(world, lost):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    if lost == "revoked":
        end_platform_grants(store, w["root"])                 # FIXTURE: the requester's grant ends in the window
    else:
        w["ident"].set_status(w["root"]["id"], "suspended")   # FIXTURE: the requester is suspended in the window
    fall_due(w, rid)
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    assert void_of(w, rid)["void_reason"] == "authority_changed"
    assert factors(store, uid)["totp"] is True
    # The person is told in words; the former administrator is not.
    assert person_told(w, uid) == (["target@example.test"], ["authority_changed"])
    mail = store.one("SELECT body FROM mail_outbox WHERE kind='second_factor_reset_void'")["body"]
    assert "申请的管理员已不再有权执行这项重置" in mail and "authority_changed" not in mail
    assert not notices(w, w["root"]["id"], "platform.second_factor_reset_void")


def test_an_address_changed_during_the_window_voids_the_reset(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    change_email(w, w["person"], "moved@example.test")
    fall_due(w, rid)
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    assert void_of(w, rid)["void_reason"] == "email_changed"
    assert factors(store, uid)["totp"] is True
    assert person_told(w, uid) == ([], []), "about the mailbox itself: neither address is told"
    assert notices(w, w["root"]["id"], "platform.second_factor_reset_void")[0]["void_reason"] == "email_changed"


def test_a_recently_changed_address_is_refused_until_seven_days_have_passed(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    change_email(w, w["person"], "moved@example.test")
    path = f"/api/admin/users/{uid}/second-factor-reset/preview"
    refused = post(w["admin"], path, {})
    assert refused.status_code == 409 and refused.json()["error"] == "recent_email_change"
    assert refused.json()["detail"] == {"days": 7}
    # FIXTURE: the change was eight days ago (its authoritative record is the audit row).
    store.execute("UPDATE audit_events SET at=? WHERE action='auth.email.changed' AND actor_id=?", (now() - 8 * 86400, uid))
    assert preview(w)["target"]["email"] == "moved@example.test"


def test_an_unverified_address_is_refused_and_a_lapsed_verification_voids_the_reset(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    unverified = w["ident"].create_user("unverified@example.test", PW, status="active")      # FIXTURE: never verified
    refused = post(w["admin"], f"/api/admin/users/{unverified['id']}/second-factor-reset/preview", {})
    assert refused.status_code == 409 and refused.json()["error"] == "email_unverified"
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    fall_due(w, rid)
    store.execute("UPDATE users SET email_verified_at=NULL WHERE id=?", (uid,))              # FIXTURE
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    assert void_of(w, rid)["void_reason"] == "email_unverified" and person_told(w, uid) == ([], [])


@pytest.mark.parametrize("use", ["authenticator_sign_in", "recovery_code_sign_in", "nothing_left"])
def test_a_factor_used_or_gone_during_the_window_voids_the_reset(world, use):
    w, store, uid = world, world["store"], world["target"]["id"]
    secret, codes = enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    if use == "nothing_left":
        store.execute("DELETE FROM totp_secrets WHERE user_id=?", (uid,))                   # FIXTURE
        store.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))                 # FIXTURE
        expected = "nothing_to_reset"
    else:
        # Proof of possession through the real sign-in: the factor is not lost.
        fresh = TestClient(w["api"])
        ticket = post(fresh, "/api/auth/login", {"email": "target@example.test", "password": PW}).json()["ticket"]
        second = ({"code": pyotp.TOTP(secret).at(int(time.time()) + 30)} if use == "authenticator_sign_in"
                  else {"recovery_code": codes[0]})
        assert post(fresh, "/api/auth/login/mfa", {"ticket": ticket, **second}).status_code == 200
        expected = "factors_changed"
    fall_due(w, rid)
    assert complete_due_second_factor_resets(store) == outcome(void=1)
    assert void_of(w, rid)["void_reason"] == expected
    if use != "nothing_left":
        assert factors(store, uid)["totp"] is True and person_told(w, uid) == (["target@example.test"], [expected])
    assert notices(w, w["root"]["id"], "platform.second_factor_reset_void")[0]["void_reason"] == expected


def test_suspending_the_account_voids_its_pending_reset_in_the_same_act(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    plan = post(w["admin"], f"/api/admin/users/{uid}/status-preview", {"status": "suspended"}).json()
    suspended = post(w["admin"], f"/api/admin/users/{uid}/status",
                     {"status": "suspended", "reason": REASON, "preview_token": plan["preview_token"],
                      "idempotency_key": uuid.uuid4().hex})
    assert suspended.status_code == 200, suspended.text
    assert void_of(w, rid) == {"status": "void", "void_reason": "account_suspended"}
    [audit] = audits(w, COMPLETE)
    meta = json.loads(audit["meta"])
    assert audit["actor_kind"] == "system" and audit["result"] == "denied" and meta["code"] == "account_suspended"
    assert meta["suspended_by"] == w["root"]["id"] and meta["operation_id"] == suspended.json()["operation_id"]
    assert person_told(w, uid) == (["target@example.test"], ["account_suspended"])
    assert notices(w, w["root"]["id"], "platform.second_factor_reset_void")[0]["void_reason"] == "account_suspended"
    fall_due(w, rid)
    assert complete_due_second_factor_resets(store) == outcome()


# ---------------------------------------------------------------- the database, the sweeper, failures
def test_the_database_holds_at_most_one_pending_reset_per_account(world):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    requested(w)
    insert = ("INSERT INTO second_factor_resets (id,user_id,requested_by,method,reason,cancel_token_hash,created_at,due_at,status) "
              "VALUES (?,?,?,'other','Fixture: a direct second row',?,?,?,?)")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(insert, ("fixture-second-pending", uid, w["root"]["id"], "fixture-hash-a", now(), now(), "pending"))
    store.execute(insert, ("fixture-closed", uid, w["root"]["id"], "fixture-hash-b", now(), now(), "cancelled"))
    assert len(store.all("SELECT * FROM second_factor_resets WHERE user_id=?", (uid,))) == 2


def test_a_due_time_or_status_sent_by_the_client_is_ignored(world):
    w, store = world, world["store"]
    enrol_totp(w["person"])
    plan = preview(w)
    response = ask(w, {**body_for(plan), "due_at": 0, "status": "completed", "delay": 1})
    assert response.status_code == 200, response.text
    row = store.one("SELECT * FROM second_factor_resets")
    assert row["status"] == "pending" and row["due_at"] == row["created_at"] + SECOND_FACTOR_RESET_DELAY_S
    assert complete_due_second_factor_resets(store) == outcome()


def test_completion_with_mail_disabled_still_completes_and_records_it(world, monkeypatch):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    fall_due(w, rid)
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    assert w["api"].state.auth_sweeper.run_once()["second_factor_resets"] == outcome(completed=1)
    assert json.loads(audits(w, COMPLETE, "ok")[0]["meta"])["mailed"] is False
    assert not store.one("SELECT 1 AS x FROM mail_outbox WHERE kind='second_factor_reset_completed'")
    assert factors(store, uid)["totp"] is False


def test_a_journal_that_cannot_be_written_leaves_the_reset_pending(world, monkeypatch):
    w, store, uid = world, world["store"], world["target"]["id"]
    enrol_totp(w["person"])
    rid = requested(w, include=False)["reset"]["id"]
    fall_due(w, rid)
    original = journal.record_account

    def unavailable(*args, **kwargs):
        raise OSError("fixture: the journal cannot be written")
    monkeypatch.setattr(journal, "record_account", unavailable)
    assert complete_due_second_factor_resets(store) == outcome(failed=1)
    monkeypatch.setattr(journal, "record_account", original)
    assert store.one("SELECT status FROM second_factor_resets WHERE id=?", (rid,))["status"] == "pending"
    assert factors(store, uid) == {"totp": True, "recovery_remaining": 10, "passkeys": 0}
    assert w["person"].get("/api/auth/me").status_code == 200, "the sessions were not ended either"
    assert not audits(w, COMPLETE) and not store.one("SELECT 1 AS x FROM mail_outbox WHERE kind='second_factor_reset_completed'")
    assert complete_due_second_factor_resets(store) == outcome(completed=1)


def test_one_reset_that_fails_does_not_block_another(world, monkeypatch):
    w, store, uid = world, world["store"], world["target"]["id"]
    other = w["verified"]("second-person@example.test")                       # FIXTURE account
    enrol_totp(w["login"](other))
    enrol_totp(w["person"])
    first, second = requested(w)["reset"]["id"], requested(w, uid=other["id"])["reset"]["id"]
    fall_due(w, first)
    fall_due(w, second)
    original = journal.record_account

    def fails_for_the_first(store_, kind, *, user_id, event_id):
        if user_id == uid:
            raise OSError("fixture: the journal refuses this one")
        return original(store_, kind, user_id=user_id, event_id=event_id)
    monkeypatch.setattr(journal, "record_account", fails_for_the_first)
    assert complete_due_second_factor_resets(store) == outcome(completed=1, failed=1)
    monkeypatch.setattr(journal, "record_account", original)
    assert [store.one("SELECT status FROM second_factor_resets WHERE id=?", (rid,))["status"] for rid in (first, second)] == [
        "pending", "completed"]


def test_one_failing_sweep_job_does_not_skip_the_others(world, monkeypatch, capfd):
    w = world
    enrol_totp(w["person"])
    rid = requested(w)["reset"]["id"]
    fall_due(w, rid)
    sweeper = w["api"].state.auth_sweeper
    original = sweeper.tickets.sweep

    def broken(*args, **kwargs):
        raise RuntimeError("fixture failure whose text must not be logged")
    monkeypatch.setattr(sweeper.tickets, "sweep", broken)
    done = sweeper.run_once()
    monkeypatch.setattr(sweeper.tickets, "sweep", original)
    assert done["mfa_tickets"] == {"error": "RuntimeError"}
    assert done["second_factor_resets"] == outcome(completed=1), "the jobs after the failed one still ran"
    assert {"sessions", "webauthn_challenges", "account_deletions", "wallet_reminders"} <= set(done)
    printed = capfd.readouterr()
    assert "auth sweep job mfa_tickets failed: RuntimeError" in printed.out
    assert "fixture failure whose text" not in printed.out + printed.err

