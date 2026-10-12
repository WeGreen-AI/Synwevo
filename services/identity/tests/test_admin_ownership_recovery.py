"""A37: platform-controlled ownership recovery — the orphan list and the handover that names an
existing member owner of a workspace or organization nobody can administer any more.

Synthetic identities and governance rows only, in the isolated identity fixture of
test_admin_operations (`world`) with the ordinary delegated administrator of
test_admin_ownership (`governed`, role access_admin). Stranded owners are written as fixtures the
way such rows reached databases before D11 — a direct UPDATE — because the product's own routes
now refuse exactly that. Successors enrol a real TOTP authenticator through the enrolment route
(the sign-in with a code is not exercised: the session opened before enrolment stays usable).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pyotp
import pytest

from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import LIVE_GRANT_EXISTS, SECOND_FACTOR_EXISTS, PlatformAccess, clock, current_rp_id
from synwevo_core.store import new_id, now
from synwevo_identity.ownership import CANDIDATE_LIMIT

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin import _audit_insert_fails
from .test_admin_operations import PW, post, world  # noqa: F401
from .test_admin_ownership import governed, org_member  # noqa: F401

REASON = "Isolated fixture: the only owner was stranded, naming the deputy"
BASE = "/api/admin"
ADMIN_EMAIL = "governance-admin@example.test"


# ---------------------------------------------------------------- fixtures and helpers
def user(w, label):
    return w["ident"].create_user(f"{label}@example.test", PW, status="active")


def admin_id(w):
    return w["ident"].by_email(ADMIN_EMAIL)["id"]


def enrol_totp(w, account):
    """The real enrolment path (as test_mfa does it): the secret is confirmed with one code and
    never read back. Returns the account's signed-in client, opened before enrolment."""
    client = w["login"](account)
    assert post(client, "/api/auth/mfa/reauth", {"password": PW}).status_code == 200
    secret = post(client, "/api/auth/mfa/totp/begin", {}).json()["secret"]
    confirmed = post(client, "/api/auth/mfa/totp/confirm", {"code": pyotp.TOTP(secret).now()})
    assert confirmed.status_code == 200, confirmed.text
    assert w["ident"].has_second_factor(account["id"])
    return client


def totp_fixture(w, uid, *, confirmed=True):
    # FIXTURE: an authenticator row with an opaque seed (never decrypted here) — enough for the
    # product's second-factor predicate, which reads only `confirmed_at`.
    w["store"].execute("INSERT INTO totp_secrets (user_id, secret_enc, confirmed_at, created_at) VALUES (?,?,?,?)",
                       (uid, "fixture:not-a-seed", now() if confirmed else None, now()))


def passkey_fixture(w, uid, *, uv=True, rp_id=None, deleted=False):
    # FIXTURE: a registered credential row (public material only is ever stored); the ceremony
    # itself is test_passkeys', the predicate reads `uv_capable`, `deleted_at` and `rp_id`.
    w["store"].execute("INSERT INTO passkeys (id, user_id, credential_id, public_key, rp_id, uv_capable, name, "
                       "created_at, deleted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (new_id("pk"), uid, new_id("cred"), "fixture-public-key", rp_id or current_rp_id(),
                        1 if uv else 0, "fixture", now(), now() if deleted else None))


def second_factor(w, account, factor):
    """Give `account` the named second factor: a real TOTP enrolment ("totp"), a user-verifying
    passkey row ("passkey"), or nothing (None). Returns the account's signed-in client."""
    if factor == "totp":
        return enrol_totp(w, account)
    client = w["login"](account)
    if factor == "passkey":
        passkey_fixture(w, account["id"])
    assert PlatformAccess(w["store"]).has_second_factor(account["id"]) is (factor is not None)
    return client


def strand(w, uid, status="suspended"):
    # FIXTURE: an owner stranded before the continuity check existed, written the way such rows
    # reached databases (a direct status change); the product's routes refuse this now (D11).
    w["store"].execute("UPDATE users SET status=?, updated_at=? WHERE id=?", (status, now(), uid))


def shared_workspace(w, owner, *members):
    wid = w["ident"].create_workspace("Fixture shared space", owner_id=owner["id"], kind="org")["id"]
    for account, role in members:
        w["ident"].add_member(wid, account["id"], role)
    return wid


def orphan(w, *, successor_role="editor", factor="totp"):
    """A shared workspace whose only owner was stranded, and the member who could take it."""
    stranded, successor = user(w, "stranded-owner"), user(w, "successor")
    wid = shared_workspace(w, stranded, (successor, successor_role))
    client = second_factor(w, successor, factor)
    strand(w, stranded["id"])
    assert w["ident"].usable_owners(wid) == []
    return {"id": wid, "stranded": stranded, "successor": successor, "client": client}


