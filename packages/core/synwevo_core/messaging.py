"""Organization-scoped messaging: direct messages and named groups, with history, read positions,
reactions, attachments and the events a realtime transport fans out.

The rules the schema encodes (decision record in docs/platform/messaging.md):

* **Organization-scoped, explicit membership.** A conversation belongs to one organization and
  only active members of that organization can be in it. A direct message is the one
  conversation between two people in that organization (`dm_key`); a group has an owner, admins
  and members and is joined by being added by an owner/admin — nobody is searchable across the
  platform, only inside an organization the caller already belongs to.
* **History starts when you join.** Every membership carries `joined_seq`; a reader sees only
  messages at or after it. Being added to an existing group shows what comes next, not what was
  said before. Leaving (or being removed, or losing organization membership) closes the
  membership (`left_at`); the conversation disappears from that person's list and every later
  read, search, stream and attachment download is refused. Being added again opens a new
  membership with a new `joined_seq` — nothing is silently restored.
* **Sequence numbers, not timestamps.** Messages are ordered by a per-conversation `seq`
  assigned inside the write transaction; read positions, unread counts, pagination cursors and
  "what did I miss" all use it. A `client_id` makes sends idempotent: a retry after a lost
  acknowledgement returns the message that was stored, not a duplicate.
* **Delete hides, audit remembers.** Deleting a message blanks its body and marks it; the row
  stays so sequences and replies keep their meaning. Nobody can claim a downloaded copy is gone.
* **Attachments go through the same acceptance chain** as every upload (scan first, refuse on
  infection or when the scanner is down) and are served only to a member who may read the
  message they hang on.
* **No presence, no read receipts of others.** A reader's own read position syncs across their
  devices; nothing about other people's activity is invented.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from .errors import ServiceError
from .identity import Identity
from .organizations import current_membership
from .store import Store, new_id, now

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('dm','group')),
  workspace_id TEXT, name TEXT, description TEXT, dm_key TEXT UNIQUE, created_by TEXT NOT NULL,
  last_seq INTEGER NOT NULL DEFAULT 0, last_message_at REAL, archived_at REAL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_conversations_org ON conversations(organization_id, updated_at);
CREATE TABLE IF NOT EXISTS message_reports (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, conversation_id TEXT NOT NULL, message_id TEXT NOT NULL, reporter_id TEXT NOT NULL,
  sender_id TEXT NOT NULL, reason TEXT NOT NULL, note TEXT, body_snapshot TEXT NOT NULL, attachment_name TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','removed','dismissed')),
  reviewed_by TEXT, reviewed_at REAL, decision_note TEXT, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_message_reports_org ON message_reports(organization_id, status, created_at);
CREATE TABLE IF NOT EXISTS conversation_members (
  conversation_id TEXT NOT NULL, user_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('owner','admin','member')),
  joined_at REAL NOT NULL, joined_seq INTEGER NOT NULL DEFAULT 0, left_at REAL, left_seq INTEGER,
  last_read_seq INTEGER NOT NULL DEFAULT 0, muted INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
  notify TEXT NOT NULL DEFAULT 'all' CHECK (notify IN ('all','mentions','none')), added_by TEXT,
  PRIMARY KEY (conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_conversation_members_user ON conversation_members(user_id, left_at);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, seq INTEGER NOT NULL, sender_id TEXT NOT NULL,
  client_id TEXT, kind TEXT NOT NULL DEFAULT 'text' CHECK (kind IN ('text','system')), body TEXT NOT NULL,
  reply_to TEXT, attachment_id TEXT, mentions TEXT, edited_at REAL, deleted_at REAL, created_at REAL NOT NULL,
  UNIQUE (conversation_id, seq), UNIQUE (conversation_id, sender_id, client_id)
);
CREATE TABLE IF NOT EXISTS message_reactions (
  message_id TEXT NOT NULL, user_id TEXT NOT NULL, emoji TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (message_id, user_id, emoji)
);
CREATE TABLE IF NOT EXISTS message_attachments (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, message_id TEXT, uploader_id TEXT NOT NULL,
  filename TEXT NOT NULL, size INTEGER NOT NULL, blob TEXT NOT NULL, content_type TEXT NOT NULL,
  scan TEXT, status TEXT NOT NULL CHECK (status IN ('accepted','rejected')), created_at REAL NOT NULL, expires_at REAL
);
CREATE INDEX IF NOT EXISTS ix_message_attachments_conv ON message_attachments(conversation_id, created_at);
"""

MAX_ANNOUNCEMENT = 1000
MAX_REPORT_NOTE = 500
REPORT_REASONS = ("harassment", "spam", "privacy", "other")
MAX_BODY = 4000
MAX_GROUP_MEMBERS = 200
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
PAGE = 50
PAGE_MAX = 200
REACTIONS = ("👍", "❤️", "😂", "🎉", "👀", "✅", "❓", "🙏")
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ATTACHMENT_UNUSED_S = 3600
Publish = Callable[[list[str], dict], None]


