"""Nothing about a closed workspace leaves the platform by mail or push (A44 round 3, A44-PAUSE-1).

A project's discussion whose workspace is frozen, deleted (still recoverable, or past its window
before the sweep has run) — a report of a message in it, and the Docs, Forms and project-copy
items of such a workspace — are refused in the app; the
send-time re-check (`Notifier.stale_reason`) must agree, or the unread digest and the Web Push carry
the conversation's name, the unread count and, under `preview = full`, the message text out while
every read in the app answers `workspace_unavailable`. The delivery is skipped with that reason,
not deferred; once the workspace is back, later items are mailed and pushed as before.

Checked where all the halves live: Message's routes, Docs' projects and comments, and the account
service's dispatcher on the composed server. FIXTURE: the push service is a loopback double (no
real push service is contacted), the clock is moved by writing `not_before` (the 30-minute
aggregation window, the end of quiet hours), a deletion past its recovery window is the row as it is
once `recover_until` has passed, and one `docs.job_finished` item is written the way Docs' job
runner writes it. Regression for the review probes `test_probe_digest.py` and
`refute1/wt/test_refute_pause1.py`."""
from __future__ import annotations

import base64
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from synwevo_core import workspace_lifecycle
from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.notifications import Notifier
from synwevo_core.push import PushSubscriptions
from synwevo_core.store import now

PW = "harbor lights guide the evening ferry home"
CSRF, WS = "X-Synwevo-CSRF", "X-Synwevo-Workspace"
M = "/api/messaging"
REASON = "Fixture: paused workspace notifications"
SECRET = "SECRET-roadmap-before-the-pause"


class PushDouble:
    """FIXTURE: a loopback push service that accepts every push (201) and remembers it."""

    def __init__(self):
        self.requests: list[str] = []
        double = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                double.requests.append(self.path)
                self.send_response(201); self.send_header("Content-Length", "0"); self.end_headers()

            def log_message(self, *a):  # noqa: D401 - quiet
                pass
        self.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown(); self.server.server_close()


def _device(endpoint: str) -> dict:
    """What PushManager.subscribe().toJSON() gives: an endpoint and the two client keys."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return {"endpoint": endpoint, "keys": {"p256dh": b64(pub), "auth": b64(b"0123456789abcdef")}}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    # the push double is on loopback; never route it through a system proxy
    for key in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(key, "127.0.0.1,localhost,::1")
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                 "MAIL_FROM": "synwevo@example.test", "PUBLIC_URL": "http://testserver", "ISSUER": "http://testserver",
                 "ROLE": "all", "APP_URL": "http://testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    from synwevo_core.organizations import Organizations
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident, store = api.state.identity, api.state.identity.store
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    created = Organizations(store, ident).create(users["owner"]["id"], "Pause Org", "Pause Team", "fixture-idem-pause-notify-0001")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    for key, role in (("ann", "editor"), ("ben", "viewer")):
        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                      (oid, users[key]["id"], users["owner"]["id"]))
        store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,?,1,1,1,?)",
                      (wid, users[key]["id"], role, users["owner"]["id"]))
    clients = {}
    for key in users:
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW}).status_code == 200
        c.headers.update({CSRF: c.cookies.get("dw_csrf")})       # Docs calls name the workspace (`_docs`)
        clients[key] = c
    double = PushDouble()
    ben = users["ben"]["id"]
    # the reader who would be told: digests on, push on, message text allowed out, one device
    Notifier(store).set_preferences(ben, {"categories": {"conversation": {"email": "digest", "push": True},
                                                         "docs": {"email": "digest", "push": True}}, "preview": "full"})
    PushSubscriptions(store).add(ben, None, _device(f"{double.base}/push/ben"))
    try:
        yield {"api": api, "store": store, "c": clients, "u": users, "oid": oid, "wid": wid, "double": double, "ben": ben,
               "dispatch": api.state.notify_dispatcher}
    finally:
        for c in clients.values():
            c.close()
        double.close()


def _discussion(w) -> tuple[str, str]:
    """A real Docs project in the organization's workspace and its discussion (owner, ann, ben)."""
    pid = _project(w)
    r = w["c"]["ann"].post(f"{M}/objects/{DOCS_APP_ID}/project/{pid}/conversation")
    assert r.status_code in (200, 201), r.text
    conv = r.json()["conversation"]
    assert w["ben"] in {m["user_id"] for m in conv["members"]}
    return pid, conv["id"]


