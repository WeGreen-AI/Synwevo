"""B3: the platform turns Synwevo Message off — and back on — for one organization from the
application catalogue (admin_apps.py, `organization_id` in place of `workspace_id`): the
organization list and its counts, the preview's impact and notes, the confirmed change (platform
audit row, the organization's own governance-log row, the security journal line, the receipt),
every refusal of the organization target, who may change it (`platform.apps.manage` alone — the
access administrator, no `platform.config.manage`), and what the platform-wide Message preview says
since the switch holds for signed-in sessions too (G1).

On the `cat` fixture of test_admin_apps (its people, with the messages registration), plus two
organizations made through the core service: A (owned by `target`; `plain`, `granted`, `both` and
the suspended account members) with a group, a direct message, one FIXTURE object discussion, an
open report, one pending mail and one pending push about A, one pending card; and B (owned by
`both`), untouched by anything done to A. Rows the product's routes would not write here are
written directly and marked FIXTURE."""
from __future__ import annotations

import json
import uuid

import pytest

from synwevo_core import journal, organization_audit
from synwevo_core.apps import DOCS_APP_ID, MESSAGES_APP_ID, organization_app_row
from synwevo_core.errors import ServiceError
from synwevo_core.messaging import Messaging
from synwevo_core.notifications import Notifier
from synwevo_core.organizations import Organizations
from synwevo_core.store import now
from synwevo_identity import admin_apps

from .platform_fixture import make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401
from .test_admin_ownership import governed  # noqa: F401
from .test_admin_apps import BASE, REASON, cat, entries, operations, park, refused, rows  # noqa: F401

AWAY_NOTES = ["data_kept", "conversations_paused", "streams_end_within_heartbeat", "notifications_skipped",
              "cards_skipped", "agent_sends_refused", "moderation_continues", "export_unaffected"]
BACK_NOTES = ["data_kept", "history_unchanged_on_enable", "skipped_not_replayed"]


@pytest.fixture()
def switched(cat):
    w = cat
    store, ident = w["store"], w["ident"]
    orgs, uid = Organizations(store, ident), w["uid"]
    target = w["target"]["id"]
    a = orgs.create(target, "Fixture organization A", "Fixture A space", "b3-fixture-organization-a")["organization"]["id"]
    b = orgs.create(uid["both"], "Fixture organization B", "Fixture B space", "b3-fixture-organization-b")["organization"]["id"]
    for name in ("plain", "granted", "both", "suspended"):
        store.insert("organization_memberships", {"organization_id": a, "user_id": uid[name], "role": "member",
                                                  "created_at": now(), "updated_at": now(), "created_by": target})
    svc = Messaging(store, ident)
    group = svc.create_group(a, target, name="Fixture A group", member_ids=[uid["plain"], uid["granted"]])["id"]
    dm = svc.open_dm(a, target, uid["plain"])["id"]
    # the reader who would be told by mail and push: digests on, push on
    Notifier(store).set_preferences(uid["plain"], {"categories": {"conversation": {"email": "digest", "push": True}}})
    sent, _ = svc.send(group, target, body="fixture mention", client_id="b3-admin-m1", mention_ids=[uid["plain"]])
    report = svc.report(sent["id"], uid["plain"], "spam")
    # FIXTURE: the inbox item the Message process's send route writes for the mention (routes._notify);
    # the account service has no Message routes to send through
    mention = Notifier(store).emit(uid["plain"], "conversation.mention", params={"who": "Fixture", "conversation": "Fixture A group"},
                                   organization_id=a, object_kind="conversation", object_id=group)
    assert mention and store.one("SELECT 1 FROM notification_deliveries WHERE notification_id=? AND status='pending'", (mention["id"],))
    if not store.one("SELECT 1 FROM push_queue WHERE notification_id=?", (mention["id"],)):
        # FIXTURE: the push the dispatcher would hold for plain's device
        store.insert("push_queue", {"notification_id": mention["id"], "user_id": uid["plain"], "status": "pending",
                                    "not_before": now(), "updated_at": now()})
    # FIXTURE: a project's discussion in A (the bridge's row shape), nobody in it but target
    ts = now()
    store.insert("conversations", {"id": "cv_b3fixobj0001", "organization_id": a, "kind": "group", "purpose": "object",
                                   "name": "Fixture object discussion", "created_by": target, "created_at": ts, "updated_at": ts})
    store.insert("conversation_members", {"conversation_id": "cv_b3fixobj0001", "user_id": target, "role": "owner",
                                          "joined_at": ts, "joined_seq": 0, "last_read_seq": 0, "notify": "mentions"})
    # FIXTURE: a card Docs queued for A, not delivered yet
    store.insert("messaging_outbox", {"id": "ob_b3fixcard0001", "app": DOCS_APP_ID, "event_id": "ev_b3fixcard0001", "kind": "docs.comment",
                                      "object_kind": "project", "object_id": "p_b3fix0001", "workspace_id": w["shared"],
                                      "organization_id": a, "actor_id": target, "payload": "{}", "created_at": ts})
    gb = svc.create_group(b, uid["both"], name="Fixture B group", member_ids=[])["id"]
    park(w, "plain", w["shared"])                       # one live session of a member of A
    return {**w, "a": a, "b": b, "group": group, "dm": dm, "gb": gb, "svc": svc, "report": report["id"], "mention": mention["id"]}