def organization(w, owner, name="Fixture organization"):
    created = Organizations(w["store"]).create(owner["id"], name, f"{name} space", "a37-" + name.replace(" ", "-"))
    return created["organization"]["id"], created["workspace"]["id"]


def orphaned_organization(w, *, successor_role="admin", factor="totp"):
    """An organization whose only owner was stranded, plus a member who could take it. The
    organization's initial workspace stays the stranded owner's: it is an orphan of its own."""
    stranded, successor = user(w, "stranded-org-owner"), user(w, "org-successor")
    oid, wid = organization(w, stranded)
    org_member(w, oid, successor["id"], successor_role)
    second_factor(w, successor, factor)
    strand(w, stranded["id"])
    assert Organizations(w["store"]).usable_owners(oid) == []
    return {"id": oid, "workspace": wid, "stranded": stranded, "successor": successor}


def recover(w, wid, uid, *, client=None, reason=REASON, body=None):
    return post(client or w["admin"], f"{BASE}/workspaces/{wid}/recover-owner",
                body if body is not None else {"user_id": uid, "reason": reason})


def recover_organization(w, oid, uid, *, client=None, reason=REASON, body=None):
    return post(client or w["admin"], f"{BASE}/organizations/{oid}/recover-owner",
                body if body is not None else {"user_id": uid, "reason": reason})