def _project(w) -> str:
    r = w["c"]["ann"].post("/api/projects", json={"name": "Pause Project"}, headers=_docs(w))
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _docs(w) -> dict:
    return {WS: w["wid"]}


def _mention(w, cid: str, body: str, client_id: str) -> None:
    r = w["c"]["ann"].post(f"{M}/conversations/{cid}/messages", json={"body": body, "client_id": client_id, "mention_ids": [w["ben"]]})
    assert r.status_code == 201, r.text


def _close(w, act: str) -> None:
    store, wid, actor = w["store"], w["wid"], w["u"]["owner"]["id"]
    if act == "freeze":
        w["api"].state.identity.set_workspace_frozen(wid, True)
    elif act in ("delete", "lost"):
        workspace_lifecycle.platform_transition(store, wid, actor, "delete", reason=REASON)
        if act == "lost":
            # FIXTURE clock: the seven days are over; the bridge's sweep has not run yet
            store.execute("UPDATE workspaces SET recover_until=? WHERE id=?", (now() - 1, wid))


def _reopen(w, act: str) -> None:
    if act == "freeze":
        w["api"].state.identity.set_workspace_frozen(w["wid"], False)
    elif act == "delete":
        workspace_lifecycle.platform_transition(w["store"], w["wid"], w["u"]["owner"]["id"], "recover", reason=REASON)


def _due(store) -> None:
    """FIXTURE clock: the aggregation window and any quiet hours are over."""
    store.execute("UPDATE notification_deliveries SET not_before=? WHERE status='pending'", (now() - 1,))
    store.execute("UPDATE push_queue SET not_before=? WHERE status='pending'", (now() - 1,))


def _outcomes(w, table: str, uid: str | None = None) -> list[tuple]:
    status = "d.status, d.last_error FROM notification_deliveries d" if table == "email" else "d.status, d.last_error FROM push_queue d"
    return [(r["kind"], r["status"], r["last_error"]) for r in w["store"].all(
        f"SELECT n.kind, {status} JOIN notifications n ON n.id=d.notification_id WHERE n.user_id=? ORDER BY n.seq", (uid or w["ben"],))]


def _mails(w, to: str = "ben@example.test") -> list[dict]:
    return w["store"].all("SELECT subject, body, COALESCE(html, '') AS html FROM mail_outbox WHERE kind='notification' AND to_addr=? ORDER BY created_at",
                          (to,))


def _leaked(w, text: str, to: str = "ben@example.test") -> bool:
    return any(text in m["subject"] or text in m["body"] or text in m["html"] for m in _mails(w, to))