def preview(w, change, *, organization=None, app=MESSAGES_APP_ID, client=None, **extra):
    body = {"change": change, **({"organization_id": organization} if organization is not None else {}), **extra}
    return post(client or w["admin"], f"{BASE}/{app}/preview", body)


def plan(w, change, **kwargs):
    r = preview(w, change, **kwargs)
    assert r.status_code == 200, r.text
    return r.json()


def command(p, *, organization, reason=REASON, key=None):
    return {"reason": reason, "preview_token": p["preview_token"], "idempotency_key": key or uuid.uuid4().hex,
            "organization_id": organization}


def execute(w, change, body, *, app=MESSAGES_APP_ID, client=None):
    return post(client or w["admin"], f"{BASE}/{app}/{change}", body)


def listed(w, oid, *, app=MESSAGES_APP_ID, q=""):
    r = w["admin"].get(f"{BASE}/{app}/organizations", params={"limit": 200, "q": q})
    assert r.status_code == 200, r.text
    return next((i for i in r.json()["items"] if i["id"] == oid), None)


def catalogue(w, app=MESSAGES_APP_ID):
    return next(item for item in w["admin"].get(BASE).json()["items"] if item["id"] == app)


def live_sessions_of(store, oid):
    t = now()
    return store.one("SELECT COUNT(*) AS n FROM sessions s JOIN organization_memberships m ON m.user_id=s.user_id "
                     "JOIN users u ON u.id=s.user_id WHERE m.organization_id=? AND s.revoked_at IS NULL AND s.idle_expires_at>? "
                     "AND s.absolute_expires_at>? AND u.status='active' AND u.deleted_at IS NULL", (oid, t, t))["n"]


def org_log(w, oid, action):
    return [i for i in organization_audit.read(w["store"], oid, category="organization")["items"] if i["action"] == action]


def history(store, cids):
    marks = ",".join("?" * len(cids))
    return (store.all(f"SELECT * FROM messages WHERE conversation_id IN ({marks}) ORDER BY id", tuple(cids)),
            store.all(f"SELECT * FROM conversation_members WHERE conversation_id IN ({marks}) ORDER BY conversation_id, user_id", tuple(cids)),
            store.all(f"SELECT * FROM conversations WHERE id IN ({marks}) ORDER BY id", tuple(cids)))