def orphans(w, **params):
    r = w["admin"].get(f"{BASE}/ownership/orphans", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def denied(w, object_id):
    row = w["store"].one("SELECT object_kind, meta FROM audit_events WHERE action='admin.access.denied' "
                         "AND object_id=? AND result='denied' ORDER BY seq DESC", (object_id,))
    return row and {"object_kind": row["object_kind"], **json.loads(row["meta"])}


def ok_rows(w, action):
    return w["store"].all("SELECT * FROM audit_events WHERE action=? AND result='ok' ORDER BY seq", (action,))


# ---------------------------------------------------------------- the list
def test_the_orphan_list_names_the_stranded_and_who_could_take_over(governed):
    w, ident, store = governed, governed["ident"], governed["store"]
    # A shared workspace whose only owner was suspended: listed, with every member explained.
    o = orphan(w)                                              # successor: editor with a second factor
    viewer, suspended_member, platform_member = user(w, "viewer"), user(w, "suspended-member"), user(w, "support-member")
    ident.add_member(o["id"], viewer["id"], "viewer")
    ident.add_member(o["id"], suspended_member["id"], "editor")
    ident.add_member(o["id"], platform_member["id"], "editor")
    strand(w, suspended_member["id"])
    make_platform_admin(store, platform_member, role="support")
    # Not listed: a workspace with a usable owner, the stranded owner's solo personal workspace,
    # and the historical data still waiting for its claim.
    healthy = shared_workspace(w, w["target"], (o["successor"], "admin"))
    personal = next(ws["id"] for ws in ident.workspaces_for(o["stranded"]["id"]) if ws["kind"] == "personal")
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    # Listed: an attached workspace whose owner lost the organization membership (the organization
    # itself keeps a usable owner); an outsider of the organization cannot be named.
    org_owner, co_owner, insider, outsider = user(w, "org-owner"), user(w, "co-owner"), user(w, "insider"), user(w, "outsider")
    oid, attached = organization(w, org_owner)
    org_member(w, oid, co_owner["id"], "owner")
    for account in (insider, outsider):
        ident.add_member(attached, account["id"], "editor")
        enrol_totp(w, account)
    org_member(w, oid, insider["id"], "member")
    # FIXTURE: the membership vanished along a path older than the strand check.
    store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (oid, org_owner["id"]))
    # Listed: an organization whose only owner was suspended.
    oo = orphaned_organization(w)
    plain_member = user(w, "plain-member")
    org_member(w, oo["id"], plain_member["id"], "member")

    page = orphans(w)
    assert page["limit"] == 50 and page["as_of"] <= now()
    listed = {item["id"]: item for item in page["workspaces"]["items"]}
    # The stranded organization's own workspace is an orphan of its own: its only owner is the
    # suspended organization owner, and it has nobody to name.
    assert page["workspaces"]["total"] == 3 and list(listed) == [o["id"], attached, oo["workspace"]], "created_at, id"
    assert not {healthy, personal, "default"} & set(listed)
    third = listed[oo["workspace"]]
    assert third["organization_id"] == oo["id"] and third["candidates"] == [] and [x["user_id"] for x in third["owners"]] == [oo["stranded"]["id"]]

    first = listed[o["id"]]
    assert {k: first[k] for k in ("kind", "status", "organization_id", "members", "projects")} == {
        "kind": "org", "status": "active", "organization_id": None, "members": 5, "projects": 0}
    assert first["owners"] == [{"user_id": o["stranded"]["id"], "email": o["stranded"]["email"], "status": "suspended",
                                "deleted": False, "organization_member": True}]
    verdicts = {c["user_id"]: (c["eligible"], c["blockers"], c["role"]) for c in first["candidates"]}
    assert verdicts == {
        o["successor"]["id"]: (True, [], "editor"),
        viewer["id"]: (False, ["second_factor_required"], "viewer"),
        suspended_member["id"]: (False, ["account_unavailable", "second_factor_required"], "editor"),
        platform_member["id"]: (False, ["second_factor_required", "platform_admin"], "editor"),
    }
    successor_row = next(c for c in first["candidates"] if c["user_id"] == o["successor"]["id"])
    assert successor_row == {"user_id": o["successor"]["id"], "email": o["successor"]["email"], "display_name": None,
                             "role": "editor", "status": "active", "second_factor": True, "organization_member": True,
                             "platform_admin": False, "eligible": True, "blockers": []}
    assert o["stranded"]["id"] not in verdicts, "the unusable owner row is not a candidate"
    assert first["candidates_total"] == 4 == len(first["candidates"])
    assert first["candidates"][0]["user_id"] == o["successor"]["id"], "eligible first"
    assert [c["email"] for c in first["candidates"][1:]] == sorted(c["email"] for c in first["candidates"][1:]), "then by address"

    second = listed[attached]
    assert second["organization_id"] == oid and second["owners"] == [
        {"user_id": org_owner["id"], "email": org_owner["email"], "status": "active", "deleted": False,
         "organization_member": False}]
    assert {c["user_id"]: (c["eligible"], c["blockers"], c["organization_member"]) for c in second["candidates"]} == {
        insider["id"]: (True, [], True), outsider["id"]: (False, ["not_organization_member"], False)}

    orgs = page["organizations"]
    assert orgs["total"] == 1 and [item["id"] for item in orgs["items"]] == [oo["id"]]
    item = orgs["items"][0]
    assert {k: item[k] for k in ("status", "recover_until", "members", "workspaces")} == {
        "status": "active", "recover_until": None, "members": 3, "workspaces": 1}
    assert item["owners"] == [{"user_id": oo["stranded"]["id"], "email": oo["stranded"]["email"],
                               "status": "suspended", "deleted": False}]
    assert {c["user_id"]: (c["eligible"], c["blockers"], c["role"], c["organization_member"]) for c in item["candidates"]} == {
        oo["successor"]["id"]: (True, [], "admin", True), plain_member["id"]: (False, ["second_factor_required"], "member", True)}
    assert item["candidates_total"] == 2 and item["candidates"][0]["user_id"] == oo["successor"]["id"]
    # Nothing in the page is content: no project name, no document; counts and addresses only.
    assert "projects" in first and all("name" not in c for c in first["candidates"])

    # The limit bounds the items, never the totals.
    page = orphans(w, limit=1)
    assert page["limit"] == 1 and page["workspaces"]["total"] == 3 and len(page["workspaces"]["items"]) == 1
    assert page["workspaces"]["items"][0]["id"] == o["id"]
    assert w["admin"].get(f"{BASE}/ownership/orphans", params={"limit": 0}).status_code == 422
    assert w["admin"].get(f"{BASE}/ownership/orphans", params={"limit": 201}).status_code == 422


@pytest.mark.parametrize("state", ["frozen_workspace", "frozen_organization", "recoverable_organization", "expired_organization"])
def test_frozen_and_recoverable_still_need_stewards_but_expired_deletions_do_not(governed, state):
    w, store = governed, governed["store"]
    o = orphan(w)
    if state == "frozen_workspace":
        store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (o["id"],))
        expected = True
    else:
        org_owner = user(w, "org-owner")
        oid, _ = organization(w, org_owner)
        org_member(w, oid, o["successor"]["id"], "member")
        store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (oid, o["id"]))
        status, until = {"frozen_organization": ("frozen", None), "recoverable_organization": ("deleted", now() + 600),
                         "expired_organization": ("deleted", now() - 600)}[state]
        store.execute("UPDATE organizations SET status=?, recover_until=? WHERE id=?", (status, until, oid))
        expected = state != "expired_organization"
    ids = [item["id"] for item in orphans(w)["workspaces"]["items"]]
    assert (o["id"] in ids) is expected
    r = recover(w, o["id"], o["successor"]["id"])
    if expected:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 409 and r.json()["error"] == "not_recoverable", r.text


