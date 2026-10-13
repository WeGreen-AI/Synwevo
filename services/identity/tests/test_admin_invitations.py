"""A47: platform oversight of invitations across workspaces, through the real account service.

Isolated data directory and file mail; no mail worker runs. Every invitation is issued through the
workspace's own route, and accepted, declined and revoked through the real public and tenant
routes. Where a state cannot be reached by a route within a test (an expired link, a bounced mail,
the re-invite cooldown) the row is moved by hand, and each such write is marked FIXTURE.

Held: the list spans workspaces with exact state groups and filters, never shows an invitee's
address, a token or a mail's stored error text; the platform revoke kills the link at preview and
acceptance and records itself in the journal, the workspace's own log (`by: platform`) and the
platform's; accepted and declined invitations are refused; a batch is a set of single acts with one
batch record; reading needs `platform.workspaces.view`, revoking `platform.workspaces.manage` and
the strict re-authentication before the body; the workspace's own invitation list is unchanged.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

from .platform_fixture import make_platform_admin

PW = "isolated-invitation-oversight-fixture-47"
REASON = "Isolated fixture: invitations used to send unsolicited mail"
#: FIXTURE: what an older release stored as a delivery error — raw transport text, echoing the
#: recipient. The oversight page must show its classified code, never this.
RAW_SMTP_ERROR = "550 5.1.1 <bounce@example.org>: Recipient address rejected: User unknown"
ITEM_KEYS = {"id", "state", "role", "created_at", "expires_at", "revoked_at", "accepted_at", "declined_at",
             "invitee", "workspace", "inviter", "via", "mail"}
ADDRESSES = {"pend": "pending.one@example.org", "known": "carol@example.net", "expired": "late@example.org",
             "accepted": "joiner@example.org", "declined": "dave@example.net", "revoked": "withdrawn@example.org",
             "failed": "bounce@example.org", "pend2": "pending.two@example.com"}


def _h(client):
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""}


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {}, headers=_h(client))


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

    def verified(email, **kw):
        user = ident.create_user(email, PW, status="active", **kw)
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        return ident.get_user(user["id"])

    def login(user):
        client = TestClient(api, raise_server_exceptions=False)
        clients.append(client)
        r = post(client, "/api/auth/login", {"email": user["email"], "password": PW})
        assert r.status_code == 200, r.text
        return client

    def reauth(client):
        r = post(client, "/api/auth/reauth", {"password": PW, "scope": "admin"})
        assert r.status_code == 200, r.text

    root = make_platform_admin(store, verified("root@example.test"))
    alice = verified("alice@example.test", display_name="Alice")      # owns her personal workspace
    bob = verified("bob@example.test", display_name="Bob")            # owns an organization's workspace
    carol = verified("carol@example.net")                             # an invitee who has an account
    dave = verified("dave@example.net")                               # an invitee who declines
    org = Organizations(store, ident).create(bob["id"], "Harbor fixture organization", "Harbor fixture workspace",
                                             "fixture-org-a47")
    ws_a, ws_b, oid = ident.workspaces_for(alice["id"])[0]["id"], org["workspace"]["id"], org["organization"]["id"]
    admin, a, b = login(root), login(alice), login(bob)
    reauth(admin)

    def invite(client, wid, email, role="viewer"):
        r = post(client, f"/api/auth/workspaces/{wid}/invitations", {"email": email, "role": role})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def token(iid):
        body = store.one("SELECT body FROM mail_outbox WHERE kind='invite' AND ref_id=?", (iid,))["body"]
        return body.split("token=")[1].split()[0]

    ids = {"pend": invite(a, ws_a, ADDRESSES["pend"]), "known": invite(a, ws_a, ADDRESSES["known"], "editor"),
           "expired": invite(a, ws_a, ADDRESSES["expired"]), "accepted": invite(a, ws_a, ADDRESSES["accepted"]),
           "declined": invite(b, ws_b, ADDRESSES["declined"]), "revoked": invite(b, ws_b, ADDRESSES["revoked"]),
           "failed": invite(b, ws_b, ADDRESSES["failed"]), "pend2": invite(b, ws_b, ADDRESSES["pend2"])}
    tokens = {name: token(iid) for name, iid in ids.items()}
    # FIXTURE: the link's expiry moved into the past (seven days cannot pass in a test).
    store.execute("UPDATE invitations SET expires_at=? WHERE id=?", (now() - 1, ids["expired"]))
    guest = TestClient(api, raise_server_exceptions=False)
    clients.append(guest)
    r = post(guest, "/api/auth/invitations/accept", {"token": tokens["accepted"], "password": PW,
                                                     "display_name": "Fixture joiner"})
    assert r.status_code == 200, r.text
    r = post(login(dave), "/api/auth/invitations/decline", {"token": tokens["declined"]})
    assert r.status_code == 200, r.text
    r = b.delete(f"/api/auth/invitations/{ids['revoked']}", headers=_h(b))
    assert r.status_code == 200, r.text
    # FIXTURE: the outbox as the worker leaves it after a permanent rejection (an older release
    # kept the transport text), and after a delivery — no worker runs in these tests.
    store.execute("UPDATE mail_outbox SET status='failed', attempts=3, last_error=?, next_attempt_at=NULL "
                  "WHERE kind='invite' AND ref_id=?", (RAW_SMTP_ERROR, ids["failed"]))
    store.execute("UPDATE mail_outbox SET status='sent', attempts=1, sent_at=? WHERE kind='invite' AND ref_id=?",
                  (now(), ids["pend2"]))
    yield {"api": api, "store": store, "ident": ident, "root": root, "alice": alice, "bob": bob, "carol": carol,
           "dave": dave, "ws_a": ws_a, "ws_b": ws_b, "oid": oid, "admin": admin, "a": a, "b": b, "guest": guest,
           "ids": ids, "tokens": tokens, "login": login, "reauth": reauth, "verified": verified, "invite": invite,
           "data": tmp_path / "data"}
    for client in clients:
        client.close()


def listing(w, client=None, **params):
    r = (client or w["admin"]).get("/api/admin/invitations", params=params)
    assert r.status_code == 200, r.text
    return r


def ids_of(response) -> set[str]:
    return {item["id"] for item in response.json()["items"]}


def names(w, *keys) -> set[str]:
    return {w["ids"][k] for k in keys}


def audits(w, action, object_id=None):
    rows = w["store"].all("SELECT * FROM audit_events WHERE action=? AND (? IS NULL OR object_id=?) ORDER BY seq",
                          (action, object_id, object_id))
    return [{**row, "meta": json.loads(row["meta"] or "{}")} for row in rows]


def revocations_journaled(w, iid) -> int:
    return sum(1 for e in journal.read(w["data"]) if e["kind"] == "invitation_revoked" and e.get("invitation_id") == iid)


ALL_GROUPS = {"pending": 4, "expired": 1, "mail_failed": 1, "revoked": 1, "accepted": 1, "declined": 1}


# ---------------------------------------------------------------- (a) the list
def test_the_list_spans_workspaces_with_exact_states_groups_and_masked_invitees(world):
    w = world
    r = listing(w, state="all")
    body = r.json()
    assert body["groups"] == ALL_GROUPS and body["total"] == 8 and body["offset"] == 0 and body["limit"] == 50
    assert isinstance(body["snapshot"], int) and body["snapshot"] > 0
    items = {item["id"]: item for item in body["items"]}
    assert {item["id"]: item["state"] for item in body["items"]} == {
        w["ids"][k]: s for k, s in (("pend", "pending"), ("known", "pending"), ("expired", "expired"),
                                    ("accepted", "accepted"), ("declined", "declined"), ("revoked", "revoked"),
                                    ("failed", "pending"), ("pend2", "pending"))}
    assert all(set(item) == ITEM_KEYS for item in body["items"])
    created = [item["created_at"] for item in body["items"]]
    assert created == sorted(created, reverse=True), "newest first"
    # The invitee: masked, with the domain and whether an account holds the address.
    assert items[w["ids"]["pend"]]["invitee"] == {"masked": "p***@example.org", "domain": "example.org",
                                                  "has_account": False}
    assert items[w["ids"]["known"]]["invitee"] == {"masked": "c***@example.net", "domain": "example.net",
                                                   "has_account": True}
    assert items[w["ids"]["accepted"]]["invitee"]["has_account"] is True, "the acceptance created the account"
    assert items[w["ids"]["declined"]]["invitee"]["has_account"] is True
    for address in ADDRESSES.values():
        assert address not in r.text, address
    # No link, token or token hash, and no stored transport text.
    for raw in w["tokens"].values():
        assert raw not in r.text
    assert "token" not in r.text and "550 5.1.1" not in r.text and "User unknown" not in r.text
    for row in w["store"].all("SELECT token_hash FROM invitations"):
        assert row["token_hash"] not in r.text
    # The workspace, the inviter (an account, named as the member list names members) and the mail.
    pend, failed, pend2 = (items[w["ids"][k]] for k in ("pend", "failed", "pend2"))
    personal = w["ident"].get_workspace(w["ws_a"])
    assert pend["workspace"] == {"id": w["ws_a"], "name": personal["name"], "kind": "personal", "status": "active",
                                 "organization_id": None}
    assert failed["workspace"] == {"id": w["ws_b"], "name": "Harbor fixture workspace", "kind": "org",
                                   "status": "active", "organization_id": w["oid"]}
    assert pend["inviter"] == {"id": w["alice"]["id"], "email": "alice@example.test", "display_name": "Alice"}
    assert failed["inviter"] == {"id": w["bob"]["id"], "email": "bob@example.test", "display_name": "Bob"}
    assert pend["via"] is None and items[w["ids"]["known"]]["role"] == "editor"
    assert pend["mail"] == {"status": "queued", "attempts": 0, "last_error_code": None}
    assert failed["mail"] == {"status": "failed", "attempts": 3, "last_error_code": "mail_delivery_failed"}
    assert pend2["mail"] == {"status": "sent", "attempts": 1, "last_error_code": None}
    assert items[w["ids"]["accepted"]]["accepted_at"] and items[w["ids"]["declined"]]["declined_at"]
    assert items[w["ids"]["revoked"]]["revoked_at"] and pend["revoked_at"] is None
    # FIXTURE: a classified code the mail tab shows as it is passes through unchanged.
    w["store"].execute("UPDATE mail_outbox SET last_error='smtp_recipient_rejected_550' WHERE kind='invite' AND ref_id=?",
                       (w["ids"]["failed"],))
    again = listing(w, state="mail_failed").json()
    assert [i["id"] for i in again["items"]] == [w["ids"]["failed"]]
    assert again["items"][0]["mail"]["last_error_code"] == "smtp_recipient_rejected_550"
    # FIXTURE: a mail the worker dropped counts as undelivered too.
    w["store"].execute("UPDATE mail_outbox SET status='dropped' WHERE kind='invite' AND ref_id=?", (w["ids"]["pend2"],))
    assert ids_of(listing(w, state="mail_failed")) == names(w, "failed", "pend2")
    assert listing(w, state="all").json()["groups"] == {**ALL_GROUPS, "mail_failed": 2}


def test_the_state_filter_selects_and_the_groups_ignore_it(world):
    w = world
    default = listing(w).json()
    assert ids_of(listing(w)) == names(w, "pend", "known", "failed", "pend2") and default["total"] == 4
    for state, keys in (("pending", ("pend", "known", "failed", "pend2")), ("mail_failed", ("failed",)),
                        ("expired", ("expired",)), ("accepted", ("accepted",)), ("declined", ("declined",)),
                        ("revoked", ("revoked",))):
        r = listing(w, state=state)
        assert ids_of(r) == names(w, *keys), state
        assert r.json()["total"] == len(keys) and r.json()["groups"] == ALL_GROUPS, state
        assert {i["state"] for i in r.json()["items"]} == {"pending" if state == "mail_failed" else state}
    assert w["admin"].get("/api/admin/invitations", params={"state": "bogus"}).status_code == 422
    assert w["admin"].get("/api/admin/invitations", params={"q": "x" * 101}).status_code == 422
    assert w["admin"].get("/api/admin/invitations", params={"limit": 201}).status_code == 422


def test_search_by_address_domain_and_workspace_name_and_the_workspace_filter(world):
    w = world
    exact = listing(w, state="all", q="  Bounce@Example.ORG ").json()
    assert [i["id"] for i in exact["items"]] == [w["ids"]["failed"]]
    assert exact["groups"] == {"pending": 1, "expired": 0, "mail_failed": 1, "revoked": 0, "accepted": 0, "declined": 0}
    assert ids_of(listing(w, state="all", q="@example.net")) == names(w, "known", "declined")
    assert ids_of(listing(w, state="all", q="@EXAMPLE.org")) == names(w, "pend", "expired", "accepted", "revoked", "failed")
    # An address is found only whole: a fragment of one matches nothing (no workspace is named so).
    for fragment in ("bounce", "bounce@example", "@example", "example.org", "%", "_"):
        r = listing(w, state="all", q=fragment).json()
        assert r["items"] == [] and r["total"] == 0, fragment
    harbor = names(w, "declined", "revoked", "failed", "pend2")
    assert ids_of(listing(w, state="all", q="harbor")) == harbor
    assert ids_of(listing(w, state="all", q="fixture workspace")) == harbor
    assert ids_of(listing(w, state="all", workspace_id=w["ws_a"])) == names(w, "pend", "known", "expired", "accepted")
    assert listing(w, state="all", workspace_id=w["ws_a"]).json()["groups"] == {
        "pending": 2, "expired": 1, "mail_failed": 0, "revoked": 0, "accepted": 1, "declined": 0}
    assert ids_of(listing(w, state="all", workspace_id=w["ws_a"], q="harbor")) == set()
    assert ids_of(listing(w, state="all", workspace_id="ws_fixture_missing")) == set()
    assert ids_of(listing(w, workspace_id=w["ws_b"], q="@example.com")) == names(w, "pend2")


def test_paging_holds_a_snapshot(world):
    w = world
    first = listing(w, state="all", limit=3).json()
    assert len(first["items"]) == 3 and first["total"] == 8
    snapshot = first["snapshot"]
    fresh = w["invite"](w["a"], w["ws_a"], "fresh@example.org")
    pages = [first] + [listing(w, state="all", limit=3, offset=offset, snapshot=snapshot).json() for offset in (3, 6)]
    assert [len(p["items"]) for p in pages] == [3, 3, 2] and {p["total"] for p in pages} == {8}
    assert {p["snapshot"] for p in pages} == {snapshot} and pages[1]["groups"] == ALL_GROUPS
    seen = [i["id"] for p in pages for i in p["items"]]
    assert len(seen) == len(set(seen)) == 8 and set(seen) == set(w["ids"].values()) and fresh not in seen
    created = [i["created_at"] for p in pages for i in p["items"]]
    assert created == sorted(created, reverse=True)
    now_ = listing(w, state="all").json()
    assert now_["total"] == 9 and now_["groups"]["pending"] == 5 and fresh in ids_of(listing(w))
    # A snapshot beyond the newest row is the newest row.
    assert listing(w, state="all", snapshot=now_["snapshot"] + 1000).json()["snapshot"] == now_["snapshot"]


# ---------------------------------------------------------------- (b) one revoke
def test_a_platform_revoke_kills_the_link_and_records_itself_everywhere(world):
    w = world
    store, iid, guest = w["store"], w["ids"]["pend"], w["guest"]
    assert guest.get("/api/auth/invitations/preview", params={"token": w["tokens"]["pend"]}).json()["state"] == "pending"
    r = post(w["admin"], f"/api/admin/invitations/{iid}/revoke", {"reason": REASON})
    assert r.status_code == 200, r.text
    revoked_at = store.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"]
    assert r.json() == {"ok": True, "already": False, "journaled": True,
                        "invitation": {"id": iid, "state": "revoked", "revoked_at": revoked_at}}
    assert ADDRESSES["pend"] not in r.text and w["tokens"]["pend"] not in r.text
    # The link fails at preview and at acceptance, through the public routes.
    assert guest.get("/api/auth/invitations/preview", params={"token": w["tokens"]["pend"]}).json()["state"] == "revoked"
    assert post(guest, "/api/auth/invitations/preview", {"token": w["tokens"]["pend"]}).json()["state"] == "revoked"
    accept = post(guest, "/api/auth/invitations/accept", {"token": w["tokens"]["pend"], "password": PW,
                                                         "display_name": "Fixture"})
    assert accept.status_code == 409 and accept.json()["error"] == "invite_revoked"
    assert not w["ident"].by_email(ADDRESSES["pend"])
    # The journal, the workspace's own record (`by: platform`) and the platform's.
    assert revocations_journaled(w, iid) == 1
    [tenant] = audits(w, "invite.revoke", iid)
    assert (tenant["workspace_id"], tenant["actor_id"], tenant["object_kind"], tenant["result"]) == (
        w["ws_a"], w["root"]["id"], "invitation", "ok")
    assert tenant["meta"] == {"by": "platform", "reason": REASON, "journaled": True}
    [platform] = audits(w, "admin.invitation.revoke", iid)
    assert (platform["workspace_id"], platform["actor_id"], platform["object_kind"]) == (None, w["root"]["id"], "invitation")
    assert platform["meta"] == {"reason": REASON, "workspace_id": w["ws_a"], "state_before": "pending", "journaled": True}
    assert ADDRESSES["pend"] not in json.dumps(platform["meta"]) + json.dumps(tenant["meta"])
    # The workspace's directory feed hears it, and the workspace's own list shows it.
    assert store.one("SELECT COUNT(*) AS n FROM job_events WHERE type='invitation.revoked' AND tenant=? "
                     "AND json_extract(data,'$.invitation_id')=?", (w["ws_a"], iid))["n"] == 1
    tenant_list = {i["id"]: i["state"] for i in w["a"].get(f"/api/auth/workspaces/{w['ws_a']}/invitations").json()["items"]}
    assert tenant_list[iid] == "revoked"
    # Revoking again writes nothing.
    rows_before = store.one("SELECT COUNT(*) AS n FROM audit_events")["n"]
    again = post(w["admin"], f"/api/admin/invitations/{iid}/revoke", {"reason": REASON})
    assert again.status_code == 200 and again.json() == {"ok": True, "already": True, "journaled": None,
                                                         "invitation": {"id": iid, "state": "revoked", "revoked_at": revoked_at}}
    assert store.one("SELECT COUNT(*) AS n FROM audit_events")["n"] == rows_before
    assert revocations_journaled(w, iid) == 1
    # The platform withdrew a link, not an address: the inviter may invite it again.
    # FIXTURE: the per-address re-invite cooldown (60 s) moved past.
    store.execute("UPDATE invitations SET created_at=? WHERE id=?", (now() - 61, iid))
    reinvited = w["invite"](w["a"], w["ws_a"], ADDRESSES["pend"])
    assert reinvited != iid and listing(w, q=ADDRESSES["pend"]).json()["items"][0]["id"] == reinvited


def test_answered_and_unknown_invitations_are_refused(world):
    w = world
    store = w["store"]
    for key in ("accepted", "declined"):
        before = store.one("SELECT * FROM invitations WHERE id=?", (w["ids"][key],))
        r = post(w["admin"], f"/api/admin/invitations/{w['ids'][key]}/revoke", {"reason": REASON})
        assert r.status_code == 409 and r.json()["error"] == "invite_not_pending", (key, r.text)
        assert store.one("SELECT * FROM invitations WHERE id=?", (w["ids"][key],)) == before
        assert not audits(w, "admin.invitation.revoke", w["ids"][key])
    r = post(w["admin"], "/api/admin/invitations/iv_fixture_missing/revoke", {"reason": REASON})
    assert r.status_code == 404 and r.json()["error"] == "no_invite"
    # Each refusal is kept as the console keeps refusals (admin_routes `_write`).
    denied = {(row["object_id"], row["meta"]["code"]) for row in audits(w, "admin.access.denied")}
    assert denied >= {(w["ids"]["accepted"], "invite_not_pending"), (w["ids"]["declined"], "invite_not_pending"),
                      ("iv_fixture_missing", "no_invite")}
    assert not audits(w, "admin.invitation.revoke")
    for reason in (None, "short", "x" * 501):
        r = post(w["admin"], f"/api/admin/invitations/{w['ids']['pend']}/revoke", {} if reason is None else {"reason": reason})
        assert r.status_code == 400 and r.json()["error"] == "missing_reason"
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (w["ids"]["pend"],))["revoked_at"] is None


def test_the_invitee_with_an_account_hears_and_the_organization_log_names_the_platform(world):
    w = world
    store = w["store"]
    known = w["ids"]["known"]
    assert store.one("SELECT action_state FROM notifications WHERE user_id=? AND kind='invitation.received' "
                     "AND object_id=?", (w["carol"]["id"], known))["action_state"] == "pending"
    assert post(w["admin"], f"/api/admin/invitations/{known}/revoke", {"reason": REASON}).status_code == 200
    assert store.one("SELECT action_state FROM notifications WHERE user_id=? AND kind='invitation.received' "
                     "AND object_id=?", (w["carol"]["id"], known))["action_state"] == "void"
    assert store.one("SELECT 1 AS x FROM notifications WHERE user_id=? AND kind='invitation.revoked' AND object_id=?",
                     (w["carol"]["id"], known))
    # An organization's workspace: its governance log shows the act as the platform's, with the reason.
    pend2 = w["ids"]["pend2"]
    assert post(w["admin"], f"/api/admin/invitations/{pend2}/revoke", {"reason": REASON}).status_code == 200
    log = w["b"].get(f"/api/auth/organizations/{w['oid']}/audit")
    assert log.status_code == 200, log.text
    [entry] = [i for i in log.json()["items"] if i["action"] == "invite.revoke" and i["detail"].get("by") == "platform"]
    assert entry["actor"] == {"kind": "platform_admin"} and entry["detail"]["reason"] == REASON
    assert entry["workspace"] == {"id": w["ws_b"], "name": "Harbor fixture workspace"}
    assert ADDRESSES["pend2"] not in log.text


# ---------------------------------------------------------------- (c) the batch
def test_a_batch_is_a_set_of_single_revokes_with_one_batch_record(world):
    w = world
    store, ids = w["store"], w["ids"]
    submitted = [ids["expired"], ids["revoked"], ids["accepted"], "iv_fixture_missing", ids["expired"]]
    r = post(w["admin"], "/api/admin/invitations/revoke", {"ids": submitted, "reason": REASON})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["ok"], body["succeeded"], body["failed"]) == (False, 2, 2)
    assert [(x["id"], x["ok"], x["code"]) for x in body["results"]] == [
        (ids["expired"], True, "ok"), (ids["revoked"], True, "already_revoked"),
        (ids["accepted"], False, "invite_not_pending"), ("iv_fixture_missing", False, "no_invite")]
    assert all(isinstance(x["message"], str) and x["message"] for x in body["results"])
    assert body["results"][0]["journaled"] is True and body["results"][1]["journaled"] is None
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (ids["expired"],))["revoked_at"]
    assert store.one("SELECT accepted_at FROM invitations WHERE id=?", (ids["accepted"],))["accepted_at"]
    [batch] = audits(w, "admin.invitation.revoke_bulk")
    assert (batch["object_kind"], batch["object_id"], batch["workspace_id"]) == ("invitation_batch", body["batch_id"], None)
    assert batch["meta"] == {"total": 4, "succeeded": 2, "failed": 2, "reason": REASON, "batch_id": body["batch_id"],
                             "codes": {"already_revoked": 1, "invite_not_pending": 1, "no_invite": 1, "ok": 1}}
    # Only the invitation actually revoked has its single act's records.
    [single] = audits(w, "admin.invitation.revoke")
    assert single["object_id"] == ids["expired"]
    assert single["meta"] == {"reason": REASON, "workspace_id": w["ws_a"], "state_before": "expired",
                              "journaled": True, "batch_id": body["batch_id"]}
    assert [row["meta"].get("by") for row in audits(w, "invite.revoke", ids["expired"])] == ["platform"]
    assert [row["meta"].get("by") for row in audits(w, "invite.revoke", ids["revoked"])] == [None], "only bob's own"
    assert revocations_journaled(w, ids["expired"]) == 1 and revocations_journaled(w, ids["revoked"]) == 1
    # The ids are bounded and must be given.
    for bad, code in (({"ids": []}, "missing_ids"), ({}, "missing_ids"), ({"ids": "iv_x"}, "missing_ids"),
                      ({"ids": [1]}, "missing_ids"), ({"ids": ["has space"]}, "missing_ids"),
                      ({"ids": [f"iv_fixture_{n}" for n in range(101)]}, "too_many")):
        r = post(w["admin"], "/api/admin/invitations/revoke", {**bad, "reason": REASON})
        assert r.status_code == 400 and r.json()["error"] == code, (bad, r.text)
    assert post(w["admin"], "/api/admin/invitations/revoke", {"ids": [ids["pend"]]}).json()["error"] == "missing_reason"
    full = post(w["admin"], "/api/admin/invitations/revoke",
                {"ids": [ids["pend"]] + [f"iv_fixture_{n}" for n in range(99)], "reason": REASON})
    assert full.status_code == 200 and full.json()["succeeded"] == 1 and len(full.json()["results"]) == 100
    assert len(audits(w, "admin.invitation.revoke_bulk")) == 2, "a refused request writes no batch record"


# ---------------------------------------------------------------- (d) who may
@pytest.mark.parametrize("role", ["support", "auditor"])
def test_readers_list_but_may_not_revoke(world, role):
    w = world
    reader = w["login"](make_platform_admin(w["store"], w["verified"](f"{role}@example.test"), role=role))
    w["reauth"](reader)
    assert listing(w, client=reader, state="all").json()["groups"] == ALL_GROUPS
    for path, body in ((f"/api/admin/invitations/{w['ids']['pend']}/revoke", {"reason": REASON}),
                       ("/api/admin/invitations/revoke", {"ids": [w["ids"]["pend"]], "reason": REASON})):
        r = post(reader, path, body)
        assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", r.text
        assert r.json()["detail"]["permission"] == "platform.workspaces.manage"
    assert w["store"].one("SELECT revoked_at FROM invitations WHERE id=?", (w["ids"]["pend"],))["revoked_at"] is None
    assert not audits(w, "admin.invitation.revoke") and not audits(w, "admin.invitation.revoke_bulk")


def test_the_strict_reauthentication_comes_before_the_body_and_others_see_nothing(world):
    w = world
    manager = w["login"](make_platform_admin(w["store"], w["verified"]("access@example.test"), role="access_admin"))
    for path, body in ((f"/api/admin/invitations/{w['ids']['pend']}/revoke", {}),
                       ("/api/admin/invitations/revoke", {"ids": []}),
                       ("/api/admin/invitations/revoke", {"ids": [w["ids"]["pend"]], "reason": REASON})):
        r = post(manager, path, body)
        assert r.status_code == 403 and r.json()["error"] == "reauth_required", (path, r.text)
    w["reauth"](manager)
    assert post(manager, f"/api/admin/invitations/{w['ids']['pend']}/revoke", {}).json()["error"] == "missing_reason"
    assert post(manager, f"/api/admin/invitations/{w['ids']['pend']}/revoke", {"reason": REASON}).status_code == 200
    # Workspace owners, organization owners and an invitee: the console does not exist for them.
    for client in (w["a"], w["b"], w["login"](w["carol"])):
        assert client.get("/api/admin/invitations").status_code == 404
        for path, body in ((f"/api/admin/invitations/{w['ids']['known']}/revoke", {"reason": REASON}),
                           ("/api/admin/invitations/revoke", {"ids": [w["ids"]["known"]], "reason": REASON})):
            r = post(client, path, body)
            assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert TestClient(w["api"]).get("/api/admin/invitations").status_code == 401
    assert w["store"].one("SELECT revoked_at FROM invitations WHERE id=?", (w["ids"]["known"],))["revoked_at"] is None


# ---------------------------------------------------------------- (e) the workspace's own list
def test_the_workspace_invitation_list_is_unchanged(world):
    w = world
    path = f"/api/auth/workspaces/{w['ws_b']}/invitations"
    before = w["b"].get(path)
    listing(w, state="all")
    listing(w, state="mail_failed", q="harbor")
    assert w["b"].get(path).text == before.text, "reading the oversight page changes nothing the workspace sees"
    body = before.json()
    assert body["groups"] == {"pending": 2, "expired": 0, "revoked": 1, "accepted": 0, "declined": 1, "mail_failed": 1}
    items = {i["id"]: i for i in body["items"]}
    failed = items[w["ids"]["failed"]]
    # The fields as before, and `revoked_by` (A47 follow-up): "platform" or null.
    assert set(failed) == {"id", "email_key", "role", "can_export", "invited_by", "invited_via", "created_at",
                           "expires_at", "accepted_at", "revoked_at", "declined_at", "invited_by_email", "state",
                           "inviter", "mail", "mail_error", "revoked_by"}
    assert {i["revoked_by"] for i in body["items"]} == {None}, "the workspace's own revoke is not the platform's"
    assert items[w["ids"]["revoked"]]["state"] == "revoked"
    # The workspace's administrators see their own invitee's address and the mail as before.
    assert (failed["email_key"], failed["state"], failed["mail"], failed["mail_error"], failed["inviter"]) == (
        ADDRESSES["failed"], "pending", "failed", RAW_SMTP_ERROR, "bob@example.test")
    assert items[w["ids"]["pend2"]]["mail"] == "sent" and items[w["ids"]["pend2"]]["mail_error"] is None
    assert w["b"].get(path, params={"state": "revoked"}).json()["groups"] == {
        "pending": 0, "expired": 0, "revoked": 1, "accepted": 0, "declined": 0, "mail_failed": 0}
    # After a platform revoke the same list shows the new state, and nothing else differs.
    assert post(w["admin"], f"/api/admin/invitations/{w['ids']['failed']}/revoke", {"reason": REASON}).status_code == 200
    after = w["b"].get(path).json()
    changed = {i["id"]: i for i in after["items"]}[w["ids"]["failed"]]
    assert changed["state"] == "revoked" and changed["revoked_at"] and changed["revoked_by"] == "platform"
    assert {k: v for k, v in changed.items() if k not in ("state", "revoked_at", "revoked_by")} == \
        {k: v for k, v in failed.items() if k not in ("state", "revoked_at", "revoked_by")}
    assert {i["id"]: i["revoked_by"] for i in after["items"]}[w["ids"]["revoked"]] is None
    revoked_view = {i["id"]: i["revoked_by"] for i in w["b"].get(path, params={"state": "revoked"}).json()["items"]}
    assert revoked_view == {w["ids"]["revoked"]: None, w["ids"]["failed"]: "platform"}
    assert [i for i in after["items"] if i["id"] != w["ids"]["failed"]] == \
        [i for i in body["items"] if i["id"] != w["ids"]["failed"]]
    assert after["groups"] == {**body["groups"], "pending": 1, "revoked": 2, "mail_failed": 0}


# ---------------------------------------------------------------- follow-ups: failure, lapse, rollback
def test_a_failure_inside_the_batch_stops_it_and_the_batch_record_is_still_written(world, monkeypatch):
    from synwevo_identity.invites import Invites
    w = world
    store, ids = w["store"], w["ids"]
    original = Invites.platform_revoke
    secret = "fixture-injected-failure-text-that-must-stay-in-the-log"

    def flaky(self, iid):
        # FIXTURE: an unexpected failure (not a refusal) at the batch's second invitation.
        if iid == ids["known"]:
            raise RuntimeError(secret)
        return original(self, iid)

    monkeypatch.setattr(Invites, "platform_revoke", flaky)
    submitted = [ids["pend"], ids["known"], ids["failed"], ids["pend2"]]
    r = post(w["admin"], "/api/admin/invitations/revoke", {"ids": submitted, "reason": REASON})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(x["id"], x["ok"], x["code"]) for x in body["results"]] == [
        (ids["pend"], True, "ok"), (ids["known"], False, "admin_operation_failed"),
        (ids["failed"], False, "batch_stopped"), (ids["pend2"], False, "batch_stopped")]
    assert (body["ok"], body["succeeded"], body["failed"]) == (False, 1, 3)
    assert secret not in r.text and "RuntimeError" not in r.text
    revoked = {row["id"]: row["revoked_at"] for row in store.all("SELECT id, revoked_at FROM invitations")}
    assert revoked[ids["pend"]] and not any(revoked[ids[k]] for k in ("known", "failed", "pend2"))
    [batch] = audits(w, "admin.invitation.revoke_bulk")
    assert batch["result"] == "error" and batch["meta"]["codes"] == {"admin_operation_failed": 1, "batch_stopped": 2, "ok": 1}
    assert (batch["meta"]["total"], batch["meta"]["succeeded"], batch["meta"]["failed"]) == (4, 1, 3)
    assert secret not in json.dumps(batch["meta"])
    assert [row["object_id"] for row in audits(w, "admin.invitation.revoke")] == [ids["pend"]]
    monkeypatch.setattr(Invites, "platform_revoke", original)


@pytest.mark.parametrize("lapse", ["reauth_expired", "grant_revoked"])
def test_a_batch_stops_when_the_administrators_own_access_lapses(world, monkeypatch, lapse):
    from synwevo_identity.invites import Invites
    from .platform_fixture import end_platform_grants
    w = world
    store, ids = w["store"], w["ids"]
    manager = make_platform_admin(store, w["verified"]("access@example.test"), role="access_admin")
    client = w["login"](manager)
    w["reauth"](client)
    original = Invites.platform_revoke

    def then_lapse(self, iid):
        out = original(self, iid)
        if iid == ids["pend"]:
            # FIXTURE: the administrator's own access ends between the first and the second
            # invitation, written in the first one's transaction (the application's own store).
            if lapse == "reauth_expired":
                self.store.execute("UPDATE sessions SET reauth_at=1 WHERE user_id=?", (manager["id"],))
            else:
                end_platform_grants(self.store, manager)
        return out

    monkeypatch.setattr(Invites, "platform_revoke", then_lapse)
    r = post(client, "/api/admin/invitations/revoke",
             {"ids": [ids["pend"], ids["known"], ids["pend2"]], "reason": REASON})
    assert r.status_code == 200, r.text
    code = "reauth_required" if lapse == "reauth_expired" else "not_found"
    assert [(x["id"], x["ok"], x["code"]) for x in r.json()["results"]] == [
        (ids["pend"], True, "ok"), (ids["known"], False, code), (ids["pend2"], False, code)]
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (ids["pend"],))["revoked_at"]
    assert not store.one("SELECT revoked_at FROM invitations WHERE id=?", (ids["known"],))["revoked_at"]
    assert not store.one("SELECT revoked_at FROM invitations WHERE id=?", (ids["pend2"],))["revoked_at"]
    [batch] = audits(w, "admin.invitation.revoke_bulk")
    assert batch["result"] == "denied" and batch["actor_id"] == manager["id"]
    assert batch["meta"]["codes"] == {code: 2, "ok": 1}
    monkeypatch.setattr(Invites, "platform_revoke", original)


def test_an_audit_failure_rolls_the_revoke_back_and_sends_no_notice(world, monkeypatch):
    import synwevo_core.auth as core_auth
    w = world
    store, known = w["store"], w["ids"]["known"]
    original = core_auth.audit_event

    def failing(store_, action, **kw):
        # FIXTURE: the platform's own record cannot be written.
        if action == "admin.invitation.revoke":
            raise OSError("fixture: audit storage unavailable")
        return original(store_, action, **kw)

    monkeypatch.setattr(core_auth, "audit_event", failing)
    r = post(w["admin"], f"/api/admin/invitations/{known}/revoke", {"reason": REASON})
    assert r.status_code == 500 and "fixture" not in r.text
    monkeypatch.setattr(core_auth, "audit_event", original)
    # Rolled back as one: the UPDATE and the workspace's record with it; the invitee hears nothing.
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (known,))["revoked_at"] is None
    assert not audits(w, "invite.revoke", known) and not audits(w, "admin.invitation.revoke", known)
    assert not store.one("SELECT 1 AS x FROM notifications WHERE kind='invitation.revoked' AND object_id=?", (known,))
    assert store.one("SELECT action_state FROM notifications WHERE user_id=? AND kind='invitation.received' "
                     "AND object_id=?", (w["carol"]["id"], known))["action_state"] == "pending"
    assert not store.one("SELECT 1 AS x FROM job_events WHERE type='invitation.revoked' "
                         "AND json_extract(data,'$.invitation_id')=?", (known,))
    assert w["guest"].get("/api/auth/invitations/preview", params={"token": w["tokens"]["known"]}).json()["state"] == "pending"
    # The journal entry preceded the failed write and is voided after the rollback, once a later
    # committed transaction found no receipt: a restore does not withdraw the link the platform
    # was told it had not withdrawn.
    assert revocations_journaled(w, known) == 1
    (line,) = [e for e in journal.read(w["data"]) if e["kind"] == "invitation_revoked" and e.get("invitation_id") == known]
    assert [(e["attempt_id"], e["voided_kind"]) for e in journal.read(w["data"]) if e["kind"] == journal.VOID_KIND] == [
        (line["attempt_id"], "invitation_revoked")]
    assert store.one("SELECT value FROM meta WHERE key=?", ("journal_void:" + line["attempt_id"],))["value"] == "invitation_revoked"
    # Nothing is left half-done: the same revoke succeeds afterwards.
    assert post(w["admin"], f"/api/admin/invitations/{known}/revoke", {"reason": REASON}).json()["already"] is False


def test_a_zero_timestamp_is_a_set_timestamp_and_the_revoke_never_loops(world):
    w = world
    store, iid = w["store"], w["ids"]["pend2"]
    # FIXTURE: a row as damaged data might hold it — declined at epoch 0, never revoked.
    store.execute("UPDATE invitations SET declined_at=0, revoked_at=NULL WHERE id=?", (iid,))
    r = post(w["admin"], f"/api/admin/invitations/{iid}/revoke", {"reason": REASON})
    assert r.status_code == 409 and r.json()["error"] == "invite_not_pending"
    assert store.one("SELECT revoked_at FROM invitations WHERE id=?", (iid,))["revoked_at"] is None
    assert {i["id"]: i["state"] for i in listing(w, state="declined").json()["items"]}[iid] == "declined"
    tenant = {i["id"]: i["state"] for i in w["b"].get(f"/api/auth/workspaces/{w['ws_b']}/invitations").json()["items"]}
    assert tenant[iid] == "declined", "the Python state and the SQL state agree"


def test_whether_an_account_holds_the_address_is_for_readers_of_accounts(world, monkeypatch):
    from synwevo_core.platform_access import PlatformAccess
    w = world
    assert listing(w, state="all").json()["items"][0]["invitee"]["has_account"] in (True, False)
    original = PlatformAccess.effective_permissions

    def without_accounts(self, user):
        # FIXTURE: a role that lists workspaces but does not read accounts (no built-in role is one).
        return frozenset(original(self, user)) - {"platform.users.view"}

    monkeypatch.setattr(PlatformAccess, "effective_permissions", without_accounts)
    items = listing(w, state="all").json()["items"]
    assert len(items) == 8 and {i["invitee"]["has_account"] for i in items} == {None}
    assert ids_of(listing(w, state="all", q=ADDRESSES["known"])) == names(w, "known")
    monkeypatch.setattr(PlatformAccess, "effective_permissions", original)


def test_the_list_reads_beside_a_writer_without_the_write_lock(world):
    import threading
    w = world
    hold, held, out = threading.Event(), threading.Event(), {}

    def writer():
        # FIXTURE: a business write in progress on another connection (BEGIN IMMEDIATE held).
        with w["store"].tx():
            held.set()
            hold.wait(30)

    def reader():
        out["r"] = w["admin"].get("/api/admin/invitations", params={"state": "all"})

    holder = threading.Thread(target=writer)
    holder.start()
    try:
        assert held.wait(10)
        page = threading.Thread(target=reader)
        page.start()
        page.join(10)
        finished = not page.is_alive()
    finally:
        hold.set()
        holder.join()
    page.join()
    assert finished, "the list waited for a writer"
    assert out["r"].status_code == 200 and out["r"].json()["groups"] == ALL_GROUPS


def test_a_lost_update_is_refused_never_retried(world, monkeypatch):
    from synwevo_core.errors import ServiceError
    from synwevo_identity.invites import Invites
    w = world
    iid = w["ids"]["declined"]
    # FIXTURE: the state read says pending while the row says declined — what a write between the
    # read and the UPDATE would look like (unreachable under the writer lock). Once refused, not looped.
    monkeypatch.setattr(Invites, "_state", staticmethod(lambda row: "pending"))
    with pytest.raises(ServiceError) as refused:
        Invites(w["store"], w["ident"]).platform_revoke(iid)
    assert refused.value.status == 409 and refused.value.code == "invite_not_pending"
    assert w["store"].one("SELECT declined_at FROM invitations WHERE id=?", (iid,))["declined_at"]