# ---------------------------------------------------------------- the list
def test_the_organization_list_names_each_organization_with_its_switch_and_counts(switched):
    w = switched
    store = w["store"]
    a = listed(w, w["a"])
    assert a["name"] == "Fixture organization A" and a["status"] == "active" and a["enabled"] is True and a["updated_at"] is None
    assert a["members"] == 4, "live members only: the suspended account is not counted"
    assert a["conversations"] == 3
    b = listed(w, w["b"])
    assert (b["enabled"], b["members"], b["conversations"]) == (True, 1, 1)
    r = w["admin"].get(f"{BASE}/{MESSAGES_APP_ID}/organizations", params={"q": "organization B"})
    assert [i["id"] for i in r.json()["items"]] == [w["b"]] and r.json()["total"] == 1
    assert [i["id"] for i in w["admin"].get(f"{BASE}/{MESSAGES_APP_ID}/organizations", params={"q": w["a"]}).json()["items"]] == [w["a"]]
    assert catalogue(w)["organizations_disabled"] == 0
    assert "organizations_disabled" not in catalogue(w, DOCS_APP_ID) or catalogue(w, DOCS_APP_ID)["organizations_disabled"] is None
    # an application not switched per organization has no such list
    r = w["admin"].get(f"{BASE}/{DOCS_APP_ID}/organizations")
    refused(r, 400, "bad_change")
    assert r.json()["detail"]["scope"] == "workspace"
    refused(w["admin"].get(f"{BASE}/no-such-app/organizations"), 404, "no_app")
    # a reader reads it; without a platform permission it does not exist
    reader = w["login"](make_platform_admin(store, w["ident"].create_user("auditor-b3@example.test", PW, status="active"), role="auditor"))
    assert reader.get(f"{BASE}/{MESSAGES_APP_ID}/organizations").status_code == 200
    refused(w["ordinary"].get(f"{BASE}/{MESSAGES_APP_ID}/organizations"), 404, "not_found")