def test_the_sql_fragments_agree_with_step_up_and_live_grants(governed):
    """The one-query candidate listing judges the second factor and the platform grant with SQL
    (platform_access.SECOND_FACTOR_EXISTS / LIVE_GRANT_EXISTS); the recovery judges them with
    `PlatformAccess.step_up()["second_factor"]` and `live_grants()`. Over every shape of fixture
    account the two must say the same — and say what the product means by each."""
    w, store = governed, governed["store"]
    access = PlatformAccess(store)
    factors = {"none": None, "totp": lambda u: totp_fixture(w, u), "totp_unconfirmed": lambda u: totp_fixture(w, u, confirmed=False),
               "passkey_uv": lambda u: passkey_fixture(w, u), "passkey_no_uv": lambda u: passkey_fixture(w, u, uv=False),
               "passkey_deleted": lambda u: passkey_fixture(w, u, deleted=True),
               "passkey_other_rp": lambda u: passkey_fixture(w, u, rp_id="elsewhere.example"),
               "both": lambda u: (totp_fixture(w, u), passkey_fixture(w, u))}
    expected_factor = {"none": False, "totp": True, "totp_unconfirmed": False, "passkey_uv": True, "passkey_no_uv": False,
                       "passkey_deleted": False, "passkey_other_rp": False, "both": True}
    grants = {"no_grant": None, "live_grant": lambda u: make_platform_admin(store, u, role="support"),
              "revoked_grant": lambda u: (make_platform_admin(store, u, role="support"), end_platform_grants(store, u)),
              # FIXTURE: a delegated grant whose expiry has passed (the product sets expiry at grant time).
              "expired_grant": lambda u: (make_platform_admin(store, u, role="support"), store.execute(
                  "UPDATE platform_role_grants SET expires_at=? WHERE user_id=?", (now() - 1, u["id"])))}
    expected_grant = {"no_grant": False, "live_grant": True, "revoked_grant": False, "expired_grant": False}
    accounts = {}
    for label, arrange in {**factors, **grants}.items():
        account = user(w, label.replace("_", "-"))
        if arrange:
            arrange(account if label in grants else account["id"])
        accounts[label] = account["id"]
    sql_factor = f"SELECT {SECOND_FACTOR_EXISTS.format(u='u.id')} AS x FROM users u WHERE u.id=?"
    sql_grant = f"SELECT {LIVE_GRANT_EXISTS.format(u='u.id')} AS x FROM users u WHERE u.id=?"
    for label, uid in accounts.items():
        by_sql = bool(store.one(sql_factor, (current_rp_id(), uid))["x"])
        by_code = access.step_up(uid)["second_factor"]
        assert by_sql is by_code is access.has_second_factor(uid) is expected_factor.get(label, False), label
        by_sql = bool(store.one(sql_grant, (clock(), uid))["x"])
        assert by_sql is bool(access.live_grants(uid)) is expected_grant.get(label, False), label
    # And the password sign-in's own TOTP-only question is deliberately not this predicate.
    assert w["ident"].has_second_factor(accounts["passkey_uv"]) is False


def test_candidates_are_capped_eligible_first_and_counted(governed):
    """More members than the page lists: the eligible ones come first whatever their address, the
    rest follow by address, `candidates_total` says how many there are in all."""
    w, ident, store = governed, governed["ident"], governed["store"]
    stranded = user(w, "stranded-owner")
    wid = shared_workspace(w, stranded)
    strand(w, stranded["id"])
    # Passwordless fixture accounts (no hashing): the listing judges rows, not sign-ins.
    crowd = [ident.create_user(f"m-{i:03d}@example.test", None, status="active") for i in range(CANDIDATE_LIMIT + 3)]
    eligible = [ident.create_user(f"zz-eligible-{i}@example.test", None, status="active") for i in range(3)]
    for account in crowd + eligible:
        ident.add_member(wid, account["id"], "viewer")
    for account in eligible:
        totp_fixture(w, account["id"])
    item = next(i for i in orphans(w)["workspaces"]["items"] if i["id"] == wid)
    assert item["members"] == CANDIDATE_LIMIT + 7 and item["candidates_total"] == CANDIDATE_LIMIT + 6
    assert len(item["candidates"]) == CANDIDATE_LIMIT
    emails = [c["email"] for c in item["candidates"]]
    assert emails[:3] == [a["email"] for a in eligible] and all(c["eligible"] for c in item["candidates"][:3])
    assert emails[3:] == [a["email"] for a in crowd][:CANDIDATE_LIMIT - 3], "then by address, the tail cut"
    assert not any(c["eligible"] for c in item["candidates"][3:])
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role<>'owner'", (wid,))["n"] == item["candidates_total"]


