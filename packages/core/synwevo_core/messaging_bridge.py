"""The bridge between Synwevo Docs and Synwevo Message (CI-75): object conversations, the outbox
Docs writes in its own transactions, the cards the messages process renders **per reader**, and
the application identity that sends them. Decision record in docs/platform/messaging.md §8.

Three rules, in the order they matter:

* **A conversation about an object is still a conversation.** A Docs project (workspace-scoped)
  may have one linked group in its workspace's organization (`object_conversations`). Who may
  open it is decided by the object's own authorization — the workspace membership the Docs side
  uses — re-checked on every open, on every join, and by the worker after every delivery; someone
  who can no longer open the project is removed from its conversation, the way an organization
  removal already closes every conversation of that organization.
* **Sending a message is not authorization.** A card is stored once (`message_cards`) and rendered
  for each reader at read time: a reader who cannot open the object gets the application's name,
  the state chip and a neutral title — never the object's title, never a link. Being in the
  conversation, or being the person the card was about, grants nothing.
* **Events, not edits.** Docs writes one row into `messaging_outbox` in the *same transaction* as
  the business change it reports (a comment stored, a run finished, a copy request decided), with a
  deterministic `event_id` so a retry is one card and never two. The worker in the messages
  process turns rows into cards; a row for an object without a conversation is marked delivered
  as skipped — an event never creates a conversation. No collaborative-edit noise: nothing here
  fires per keystroke or per save.

The sender of every card is an **application account**: a `users` row with no password, flagged
in `app_accounts`, refused by `Sessions.create` (it can never sign in), never an organization or
workspace member (so it appears in no directory). Every link in a card is built here from the
application registry and the object id — never from an event payload.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Callable

from . import env
from .apps import DOCS_APP_ID
from .config import DOCS_NAME
from .errors import ServiceError
from .identity import Identity
from .organizations import current_membership, organization_for_workspace, workspace_access_failure
from .store import Store, new_id, now

log = logging.getLogger("synwevo.messaging_bridge")

SCHEMA = """
CREATE TABLE IF NOT EXISTS object_conversations (
  app TEXT NOT NULL, kind TEXT NOT NULL, object_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
  workspace_id TEXT, organization_id TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (app, kind, object_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_object_conversations_conv ON object_conversations(conversation_id);
CREATE TABLE IF NOT EXISTS messaging_outbox (
  id TEXT PRIMARY KEY, app TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  object_kind TEXT NOT NULL, object_id TEXT NOT NULL, workspace_id TEXT, organization_id TEXT, actor_id TEXT,
  payload TEXT, created_at REAL NOT NULL, delivered_at REAL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE INDEX IF NOT EXISTS ix_messaging_outbox_pending ON messaging_outbox(delivered_at, created_at);
CREATE TABLE IF NOT EXISTS message_cards (message_id TEXT PRIMARY KEY, card TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_accounts (user_id TEXT PRIMARY KEY, app TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);
"""
#: `purpose` marks a group that exists for an object (`object`); NULL is an ordinary group. Added
#: as a nullable column, not a new `kind` value (the CHECK on `kind` is not redefined).
COLUMNS = [("conversations", "purpose", "TEXT")]

#: Applications that may send cards, by registry id, with their display names.
APPS = {DOCS_APP_ID: DOCS_NAME}
#: What a card can be about.
OBJECT_KINDS = ("project", "job", "comment", "copy_request")
#: What can be discussed. A job is discussed in its project's conversation.
CONVERSATION_KINDS = ("project", "job", "copy_request")
#: The outbox event kinds Docs emits today. A kind not listed here is refused at emit time, so a
#: typo cannot become an undeliverable row.
EVENT_KINDS = ("docs.comment", "docs.job_finished", "copy.decided", "copy.finished")
SKIPPED_NO_CONVERSATION = "skipped_no_conversation"
SKIPPED_OBJECT_GONE = "skipped_object_gone"
SKIPPED_ARCHIVED = "skipped_archived"
MAX_ATTEMPTS = 10
# A person's own message queued by another process (Suite on the workplace origin), delivered
# here so that fan-out, notifications and digests behave exactly as for a message typed in Message.
USER_MESSAGE = "user.message"
#: Definite refusals at delivery. Recorded on the row; the sender's receipt becomes failed.
USER_MESSAGE_REFUSALS = frozenset({"archived", "audience_changed", "no_conversation", "not_member",
                                   "account_unavailable", "blocked", "message_too_long", "empty_message"})
MAX_TITLE = 80
MAX_SUBTITLE = 80
LOCALES = ("zh-Hans", "zh-Hant", "en")
#: What a reader without access to the object sees instead of its title.
NEUTRAL_TITLE = {"zh-Hans": "你没有权限查看的文档", "zh-Hant": "你沒有權限查看的文件", "en": "A document you cannot open"}
#: Action labels, rendered in the reader's language at read time (the stored card holds the id).
ACTION_LABELS = {
    "open_project": {"zh-Hans": "在 Synwevo Docs 中打开", "zh-Hant": "在 Synwevo Docs 中開啟", "en": "Open in Synwevo Docs"},
    "open_job": {"zh-Hans": "查看任务", "zh-Hant": "查看任務", "en": "View the job"},
    "open_comment": {"zh-Hans": "查看评论", "zh-Hant": "查看評論", "en": "View the comment"},
    "open_approval": {"zh-Hans": "查看审批", "zh-Hant": "查看審批", "en": "View the approval"},
}
#: Templated subtitles, rendered in the reader's language (`subtitle_t` in the stored card).
SUBTITLES = {
    "job_counts": {"zh-Hans": "{done} 份成功，{failed} 份失败", "zh-Hant": "{done} 份成功，{failed} 份失敗", "en": "{done} succeeded, {failed} failed"},
}
#: The plain-text body of a card message (what a list preview, a search hit or a shell without
#: cards shows). zh-Hans source text like every server-side string; it names the kind of event and
#: its state, never the object. The shell renders the card instead of the body when it has one.
BODY = {
    "docs.comment": "Synwevo Docs · 新评论",
    "docs.job_finished": {"completed": "Synwevo Docs · 生成任务已完成", "completed_with_errors": "Synwevo Docs · 生成任务部分失败",
                          "failed": "Synwevo Docs · 生成任务失败", "cancelled": "Synwevo Docs · 生成任务已取消"},
    "copy.decided": {"approved": "Synwevo Docs · 复制申请已批准", "rejected": "Synwevo Docs · 复制申请被拒绝"},
    "copy.finished": {"done": "Synwevo Docs · 副本已创建", "failed": "Synwevo Docs · 复制失败", "stale": "Synwevo Docs · 复制申请需要重新提交"},
}
APP_ACCOUNT_DOMAIN = "apps.synwevo.invalid"      # RFC 2606: no mailbox can ever exist here
JOB_STATES = ("queued", "running", "cancelling", "completed", "completed_with_errors", "failed", "cancelled")


def _load(v):
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _locale(store: Store, uid: str | None) -> str:
    if not uid:
        return "zh-Hans"
    row = store.one("SELECT locale FROM users WHERE id=?", (uid,))
    loc = (row or {}).get("locale")
    return loc if loc in LOCALES else "zh-Hans"


def _excerpt(text: str, n: int = MAX_SUBTITLE) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= n else one[: n - 1] + "…"


# ---------------------------------------------------------------------- the application identity
def app_account_id(store: Store, app: str) -> str | None:
    try:
        row = store.one("SELECT user_id FROM app_accounts WHERE app=?", (app,))
    except sqlite3.OperationalError:            # a database from before the bridge existed
        return None
    return row["user_id"] if row else None


def is_app_account(store: Store, user_id: str | None) -> bool:
    """Whether `user_id` is an application identity. Tolerant of the table being absent: a
    database that predates the bridge simply has no application accounts."""
    if not user_id:
        return False
    try:
        return store.one("SELECT 1 AS x FROM app_accounts WHERE user_id=?", (user_id,)) is not None
    except sqlite3.OperationalError:
        return False


def ensure_app_account(store: Store, ident: Identity, app: str) -> str:
    """The `users` row that sends an application's cards, created once per database.

    Active (so its name resolves like anyone's), no password, no personal workspace, no
    organization or workspace membership — and flagged in `app_accounts`, which `Sessions.create`
    consults: no session can ever be minted for it, so no login route, passkey, mailed code or
    external identity can turn it into a person. Idempotent; safe on every start.
    """
    if app not in APPS:
        raise ServiceError(400, "unknown_app", f"未知的应用 {app!r}")
    with store.tx() as t:
        row = t.execute("SELECT user_id FROM app_accounts WHERE app=?", (app,)).fetchone()
        if row:
            return row["user_id"]
        email = f"{app}@{APP_ACCOUNT_DOMAIN}"
        existing = t.execute("SELECT id FROM users WHERE email_key=?", (email,)).fetchone()
        if existing:                            # a half-finished earlier start: adopt the row
            uid = existing["id"]
            t.execute("UPDATE users SET status='active', display_name=?, updated_at=? WHERE id=?", (APPS[app], now(), uid))
        else:
            uid = ident.create_user(email, None, display_name=APPS[app], status="active", with_personal_workspace=False)["id"]
        t.execute("INSERT INTO app_accounts (user_id, app, created_at) VALUES (?,?,?)", (uid, app, now()))
        return uid


# ---------------------------------------------------------------------- the outbox (Docs writes, the worker reads)
_wakers: list[Callable[[], None]] = []


def emit(t: sqlite3.Connection | sqlite3.Cursor, *, app: str, event_id: str, kind: str, object_kind: str, object_id: str,
         workspace_id: str | None = None, organization_id: str | None = None, actor_id: str | None = None,
         payload: dict | None = None) -> bool:
    """Queue one event on the caller's open transaction. `event_id` is the idempotency key: a
    second emit of the same event (a retried request, a re-run) is a no-op, so there is never a
    second card. Returns whether a row was written.

    `object_kind`/`object_id` name the object whose conversation receives the card (a comment's or
    a job's *project*); what the card is about is in `payload`. Never raises for a missing table —
    the business change this rides on must stand even if the bridge's table is not there yet."""
    if app not in APPS:
        raise ValueError(f"unknown app {app!r}")
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind {kind!r}")
    if object_kind not in CONVERSATION_KINDS:
        raise ValueError(f"unknown object kind {object_kind!r}")
    try:
        cur = t.execute("INSERT OR IGNORE INTO messaging_outbox (id,app,event_id,kind,object_kind,object_id,workspace_id,organization_id,actor_id,payload,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (new_id("ob"), app, event_id, kind, object_kind, object_id, workspace_id, organization_id, actor_id,
                         json.dumps(payload or {}, ensure_ascii=False), now()))
    except sqlite3.OperationalError as e:
        log.warning("messaging outbox unavailable (%s); event %s not queued", e, event_id)
        return False
    written = cur.rowcount > 0
    if written:
        for w in list(_wakers):
            try:
                w()
            except Exception:  # noqa: BLE001
                pass
    return written


def audience_fingerprint(conv: dict) -> str:
    """Who a message would reach, as `Messaging.get` shows it to a member: the destination is
    confirmed against this before queueing and checked again at delivery."""
    value = {"id": conv["id"], "workspace_id": conv["workspace_id"], "organization_id": conv["organization_id"],
             "archived": conv["archived"], "members": sorted(m["user_id"] for m in conv["members"])}
    # The canonical form of `capabilities.fingerprint`, kept here so the account service, which runs
    # this worker, does not load the capability registry.
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def emit_user_message(t, *, event_id: str, conversation_id: str, workspace_id: str, organization_id: str | None,
                      sender_id: str, body: str, audience: str) -> bool:
    """Queue a person's own message on the caller's transaction. `event_id` (the caller's operation
    run) becomes the message's client id, so a redelivery can never become a second message."""
    cur = t.execute("INSERT OR IGNORE INTO messaging_outbox (id,app,event_id,kind,object_kind,object_id,workspace_id,"
                    "organization_id,actor_id,payload,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id("ob"), "messages", event_id, USER_MESSAGE, "conversation", conversation_id, workspace_id,
                     organization_id, sender_id, json.dumps({"conversation_id": conversation_id, "body": body,
                                                              "audience": audience}, ensure_ascii=False), now()))
    if cur.rowcount > 0:
        for w in list(_wakers):
            try:
                w()
            except Exception:  # noqa: BLE001
                pass
    return cur.rowcount > 0


def user_message_state(store: Store, event_id: str) -> dict:
    """Where a queued message stands: queued, recorded (with its message), failed (with a code), or
    unknown when no queue row exists. Read-only; absence never means it is safe to send again."""
    row = store.one("SELECT * FROM messaging_outbox WHERE event_id=? AND kind=?", (event_id, USER_MESSAGE))
    if not row:
        return {"state": "unknown"}
    found = store.one("SELECT id, seq FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?",
                      (row["object_id"], row["actor_id"], event_id))
    if found:
        return {"state": "recorded", "message_id": found["id"], "seq": found["seq"]}
    if row["delivered_at"] is not None:
        code = row["last_error"] if row["last_error"] in USER_MESSAGE_REFUSALS else "delivery_failed"
        return {"state": "failed", "code": code}
    if row["attempts"] >= MAX_ATTEMPTS:
        return {"state": "failed", "code": "delivery_failed"}
    return {"state": "queued"}


def emit_comment(t, *, comment_id: str, project_id: str, workspace_id: str, author_id: str,
                 mentions: list[str] | None = None, parent_id: str | None = None) -> bool:
    """A comment was stored (a reply is a comment too). One card per comment; the body is read
    from `project_comments` at delivery, never copied here."""
    return emit(t, app=DOCS_APP_ID, event_id=f"comment:{comment_id}", kind="docs.comment", object_kind="project", object_id=project_id,
                workspace_id=workspace_id, actor_id=author_id,
                payload={"comment_id": comment_id, "project_id": project_id, "mentions": list(mentions or []), "parent_id": parent_id})


def emit_job_finished(t, *, job_id: str, project_id: str, workspace_id: str | None, run_no: int, status: str,
                      actor_id: str | None = None) -> bool:
    """A run of a generation job reached a terminal status. Keyed by run, like the inbox item."""
    return emit(t, app=DOCS_APP_ID, event_id=f"job:{job_id}:{run_no}", kind="docs.job_finished", object_kind="project", object_id=project_id,
                workspace_id=workspace_id, actor_id=actor_id, payload={"job_id": job_id, "project_id": project_id, "run_no": run_no, "status": status})


def emit_copy(t, *, copy_id: str, status: str, organization_id: str | None, workspace_id: str | None,
              actor_id: str | None = None) -> bool:
    """A copy request was decided (`approved`/`rejected`) or its execution ended (`done`/`failed`/
    `stale`). The card goes to the request's own conversation, if one was opened."""
    kind = "copy.decided" if status in ("approved", "rejected") else "copy.finished"
    return emit(t, app=DOCS_APP_ID, event_id=f"copy:{copy_id}:{status}", kind=kind, object_kind="copy_request", object_id=copy_id,
                workspace_id=workspace_id, organization_id=organization_id, actor_id=actor_id, payload={"copy_id": copy_id, "status": status})


# ---------------------------------------------------------------------- objects and who may open them
class Objects:
    """Read-side access to the objects cards are about: only ids, names and states from the tables
    the shared package creates (`projects`, `jobs`, `project_comments`, `project_copies`), and the
    question "may this reader open it", answered the way Docs answers it (workspace membership,
    organization access) — never by calling Docs code."""

    def __init__(self, store: Store, ident: Identity):
        self.store, self.ident = store, ident

    def anchor(self, app: str, kind: str, object_id: str) -> dict | None:
        """The object a conversation hangs on: `{"kind": "project"|"copy_request", "id", "workspace_id",
        "organization_id", "title"}`. A job resolves to its project. None when there is no such
        (live) object — the caller answers 404, the same nothing an outsider gets."""
        if app not in APPS or kind not in CONVERSATION_KINDS or not isinstance(object_id, str) or not 1 <= len(object_id) <= 64:
            return None
        if kind == "job":
            j = self.store.one("SELECT project_id FROM jobs WHERE id=?", (object_id,))
            if not j:
                return None
            kind, object_id = "project", j["project_id"]
        if kind == "project":
            p = self.store.one("SELECT id, name, tenant, deleted, archived_at FROM projects WHERE id=?", (object_id,))
            if not p or p.get("deleted") or not p.get("tenant"):
                return None
            org = organization_for_workspace(self.store, p["tenant"])
            return {"kind": "project", "id": p["id"], "workspace_id": p["tenant"], "organization_id": org["id"] if org else None,
                    "title": p["name"] or "—", "archived": bool(p.get("archived_at"))}
        r = self.store.one("SELECT id, name, status, requester_id, target_organization_id, target_workspace_id FROM project_copies WHERE id=?", (object_id,))
        if not r:
            return None
        return {"kind": "copy_request", "id": r["id"], "workspace_id": r["target_workspace_id"], "organization_id": r["target_organization_id"],
                "title": r["name"] or "—", "status": r["status"], "requester_id": r["requester_id"]}

    def may_open(self, anchor: dict, uid: str | None) -> bool:
        """The reader's own access to the object, re-read now. Same rule as `docs_links` for a
        project (active member of an active workspace whose organization they are still in); for
        a copy request, the requester or someone who may decide it."""
        if not uid or not anchor:
            return False
        u = self.ident.get_user(uid)
        if not u or u.get("status") != "active":
            return False
        if anchor["kind"] == "project":
            ws = self.ident.get_workspace(anchor["workspace_id"])
            if not ws or ws.get("status", "active") != "active" or ws.get("deleted_at"):
                return False
            if not self.ident.membership(anchor["workspace_id"], uid):
                return False
            return workspace_access_failure(self.store, anchor["workspace_id"], uid) is None
        if anchor["kind"] == "copy_request":
            if uid == anchor.get("requester_id"):
                return True
            from .project_copies import ProjectCopies
            return ProjectCopies(self.store, self.ident).can_decide(anchor["organization_id"], anchor["workspace_id"], uid)
        return False

    def initial_members(self, anchor: dict) -> list[str]:
        """Who is in the conversation when it is created: everyone who may open the object now."""
        if anchor["kind"] == "project":
            rows = self.store.all("SELECT m.user_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? "
                                  "AND u.status='active' AND u.deleted_at IS NULL ORDER BY m.created_at, m.user_id", (anchor["workspace_id"],))
            return [r["user_id"] for r in rows if self.may_open(anchor, r["user_id"])]
        from .project_copies import ProjectCopies
        copies = ProjectCopies(self.store, self.ident)
        ids = [anchor["requester_id"]] + copies.deciders(anchor["organization_id"], anchor["workspace_id"])
        return [x for x in dict.fromkeys(ids) if self.may_open(anchor, x)]


# ---------------------------------------------------------------------- object conversations
class ObjectConversations:
    def __init__(self, store: Store, ident: Identity, messaging):
        self.store, self.ident, self.messaging = store, ident, messaging
        self.objects = Objects(store, ident)

    def _mapping(self, app: str, kind: str, object_id: str) -> dict | None:
        return self.store.one("SELECT * FROM object_conversations WHERE app=? AND kind=? AND object_id=?", (app, kind, object_id))

    def _anchor_for(self, app: str, kind: str, object_id: str, uid: str) -> dict:
        anchor = self.objects.anchor(app, kind, object_id)
        if not anchor or not self.objects.may_open(anchor, uid):
            # 404, never 403: a person who cannot open the object must not learn that it exists
            raise ServiceError(404, "no_object", "找不到该对象，或你没有权限查看它")
        return anchor

    def _object_out(self, app: str, anchor: dict) -> dict:
        return {"app": app, "app_name": APPS[app], "kind": anchor["kind"], "id": anchor["id"], "title": anchor["title"],
                "href": object_href(self.store, anchor), "workspace_id": anchor["workspace_id"]}

    def get(self, app: str, kind: str, object_id: str, uid: str) -> dict:
        """The linked conversation as this caller sees it, or 404 (no object, no access, no
        conversation yet, or the caller is not — or no longer — a member)."""
        anchor = self._anchor_for(app, kind, object_id, uid)
        m = self._mapping(app, anchor["kind"], anchor["id"])
        if not m:
            raise ServiceError(404, "no_conversation", "这个对象还没有讨论")
        return {"object": self._object_out(app, anchor), "conversation": self.messaging.get(m["conversation_id"], uid), "created": False, "joined": False}

    def open(self, app: str, kind: str, object_id: str, uid: str) -> dict:
        """Return the object's conversation, creating it on first call: a named group in the
        object's organization with the caller as owner and everyone who may open the object as
        members. A later caller who may open the object but is not a member joins. One transaction
        around the check-and-create, so two first callers get one group."""
        anchor = self._anchor_for(app, kind, object_id, uid)
        oid = anchor.get("organization_id")
        if not oid:
            raise ServiceError(409, "no_organization", "个人工作空间的项目没有组织会话；复制到组织工作空间后才能讨论")
        org = self.store.one("SELECT status FROM organizations WHERE id=?", (oid,))
        if not org or org["status"] != "active":
            raise ServiceError(409, "organization_unavailable", "该组织当前不可用")
        created = joined = False
        with self.store.tx() as t:
            m = t.execute("SELECT conversation_id FROM object_conversations WHERE app=? AND kind=? AND object_id=?", (app, anchor["kind"], anchor["id"])).fetchone()
            if m:
                cid = m["conversation_id"]
                joined = self._join(t, cid, uid, app)
            else:
                others = [x for x in self.objects.initial_members(anchor) if x != uid]
                conv = self.messaging.create_group(oid, uid, name=anchor["title"][:MAX_TITLE] or "—", member_ids=others,
                                                   workspace_id=anchor["workspace_id"] if anchor["kind"] == "project" else None)
                cid = conv["id"]
                t.execute("UPDATE conversations SET purpose='object' WHERE id=?", (cid,))
                t.execute("INSERT INTO object_conversations (app,kind,object_id,conversation_id,workspace_id,organization_id,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                          (app, anchor["kind"], anchor["id"], cid, anchor["workspace_id"], oid, uid, now()))
                created = True
        if joined:
            self.messaging.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "added"})
            self.messaging.publish(self.messaging._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return {"object": self._object_out(app, anchor), "conversation": self.messaging.get(cid, uid), "created": created, "joined": joined}

    def _join(self, t, cid: str, uid: str, app: str) -> bool:
        """Add a caller who may open the object to an existing object conversation (history from
        now, like any added member). Actor of the system row is the application: it is the
        object's access that admits them, not a person's invitation."""
        m = t.execute("SELECT left_at FROM conversation_members WHERE conversation_id=? AND user_id=?", (cid, uid)).fetchone()
        if m and m["left_at"] is None:
            return False
        conv = t.execute("SELECT last_seq, archived_at FROM conversations WHERE id=?", (cid,)).fetchone()
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "这个讨论已归档")
        last, ts = conv["last_seq"], now()
        actor = app_account_id(self.store, app) or uid
        if m:
            t.execute("UPDATE conversation_members SET left_at=NULL, left_seq=NULL, role='member', joined_at=?, joined_seq=?, last_read_seq=?, added_by=? WHERE conversation_id=? AND user_id=?",
                      (ts, last + 1, last, actor, cid, uid))
        else:
            t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,last_read_seq,notify,added_by) VALUES (?,?,'member',?,?,?,'mentions',?)",
                      (cid, uid, ts, last + 1, last, actor))
        self.messaging._system(t, cid, actor, {"event": "added", "added": [uid]})
        return True

    def object_of(self, cid: str, uid: str) -> dict | None:
        """The object a conversation is about, as this reader may see it: the full summary when they
        may open it, a neutral one otherwise; None for an ordinary conversation. The caller must be
        a member (the route checks through `Messaging.get`)."""
        m = self.store.one("SELECT * FROM object_conversations WHERE conversation_id=?", (cid,))
        if not m:
            return None
        anchor = self.objects.anchor(m["app"], m["kind"], m["object_id"])
        if anchor and self.objects.may_open(anchor, uid):
            return self._object_out(m["app"], anchor)
        return {"app": m["app"], "app_name": APPS.get(m["app"], m["app"]), "kind": m["kind"], "title": NEUTRAL_TITLE[_locale(self.store, uid)], "degraded": True}

    # ------------------------------------------------------------------ keeping membership honest
    def reconcile(self, cid: str) -> list[str]:
        """Remove every member who can no longer open the object (the workspace membership they had
        is gone, the workspace or organization is not active). The owner losing access hands the
        group to the earliest remaining member; nobody left archives it. Returns who was removed."""
        m = self.store.one("SELECT * FROM object_conversations WHERE conversation_id=?", (cid,))
        conv = self.store.one("SELECT * FROM conversations WHERE id=?", (cid,)) if m else None
        if not m or not conv or conv["archived_at"]:
            return []
        anchor = self.objects.anchor(m["app"], m["kind"], m["object_id"])
        actor = app_account_id(self.store, m["app"])
        members = self.store.all("SELECT user_id, role FROM conversation_members WHERE conversation_id=? AND left_at IS NULL ORDER BY joined_at, user_id", (cid,))
        gone = [x for x in members if not is_app_account(self.store, x["user_id"]) and not (anchor and self.objects.may_open(anchor, x["user_id"]))]
        if not gone:
            return []
        removed = [x["user_id"] for x in gone]
        with self.store.tx() as t:
            for x in gone:
                self.messaging._close(t, cid, x["user_id"])
            self.messaging._system(t, cid, actor or removed[0], {"event": "removed", "removed": removed, "reason": "object_access_lost"})
            left = [x for x in members if x["user_id"] not in removed]
            if not left:
                t.execute("UPDATE conversations SET archived_at=COALESCE(archived_at,?), updated_at=? WHERE id=?", (now(), now(), cid))
                self.messaging._system(t, cid, actor or removed[0], {"event": "archived"})
            elif any(x["role"] == "owner" for x in gone):
                heir = next((x for x in left if x["role"] == "admin"), left[0])
                t.execute("UPDATE conversation_members SET role='owner' WHERE conversation_id=? AND user_id=?", (cid, heir["user_id"]))
                self.messaging._system(t, cid, actor or heir["user_id"], {"event": "transferred", "to": heir["user_id"]})
        for uid in removed:
            self.messaging.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "removed"})
        self.messaging.publish(self.messaging._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return removed

    def reconcile_all(self) -> int:
        n = 0
        for r in self.store.all("SELECT o.conversation_id FROM object_conversations o JOIN conversations c ON c.id=o.conversation_id WHERE c.archived_at IS NULL"):
            try:
                n += len(self.reconcile(r["conversation_id"]))
            except Exception:  # noqa: BLE001 - one conversation's failure must not stop the sweep
                log.exception("reconciling object conversation %s failed", r["conversation_id"])
        return n


# ---------------------------------------------------------------------- links
def object_href(store: Store, anchor: dict) -> str | None:
    """The deep link to an object, from the registry (Docs) or the configured account origin
    (approvals). None when the origin is not known — never a guessed host."""
    if anchor["kind"] == "project":
        return _href(store, "docs", f"/p/{anchor['id']}")
    if anchor["kind"] == "copy_request":
        return _href(store, "accounts", f"/organizations/{anchor['organization_id']}?approval={anchor['id']}")
    return None


def _href(store: Store, origin: str, path: str) -> str | None:
    base = None
    if origin == "docs":
        from .docs_links import docs_origin
        base = docs_origin(store)
    elif origin == "accounts":
        base = (env.get("ISSUER") or "").rstrip("/") or None
    return base + path if base else None


# ---------------------------------------------------------------------- cards
def render_card(store: Store, ident: Identity, reader: str | None, card: dict) -> dict | None:
    """The card as one reader may see it. `reader=None` (an event fanned out to nobody in
    particular) renders nothing: the shell keeps the card it has and re-reads history for a
    fresh one. With access: title, subtitle, state, object and links in the reader's language.
    Without: the application's name, the state and a neutral title — no object id, no links."""
    if not reader or not isinstance(card, dict):
        return None
    app = card.get("app")
    out = {"app": app, "app_name": APPS.get(app, card.get("app_name") or app)}
    if card.get("state"):
        out["state"] = card["state"]
    objects = Objects(store, ident)
    anchor = objects.anchor(app, (card.get("_access") or {}).get("kind", ""), (card.get("_access") or {}).get("id", ""))
    loc = _locale(store, reader)
    if not anchor or not objects.may_open(anchor, reader):
        out.update(title=NEUTRAL_TITLE[loc], object={"kind": (card.get("object") or {}).get("kind")}, degraded=True)
        return out
    out["title"] = card.get("title") or anchor["title"]
    if card.get("subtitle"):
        out["subtitle"] = card["subtitle"]
    elif isinstance(card.get("subtitle_t"), dict):
        tpl = SUBTITLES.get(card["subtitle_t"].get("key") or "", {}).get(loc)
        if tpl:
            try:
                out["subtitle"] = tpl.format(**(card["subtitle_t"].get("params") or {}))
            except (KeyError, IndexError):
                pass
    out["object"] = dict(card.get("object") or {})
    actions = []
    for a in card.get("actions") or []:
        href = _href(store, a.get("origin", "docs"), a.get("path") or "/")
        label = ACTION_LABELS.get(a.get("id") or "", {}).get(loc)
        if href and label:
            actions.append({"label": label, "href": href})
    if actions:
        out["actions"] = actions
    return out


def decorate_message(store: Store, ident: Identity, out: dict, reader: str | None) -> None:
    """Called by `Messaging._message_out` for every message: marks the sender's kind and attaches
    the reader's rendering of the card, when the message has one and is not deleted. Tolerant of
    a database without the bridge's tables."""
    out["sender_kind"] = "app" if is_app_account(store, out.get("sender_id")) else "user"
    if out.get("deleted") or not reader:
        return
    try:
        row = store.one("SELECT card FROM message_cards WHERE message_id=?", (out["id"],))
    except sqlite3.OperationalError:
        return
    if row:
        card = render_card(store, ident, reader, _load(row["card"]) or {})
        if card:
            out["card"] = card


# ---------------------------------------------------------------------- the worker (messages process)
class BridgeWorker:
    """Turns outbox rows into card messages, in the process that serves conversations (the
    `messages` role, or `all`), so the fan-out reaches the open streams. Polls the outbox (one
    indexed read every quarter second; woken at once on `all`, where Docs shares the process) and
    reconciles object-conversation membership after each delivery and once a minute."""

    POLL_S = 0.25
    SWEEP_S = 60.0

    def __init__(self, store: Store, ident: Identity, messaging, *, publish: Callable[[list[str], dict], None] | None = None,
                 start: bool = True):
        self.store, self.ident, self.messaging = store, ident, messaging
        self.publish = publish or messaging.publish
        self.objects = ObjectConversations(store, ident, messaging)
        self._wake, self._stopped = threading.Event(), threading.Event()
        self._thread: threading.Thread | None = None
        self._last_sweep = 0.0
        if start:
            # Passive workers are driven by run_once(), so they have nothing to wake.
            # Registering their bound method globally would retain every fixture Store
            # even after its application and the thread-cleanup registry let it go.
            _wakers.append(self.wake)
            from . import workers
            self._thread = threading.Thread(target=self._run, name="messaging-bridge", daemon=True)
            self._thread.start()
            workers.register(self, self._thread)

    def wake(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()
        if self.wake in _wakers:
            _wakers.remove(self.wake)
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(3.0)

    def _run(self) -> None:
        while not self._stopped.is_set():
            self._wake.wait(self.POLL_S)
            self._wake.clear()
            if self._stopped.is_set():
                break
            try:
                self.run_once()
                if time.monotonic() - self._last_sweep > self.SWEEP_S:
                    self.sweep()
            except Exception:  # noqa: BLE001 - the loop must survive one bad row
                log.exception("messaging bridge tick failed")

    def sweep(self) -> int:
        self._last_sweep = time.monotonic()
        return self.objects.reconcile_all()

    def pending(self) -> list[dict]:
        return self.store.all("SELECT * FROM messaging_outbox WHERE delivered_at IS NULL AND attempts<? ORDER BY created_at LIMIT 50", (MAX_ATTEMPTS,))

    def run_once(self) -> dict:
        out = {"delivered": 0, "skipped": 0, "failed": 0}
        for row in self.pending():
            try:
                result = self.deliver(row)
            except Exception as e:  # noqa: BLE001 - recorded on the row, retried next tick
                log.exception("delivering outbox row %s failed", row["id"])
                self.store.execute("UPDATE messaging_outbox SET attempts=attempts+1, last_error=? WHERE id=?", (f"{type(e).__name__}: {e}"[:200], row["id"]))
                out["failed"] += 1
                continue
            out["delivered" if result == "delivered" else "skipped"] += 1
        return out

    def _skip(self, row: dict, why: str) -> str:
        self.store.execute("UPDATE messaging_outbox SET delivered_at=?, attempts=attempts+1, last_error=? WHERE id=?", (now(), why, row["id"]))
        return why

    def _deliver_user(self, row: dict) -> str:
        """A person's queued message, sent as them after re-checking everything that may have
        changed since it was confirmed: their access, the object behind the discussion, the archive
        state and the exact audience. A definite refusal is recorded, never retried."""
        payload = _load(row.get("payload")) or {}
        cid, sender = payload.get("conversation_id"), row["actor_id"]
        user = self.ident.get_user(sender) if sender else None
        if not user or user.get("status") != "active":
            return self._skip(row, "account_unavailable")
        try:
            conv = self.messaging.get(cid, sender)
        except ServiceError as e:
            return self._skip(row, e.code if e.code in USER_MESSAGE_REFUSALS else "no_conversation")
        if conv["workspace_id"] != row["workspace_id"]:
            return self._skip(row, "no_conversation")
        if self.store.one("SELECT 1 AS x FROM object_conversations WHERE conversation_id=?", (cid,)):
            obj = self.objects.object_of(cid, sender)
            if not obj or obj.get("degraded"):
                return self._skip(row, "no_conversation")
        if conv["archived"]:
            return self._skip(row, "archived")
        if audience_fingerprint(conv) != payload.get("audience"):
            return self._skip(row, "audience_changed")
        try:
            # The same write path as Message's own route: fan-out, unread state and digests follow.
            self.messaging.send(cid, sender, body=payload.get("body") or "", client_id=row["event_id"])
        except ServiceError as e:
            return self._skip(row, e.code if e.code in USER_MESSAGE_REFUSALS else "delivery_failed")
        self.store.execute("UPDATE messaging_outbox SET delivered_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                           (now(), row["id"]))
        return "delivered"

    def deliver(self, row: dict) -> str:
        """One row → one card message in the object's conversation, or a recorded skip."""
        if row["kind"] == USER_MESSAGE:
            return self._deliver_user(row)
        mapping = self.store.one("SELECT conversation_id FROM object_conversations WHERE app=? AND kind=? AND object_id=?", (row["app"], row["object_kind"], row["object_id"]))
        if not mapping:
            return self._skip(row, SKIPPED_NO_CONVERSATION)
        cid = mapping["conversation_id"]
        conv = self.store.one("SELECT * FROM conversations WHERE id=?", (cid,))
        if not conv or conv["archived_at"]:
            return self._skip(row, SKIPPED_ARCHIVED)
        card = build_card(self.store, self.ident, row)
        if not card:
            return self._skip(row, SKIPPED_OBJECT_GONE)
        sender = ensure_app_account(self.store, self.ident, row["app"])
        body = BODY[row["kind"]]
        if isinstance(body, dict):
            body = body.get(card.get("state") or "", f"{APPS[row['app']]}")
        ts = now()
        with self.store.tx() as t:
            dup = t.execute("SELECT id FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?", (cid, sender, row["id"])).fetchone()
            if dup:
                mid = dup["id"]                                 # a crash between the insert and the mark: same card, not a second one
            else:
                seq = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()["last_seq"] + 1
                mid = new_id("msg")
                t.execute("INSERT INTO messages (id,conversation_id,seq,sender_id,client_id,kind,body,created_at) VALUES (?,?,?,?,?,'text',?,?)",
                          (mid, cid, seq, sender, row["id"], body, ts))
                t.execute("UPDATE conversations SET last_seq=?, last_message_at=?, updated_at=? WHERE id=?", (seq, ts, ts, cid))
                t.execute("INSERT OR REPLACE INTO message_cards (message_id, card) VALUES (?,?)", (mid, json.dumps(card, ensure_ascii=False)))
            t.execute("UPDATE messaging_outbox SET delivered_at=?, attempts=attempts+1, last_error=NULL WHERE id=?", (ts, row["id"]))
        stored = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        # Fanned out per reader: the card in each person's event is the one they may see.
        for uid in self.messaging._active_user_ids(cid):
            if is_app_account(self.store, uid):
                continue
            self.publish([uid], {"type": "message", "conversation_id": cid, "message": self.messaging._message_out(dict(stored), viewer=uid)})
        try:
            self.objects.reconcile(cid)
        except Exception:  # noqa: BLE001 - the card is delivered; the sweep retries
            log.exception("reconciling %s after delivery failed", cid)
        return "delivered"


def build_card(store: Store, ident: Identity, row: dict) -> dict | None:
    """The stored card for an outbox row, from the object's **current** state (title and state as
    they are at delivery, not as they were when the event happened). `_access` names the object
    whose access decides what a reader sees; it is stripped on output. None when the object is
    gone (a deleted comment, a vanished job)."""
    payload = _load(row.get("payload")) or {}
    app = row["app"]
    kind = row["kind"]
    if kind == "docs.comment":
        cm = store.one("SELECT * FROM project_comments WHERE id=?", (payload.get("comment_id"),))
        p = store.one("SELECT id, name FROM projects WHERE id=? AND deleted=0", (row["object_id"],)) if cm else None
        if not cm or not p or cm.get("deleted_at"):
            return None
        author = ident.get_user(cm["author_id"]) or {}
        who = author.get("display_name") or (author.get("email") or "").split("@")[0] or "—"
        return {"app": app, "app_name": APPS[app], "title": (p["name"] or "—")[:MAX_TITLE], "subtitle": _excerpt(f"{who}: {cm['body']}"),
                "state": "resolved" if cm.get("resolved_at") else "open", "object": {"kind": "comment", "id": cm["id"]},
                "actions": [{"id": "open_comment", "origin": "docs", "path": f"/p/{p['id']}?comment={cm['id']}"}],
                "_access": {"kind": "project", "id": p["id"]}}
    if kind == "docs.job_finished":
        j = store.one("SELECT id, project_id, status, counts FROM jobs WHERE id=?", (payload.get("job_id"),))
        p = store.one("SELECT id, name FROM projects WHERE id=? AND deleted=0", (row["object_id"],)) if j else None
        if not j or not p:
            return None
        counts = _load(j.get("counts")) if not isinstance(j.get("counts"), dict) else j["counts"]
        counts = counts if isinstance(counts, dict) else {}
        state = j["status"] if j["status"] in JOB_STATES else (payload.get("status") or "completed")
        failed = int(counts.get("failed") or 0) + int(counts.get("skipped") or 0) + int(counts.get("cancelled") or 0)
        return {"app": app, "app_name": APPS[app], "title": (p["name"] or "—")[:MAX_TITLE],
                "subtitle_t": {"key": "job_counts", "params": {"done": str(int(counts.get("success") or 0)), "failed": str(failed)}},
                "state": state, "object": {"kind": "job", "id": j["id"]},
                "actions": [{"id": "open_job", "origin": "docs", "path": f"/p/{p['id']}/jobs/{j['id']}"}],
                "_access": {"kind": "project", "id": p["id"]}}
    if kind in ("copy.decided", "copy.finished"):
        r = store.one("SELECT * FROM project_copies WHERE id=?", (row["object_id"],))
        if not r:
            return None
        decider = ident.get_user(r["decided_by"]) if r.get("decided_by") else None
        who = (decider or {}).get("display_name") or ((decider or {}).get("email") or "").split("@")[0]
        subtitle = _excerpt(f"{who}: {r['decision_reason']}" if who and r.get("decision_reason") else (r.get("error") or ""))
        card = {"app": app, "app_name": APPS[app], "title": (r["name"] or "—")[:MAX_TITLE], "state": r["status"],
                "object": {"kind": "copy_request", "id": r["id"]},
                "actions": [{"id": "open_approval", "origin": "accounts", "path": f"/organizations/{r['target_organization_id']}?approval={r['id']}"}],
                "_access": {"kind": "copy_request", "id": r["id"]}}
        if subtitle:
            card["subtitle"] = subtitle
        result = _load(r.get("result")) if not isinstance(r.get("result"), dict) else r["result"]
        if r["status"] == "done" and isinstance(result, dict) and result.get("project_id"):
            card["actions"].insert(0, {"id": "open_project", "origin": "docs", "path": f"/p/{result['project_id']}"})
        return card
    return None