# ---------------------------------------------------------------- off, and back on
def test_switching_message_off_and_on_for_one_organization(switched):
    w = switched
    store, svc, a, b = w["store"], w["svc"], w["a"], w["b"]
    plain, both = w["uid"]["plain"], w["uid"]["both"]
    before = history(store, [w["group"], w["dm"]])
    p = plan(w, "disable", organization=a)                      # the access administrator: platform.apps.manage alone
    assert (p["scope"], p["action"], p["risk"]) == ("organization", "admin.app.organization_disable", "high")
    assert p["target"]["organization"] == listed(w, a)
    assert p["impact"] == {"members_affected": 4, "members_with_conversations": 3, "members_left_without_messages": 3,
                           "conversations": {"groups": 1, "direct": 1, "object": 1}, "open_reports": 1,
                           "pending_notifications": {"email": 1, "push": 1}, "pending_cards": 1,
                           "signed_in_sessions": live_sessions_of(store, a), "open_streams": None}
    assert p["impact"]["signed_in_sessions"] >= 2, "target (signed in) and plain (parked)"
    assert p["notes"] == AWAY_NOTES
    # a plan bound to the organization is not a platform-wide act
    refused(execute(w, "disable", {**command(p, organization=a), "organization_id": None}, client=w["super"]), 409, "admin_preview_invalid")
    out = execute(w, "disable", command(p, organization=a)).json()
    assert out["ok"] is True and out["journaled"] is True and out["scope"] == "organization"
    assert out["organization"] == listed(w, a) and out["organization"]["enabled"] is False
    assert out["organization"]["updated_at"] is not None and out["app"]["organizations_disabled"] == 1
    assert organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "disabled"
    # the member of A is refused on list and read; the member of B is not
    with pytest.raises(ServiceError) as e:
        svc.list_conversations(plain, organization_id=a)
    assert (e.value.status, e.value.code, e.value.detail["scope"]) == (403, "messages_disabled", "organization")
    with pytest.raises(ServiceError) as e:
        svc.get(w["group"], plain)
    assert e.value.code == "messages_disabled"
    assert [c["id"] for c in svc.list_conversations(plain)] == []
    assert [c["id"] for c in svc.list_conversations(both)] == [w["gb"]]
    assert svc.get(w["gb"], both)["id"] == w["gb"]
    # the records: the journal line, the platform row, the organization's own row
    (entry,) = entries(w, "organization_app_disabled")
    assert (entry["organization_id"], entry["app_id"]) == (a, MESSAGES_APP_ID)
    (row,) = rows(w, "admin.app.organization_disable", "ok")
    meta = json.loads(row["meta"])
    assert row["object_id"] == MESSAGES_APP_ID and row["workspace_id"] is None
    assert meta["organization_id"] == a and meta["before"] == {"enabled": True} and meta["after"] == {"enabled": False}
    assert meta["reason"] == REASON and meta["impact"]["conversations"] == {"groups": 1, "direct": 1, "object": 1}
    (log,) = org_log(w, a, "organization.app.disable")
    assert log["actor"] == {"kind": "platform_admin"} and log["object"] == {"kind": "organization"}
    assert log["detail"] == {"reason": REASON, "app_id": MESSAGES_APP_ID, "enabled": False, "by": "platform"}
    assert not org_log(w, b, "organization.app.disable")
    assert history(store, [w["group"], w["dm"]]) == before, "nothing is deleted or rewritten"
    assert store.one("SELECT status FROM message_reports WHERE id=?", (w["report"],))["status"] == "open"
    refused(preview(w, "disable", organization=a), 409, "no_change")

    back = plan(w, "enable", organization=a)
    assert (back["scope"], back["action"], back["risk"]) == ("organization", "admin.app.organization_enable", "medium")
    assert back["notes"] == BACK_NOTES and back["impact"]["members_affected"] == 4
    out = execute(w, "enable", command(back, organization=a)).json()
    assert out["journaled"] is None and out["organization"]["enabled"] is True and out["app"]["organizations_disabled"] == 0
    assert organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "active", "the row is kept, never deleted"
    assert svc.get(w["group"], plain)["id"] == w["group"]
    assert {c["id"] for c in svc.list_conversations(plain)} == {w["group"], w["dm"]}
    assert history(store, [w["group"], w["dm"]]) == before, "history is unchanged after re-enabling"
    assert len(entries(w, "organization_app_disabled")) == 1, "enabling is not journalled"
    (log,) = org_log(w, a, "organization.app.enable")
    assert log["detail"] == {"reason": REASON, "app_id": MESSAGES_APP_ID, "enabled": True, "by": "platform"}
    assert len(operations(w)) == 2