def sniff_content_type(data: bytes, filename: str) -> str:
    """Server-side type from the bytes; the client's claim is not consulted. Only image types
    are ever rendered inline; everything else downloads as an attachment."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        return {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}.get(ext, "application/zip")
    try:
        data[:4096].decode("utf-8")
        return "text/plain"
    except UnicodeDecodeError:
        return "application/octet-stream"


def _load(v):
    if isinstance(v, (dict, list)) or v is None:
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


class Messaging:
    def __init__(self, store: Store, ident: Identity, *, publish: Publish | None = None):
        self.store, self.ident = store, ident
        self.publish = publish or (lambda users, event: None)

    # ------------------------------------------------------------------ guards
    def _org_member(self, oid: str, uid: str) -> dict:
        m = current_membership(self.store, oid, uid)
        if not m:
            raise ServiceError(404, "no_conversation", "找不到该会话")
        return m

    def _member(self, cid: str, uid: str) -> tuple[dict, dict]:
        """The conversation and the caller's *active* membership, or 404 (never 403: a person
        who is not in a conversation must not learn that it exists)."""
        conv = self.store.one("SELECT * FROM conversations WHERE id=?", (cid,))
        m = self.store.one("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (cid, uid)) if conv else None
        if not conv or not m:
            raise ServiceError(404, "no_conversation", "找不到该会话")
        # organization membership is checked on every read and write, not only at join time
        self._org_member(conv["organization_id"], uid)
        return conv, m

    def _manager(self, cid: str, uid: str, *, owner: bool = False) -> tuple[dict, dict]:
        conv, m = self._member(cid, uid)
        if conv["kind"] != "group":
            raise ServiceError(409, "not_a_group", "私聊没有群管理")
        if (owner and m["role"] != "owner") or (not owner and m["role"] not in ("owner", "admin")):
            raise ServiceError(403, "forbidden", "只有群主或群管理员可以这样做")
        return conv, m

    def _active_user_ids(self, cid: str) -> list[str]:
        return [r["user_id"] for r in self.store.all("SELECT user_id FROM conversation_members WHERE conversation_id=? AND left_at IS NULL", (cid,))]

    def _name(self, uid: str) -> str:
        u = self.ident.get_user(uid) or {}
        return u.get("display_name") or (u.get("email") or "").split("@")[0] or "—"

    # ------------------------------------------------------------------ conversations
    def directory(self, oid: str, uid: str, q: str = "", limit: int = 20) -> list[dict]:
        """People a member may start a conversation with: active members of the same
        organization, by name. Names only — no addresses, no other organizations."""
        self._org_member(oid, uid)
        like = f"%{q.strip()}%" if q and q.strip() else "%"
        rows = self.store.all("SELECT u.id, u.display_name, u.email FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.organization_id=? AND u.status='active' AND u.deleted_at IS NULL AND u.id<>? "
                              "AND (u.display_name LIKE ? OR u.email LIKE ?) ORDER BY u.display_name, u.email LIMIT ?",
                              (oid, uid, like, like, max(1, min(limit, 50))))
        return [{"user_id": r["id"], "name": r["display_name"] or r["email"].split("@")[0]} for r in rows]

    def open_dm(self, oid: str, uid: str, other: str) -> dict:
        if other == uid:
            raise ServiceError(400, "invalid_field", "不能和自己私聊", {"field": "user_id"})
        self._org_member(oid, uid)
        self._org_member(oid, other)
        key = f"{oid}:{min(uid, other)}:{max(uid, other)}"
        ts = now()
        with self.store.tx() as t:
            conv = t.execute("SELECT * FROM conversations WHERE dm_key=?", (key,)).fetchone()
            if conv:
                conv = dict(conv)
                for who in (uid, other):
                    m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=?", (conv["id"], who)).fetchone()
                    if m is None:
                        t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify) VALUES (?,?,'member',?,?,'all')",
                                  (conv["id"], who, ts, conv["last_seq"]))
                    elif m["left_at"] is not None:
                        # a person who left the organization and came back sees the DM from now on
                        t.execute("UPDATE conversation_members SET left_at=NULL, left_seq=NULL, joined_at=?, joined_seq=?, last_read_seq=? WHERE conversation_id=? AND user_id=?",
                                  (ts, conv["last_seq"] + 1, conv["last_seq"], conv["id"], who))
                return self.get(conv["id"], uid)
            cid = new_id("cv")
            t.execute("INSERT INTO conversations (id,organization_id,kind,dm_key,created_by,created_at,updated_at) VALUES (?,?,'dm',?,?,?,?)",
                      (cid, oid, key, uid, ts, ts))
            for who in (uid, other):
                t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify) VALUES (?,?,'member',?,0,'all')", (cid, who, ts))
        self.publish([other], {"type": "conversation", "conversation_id": cid, "change": "added"})
        return self.get(cid, uid)

    def create_group(self, oid: str, uid: str, *, name: str, member_ids: list[str], description: str | None = None,
                     workspace_id: str | None = None) -> dict:
        self._org_member(oid, uid)
        name = (name or "").strip()
        if not 1 <= len(name) <= MAX_NAME:
            raise ServiceError(400, "invalid_field", f"群名称需要 1–{MAX_NAME} 个字符", {"field": "name"})
        description = (description or "").strip()[:MAX_DESCRIPTION] or None
        others = []
        for mid in dict.fromkeys(member_ids or []):
            if mid != uid:
                self._org_member(oid, mid)
                others.append(mid)
        if len(others) + 1 > MAX_GROUP_MEMBERS:
            raise ServiceError(400, "too_many_members", f"群成员最多 {MAX_GROUP_MEMBERS} 人")
        if workspace_id:
            ws = self.ident.get_workspace(workspace_id)
            if not ws or ws.get("organization_id") != oid:
                raise ServiceError(400, "bad_workspace", "工作空间不属于这个组织", {"field": "workspace_id"})
        ts = now()
        cid = new_id("cv")
        with self.store.tx() as t:
            t.execute("INSERT INTO conversations (id,organization_id,kind,workspace_id,name,description,created_by,created_at,updated_at) VALUES (?,?,'group',?,?,?,?,?,?)",
                      (cid, oid, workspace_id, name, description, uid, ts, ts))
            t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify) VALUES (?,?,'owner',?,0,'all')", (cid, uid, ts))
            for mid in others:
                t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify,added_by) VALUES (?,?,'member',?,0,'mentions',?)", (cid, mid, ts, uid))
            self._system(t, cid, uid, {"event": "created", "name": name, "added": others})
        self.publish(others, {"type": "conversation", "conversation_id": cid, "change": "added"})
        return self.get(cid, uid)

    def update_group(self, cid: str, uid: str, *, name=None, description=None) -> dict:
        conv, _ = self._manager(cid, uid)
        fields, args = [], []
        if name is not None:
            name = str(name).strip()
            if not 1 <= len(name) <= MAX_NAME:
                raise ServiceError(400, "invalid_field", f"群名称需要 1–{MAX_NAME} 个字符", {"field": "name"})
            fields.append("name=?"); args.append(name)
        if description is not None:
            fields.append("description=?"); args.append(str(description).strip()[:MAX_DESCRIPTION] or None)
        if fields:
            with self.store.tx() as t:
                t.execute(f"UPDATE conversations SET {', '.join(fields)}, updated_at=? WHERE id=?", (*args, now(), cid))
                if name is not None:
                    self._system(t, cid, uid, {"event": "renamed", "name": name})
            self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def set_announcement(self, cid: str, uid: str, text) -> dict:
        """A group's announcement: one pinned text at the top of the thread, set or cleared by
        its managers. The route tells the members through the inbox (digest)."""
        conv, _ = self._manager(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "已归档的群不能修改公告")
        text = (str(text).strip()[:MAX_ANNOUNCEMENT] if text is not None else "") or None
        with self.store.tx() as t:
            t.execute("UPDATE conversations SET announcement=?, announcement_by=?, announcement_at=?, updated_at=? WHERE id=?",
                      (text, uid if text else None, now() if text else None, now(), cid))
            self._system(t, cid, uid, {"event": "announcement", "cleared": text is None})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    # ------------------------------------------------------------------ reports (abuse, harassment, spam)
    def report(self, mid: str, uid: str, reason: str, note: str | None = None) -> dict:
        """A member reports a message they can see. What the organization's messaging managers
        get is exactly this: the reported message as it was at that moment (a snapshot, so a
        later edit cannot hide it), who sent it, who reported it and why — not the rest of the
        conversation. One open report per message and reporter."""
        if reason not in REPORT_REASONS:
            raise ServiceError(400, "invalid_field", "举报原因无效", {"field": "reason", "allowed": list(REPORT_REASONS)})
        msg = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not msg:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(msg["conversation_id"], uid)
        if msg["seq"] < me["joined_seq"] or msg["kind"] != "text" or msg.get("deleted_at"):
            raise ServiceError(404, "no_message", "找不到该消息")
        if msg["sender_id"] == uid:
            raise ServiceError(400, "invalid_field", "不能举报自己的消息", {"field": "message"})
        if self.store.one("SELECT 1 AS x FROM message_reports WHERE message_id=? AND reporter_id=? AND status='open'", (mid, uid)):
            raise ServiceError(409, "already_reported", "你已经举报过这条消息，管理员处理后会通知你")
        att = (self.store.one("SELECT filename FROM message_attachments WHERE id=?", (msg["attachment_id"],)) if msg.get("attachment_id") else None) or {}
        row = {"id": new_id("rp"), "organization_id": conv["organization_id"], "conversation_id": conv["id"], "message_id": mid, "reporter_id": uid,
               "sender_id": msg["sender_id"], "reason": reason, "note": (str(note).strip()[:MAX_REPORT_NOTE] or None) if note else None,
               "body_snapshot": msg["body"] or "", "attachment_name": att.get("filename"), "status": "open", "reviewed_by": None, "reviewed_at": None,
               "decision_note": None, "created_at": now()}
        self.store.insert("message_reports", row)
        return self._report_out(row)

    def reports_for_organization(self, oid: str, *, status: str | None = "open") -> list[dict]:
        rows = self.store.all("SELECT * FROM message_reports WHERE organization_id=? " + ("AND status=? " if status else "") + "ORDER BY created_at DESC LIMIT 200",
                              (oid, status) if status else (oid,))
        return [self._report_out(r) for r in rows]

    def review_report(self, rid: str, uid: str, decision: str, note: str | None = None) -> dict:
        """`removed` deletes the reported message for everyone (as a group moderator would),
        `dismissed` leaves it. Other open reports of the same message are settled by the same
        decision. Who may review is the route's check (org.messaging.manage, fresh reauth)."""
        if decision not in ("removed", "dismissed"):
            raise ServiceError(400, "invalid_field", "处理结果只能是 removed 或 dismissed", {"field": "decision"})
        r = self.store.one("SELECT * FROM message_reports WHERE id=?", (rid,))
        if not r:
            raise ServiceError(404, "no_report", "找不到该举报")
        if r["status"] != "open":
            raise ServiceError(409, "already_reviewed", "这条举报已经处理过了")
        if decision == "removed":
            with self.store.tx() as t:
                t.execute("UPDATE messages SET deleted_at=COALESCE(deleted_at,?), body='' WHERE id=?", (now(), r["message_id"]))
                t.execute("DELETE FROM message_reactions WHERE message_id=?", (r["message_id"],))
            msg = self.store.one("SELECT * FROM messages WHERE id=?", (r["message_id"],))
            if msg:
                self.publish(self._active_user_ids(r["conversation_id"]), {"type": "message_updated", "conversation_id": r["conversation_id"], "message": self._message_out(dict(msg))})
        self.store.update("message_reports", {"id": rid}, {"status": decision, "reviewed_by": uid, "reviewed_at": now(), "decision_note": (str(note).strip()[:MAX_REPORT_NOTE] or None) if note else None})
        # other open reports of the same message are settled by the same decision
        self.store.execute("UPDATE message_reports SET status=?, reviewed_by=?, reviewed_at=?, decision_note=? WHERE message_id=? AND status='open'",
                           (decision, uid, now(), None, r["message_id"]))
        return self._report_out(self.store.one("SELECT * FROM message_reports WHERE id=?", (rid,)))

    def report_get(self, rid: str) -> dict:
        r = self.store.one("SELECT * FROM message_reports WHERE id=?", (rid,))
        if not r:
            raise ServiceError(404, "no_report", "找不到该举报")
        return self._report_out(r)

    def messaging_managers(self, oid: str) -> list[str]:
        """Active members holding org.messaging.manage — the people a report goes to."""
        from .org_roles import OrgRoles
        roles = OrgRoles(self.store)
        rows = self.store.all("SELECT m.user_id FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                              "WHERE m.organization_id=? AND u.status='active' AND u.deleted_at IS NULL", (oid,))
        return [r["user_id"] for r in rows if roles.has(oid, r["user_id"], "org.messaging.manage")]

    def _report_out(self, r: dict) -> dict:
        conv = self.store.one("SELECT kind, name FROM conversations WHERE id=?", (r["conversation_id"],)) or {}
        return {"id": r["id"], "organization_id": r["organization_id"], "conversation_id": r["conversation_id"], "message_id": r["message_id"],
                "conversation": {"kind": conv.get("kind"), "name": conv.get("name") if conv.get("kind") == "group" else None},
                "reporter": {"user_id": r["reporter_id"], "name": self._name(r["reporter_id"])}, "sender": {"user_id": r["sender_id"], "name": self._name(r["sender_id"])},
                "reason": r["reason"], "note": r.get("note"), "body_snapshot": r["body_snapshot"], "attachment_name": r.get("attachment_name"),
                "status": r["status"], "reviewed_by": ({"user_id": r["reviewed_by"], "name": self._name(r["reviewed_by"])} if r.get("reviewed_by") else None),
                "reviewed_at": r.get("reviewed_at"), "decision_note": r.get("decision_note"), "created_at": r["created_at"]}

    def add_members(self, cid: str, uid: str, member_ids: list[str]) -> dict:
        conv, _ = self._manager(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "已归档的群不能再加人")
        added = []
        ts = now()
        with self.store.tx() as t:
            count = t.execute("SELECT COUNT(*) AS n FROM conversation_members WHERE conversation_id=? AND left_at IS NULL", (cid,)).fetchone()["n"]
            for mid in dict.fromkeys(member_ids or []):
                self._org_member(conv["organization_id"], mid)
                m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=?", (cid, mid)).fetchone()
                if m and m["left_at"] is None:
                    continue
                if count >= MAX_GROUP_MEMBERS:
                    raise ServiceError(400, "too_many_members", f"群成员最多 {MAX_GROUP_MEMBERS} 人")
                last = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()["last_seq"]
                # history starts with the first message *after* the join (the system notice of
                # the join itself); everything up to `last` stays invisible to this member
                if m:      # rejoining: history starts again from now
                    t.execute("UPDATE conversation_members SET left_at=NULL, left_seq=NULL, role='member', joined_at=?, joined_seq=?, last_read_seq=?, added_by=? WHERE conversation_id=? AND user_id=?",
                              (ts, last + 1, last, uid, cid, mid))
                else:
                    t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,last_read_seq,notify,added_by) VALUES (?,?,'member',?,?,?,'mentions',?)",
                              (cid, mid, ts, last + 1, last, uid))
                added.append(mid); count += 1
            if added:
                self._system(t, cid, uid, {"event": "added", "added": added})
        if added:
            self.publish(added, {"type": "conversation", "conversation_id": cid, "change": "added"})
        return self.get(cid, uid)

    def remove_member(self, cid: str, uid: str, target: str) -> dict:
        conv, me = self._manager(cid, uid)
        if target == uid:
            raise ServiceError(400, "invalid_field", "请用「退出群聊」离开", {"field": "user_id"})
        with self.store.tx() as t:
            m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (cid, target)).fetchone()
            if not m:
                raise ServiceError(404, "no_member", "该成员不在群里")
            if m["role"] == "owner" or (m["role"] == "admin" and me["role"] != "owner"):
                raise ServiceError(403, "forbidden", "只有群主可以移除管理员；群主不能被移除")
            self._close(t, cid, target)
            self._system(t, cid, uid, {"event": "removed", "removed": [target]})
        self.publish([target], {"type": "conversation", "conversation_id": cid, "change": "removed"})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def leave(self, cid: str, uid: str) -> None:
        conv, me = self._member(cid, uid)
        if conv["kind"] == "dm":
            raise ServiceError(409, "not_a_group", "私聊不能退出；可以静音或隐藏")
        with self.store.tx() as t:
            others = t.execute("SELECT COUNT(*) AS n FROM conversation_members WHERE conversation_id=? AND left_at IS NULL AND user_id<>?", (cid, uid)).fetchone()["n"]
            if me["role"] == "owner" and others:
                raise ServiceError(409, "owner_must_transfer", "群主退出前请先把群主交接给其他成员，或归档群聊")
            self._close(t, cid, uid)
            self._system(t, cid, uid, {"event": "left"})
            if not others:
                t.execute("UPDATE conversations SET archived_at=COALESCE(archived_at,?), updated_at=? WHERE id=?", (now(), now(), cid))
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})

    def transfer(self, cid: str, uid: str, target: str) -> dict:
        self._manager(cid, uid, owner=True)
        with self.store.tx() as t:
            m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (cid, target)).fetchone()
            if not m or target == uid:
                raise ServiceError(404, "no_member", "该成员不在群里")
            t.execute("UPDATE conversation_members SET role='owner' WHERE conversation_id=? AND user_id=?", (cid, target))
            t.execute("UPDATE conversation_members SET role='admin' WHERE conversation_id=? AND user_id=?", (cid, uid))
            self._system(t, cid, uid, {"event": "transferred", "to": target})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def set_role(self, cid: str, uid: str, target: str, role: str) -> dict:
        self._manager(cid, uid, owner=True)
        if role not in ("admin", "member") or target == uid:
            raise ServiceError(400, "invalid_field", "角色只能是 admin 或 member", {"field": "role"})
        n = self.store.execute("UPDATE conversation_members SET role=? WHERE conversation_id=? AND user_id=? AND left_at IS NULL AND role<>'owner'", (role, cid, target))
        if not n:
            raise ServiceError(404, "no_member", "该成员不在群里")
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def archive(self, cid: str, uid: str, archived: bool = True) -> dict:
        self._manager(cid, uid, owner=True)
        with self.store.tx() as t:
            t.execute("UPDATE conversations SET archived_at=?, updated_at=? WHERE id=?", (now() if archived else None, now(), cid))
            self._system(t, cid, uid, {"event": "archived" if archived else "unarchived"})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def set_preferences(self, cid: str, uid: str, *, muted=None, pinned=None, notify=None) -> dict:
        self._member(cid, uid)
        fields, args = [], []
        if muted is not None:
            fields.append("muted=?"); args.append(1 if muted else 0)
        if pinned is not None:
            fields.append("pinned=?"); args.append(1 if pinned else 0)
        if notify is not None:
            if notify not in ("all", "mentions", "none"):
                raise ServiceError(400, "invalid_field", "notify 只能是 all、mentions 或 none", {"field": "notify"})
            fields.append("notify=?"); args.append(notify)
        if fields:
            self.store.execute(f"UPDATE conversation_members SET {', '.join(fields)} WHERE conversation_id=? AND user_id=?", (*args, cid, uid))
        return self.get(cid, uid)

    def _close(self, t, cid: str, uid: str) -> None:
        last = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()["last_seq"]
        t.execute("UPDATE conversation_members SET left_at=?, left_seq=? WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (now(), last, cid, uid))

    def withdraw_from_organization(self, oid: str, uid: str) -> int:
        """Called when someone leaves or is removed from an organization: every conversation of
        that organization closes for them at once — lists, history, streams and attachments."""
        ts = now()
        with self.store.tx() as t:
            rows = t.execute("SELECT m.conversation_id FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id "
                             "WHERE c.organization_id=? AND m.user_id=? AND m.left_at IS NULL", (oid, uid)).fetchall()
            for r in rows:
                t.execute("UPDATE conversation_members SET left_at=?, left_seq=(SELECT last_seq FROM conversations WHERE id=?) "
                          "WHERE conversation_id=? AND user_id=?", (ts, r["conversation_id"], r["conversation_id"], uid))
        if rows:
            self.publish([uid], {"type": "conversation", "change": "withdrawn", "organization_id": oid})
        return len(rows)

    # ------------------------------------------------------------------ reading
    def get(self, cid: str, uid: str) -> dict:
        conv, me = self._member(cid, uid)
        return self._conv_out(conv, me, uid)

    def _conv_out(self, conv: dict, me: dict, uid: str) -> dict:
        members = self.store.all("SELECT m.user_id, m.role, m.joined_at, u.display_name, u.email FROM conversation_members m JOIN users u ON u.id=m.user_id "
                                 "WHERE m.conversation_id=? AND m.left_at IS NULL ORDER BY m.joined_at", (conv["id"],))
        peer = next((m for m in members if m["user_id"] != uid), None) if conv["kind"] == "dm" else None
        unread = max(0, conv["last_seq"] - max(me["last_read_seq"], me["joined_seq"]))
        last = self.store.one("SELECT * FROM messages WHERE conversation_id=? AND seq=?", (conv["id"], conv["last_seq"])) if conv["last_seq"] else None
        return {"id": conv["id"], "organization_id": conv["organization_id"], "kind": conv["kind"], "workspace_id": conv["workspace_id"],
                "name": conv["name"] if conv["kind"] == "group" else (peer["display_name"] or peer["email"].split("@")[0] if peer else "—"),
                "description": conv["description"], "archived": bool(conv["archived_at"]), "created_by": conv["created_by"],
                "announcement": conv.get("announcement") or None, "announcement_at": conv.get("announcement_at"),
                "last_seq": conv["last_seq"], "last_message_at": conv["last_message_at"], "updated_at": conv["updated_at"],
                "me": {"role": me["role"], "joined_seq": me["joined_seq"], "last_read_seq": me["last_read_seq"], "muted": bool(me["muted"]),
                       "pinned": bool(me["pinned"]), "notify": me["notify"]},
                "unread": unread, "peer": ({"user_id": peer["user_id"], "name": peer["display_name"] or peer["email"].split("@")[0]} if peer else None),
                "members": [{"user_id": m["user_id"], "name": m["display_name"] or m["email"].split("@")[0], "role": m["role"]} for m in members],
                "member_count": len(members),
                "last_message": self._preview(last, me) if last else None}

    def _preview(self, msg: dict, me: dict) -> dict | None:
        if msg["seq"] < me["joined_seq"]:
            return None
        return {"seq": msg["seq"], "sender_id": msg["sender_id"], "sender": self._name(msg["sender_id"]), "kind": msg["kind"],
                "body": "" if msg["deleted_at"] else msg["body"][:120], "deleted": bool(msg["deleted_at"]), "created_at": msg["created_at"],
                "attachment": bool(msg["attachment_id"])}

    def list_conversations(self, uid: str, *, organization_id: str | None = None) -> list[dict]:
        where, args = "m.user_id=? AND m.left_at IS NULL", [uid]
        if organization_id:
            where += " AND c.organization_id=?"; args.append(organization_id)
        rows = self.store.all(f"SELECT c.*, m.role AS m_role, m.joined_seq AS m_joined_seq, m.last_read_seq AS m_last_read_seq, m.muted AS m_muted, "
                              f"m.pinned AS m_pinned, m.notify AS m_notify FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id "
                              f"WHERE {where} ORDER BY m.pinned DESC, COALESCE(c.last_message_at, c.created_at) DESC LIMIT 500", tuple(args))
        out = []
        for r in rows:
            oid = r["organization_id"]
            if not current_membership(self.store, oid, uid):
                continue                                           # organization access gone: not listed
            me = {"role": r["m_role"], "joined_seq": r["m_joined_seq"], "last_read_seq": r["m_last_read_seq"], "muted": r["m_muted"], "pinned": r["m_pinned"], "notify": r["m_notify"]}
            conv = {k: r[k] for k in r.keys() if not k.startswith("m_")}
            out.append(self._conv_out(conv, me, uid))
        return out

    def unread_total(self, uid: str) -> dict:
        rows = self.store.all("SELECT c.organization_id, c.last_seq, m.last_read_seq, m.joined_seq, m.muted FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id "
                              "WHERE m.user_id=? AND m.left_at IS NULL", (uid,))
        total, conversations = 0, 0
        seen_orgs: dict[str, bool] = {}
        for r in rows:
            if r["muted"]:
                continue
            oid = r["organization_id"]
            if oid not in seen_orgs:
                seen_orgs[oid] = bool(current_membership(self.store, oid, uid))
            if not seen_orgs[oid]:
                continue
            n = max(0, r["last_seq"] - max(r["last_read_seq"], r["joined_seq"]))
            if n:
                total += n; conversations += 1
        return {"unread": total, "conversations": conversations}

    def _message_out(self, m: dict, reactions: dict | None = None, names: dict | None = None) -> dict:
        names = names if names is not None else {}
        if m["sender_id"] not in names:
            names[m["sender_id"]] = self._name(m["sender_id"])
        out = {"id": m["id"], "conversation_id": m["conversation_id"], "seq": m["seq"], "sender_id": m["sender_id"], "sender": names[m["sender_id"]],
               "kind": m["kind"], "body": "" if m["deleted_at"] else m["body"], "reply_to": m["reply_to"], "mentions": _load(m["mentions"]) or [],
               "edited_at": m["edited_at"], "deleted": bool(m["deleted_at"]), "created_at": m["created_at"], "client_id": m["client_id"],
               "reactions": (reactions or {}).get(m["id"], []), "attachment": None}
        if m["kind"] == "system":
            out["system"] = _load(m["body"]) or {}
            out["body"] = ""
        if m["attachment_id"] and not m["deleted_at"]:
            a = self.store.one("SELECT id, filename, size, content_type, status FROM message_attachments WHERE id=?", (m["attachment_id"],))
            if a:
                out["attachment"] = {"id": a["id"], "filename": a["filename"], "size": a["size"], "content_type": a["content_type"],
                                     "inline": a["content_type"] in INLINE_TYPES}
        if m["reply_to"]:
            r = self.store.one("SELECT id, sender_id, body, deleted_at, seq FROM messages WHERE id=? AND conversation_id=?", (m["reply_to"], m["conversation_id"]))
            if r:
                out["reply"] = {"id": r["id"], "seq": r["seq"], "sender": self._name(r["sender_id"]), "body": "" if r["deleted_at"] else r["body"][:160], "deleted": bool(r["deleted_at"])}
        return out

    def _reactions_for(self, ids: list[str]) -> dict:
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        rows = self.store.all(f"SELECT message_id, emoji, user_id FROM message_reactions WHERE message_id IN ({marks}) ORDER BY created_at", tuple(ids))
        out: dict[str, dict[str, list[str]]] = {}
        for r in rows:
            out.setdefault(r["message_id"], {}).setdefault(r["emoji"], []).append(r["user_id"])
        return {mid: [{"emoji": e, "users": us, "count": len(us)} for e, us in per.items()] for mid, per in out.items()}

    def history(self, cid: str, uid: str, *, before_seq: int | None = None, after_seq: int | None = None, limit: int = PAGE) -> dict:
        """A page of messages the caller may see (seq ≥ their joined_seq). `before_seq` pages
        backwards for scrolling up; `after_seq` fetches what arrived since a known point."""
        conv, me = self._member(cid, uid)
        limit = max(1, min(int(limit or PAGE), PAGE_MAX))
        floor = me["joined_seq"]
        if after_seq is not None:
            rows = self.store.all("SELECT * FROM messages WHERE conversation_id=? AND seq>? AND seq>=? ORDER BY seq LIMIT ?", (cid, int(after_seq), floor, limit))
            more = len(rows) == limit
        else:
            top = int(before_seq) if before_seq is not None else conv["last_seq"] + 1
            rows = self.store.all("SELECT * FROM messages WHERE conversation_id=? AND seq<? AND seq>=? ORDER BY seq DESC LIMIT ?", (cid, top, floor, limit))
            rows = list(reversed(rows))
            more = bool(rows) and rows[0]["seq"] > floor + 0 and self.store.one("SELECT 1 FROM messages WHERE conversation_id=? AND seq<? AND seq>=?", (cid, rows[0]["seq"], floor)) is not None
        reactions = self._reactions_for([r["id"] for r in rows])
        names: dict[str, str] = {}
        return {"items": [self._message_out(dict(r), reactions, names) for r in rows], "has_more": more,
                "floor": floor, "last_seq": conv["last_seq"], "last_read_seq": me["last_read_seq"]}

    def search(self, uid: str, q: str, *, organization_id: str | None = None, limit: int = 50) -> list[dict]:
        """Text search over what the caller may read: their active memberships, at or after
        their joined_seq. No index yet — bounded LIKE over the caller's conversations."""
        q = (q or "").strip()
        if len(q) < 2:
            raise ServiceError(400, "invalid_field", "搜索至少需要 2 个字符", {"field": "q"})
        where, args = "m.user_id=? AND m.left_at IS NULL AND x.seq>=m.joined_seq AND x.kind='text' AND x.deleted_at IS NULL AND x.body LIKE ?", [uid, f"%{q}%"]
        if organization_id:
            where += " AND c.organization_id=?"; args.append(organization_id)
        rows = self.store.all(f"SELECT x.*, c.organization_id AS c_org FROM messages x JOIN conversation_members m ON m.conversation_id=x.conversation_id "
                              f"JOIN conversations c ON c.id=x.conversation_id WHERE {where} ORDER BY x.created_at DESC LIMIT ?", (*args, max(1, min(limit, 100))))
        out, names, seen = [], {}, {}
        for r in rows:
            oid = r["c_org"]
            if oid not in seen:
                seen[oid] = bool(current_membership(self.store, oid, uid))
            if seen[oid]:
                out.append(self._message_out({k: r[k] for k in r.keys() if k != "c_org"}, {}, names))
        return out

    # ------------------------------------------------------------------ writing
    def send(self, cid: str, uid: str, *, body: str, client_id: str | None = None, reply_to: str | None = None,
             attachment_id: str | None = None, mention_ids: list[str] | None = None) -> tuple[dict, bool]:
        """Store one message; returns (message, created). A repeated `client_id` from the same
        sender returns the stored message with created=False — the retry after a lost
        acknowledgement that must not become a duplicate."""
        conv, me = self._member(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "会话已归档，不能再发消息")
        body = (body or "").replace("\r\n", "\n").strip()
        if len(body) > MAX_BODY:
            raise ServiceError(400, "message_too_long", f"消息最多 {MAX_BODY} 个字符", {"field": "body", "limit": MAX_BODY})
        if not body and not attachment_id:
            raise ServiceError(400, "empty_message", "消息不能为空", {"field": "body"})
        if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", client_id)):
            raise ServiceError(400, "invalid_field", "client_id 无效", {"field": "client_id"})
        mentions = []
        active = set(self._active_user_ids(cid))
        for mid in dict.fromkeys(mention_ids or []):
            if mid in active and mid != uid:
                mentions.append(mid)
        ts = now()
        with self.store.tx() as t:
            if client_id:
                dup = t.execute("SELECT * FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?", (cid, uid, client_id)).fetchone()
                if dup:
                    return self._message_out(dict(dup), self._reactions_for([dup["id"]])), False
            if reply_to:
                r = t.execute("SELECT seq FROM messages WHERE id=? AND conversation_id=?", (reply_to, cid)).fetchone()
                if not r or r["seq"] < me["joined_seq"]:
                    raise ServiceError(404, "no_message", "找不到要回复的消息")
            if attachment_id:
                a = t.execute("SELECT * FROM message_attachments WHERE id=? AND conversation_id=? AND uploader_id=? AND status='accepted'", (attachment_id, cid, uid)).fetchone()
                if not a or a["message_id"]:
                    raise ServiceError(404, "no_attachment", "附件不存在或已使用")
            row = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()
            seq = row["last_seq"] + 1
            mid_ = new_id("msg")
            t.execute("INSERT INTO messages (id,conversation_id,seq,sender_id,client_id,kind,body,reply_to,attachment_id,mentions,created_at) VALUES (?,?,?,?,?,'text',?,?,?,?,?)",
                      (mid_, cid, seq, uid, client_id, body, reply_to, attachment_id, json.dumps(mentions) if mentions else None, ts))
            t.execute("UPDATE conversations SET last_seq=?, last_message_at=?, updated_at=? WHERE id=?", (seq, ts, ts, cid))
            t.execute("UPDATE conversation_members SET last_read_seq=? WHERE conversation_id=? AND user_id=?", (seq, cid, uid))
            if attachment_id:
                t.execute("UPDATE message_attachments SET message_id=?, expires_at=NULL WHERE id=?", (mid_, attachment_id))
            stored = dict(t.execute("SELECT * FROM messages WHERE id=?", (mid_,)).fetchone())
        out = self._message_out(stored)
        self.publish(sorted(active), {"type": "message", "conversation_id": cid, "message": out})
        return out, True

    def _system(self, t, cid: str, actor: str, event: dict) -> None:
        row = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()
        seq = row["last_seq"] + 1
        ts = now()
        t.execute("INSERT INTO messages (id,conversation_id,seq,sender_id,kind,body,created_at) VALUES (?,?,?,?,'system',?,?)",
                  (new_id("msg"), cid, seq, actor, json.dumps({**event, "actor": actor}, ensure_ascii=False), ts))
        t.execute("UPDATE conversations SET last_seq=?, last_message_at=?, updated_at=? WHERE id=?", (seq, ts, ts, cid))

    def edit(self, mid: str, uid: str, body: str) -> dict:
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        if m["sender_id"] != uid or m["kind"] != "text" or m["deleted_at"] or m["seq"] < me["joined_seq"]:
            raise ServiceError(403, "forbidden", "只能编辑自己的消息")
        body = (body or "").replace("\r\n", "\n").strip()
        if not body or len(body) > MAX_BODY:
            raise ServiceError(400, "invalid_field", f"消息需要 1–{MAX_BODY} 个字符", {"field": "body"})
        self.store.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (body, now(), mid))
        out = self._message_out(dict(self.store.one("SELECT * FROM messages WHERE id=?", (mid,))), self._reactions_for([mid]))
        self.publish(self._active_user_ids(m["conversation_id"]), {"type": "message_updated", "conversation_id": m["conversation_id"], "message": out})
        return out

    def delete(self, mid: str, uid: str) -> dict:
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        moderator = conv["kind"] == "group" and me["role"] in ("owner", "admin")
        if m["kind"] != "text" or m["seq"] < me["joined_seq"] or (m["sender_id"] != uid and not moderator):
            raise ServiceError(403, "forbidden", "只能删除自己的消息（群主/管理员可删除群内消息）")
        with self.store.tx() as t:
            t.execute("UPDATE messages SET deleted_at=COALESCE(deleted_at,?), body='' WHERE id=?", (now(), mid))
            t.execute("DELETE FROM message_reactions WHERE message_id=?", (mid,))
        out = self._message_out(dict(self.store.one("SELECT * FROM messages WHERE id=?", (mid,))))
        self.publish(self._active_user_ids(m["conversation_id"]), {"type": "message_updated", "conversation_id": m["conversation_id"], "message": out})
        return out

    def react(self, mid: str, uid: str, emoji: str, on: bool = True) -> dict:
        if emoji not in REACTIONS:
            raise ServiceError(400, "invalid_field", "不支持的表情", {"field": "emoji", "allowed": list(REACTIONS)})
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        if m["seq"] < me["joined_seq"] or m["deleted_at"]:
            raise ServiceError(404, "no_message", "找不到该消息")
        if on:
            self.store.execute("INSERT OR IGNORE INTO message_reactions (message_id,user_id,emoji,created_at) VALUES (?,?,?,?)", (mid, uid, emoji, now()))
        else:
            self.store.execute("DELETE FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?", (mid, uid, emoji))
        out = self._message_out(dict(m), self._reactions_for([mid]))
        self.publish(self._active_user_ids(m["conversation_id"]), {"type": "message_updated", "conversation_id": m["conversation_id"], "message": out})
        return out

    def mark_read(self, cid: str, uid: str, seq: int) -> dict:
        conv, me = self._member(cid, uid)
        target = max(me["last_read_seq"], min(int(seq), conv["last_seq"]))
        if target != me["last_read_seq"]:
            self.store.execute("UPDATE conversation_members SET last_read_seq=? WHERE conversation_id=? AND user_id=?", (target, cid, uid))
            self.publish([uid], {"type": "read", "conversation_id": cid, "last_read_seq": target})
        return {"conversation_id": cid, "last_read_seq": target, "unread": max(0, conv["last_seq"] - max(target, me["joined_seq"]))}

    # ------------------------------------------------------------------ attachments
    def attach(self, cid: str, uid: str, *, filename: str, data: bytes, scan: dict) -> dict:
        conv, _ = self._member(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "会话已归档")
        if not data:
            raise ServiceError(400, "empty_file", "文件是空的")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ServiceError(413, "file_too_large", f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限")
        blob = self.store.put_blob(data)
        aid = new_id("att")
        ctype = sniff_content_type(data, filename)
        self.store.insert("message_attachments", {"id": aid, "conversation_id": cid, "message_id": None, "uploader_id": uid, "filename": filename[:200] or "file",
                                                  "size": len(data), "blob": blob, "content_type": ctype, "scan": json.dumps(scan or {}, ensure_ascii=False),
                                                  "status": "accepted", "created_at": now(), "expires_at": now() + ATTACHMENT_UNUSED_S})
        return {"id": aid, "filename": filename[:200] or "file", "size": len(data), "content_type": ctype, "inline": ctype in INLINE_TYPES, "scan": scan}

    def attachment_for_download(self, aid: str, uid: str) -> dict:
        a = self.store.one("SELECT * FROM message_attachments WHERE id=?", (aid,))
        if not a or a["status"] != "accepted":
            raise ServiceError(404, "no_attachment", "找不到该附件")
        conv, me = self._member(a["conversation_id"], uid)
        if a["message_id"]:
            m = self.store.one("SELECT seq, deleted_at FROM messages WHERE id=?", (a["message_id"],))
            if not m or m["deleted_at"] or m["seq"] < me["joined_seq"]:
                raise ServiceError(404, "no_attachment", "找不到该附件")
        elif a["uploader_id"] != uid:
            raise ServiceError(404, "no_attachment", "找不到该附件")
        return dict(a)

    def sweep_unused_attachments(self) -> int:
        rows = self.store.all("SELECT id FROM message_attachments WHERE message_id IS NULL AND expires_at<?", (now(),))
        for r in rows:
            self.store.execute("DELETE FROM message_attachments WHERE id=?", (r["id"],))
        return len(rows)

    # ------------------------------------------------------------------ who to tell
    def recipients_for(self, cid: str, message: dict) -> dict[str, str]:
        """Who should get an inbox item for this message and why: `dm` for the other side of a
        direct message, `mention` for people the message mentions — honouring per-conversation
        notify modes and mute. The sender is never told about their own message."""
        conv = self.store.one("SELECT * FROM conversations WHERE id=?", (cid,))
        rows = self.store.all("SELECT * FROM conversation_members WHERE conversation_id=? AND left_at IS NULL AND user_id<>?", (cid, message["sender_id"]))
        out: dict[str, str] = {}
        for m in rows:
            if m["muted"] or m["notify"] == "none":
                continue
            if m["user_id"] in (message.get("mentions") or []):
                out[m["user_id"]] = "mention"
            elif conv["kind"] == "dm" and m["notify"] == "all":
                out[m["user_id"]] = "dm"
            elif conv["kind"] == "group" and m["notify"] == "all":
                out[m["user_id"]] = "group"
        return out