@pytest.mark.parametrize("act", ["freeze", "delete", "lost"])
def test_a_mention_in_a_closed_project_discussion_is_neither_mailed_nor_pushed(world, act):
    w = world
    store, n, ben = w["store"], w["api"].state.notifier, w["ben"]
    _, cid = _discussion(w)
    _mention(w, cid, SECRET, "pn-1")
    _close(w, act)
    if act != "lost":
        assert w["api"].state.bridge_worker.sweep() == 0                 # a pause removes nobody
    live = w["c"]["ben"].get(f"{M}/conversations/{cid}/messages")
    assert (live.status_code, live.json()["error"]) == ((404, "no_conversation") if act == "lost" else (403, "workspace_unavailable"))
    # should anything render the item anyway, no message text is in it
    row = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='conversation.mention'", (ben,))
    assert n.conversation_unread(cid, ben) is None
    assert n.render_external(row, "zh-Hans", n.preferences(ben))["body"] == ""
    _due(store)
    out = w["dispatch"].run_once()
    assert out["queued"] == 0 and out["pushed"] == 0, out
    assert _outcomes(w, "email") == [("conversation.mention", "skipped", "workspace_unavailable")]
    assert _outcomes(w, "push") == [("conversation.mention", "skipped", "workspace_unavailable")]
    assert _mails(w) == [] and w["double"].requests == []
    if act == "lost":
        assert w["api"].state.bridge_worker.sweep() == 3                 # the sweep still does the removal
        return
    # back: ben reads what he missed, ann mentions him again — mailed and pushed as before
    _reopen(w, act)
    hb = w["c"]["ben"]
    last = hb.get(f"{M}/conversations/{cid}").json()["last_seq"]
    assert hb.post(f"{M}/conversations/{cid}/read", json={"seq": last}).status_code == 200
    _mention(w, cid, "after-the-pause", "pn-2")
    _due(store)
    out = w["dispatch"].run_once()
    assert out["queued"] == 1 and out["pushed"] == 1, out
    assert _outcomes(w, "email")[-1] == ("conversation.mention", "queued", None)
    assert _outcomes(w, "push")[-1] == ("conversation.mention", "sent", None)
    assert _leaked(w, "after-the-pause") and not _leaked(w, SECRET)
    assert w["double"].requests == ["/push/ben"]


def test_a_push_held_back_by_quiet_hours_does_not_go_out_once_the_workspace_is_frozen(world):
    w = world
    store, n, ben = w["store"], w["api"].state.notifier, w["ben"]
    _, cid = _discussion(w)
    hh = datetime.now(ZoneInfo("Asia/Shanghai")).hour
    n.set_preferences(ben, {"quiet_start": f"{hh:02d}:00", "quiet_end": f"{(hh + 2) % 24:02d}:00", "timezone": "Asia/Shanghai"})
    _mention(w, cid, SECRET, "pq-1")
    out = w["dispatch"].run_once()
    assert out["push_deferred"] == 1 and out["pushed"] == 0, out
    _close(w, "freeze")
    n.set_preferences(ben, {"quiet_start": None, "quiet_end": None})
    _due(store)
    out = w["dispatch"].run_once()
    assert out["pushed"] == 0 and out["queued"] == 0, out
    assert _outcomes(w, "push") == [("conversation.mention", "skipped", "workspace_unavailable")]
    assert _outcomes(w, "email") == [("conversation.mention", "skipped", "workspace_unavailable")]
    assert w["double"].requests == [] and _mails(w) == []


@pytest.mark.parametrize("paused", [False, True])
def test_an_announcement_digest_of_a_paused_discussion_is_not_mailed(world, paused):
    """The announcement item carries its excerpt; with the workspace available it is mailed (the
    control), while paused it is skipped like every other item of the discussion."""
    w = world
    _, cid = _discussion(w)
    r = w["c"]["ann"].put(f"{M}/conversations/{cid}/announcement", json={"text": SECRET})
    assert r.status_code == 200, r.text
    if paused:
        _close(w, "freeze")
    _due(w["store"])
    w["dispatch"].run_once()
    kinds = [k for k, *_ in _outcomes(w, "email")]
    assert kinds == ["conversation.announcement"]
    if paused:
        assert _outcomes(w, "email") == [("conversation.announcement", "skipped", "workspace_unavailable")]
        assert _outcomes(w, "push") == [("conversation.announcement", "skipped", "workspace_unavailable")]
        assert _mails(w) == [] and w["double"].requests == []
    else:
        assert _outcomes(w, "email") == [("conversation.announcement", "queued", None)]
        assert _leaked(w, SECRET) and w["double"].requests == ["/push/ben"]