# ---------------------------------------------------------------- refusals and who may
def test_every_refusal_of_the_organization_target(switched):
    w = switched
    store, a = w["store"], w["a"]
    r = preview(w, "disable", organization=a, app=DOCS_APP_ID)
    refused(r, 400, "bad_change")
    assert r.json()["detail"]["scope"] == "workspace"
    refused(preview(w, "disable", organization=a, workspace_id=w["shared"]), 400, "bad_change")
    r = preview(w, "disable", organization="")
    refused(r, 400, "bad_change")
    assert r.json()["detail"]["field"] == "organization_id", "a blank organization never becomes the whole platform"
    refused(preview(w, "disable", organization=7), 400, "bad_change")
    refused(preview(w, "policy_explicit", organization=a), 400, "bad_change")
    refused(preview(w, "disable", organization="org_fixture_missing"), 404, "no_organization")
    refused(preview(w, "enable", organization=a), 409, "no_change")
    for change in ("disable", "enable"):
        refused(preview(w, change, organization=a, app="platform"), 409, "portal_required")
    # the same refusals at confirmation, before any token is looked at
    refused(execute(w, "disable", {"reason": REASON, "organization_id": a, "workspace_id": w["shared"]}), 400, "bad_change")
    refused(execute(w, "disable", {"reason": REASON, "organization_id": "org_fixture_missing"}), 404, "no_organization")
    refused(execute(w, "enable", {"reason": REASON, "organization_id": a}), 409, "no_change")
    refused(execute(w, "disable", {"reason": REASON, "organization_id": a}), 400, "admin_confirmation_required")
    # lifecycle: a frozen organization may be switched either way; one deleted and still recoverable
    # too; one past its window — or deleted with no window — cannot be enabled
    Organizations(store, w["ident"]).platform_transition(a, w["root"]["id"], "freeze", reason=REASON)
    assert execute(w, "disable", command(plan(w, "disable", organization=a), organization=a)).status_code == 200
    assert listed(w, a)["status"] == "frozen"
    assert execute(w, "enable", command(plan(w, "enable", organization=a), organization=a)).status_code == 200
    Organizations(store, w["ident"]).platform_transition(a, w["root"]["id"], "delete", reason=REASON)
    assert plan(w, "disable", organization=a)["target"]["organization"]["status"] == "deleted"
    assert execute(w, "disable", command(plan(w, "disable", organization=a), organization=a)).status_code == 200
    assert preview(w, "enable", organization=a).status_code == 200, "deleted, still recoverable: enabling is allowed"
    store.execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() - 1, a))        # FIXTURE clock
    r = preview(w, "enable", organization=a)
    refused(r, 409, "organization_unavailable")
    assert r.json()["detail"]["organization_status"] == "deleted"
    refused(execute(w, "enable", {"reason": REASON, "organization_id": a}), 409, "organization_unavailable")
    assert organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "disabled"
    # refused acts are recorded under the organization action they named, with a code and the organization
    denied = rows(w, "admin.app.organization_disable", "denied")
    assert denied and all(json.loads(r["meta"])["code"] for r in denied)
    assert a in {json.loads(r["meta"]).get("organization_id") for r in denied}
    assert "org_fixture_missing" not in {json.loads(r["meta"]).get("organization_id") for r in denied}, "an unknown id is the caller's text"


def test_the_organization_scope_needs_the_catalogue_permission_alone_and_readers_are_refused(switched):
    w = switched
    a = w["a"]
    reader = w["login"](make_platform_admin(w["store"], w["ident"].create_user("support-b3@example.test", PW, status="active"), role="support"))
    w["reauth"](reader)
    p = plan(w, "disable", organization=a)
    for r in (preview(w, "disable", organization=a, client=reader), execute(w, "disable", command(p, organization=a), client=reader)):
        refused(r, 403, "platform_permission_denied")
        assert r.json()["detail"]["permission"] == "platform.apps.manage"
    refused(preview(w, "disable", organization=a, client=w["ordinary"]), 404, "not_found")
    assert organization_app_row(w["store"], a, MESSAGES_APP_ID) is None
    # the super administrator may too, and a change needs the strict re-authentication before its body
    fresh = make_platform_admin(w["store"], w["ident"].create_user("fresh-b3@example.test", PW, status="active"), role="access_admin")
    c = w["login"](fresh)
    q = plan(w, "disable", organization=a, client=c)
    refused(execute(w, "disable", command(q, organization=a), client=c), 403, "reauth_required")
    w["reauth"](c)
    assert execute(w, "disable", command(q, organization=a), client=c).status_code == 200
    assert rows(w, "admin.app.organization_disable", "ok")[0]["actor_id"] == fresh["id"]
    assert execute(w, "enable", command(plan(w, "enable", organization=a, client=w["super"]), organization=a), client=w["super"]).status_code == 200