# ---------------------------------------------------------------- the handover
@pytest.mark.parametrize("factor", ["totp", "passkey"])
def test_recovering_a_workspace_owner_names_a_member_and_grants_the_administrator_nothing(governed, factor):
    w, ident, store = governed, governed["ident"], governed["store"]
    o = orphan(w, factor=factor)
    third = user(w, "third")
    ident.add_member(o["id"], third["id"], "viewer")
    # Before: the successor is an editor, and the product's governance routes say so.
    assert o["client"].get(f"/api/auth/workspaces/{o['id']}/members").status_code == 403
    assert o["id"] in [item["id"] for item in orphans(w)["workspaces"]["items"]]

    r = recover(w, o["id"], o["successor"]["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["member"] == {"user_id": o["successor"]["id"], "email": o["successor"]["email"], "role": "owner",
                              "previous_role": "editor"}
    assert body["workspace"]["id"] == o["id"] and body["workspace"]["members"] == 3 and body["workspace"]["projects"] == 0
    assert set(body["workspace"]) == {"id", "name", "kind", "status", "created_at", "members", "projects", "apps"}

    membership = ident.membership(o["id"], o["successor"]["id"])
    assert membership["role"] == "owner" and membership["can_export"] == 1
    assert ident.membership(o["id"], o["stranded"]["id"])["role"] == "owner", "the stranded row is not rewritten"
    assert ident.usable_owners(o["id"]) == [{"user_id": o["successor"]["id"], "email": o["successor"]["email"], "status": "active"}]

    rows = ok_rows(w, "admin.workspace.recover_owner")
    assert len(rows) == 1
    row, meta = rows[0], json.loads(rows[0]["meta"])
    assert (row["actor_id"], row["workspace_id"], row["object_kind"], row["object_id"]) == (admin_id(w), o["id"], "workspace", o["id"])
    assert meta["before"] == {"role": "editor"} and meta["after"] == {"role": "owner"} and meta["changed"] == ["role"]
    assert {k: meta[k] for k in ("target_user", "target_email", "previous_role", "unusable_owners", "reason")} == {
        "target_user": o["successor"]["id"], "target_email": o["successor"]["email"], "previous_role": "editor",
        "unusable_owners": 1, "reason": REASON}

    # The administrator gained nothing: no membership, no entitlement, no token in that workspace.
    for table in ("memberships", "app_entitlements"):
        assert not store.one(f"SELECT 1 FROM {table} WHERE workspace_id=? AND user_id=?", (o["id"], admin_id(w))), table
    assert not store.one("SELECT 1 FROM oauth_tokens WHERE workspace_id=? AND user_id=?", (o["id"], admin_id(w)))
    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (o["id"],))["n"] == 3

    # After: the recovered owner passes the product's owner authority — the governance route that
    # admits only admins and owners, and the owner-only core act (`transfer_ownership` refuses a
    # non-owner with `not_owner`). The owner-only HTTP act needs a fresh re-authentication of a
    # session that now has a second factor, so the core authority the routes call is asserted.
    assert o["client"].get(f"/api/auth/workspaces/{o['id']}/members").status_code == 200
    handed = ident.transfer_ownership(o["id"], o["successor"]["id"], third["id"], demote_self_to="admin")
    assert handed["target_previous_role"] == "viewer"
    assert o["id"] not in [item["id"] for item in orphans(w)["workspaces"]["items"]]


REFUSALS = ["owner_present", "target_unavailable:outsider", "target_unavailable:suspended", "not_organization_member",
            "successor_second_factor_required", "self_assignment", "target_is_platform_admin", "missing_reason",
            "missing_user", "not_recoverable:pending_claim", "not_recoverable:solo_personal", "not_recoverable:status_deleted",
            "no_workspace:deleted_at", "no_workspace:unknown"]