@pytest.mark.parametrize("act", [None, "freeze", "delete", "lost"])
def test_docs_items_of_a_closed_workspace_are_neither_mailed_nor_pushed(world, act):
    """Docs' own items carry the workspace: a mention and a reply on a project's comment (through
    Docs' comment API) and a finished job (FIXTURE: the item Docs' job runner writes). Mailed and
    pushed while the workspace is available (the control); skipped while it is not."""
    w = world
    store, ben, wid = w["store"], w["ben"], w["wid"]
    ann, hb = w["c"]["ann"], w["c"]["ben"]
    pid = _project(w)
    r = ann.post(f"/api/projects/{pid}/comments", json={"body": f"@Ben Member {SECRET}-1", "mentions": [ben]}, headers=_docs(w))
    assert r.status_code == 201, r.text
    first = r.json()["id"]
    assert hb.post(f"/api/projects/{pid}/comments", json={"body": "ok", "parent_id": first}, headers=_docs(w)).status_code == 201
    assert ann.post(f"/api/projects/{pid}/comments", json={"body": f"{SECRET}-2", "parent_id": first}, headers=_docs(w)).status_code == 201
    w["api"].state.notifier.emit(ben, "docs.job_finished", params={"project": "Pause Project", "outcome": "已完成", "done": "1", "failed": "0"},
                                 workspace_id=wid, object_kind="job", object_id="j_pausefixture", dedupe_key="job:j_pausefixture:1",
                                 target={"origin": "docs", "path": f"/p/{pid}/jobs/j_pausefixture"})
    kinds = ["docs.mention", "docs.comment", "docs.job_finished"]
    assert [k for k, *_ in _outcomes(w, "email")] == kinds
    if act:
        _close(w, act)
    _due(store)
    out = w["dispatch"].run_once()
    if act is None:
        assert _outcomes(w, "email") == [(k, "queued", None) for k in kinds]
        assert _outcomes(w, "push") == [(k, "sent", None) for k in kinds]
        assert _leaked(w, f"{SECRET}-1") and len(w["double"].requests) == 3
        return
    assert out["queued"] == 0 and out["pushed"] == 0, out
    assert _outcomes(w, "email") == [(k, "skipped", "workspace_unavailable") for k in kinds]
    assert _outcomes(w, "push") == [(k, "skipped", "workspace_unavailable") for k in kinds]
    assert _mails(w) == [] and w["double"].requests == []
    # the items stay in the inbox: the reader sees them in the app once the workspace is back
    assert [x["kind"] for x in w["api"].state.notifier.list(ben)["items"]] == list(reversed(kinds))


def _workspace_items(w) -> None:
    """FIXTURE: three items for ben, written the way their writers write them — Forms' request
    (forms/service.py `_notify`: the form's workspace), a project copy into the workspace
    (copies.announce_request: the target workspace and its organization) and the account
    service's notice that ben's own role changed there (members.py: the same `workspace_id`)."""
    n, ben, wid, oid = w["api"].state.notifier, w["ben"], w["wid"], w["oid"]
    n.emit(ben, "forms.request.assigned", params={"requester": "Ann Member", "form": "Pause Form"}, workspace_id=wid,
           object_kind="form_request", object_id="frq_pausefixture", dedupe_key="forms.request.assigned:frq_pausefixture",
           target={"origin": "portal", "path": "/forms/fill/fixture-token"}, actionable=True)
    n.emit(ben, "copy.requested", params={"requester": "Ann Member", "project": "Pause Project", "workspace": "Pause Team"},
           organization_id=oid, workspace_id=wid, object_kind="project_copy", object_id="pc_pausefixture",
           dedupe_key=f"copy:pc_pausefixture:requested:{ben}", actionable=True,
           target={"origin": "accounts", "path": f"/organizations/{oid}?approval=pc_pausefixture"})
    n.emit(ben, "member.role_changed", params={"workspace": "Pause Team", "role": "editor", "actor": "Ola Owner"},
           workspace_id=wid, object_kind="membership", object_id=ben, target={"origin": "accounts", "path": "/account#workspaces"})