@pytest.mark.parametrize("change", ["member_joins", "switch_changed", "lifecycle_changed", "app_changed"])
def test_a_changed_organization_makes_the_preview_stale(switched, change):
    w = switched
    store, a = w["store"], w["a"]
    p = plan(w, "disable", organization=a)
    if change == "member_joins":
        store.insert("organization_memberships", {"organization_id": a, "user_id": w["uid"]["revoked"], "role": "member",
                                                  "created_at": now(), "updated_at": now(), "created_by": w["target"]["id"]})
    elif change == "switch_changed":
        w["apps"].disable_for_organization(a, MESSAGES_APP_ID)
        w["apps"].enable_for_organization(a, MESSAGES_APP_ID)
    elif change == "lifecycle_changed":
        Organizations(store, w["ident"]).platform_transition(a, w["root"]["id"], "freeze", reason=REASON)
    else:
        w["apps"].set_status(MESSAGES_APP_ID, "disabled")
    refused(execute(w, "disable", command(p, organization=a)), 409, "admin_preview_stale")
    assert organization_app_row(store, a, MESSAGES_APP_ID) is None or organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "active"
    assert not rows(w, "admin.app.organization_disable", "ok")


def test_identical_retries_return_the_stored_result_and_write_nothing_twice(switched):
    w = switched
    a = w["a"]
    body = command(plan(w, "disable", organization=a), organization=a)
    first = execute(w, "disable", body)
    assert first.status_code == 200, first.text
    retry = execute(w, "disable", {**body, "reason": "  " + REASON + "  "})
    assert retry.status_code == 200 and retry.json() == first.json()
    assert len(operations(w)) == len(rows(w, "admin.app.organization_disable", "ok")) == len(entries(w, "organization_app_disabled")) == 1
    assert len(org_log(w, a, "organization.app.disable")) == 1
    refused(execute(w, "disable", {**body, "organization_id": w["b"]}), 409, "idempotency_conflict")
    refused(execute(w, "disable", {**body, "reason": REASON + " changed"}), 409, "idempotency_conflict")


def test_a_journal_that_cannot_be_written_commits_nothing_for_an_organization(switched, monkeypatch):
    w = switched
    store, a = w["store"], w["a"]

    def unavailable(*args, **kwargs):
        raise OSError("fixture: security journal unavailable")

    p = plan(w, "disable", organization=a)
    original = journal.record
    monkeypatch.setattr(journal, "record", unavailable)
    r = execute(w, "disable", command(p, organization=a))
    monkeypatch.setattr(journal, "record", original)
    refused(r, 503, "journal_unavailable")
    assert "fixture: security journal unavailable" not in r.text
    assert organization_app_row(store, a, MESSAGES_APP_ID) is None, "the switch rolled back with the journal"
    assert not rows(w, "admin.app.organization_disable", "ok") and not org_log(w, a, "organization.app.disable")
    assert json.loads(rows(w, "admin.app.organization_disable", "error")[-1]["meta"])["code"] == "journal_unavailable"
    assert not operations(w) and not entries(w, "organization_app_disabled")
    assert w["svc"].get(w["group"], w["uid"]["plain"])["id"] == w["group"]


# ---------------------------------------------------------------- G1: the platform-wide preview
def test_the_platform_wide_message_preview_says_the_switch_holds_for_signed_in_sessions(switched):
    w = switched
    off = plan(w, "disable", client=w["super"])
    assert off["scope"] == "platform" and off["notes"] == AWAY_NOTES
    assert off["impact"]["people_losing_access"] == 7
    assert execute(w, "disable", {"reason": REASON, "preview_token": off["preview_token"], "idempotency_key": uuid.uuid4().hex},
                   client=w["super"]).status_code == 200
    with pytest.raises(ServiceError) as e:
        w["svc"].get(w["group"], w["uid"]["plain"])
    assert (e.value.code, e.value.detail["scope"]) == ("messages_disabled", "platform")
    on = plan(w, "enable", client=w["super"])
    assert on["notes"] == BACK_NOTES
    # the platform switch outranks an organization's: said in the organization's preview while it is off
    assert plan(w, "disable", organization=w["a"])["notes"] == AWAY_NOTES + ["platform_switch_outranks"]
    docs = plan(w, "disable", app=DOCS_APP_ID, client=w["super"])
    assert "conversations_paused" not in docs["notes"], "the Message notes are Message's alone"