@pytest.mark.parametrize("case", REFUSALS)
def test_every_refusal_leaves_the_membership_unchanged_and_is_recorded(governed, case):
    """Each refusal code of the contract, on its own. A refusal raised with the write (inside the
    transaction, as the claim's are) is kept as a denied record after the rollback; one raised
    before the transaction (bad input, an unknown or out-of-scope workspace) is not."""
    w, ident, store = governed, governed["ident"], governed["store"]
    code, _, variant = case.partition(":")
    body = None
    recorded = True
    if code == "owner_present":
        o = orphan(w)
        store.execute("UPDATE users SET status='active' WHERE id=?", (o["stranded"]["id"],))  # the owner came back
        wid, target = o["id"], o["successor"]
    elif code == "target_unavailable" and variant == "outsider":
        o = orphan(w)
        wid, target = o["id"], user(w, "never-a-member")
    elif code == "target_unavailable":
        o = orphan(w)
        strand(w, o["successor"]["id"])
        wid, target = o["id"], o["successor"]
    elif code == "not_organization_member":
        o = orphan(w)
        oid, _ = organization(w, user(w, "org-owner"))
        store.execute("UPDATE workspaces SET organization_id=? WHERE id=?", (oid, o["id"]))
        wid, target = o["id"], o["successor"]
    elif code == "successor_second_factor_required":
        o = orphan(w, factor=None)
        wid, target = o["id"], o["successor"]
    elif code == "self_assignment":
        o = orphan(w)
        wid, target = o["id"], ident.by_email(ADMIN_EMAIL)
    elif code == "target_is_platform_admin":
        o = orphan(w)
        make_platform_admin(store, o["successor"], role="support")
        wid, target = o["id"], o["successor"]
    elif code == "missing_reason":
        o = orphan(w)
        wid, target, recorded = o["id"], o["successor"], False
        body = {"user_id": target["id"], "reason": "short"}
    elif code == "missing_user":
        o = orphan(w)
        wid, target, recorded = o["id"], o["successor"], False
        body = {"reason": REASON}
    elif code == "not_recoverable" and variant == "pending_claim":
        ts = now()
        store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                      "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
        wid, target, recorded = "default", w["target"], False
    elif code == "not_recoverable" and variant == "solo_personal":
        stranded = user(w, "stranded-owner")
        wid = next(ws["id"] for ws in ident.workspaces_for(stranded["id"]) if ws["kind"] == "personal")
        strand(w, stranded["id"])
        target, recorded = w["target"], False
    elif code == "not_recoverable":
        o = orphan(w)
        store.execute("UPDATE workspaces SET status='deleted' WHERE id=?", (o["id"],))
        wid, target, recorded = o["id"], o["successor"], False
    elif code == "no_workspace" and variant == "deleted_at":
        o = orphan(w)
        store.execute("UPDATE workspaces SET deleted_at=? WHERE id=?", (now(), o["id"]))
        wid, target, recorded = o["id"], o["successor"], False
    else:
        o = orphan(w)
        wid, target, recorded = "w_nowhere", o["successor"], False
    before = store.all("SELECT workspace_id,user_id,role,can_export FROM memberships ORDER BY workspace_id,user_id")

    r = recover(w, wid, target["id"], body=body)
    expected_status = {"missing_reason": 400, "missing_user": 400, "no_workspace": 404}.get(code, 409)
    assert r.status_code == expected_status and r.json()["error"] == code, (case, r.text)
    assert store.all("SELECT workspace_id,user_id,role,can_export FROM memberships ORDER BY workspace_id,user_id") == before
    assert not ok_rows(w, "admin.workspace.recover_owner")
    record = denied(w, wid)
    if recorded:
        assert record and record["code"] == code and record["object_kind"] == "workspace", case
    else:
        assert record is None, case
    # Against the core authority directly, the same answer (the route adds nothing of its own
    # for the codes the write holds).
    if code in ("owner_present", "target_unavailable", "not_organization_member", "successor_second_factor_required"):
        with pytest.raises(ServiceError) as caught:
            ident.recover_ownership(wid, target["id"])
        assert caught.value.code == code


def test_the_support_role_may_look_but_not_hand_over(governed):
    w, store = governed, governed["store"]
    o = orphan(w)
    support = make_platform_admin(store, user(w, "support"), role="support")
    client = w["login"](support)
    w["reauth"](client)
    page = client.get(f"{BASE}/ownership/orphans")
    assert page.status_code == 200 and [i["id"] for i in page.json()["workspaces"]["items"]] == [o["id"]]
    r = recover(w, o["id"], o["successor"]["id"], client=client)
    assert r.status_code == 403 and r.json()["error"] == "platform_permission_denied", r.text
    assert w["ident"].membership(o["id"], o["successor"]["id"])["role"] == "editor"
    assert denied(w, "platform.workspaces.manage")["code"] == "platform_permission_denied"
    # An ordinary account learns nothing about the page's existence.
    assert w["ordinary"].get(f"{BASE}/ownership/orphans").status_code == 404
    assert recover(w, o["id"], o["successor"]["id"], client=w["ordinary"]).status_code == 404