@pytest.mark.parametrize("act", [None, "freeze", "delete", "lost"])
def test_forms_and_copy_items_of_a_closed_workspace_are_skipped_but_a_notice_of_ones_own_role_is_not(world, act):
    """A44-44c-1: not only Docs items carry the workspace their content is in. A Forms request
    (`automation`) and a project copy into the workspace (`approval`) left by mail and push while
    the workspace was frozen or deleted, naming the form, the project and the workspace. They are
    skipped like Docs items now. A notice about the reader's own membership there (`organization`
    category) is not about the workspace's content: it still goes out, whatever its state."""
    w = world
    store, ben = w["store"], w["ben"]
    _workspace_items(w)
    kinds = ["forms.request.assigned", "copy.requested", "member.role_changed"]
    assert [k for k, *_ in _outcomes(w, "email")] == kinds
    if act:
        _close(w, act)
    _due(store)
    w["dispatch"].run_once()
    if act is None:
        assert _outcomes(w, "email") == [(k, "queued", None) for k in kinds]
        assert _outcomes(w, "push") == [(k, "sent", None) for k in kinds]
        assert _leaked(w, "Pause Form") and _leaked(w, "Pause Project")
        return
    content = [(k, "skipped", "workspace_unavailable") for k in kinds[:2]]
    assert _outcomes(w, "email") == content + [("member.role_changed", "queued", None)]
    assert _outcomes(w, "push") == content + [("member.role_changed", "sent", None)]
    assert not _leaked(w, "Pause Form") and not _leaked(w, "Pause Project")
    assert _leaked(w, "Pause Team") and w["double"].requests == ["/push/ben"]
    # the items stay in the inbox: the reader sees them in the app once the workspace is back
    assert [x["kind"] for x in w["api"].state.notifier.list(ben)["items"]] == list(reversed(kinds))


@pytest.mark.parametrize("act", [None, "freeze", "delete", "lost"])
def test_a_report_of_a_message_in_a_closed_project_discussion_is_neither_mailed_nor_pushed(world, act):
    """A44-44c-2: `conversation.reported` is keyed by the report, not by the conversation, so a
    report filed before the pause (held for its digest window, or by quiet hours) still went to the
    organization's messaging managers by mail and push — "Ben Member reported a message in “Pause
    Project”" — while the discussion was paused or already lost. The send-time check now follows
    report → message → conversation and skips it like every other item of the discussion. The
    reporter's outcome items (`conversation.report_*`, the same object) are held back the same way."""
    w = world
    store, n, owner = w["store"], w["api"].state.notifier, w["u"]["owner"]["id"]
    n.set_preferences(owner, {"categories": {"conversation": {"email": "digest", "push": True}}})
    PushSubscriptions(store).add(owner, None, _device(f"{w['double'].base}/push/owner"))
    _, cid = _discussion(w)
    r = w["c"]["ann"].post(f"{M}/conversations/{cid}/messages", json={"body": SECRET, "client_id": "pr-1"})
    assert r.status_code == 201, r.text
    r = w["c"]["ben"].post(f"{M}/messages/{r.json()['message']['id']}/report", json={"reason": "privacy", "note": "fixture note"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    def reported(table: str) -> list[tuple]:
        return [x for x in _outcomes(w, table, owner) if x[0] == "conversation.reported"]

    assert reported("email") == [("conversation.reported", "pending", None)]
    if act:
        _close(w, act)
    _due(store)
    w["dispatch"].run_once()
    outcome = {"category": "conversation", "object_kind": "message_report", "object_id": rid}
    if act is None:
        assert reported("email") == [("conversation.reported", "queued", None)]
        assert reported("push") == [("conversation.reported", "sent", None)]
        assert _leaked(w, "Pause Project", "owner@example.test") and "/push/owner" in w["double"].requests
        assert not n.workspace_closed(outcome)
        return
    assert reported("email") == [("conversation.reported", "skipped", "workspace_unavailable")]
    assert reported("push") == [("conversation.reported", "skipped", "workspace_unavailable")]
    assert _mails(w, "owner@example.test") == [] and "/push/owner" not in w["double"].requests
    assert n.workspace_closed(outcome)
    # the to-do stays in the managers' inbox
    assert "conversation.reported" in [x["kind"] for x in n.list(owner)["items"]]