# ---------------------------------------------------------------- the command line (parity)
@pytest.fixture()
def cli_world(tmp_path, monkeypatch):
    from synwevo_core.apps import Apps, bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.store import Store
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.delenv("SYNWEVO_SECRET", raising=False)
    store = Store(d)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    # FIXTURE: messages registered, as its own process does at start-up
    Apps(store, ident).register(app_id=MESSAGES_APP_ID, name="Fixture messages", icon="chat", launch_url="http://messages.localhost:8771/")
    alice = ident.create_user("alice-b3@example.test", PW, status="active")
    a = Organizations(store, ident).create(alice["id"], "Fixture CLI organization", "Fixture CLI space", "b3-cli-organization")["organization"]["id"]
    return {"d": str(d), "store": store, "a": a, "mp": monkeypatch}


def run_cli(w, capsys, *argv):
    import sys
    from synwevo_identity import cli as cli_mod
    w["mp"].setattr(sys, "argv", ["synwevo_identity.cli", "--data", w["d"], *argv])
    code = cli_mod.main()
    out = capsys.readouterr()
    return code, out.out, out.err


def test_the_command_line_switches_message_for_one_organization_through_the_same_service(cli_world, capsys):
    w = cli_world
    store, a = w["store"], w["a"]
    code, out, _ = run_cli(w, capsys, "app", "disable", MESSAGES_APP_ID, "--organization", a)
    assert code == 0 and "已停用" in out
    assert organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "disabled"
    (entry,) = [e for e in journal.read(w["d"]) if e["kind"] == "organization_app_disabled"]
    assert (entry["organization_id"], entry["app_id"]) == (a, MESSAGES_APP_ID)
    assert store.one("SELECT 1 FROM audit_events WHERE action='cli.app.organization_disable'")
    (log,) = [i for i in organization_audit.read(store, a, category="organization")["items"] if i["action"] == "organization.app.disable"]
    assert log["detail"] == {"app_id": MESSAGES_APP_ID, "enabled": False, "by": "platform"}
    # again: nothing to change, nothing journalled or recorded twice
    code, out, _ = run_cli(w, capsys, "app", "disable", MESSAGES_APP_ID, "--organization", a)
    assert code == 0 and "没有需要修改的" in out
    assert len([e for e in journal.read(w["d"]) if e["kind"] == "organization_app_disabled"]) == 1
    # the refusals: not organization-scoped, an unknown organization, both targets
    code, _, err = run_cli(w, capsys, "app", "disable", "docweave", "--organization", a)
    assert code == 2 and "bad_change" in err
    code, _, err = run_cli(w, capsys, "app", "disable", MESSAGES_APP_ID, "--organization", "org_fixture_missing")
    assert code == 2 and "no_organization" in err
    code, _, err = run_cli(w, capsys, "app", "disable", MESSAGES_APP_ID, "--organization", a, "--workspace", "w_fixture")
    assert code == 2
    code, out, _ = run_cli(w, capsys, "app", "enable", MESSAGES_APP_ID, "--organization", a)
    assert code == 0 and "已启用" in out and organization_app_row(store, a, MESSAGES_APP_ID)["status"] == "active"
    assert len([e for e in journal.read(w["d"]) if e["kind"] == "organization_app_disabled"]) == 1, "enabling is not journalled"


def test_a_journal_failure_on_the_command_line_leaves_message_on(cli_world, capsys):
    w = cli_world
    real = journal.record

    def unavailable(*args, **kwargs):
        raise PermissionError("fixture: journal not writable")
    w["mp"].setattr(journal, "record", unavailable)
    code, _, err = run_cli(w, capsys, "app", "disable", MESSAGES_APP_ID, "--organization", w["a"])
    w["mp"].setattr(journal, "record", real)
    assert code == 3 and "仍是启用状态" in err
    assert organization_app_row(w["store"], w["a"], MESSAGES_APP_ID) is None, "rolled back with the journal"
    assert not [i for i in organization_audit.read(w["store"], w["a"], category="organization")["items"]
                if i["action"] == "organization.app.disable"]