def test_a_workspace_recovery_is_undone_when_its_audit_cannot_be_written(governed, monkeypatch):
    """The role write and the platform's record commit together: with the record failing, the
    membership is as it was and nothing is recorded."""
    w, ident = governed, governed["ident"]
    o = orphan(w)
    restore = _audit_insert_fails(monkeypatch, "admin.workspace.recover_owner")
    r = recover(w, o["id"], o["successor"]["id"])
    restore()
    assert r.status_code >= 500, r.text
    membership = ident.membership(o["id"], o["successor"]["id"])
    assert (membership["role"], membership["can_export"]) == ("editor", 1)
    assert ident.usable_owners(o["id"]) == [] and not ok_rows(w, "admin.workspace.recover_owner")
    assert "fixture: audit storage unavailable" not in r.text
    # The next attempt, with the audit writable again, goes through.
    assert recover(w, o["id"], o["successor"]["id"]).status_code == 200


def test_the_handover_needs_the_administrator_step_up(governed):
    w = governed
    o = orphan(w)
    w["store"].execute("UPDATE sessions SET reauth_at=NULL, reauth_scope=NULL")
    r = recover(w, o["id"], o["successor"]["id"])
    assert r.status_code == 403 and r.json()["error"] == "reauth_required", r.text
    assert w["ident"].membership(o["id"], o["successor"]["id"])["role"] == "editor"


# ---------------------------------------------------------------- organizations
def test_recovering_an_organization_owner_writes_both_records_in_one_transaction(governed):
    w, store = governed, governed["store"]
    oo = orphaned_organization(w)
    r = recover_organization(w, oo["id"], oo["successor"]["id"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["organization"] == {"id": oo["id"], "name": "Fixture organization", "status": "active"}
    assert body["member"] == {"user_id": oo["successor"]["id"], "email": oo["successor"]["email"], "role": "owner",
                              "previous_role": "admin"}
    assert store.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?",
                     (oo["id"], oo["successor"]["id"]))["role"] == "owner"
    assert Organizations(store).usable_owners(oo["id"]) == [
        {"user_id": oo["successor"]["id"], "email": oo["successor"]["email"], "status": "active"}]

    own = ok_rows(w, "organization.owner.recover")
    platform = ok_rows(w, "admin.organization.recover_owner")
    assert len(own) == 1 and len(platform) == 1
    for row in (own[0], platform[0]):
        assert (row["actor_id"], row["object_kind"], row["object_id"]) == (admin_id(w), "organization", oo["id"])
        assert row["workspace_id"] is None, "a platform action on an organization is not the administrator's session workspace's"
    assert json.loads(own[0]["meta"]) == {"user_id": oo["successor"]["id"], "before": "admin", "after": "owner", "reason": REASON}
    meta = json.loads(platform[0]["meta"])
    assert meta["before"] == {"role": "admin"} and meta["after"] == {"role": "owner"} and meta["changed"] == ["role"]
    assert {k: meta[k] for k in ("target_user", "target_email", "previous_role", "reason")} == {
        "target_user": oo["successor"]["id"], "target_email": oo["successor"]["email"], "previous_role": "admin", "reason": REASON}
    # The administrator is no member of the organization, and the organization's own governance
    # log shows the recovery under its members category.
    assert not store.one("SELECT 1 FROM organization_memberships WHERE organization_id=? AND user_id=?", (oo["id"], admin_id(w)))
    assert oo["id"] not in [item["id"] for item in orphans(w)["organizations"]["items"]]


def test_an_organization_recovery_is_undone_when_the_platform_record_cannot_be_written(governed, monkeypatch):
    """The organization's own record, the role write and the platform's record commit together:
    with the platform record failing, the role and the organization's record are gone too."""
    w, store = governed, governed["store"]
    oo = orphaned_organization(w)
    restore = _audit_insert_fails(monkeypatch, "admin.organization.recover_owner")
    r = recover_organization(w, oo["id"], oo["successor"]["id"])
    restore()
    assert r.status_code >= 500, r.text
    assert store.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?",
                     (oo["id"], oo["successor"]["id"]))["role"] == "admin"
    assert not ok_rows(w, "organization.owner.recover") and not ok_rows(w, "admin.organization.recover_owner")
    assert "fixture: audit storage unavailable" not in r.text


def test_an_organization_with_a_usable_owner_is_not_handed_over(governed):
    w, store = governed, governed["store"]
    oo = orphaned_organization(w)
    store.execute("UPDATE users SET status='active' WHERE id=?", (oo["stranded"]["id"],))   # the owner came back
    r = recover_organization(w, oo["id"], oo["successor"]["id"])
    assert r.status_code == 409 and r.json()["error"] == "owner_present", r.text
    assert store.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?",
                     (oo["id"], oo["successor"]["id"]))["role"] == "admin"
    assert denied(w, oo["id"]) == {"object_kind": "organization", "code": "owner_present", "method": "POST",
                                   "path": f"{BASE}/organizations/{oo['id']}/recover-owner"}
    assert not ok_rows(w, "organization.owner.recover")


@pytest.mark.parametrize("code", ["target_unavailable", "successor_second_factor_required", "self_assignment",
                                  "target_is_platform_admin", "not_recoverable", "missing_reason", "no_organization"])
def test_organization_refusals(governed, code):
    """Each refusal of the organization route on its own; as for workspaces, a refusal raised with
    the write is kept as a denied record, one raised before it (bad input, an unknown or
    out-of-scope organization) is not."""
    w, ident, store = governed, governed["ident"], governed["store"]
    oo = orphaned_organization(w, factor=None if code == "successor_second_factor_required" else "totp")
    oid, target, body = oo["id"], oo["successor"], None
    recorded = code not in ("not_recoverable", "missing_reason", "no_organization")
    if code == "target_unavailable":
        target = user(w, "never-a-member")
    elif code == "self_assignment":
        target = ident.by_email(ADMIN_EMAIL)
    elif code == "target_is_platform_admin":
        make_platform_admin(store, target, role="support")
    elif code == "not_recoverable":
        store.execute("UPDATE organizations SET status='deleted', recover_until=? WHERE id=?", (now() - 1, oid))
    elif code == "missing_reason":
        body = {"user_id": target["id"], "reason": "why"}
    elif code == "no_organization":
        oid = "org_nowhere"
    r = recover_organization(w, oid, target["id"], body=body)
    assert r.status_code == {"missing_reason": 400, "no_organization": 404}.get(code, 409) and r.json()["error"] == code, r.text
    assert store.one("SELECT role FROM organization_memberships WHERE organization_id=? AND user_id=?",
                     (oo["id"], oo["successor"]["id"]))["role"] == "admin"
    assert not ok_rows(w, "organization.owner.recover") and not ok_rows(w, "admin.organization.recover_owner")
    record = denied(w, oid)
    if recorded:
        assert record and record["code"] == code and record["object_kind"] == "organization", code
    else:
        assert record is None, code
    if code == "not_recoverable":
        # Core refuses the same thing inside the transaction, for a caller that skipped the route's pre-check.
        with pytest.raises(ServiceError) as caught:
            Organizations(store, ident).recover_owner(oid, target["id"], actor_id=admin_id(w), reason=REASON)
        assert caught.value.code == "not_recoverable"


def test_the_specific_route_wins_over_the_generic_organization_action(governed):
    """`/organizations/{oid}/recover-owner` is declared by the ownership sub-router, included
    before the admin router's generic `/organizations/{oid}/{action}`; the generic route would
    answer the non-member administrator 404 `no_organization` (core `require`) or 409
    `organization_state_conflict` — never a handover."""
    w = governed
    paths = list(w["api"].openapi()["paths"])
    specific, generic = "/api/admin/organizations/{oid}/recover-owner", "/api/admin/organizations/{oid}/{action}"
    assert specific in paths and generic in paths and paths.index(specific) < paths.index(generic)
    oo = orphaned_organization(w)
    r = recover_organization(w, oo["id"], oo["successor"]["id"])
    assert r.status_code == 200 and r.json()["member"]["role"] == "owner", r.text
    # The generic route still answers the other actions as before.
    generic_answer = post(w["admin"], f"{BASE}/organizations/{oo['id']}/recover", {"reason": REASON})
    assert generic_answer.status_code in (404, 409) and generic_answer.json()["error"] in ("no_organization", "organization_state_conflict")


# ---------------------------------------------------------------- the race
def test_two_concurrent_recoveries_end_with_exactly_one_owner(governed):
    w, ident = governed, governed["ident"]
    o = orphan(w)
    other = user(w, "other-successor")
    ident.add_member(o["id"], other["id"], "admin")
    enrol_totp(w, other)
    barrier = Barrier(2)

    def attempt(uid):
        barrier.wait(timeout=10)
        return recover(w, o["id"], uid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, [o["successor"]["id"], other["id"]]))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    assert next(r for r in responses if r.status_code == 409).json()["error"] == "owner_present"
    winner = next(r for r in responses if r.status_code == 200).json()["member"]["user_id"]
    assert [row["user_id"] for row in ident.usable_owners(o["id"])] == [winner]
    previous = {o["successor"]["id"]: "editor", other["id"]: "admin"}
    assert {uid: ident.membership(o["id"], uid)["role"] for uid in previous} == {
        uid: "owner" if uid == winner else role for uid, role in previous.items()}, "the loser keeps their role"
    assert len(ok_rows(w, "admin.workspace.recover_owner")) == 1
    assert denied(w, o["id"])["code"] == "owner_present"
