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
  said before — unless the manager adding them chose `history_from = all` (CI-74), which is
  recorded on the membership and said in the system row. Leaving (or being removed, or losing
  organization membership) closes the membership (`left_at`); the conversation disappears from
  that person's list and every later read, search, stream and attachment download is refused.
  Being added again opens a new membership with a new `joined_seq` — nothing is silently restored.
* **Sequence numbers, not timestamps.** Messages are ordered by a per-conversation `seq`
  assigned inside the write transaction; read positions, unread counts, pagination cursors and
  "what did I miss" all use it. A `client_id` makes sends idempotent: a retry after a lost
  acknowledgement returns the message that was stored, not a duplicate.
* **Recall for everyone, delete for me (CI-74).** The sender may *recall* their own message for
  everyone within `RECALL_WINDOW_S`: the row stays (sequences and replies keep their meaning),
  its body, attachment, reactions and search tokens go, and readers see the marker
  「X 撤回了一条消息」. *Delete* is per reader: a row in `message_hides` that only that person's
  reads honour. A group owner/admin may *remove* a message for everyone at any time, and the
  marker names them; a report review removes it the same way (unchanged). Nobody can claim a
  downloaded copy is gone.
* **Attachments go through the same acceptance chain** as every upload (scan first, refuse on
  infection or when the scanner is down) and are served only to a member who may read the
  message they hang on. Forwarding re-binds a new attachment row to the same stored bytes, and
  only when the forwarder can read the original.
* **Presence and read receipts are opt-in and honest (CI-74).** Presence comes from heartbeats a
  visible page sends and is one of three states by heartbeat age (`messaging_presence`), never
  stored; a person who hides theirs sees nobody's. Read positions of others are shown only to and
  for members who turned `read_receipts` on. There is no "delivered": the server cannot know it.
* **Blocking protects the blocker, quietly (CI-78).** A person may block someone they share an
  organization with. Between the two there is then no new direct message and no sending in the
  existing one (either way), and neither sees the other's presence, typing or read position.
  Groups stay shared spaces: the blocked person's messages there remain visible, but they create
  no inbox item, no @ mark and no digest mail for the blocker. The blocked person is not told;
  a refused send says only 「暂时无法向对方发送消息」. Moderation (reports) is untouched.
"""
from __future__ import annotations

import json
import sqlite3
import os
import re
from typing import Callable

from . import quarantine
from .errors import ServiceError
from .identity import Identity
from .organizations import current_membership
from .store import Store, new_id, now
from . import messaging_search as fts
from .messaging_presence import Presence

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
-- v24 (CI-74): a person's messaging privacy switches. `show_presence` (default on) and
-- `read_receipts` (default OFF) — both mutual: hiding yours hides everyone's from you.
CREATE TABLE IF NOT EXISTS messaging_settings (
  user_id TEXT PRIMARY KEY, show_presence INTEGER NOT NULL DEFAULT 1, read_receipts INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);
-- v24 (CI-74): "delete for me" — the rows one reader chose not to see; nothing else changes.
CREATE TABLE IF NOT EXISTS message_hides (
  message_id TEXT NOT NULL, user_id TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (message_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_message_hides_user ON message_hides(user_id);
-- v26 (CI-78): a group's avatar — the platform's avatar rules (`avatar_image.canonical_avatar`:
-- a rebuilt PNG ≤ 256 KB, ≤ 512 × 512), served only to the group's current members.
CREATE TABLE IF NOT EXISTS conversation_avatars (
  conversation_id TEXT PRIMARY KEY, image BLOB NOT NULL, updated_by TEXT NOT NULL, updated_at REAL NOT NULL
);
-- v26 (CI-78): the people a person blocked. Only between people who share an organization; the
-- effects (no direct messages either way, no presence/typing/receipts between the two, no inbox
-- items or digest mail from the blocked person) are read from here, never copied elsewhere.
CREATE TABLE IF NOT EXISTS message_blocks (
  user_id TEXT NOT NULL, blocked_id TEXT NOT NULL, created_at REAL NOT NULL,
  PRIMARY KEY (user_id, blocked_id)
);
CREATE INDEX IF NOT EXISTS ix_message_blocks_blocked ON message_blocks(blocked_id);
"""

#: v24 (CI-74), all additive; existing CHECK sets untouched (AGENTS.md / brief 4.2-12).
V24_COLUMNS = [
    ("conversation_members", "marked_unread", "INTEGER NOT NULL DEFAULT 0"),   # "mark as unread": a flag, never a read cursor moved back
    ("conversation_members", "muted_until", "REAL"),                           # mute for a while, next to the standing `muted`
    ("conversation_members", "history_from", "TEXT"),                          # 'all' when the adder shared the whole history; NULL = from the join
    ("conversation_members", "last_read_at", "REAL"),                          # when the read cursor last moved (read receipts show it)
    ("conversation_members", "updated_at", "REAL"),                            # any change to this membership row (the sync cursor watches it)
    ("messages", "recalled_at", "REAL"),                                       # recalled by the sender (deleted_at is set too)
    ("messages", "removed_by", "TEXT"),                                        # removed for everyone by a group owner/admin (deleted_at is set too)
    ("messages", "forwarded_from", "TEXT"),                                    # JSON {message_id, conversation_id, sender_id} of the original
    ("message_attachments", "duration_ms", "INTEGER"),                         # voice messages: the client-declared, validated length
]

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
#: Voice messages (CI-74): what browsers record (`MediaRecorder`), sniffed from the bytes.
VOICE_TYPES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/aac")
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_VOICE_MS = 5 * 60 * 1000
#: Rendered inline (with `nosniff` + a sandboxing CSP): images and voice; everything else downloads.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", *VOICE_TYPES}
ATTACHMENT_UNUSED_S = 3600
#: Forwarding: one message to at most this many conversations per request.
MAX_FORWARD_TARGETS = 5
HISTORY_FROM = ("join", "all")
Publish = Callable[[list[str], dict], None]


def _window(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return max(0, v)


#: Recall (for everyone) is the sender's within this many seconds of sending — 120 s like WeChat
#: and QQ (brief §五); `SYNWEVO_MESSAGING_RECALL_WINDOW_S` changes it per deployment.
RECALL_WINDOW_S = _window("SYNWEVO_MESSAGING_RECALL_WINDOW_S", 120)
#: Editing one's own message: 15 minutes like WhatsApp; `SYNWEVO_MESSAGING_EDIT_WINDOW_S`.
EDIT_WINDOW_S = _window("SYNWEVO_MESSAGING_EDIT_WINDOW_S", 15 * 60)


def sniff_content_type(data: bytes, filename: str) -> str:
    """Server-side type from the bytes; the client's claim is not consulted. Only image and voice
    types are ever rendered inline; everything else downloads as an attachment."""
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
    # --- voice (CI-74): the containers browsers record into
    if head.startswith(b"\x1a\x45\xdf\xa3"):                       # EBML: Matroska/WebM
        return "audio/webm" if b"webm" in data[:64] else "video/webm"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if len(data) >= 12 and data[4:8] == b"ftyp":                  # ISO BMFF (MP4/M4A): voice only without a video track
        has_video = any(tag in data for tag in (b"avc1", b"avc3", b"hvc1", b"hev1", b"vp09", b"av01", b"mp4v"))
        has_audio = any(tag in data for tag in (b"mp4a", b"Opus", b"alac", b"ac-3", b"ec-3"))
        return "audio/mp4" if has_audio and not has_video else "video/mp4"
    if len(data) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0:  # ADTS AAC frame sync
        return "audio/aac"
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


def conversation_target(store: Store, cid: str | None) -> dict:
    """The inbox target for a conversation (or the list when `cid` is None), as the deployment is.

    Since CI-66 conversations are served by Synwevo Message on its own origin: while that
    application is registered, targets name origin ``messages`` with the path the messages shell
    routes (`/c/<cid>`); on a deployment without it they name the account origin's `/messages`
    paths as before. Decided when the item is written, resolved through the registry when it is
    read (`notifications.resolve_target`), so neither side guesses a host.
    """
    from .apps import messages_launch_url
    if messages_launch_url(store):
        return {"origin": "messages", "path": f"/c/{cid}" if cid else "/"}
    return {"origin": "accounts", "path": f"/messages/{cid}" if cid else "/messages"}


def is_muted(m: dict, ts: float | None = None) -> bool:
    """A membership is silent while `muted` is set or `muted_until` lies ahead."""
    ts = now() if ts is None else ts
    until = m.get("muted_until")
    return bool(m.get("muted")) or bool(until and float(until) > ts)


#: What a refused direct message says (CI-78): plainly to the person who blocked, neutrally to the
#: person who was blocked — who is never told. Both are the same code, `blocked`.
BLOCKED_BY_ME = "你已屏蔽对方，解除后才能发送"
BLOCKED_NEUTRAL = "暂时无法向对方发送消息"


def blocks_for_export(store: Store, uid: str) -> list[dict]:
    """The person's own block list for their account export: whom they blocked and when — never
    who blocked them."""
    return store.all("SELECT blocked_id AS user_id, created_at FROM message_blocks WHERE user_id=? ORDER BY created_at", (uid,))


def messaging_export(store: Store, uid: str) -> dict:
    """The person's own messaging data for their account export (format synwevo-account-2): the
    messages they sent that still exist (text, times, the conversation and position, an attached
    file's name — never another person's words and never file bytes), the conversations they are or
    were in (a group's name; a direct message only as its kind, since the other side's name is the
    other side's data), and their messaging settings. Tolerant of tables a build may not have."""
    out: dict = {"messages_sent": [], "conversations": [], "settings": None}
    try:
        out["messages_sent"] = store.all(
            "SELECT m.conversation_id, m.seq, m.body, m.created_at, m.edited_at, a.filename AS attachment_name "
            "FROM messages m LEFT JOIN message_attachments a ON a.id=m.attachment_id "
            "WHERE m.sender_id=? AND m.kind='text' AND m.deleted_at IS NULL ORDER BY m.created_at", (uid,))
        out["conversations"] = [
            {"conversation_id": r["id"], "kind": r["kind"], "name": r["name"] if r["kind"] == "group" else None,
             "organization_id": r["organization_id"], "role": r["role"], "joined_at": r["joined_at"], "left_at": r["left_at"]}
            for r in store.all("SELECT c.id, c.kind, c.name, c.organization_id, m.role, m.joined_at, m.left_at "
                               "FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id WHERE m.user_id=? "
                               "ORDER BY m.joined_at", (uid,))]
        row = store.one("SELECT show_presence, read_receipts FROM messaging_settings WHERE user_id=?", (uid,))
        out["settings"] = ({"show_presence": bool(row["show_presence"]), "read_receipts": bool(row["read_receipts"])}
                           if row else {"show_presence": True, "read_receipts": False, "default": True})
    except sqlite3.OperationalError:
        pass
    return out


def forget_blocks(store: Store, uid: str) -> int:
    """Account deletion: the person's block list goes, and so do blocks naming them — a deleted
    account can never send again, and a nameless row would only linger in someone's list."""
    return store.execute("DELETE FROM message_blocks WHERE user_id=? OR blocked_id=?", (uid, uid))


class Messaging:
    def __init__(self, store: Store, ident: Identity, *, publish: Publish | None = None, presence: Presence | None = None):
        self.store, self.ident = store, ident
        self.publish = publish or (lambda users, event: None)
        #: The process's in-memory presence registry; None when this process serves no heartbeat
        #: (the account service's moderation routes) — then nobody is shown any state.
        self.presence = presence
        self._search_info: dict | None = None

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

    def _visible_message(self, mid: str, uid: str) -> tuple[dict, dict, dict]:
        """A message the caller may act on: exists, in a conversation they are in, at or after
        their join, not hidden for them. Anything else is the same 404."""
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        if m["seq"] < me["joined_seq"] or self.store.one("SELECT 1 AS x FROM message_hides WHERE message_id=? AND user_id=?", (mid, uid)):
            raise ServiceError(404, "no_message", "找不到该消息")
        return m, conv, me

    # ------------------------------------------------------------------ settings (CI-74)
    def settings(self, uid: str) -> dict:
        return self._settings_for([uid])[uid]

    def _settings_for(self, ids) -> dict[str, dict]:
        ids = list(dict.fromkeys(ids))
        out = {u: {"show_presence": True, "read_receipts": False} for u in ids}
        if ids:
            marks = ",".join("?" for _ in ids)
            for r in self.store.all(f"SELECT user_id, show_presence, read_receipts FROM messaging_settings WHERE user_id IN ({marks})", tuple(ids)):
                out[r["user_id"]] = {"show_presence": bool(r["show_presence"]), "read_receipts": bool(r["read_receipts"])}
        return out

    def set_settings(self, uid: str, *, show_presence=None, read_receipts=None) -> dict:
        """Both switches are the person's own and mutual (docs/platform/messaging.md §7.2)."""
        cur = self.settings(uid)
        new = {"show_presence": cur["show_presence"] if show_presence is None else bool(show_presence),
               "read_receipts": cur["read_receipts"] if read_receipts is None else bool(read_receipts)}
        if new != cur:
            self.store.execute("INSERT INTO messaging_settings (user_id, show_presence, read_receipts, updated_at) VALUES (?,?,?,?) "
                               "ON CONFLICT(user_id) DO UPDATE SET show_presence=excluded.show_presence, read_receipts=excluded.read_receipts, updated_at=excluded.updated_at",
                               (uid, 1 if new["show_presence"] else 0, 1 if new["read_receipts"] else 0, now()))
            if self.presence is not None and not new["show_presence"]:
                self.presence.forget(uid)
        return new

    # ------------------------------------------------------------------ blocking (CI-78)
    def _blocked(self, a: str, b: str) -> tuple[bool, bool]:
        """(`a` blocked `b`, `b` blocked `a`)."""
        rows = self.store.all("SELECT user_id FROM message_blocks WHERE (user_id=? AND blocked_id=?) OR (user_id=? AND blocked_id=?)", (a, b, b, a))
        who = {r["user_id"] for r in rows}
        return a in who, b in who

    def has_blocked(self, uid: str, other: str | None) -> bool:
        """Did `uid` block `other`? (One direction: what decides the blocker's inbox and marks.)"""
        return bool(other) and self.store.one("SELECT 1 AS x FROM message_blocks WHERE user_id=? AND blocked_id=?", (uid, other)) is not None

    def blocked_ids(self, uid: str) -> set[str]:
        """The people `uid` blocked."""
        return {r["blocked_id"] for r in self.store.all("SELECT blocked_id FROM message_blocks WHERE user_id=?", (uid,))}

    def block_partners(self, uid: str) -> set[str]:
        """Everyone in a block with `uid`, either direction: presence, typing and read positions are
        not shown between them — both ways, so neither can watch the other."""
        rows = self.store.all("SELECT blocked_id AS u FROM message_blocks WHERE user_id=? UNION SELECT user_id AS u FROM message_blocks WHERE blocked_id=?", (uid, uid))
        return {r["u"] for r in rows}

    def _share_organization(self, a: str, b: str) -> bool:
        return self.store.one("SELECT 1 AS x FROM organization_memberships m1 JOIN organization_memberships m2 ON m2.organization_id=m1.organization_id "
                              "JOIN organizations o ON o.id=m1.organization_id JOIN users u ON u.id=m2.user_id JOIN users me ON me.id=m1.user_id "
                              "WHERE m1.user_id=? AND m2.user_id=? AND o.status='active' AND u.status='active' AND u.deleted_at IS NULL "
                              "AND me.status='active' AND me.deleted_at IS NULL LIMIT 1", (a, b)) is not None

    def _dm_ids_between(self, a: str, b: str) -> list[str]:
        lo, hi = min(a, b), max(a, b)
        return [r["id"] for r in self.store.all("SELECT id FROM conversations WHERE kind='dm' AND dm_key LIKE ? ESCAPE '\\'",
                                                 (f"%:{lo}:{hi}".replace("_", "\\_"),))]

    def _dm_peer(self, conv: dict, uid: str) -> str | None:
        if conv.get("kind") != "dm" or not conv.get("dm_key"):
            return None
        a, b = conv["dm_key"].rsplit(":", 2)[1:]
        return b if a == uid else a

    def _guard_dm(self, conv: dict, uid: str) -> None:
        """No sending in a direct message while either side has blocked the other. The blocker is
        told plainly; the blocked person gets the neutral sentence and nothing else."""
        peer = self._dm_peer(conv, uid)
        if not peer:
            return
        mine, theirs = self._blocked(uid, peer)
        if mine:
            raise ServiceError(403, "blocked", BLOCKED_BY_ME, {"by_me": True})
        if theirs:
            raise ServiceError(403, "blocked", BLOCKED_NEUTRAL)

    def blocks(self, uid: str) -> list[dict]:
        rows = self.store.all("SELECT blocked_id, created_at FROM message_blocks WHERE user_id=? ORDER BY created_at DESC", (uid,))
        return [{"user_id": r["blocked_id"], "name": self._name(r["blocked_id"]), "created_at": r["created_at"]} for r in rows]

    def block(self, uid: str, other: str) -> tuple[dict, bool]:
        """Block `other` (someone who shares an active organization with `uid`). Idempotent;
        returns (entry, created). The blocker's own devices hear about their direct messages
        with that person; the blocked person hears nothing."""
        from .messaging_bridge import is_app_account
        other = str(other or "")
        if other == uid:
            raise ServiceError(400, "invalid_field", "不能屏蔽自己", {"field": "user_id"})
        if is_app_account(self.store, other):
            raise ServiceError(400, "invalid_field", "应用发出的消息不能屏蔽；可以静音它所在的会话", {"field": "user_id"})
        if not other or not self._share_organization(uid, other):
            raise ServiceError(404, "no_user", "找不到这个成员；只能屏蔽同一组织的成员")
        created = bool(self.store.execute("INSERT OR IGNORE INTO message_blocks (user_id, blocked_id, created_at) VALUES (?,?,?)", (uid, other, now())))
        row = self.store.one("SELECT created_at FROM message_blocks WHERE user_id=? AND blocked_id=?", (uid, other))
        if created:
            for cid in self._dm_ids_between(uid, other):
                self.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return {"user_id": other, "name": self._name(other), "blocked": True, "created_at": row["created_at"]}, created

    def unblock(self, uid: str, other: str) -> tuple[dict, bool]:
        removed = bool(self.store.execute("DELETE FROM message_blocks WHERE user_id=? AND blocked_id=?", (uid, str(other or ""))))
        if removed:
            for cid in self._dm_ids_between(uid, other):
                self.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return {"user_id": other, "blocked": False}, removed

    def visible_conversation_ids(self, uid: str) -> list[str]:
        """The conversations `uid` may see right now: active memberships in organizations they
        are still a member of. The stream compares this at every heartbeat with what the tab
        knew, so a membership closed by another process (organization removal on the account
        service, a moderation decision) reaches the open tab within one tick."""
        rows = self.store.all("SELECT m.conversation_id, c.organization_id FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id "
                              "WHERE m.user_id=? AND m.left_at IS NULL", (uid,))
        orgs: dict[str, bool] = {}
        out = []
        for r in rows:
            oid = r["organization_id"]
            if oid not in orgs:
                orgs[oid] = bool(current_membership(self.store, oid, uid))
            if orgs[oid]:
                out.append(r["conversation_id"])
        return out

    def membership_closed_at(self, cid: str, uid: str) -> float | None:
        row = self.store.one("SELECT left_at FROM conversation_members WHERE conversation_id=? AND user_id=?", (cid, uid))
        return row["left_at"] if row else None

    def co_members(self, uid: str) -> list[str]:
        """Everyone who shares an active conversation with `uid` — the audience of that person's
        presence changes. Organization membership is re-checked per conversation elsewhere; a
        closed membership is already out of this set."""
        rows = self.store.all("SELECT DISTINCT m2.user_id AS u FROM conversation_members m1 JOIN conversation_members m2 ON m2.conversation_id=m1.conversation_id "
                              "WHERE m1.user_id=? AND m1.left_at IS NULL AND m2.left_at IS NULL AND m2.user_id<>?", (uid, uid))
        return [r["u"] for r in rows]

    def presence_audience(self, uid: str) -> list[str]:
        """Co-members who may see `uid`'s presence: none when `uid` hides theirs, and only those
        who show their own (mutual)."""
        if self.presence is None or not self.settings(uid)["show_presence"]:
            return []
        partners = self.block_partners(uid)
        peers = [p for p in self.co_members(uid) if p not in partners]
        st = self._settings_for(peers)
        return [p for p in peers if st[p]["show_presence"]]

    def presence_of(self, viewer: str, ids: list[str], settings: dict[str, dict] | None = None,
                    partners: set[str] | None = None) -> dict[str, str | None]:
        """Presence as `viewer` may see it: `online|away|offline`, or None when it is not shown
        (this process has no registry, the viewer hides theirs, that person hides theirs, or one
        of the two blocked the other)."""
        ids = list(dict.fromkeys(ids))
        if self.presence is None:
            return {u: None for u in ids}
        st = settings if settings is not None else self._settings_for([viewer, *ids])
        if not st[viewer]["show_presence"]:
            return {u: None for u in ids}
        partners = partners if partners is not None else self.block_partners(viewer)
        states = self.presence.states(ids)
        return {u: (states[u] if st[u]["show_presence"] and u not in partners else None) for u in ids}

    def typing_targets(self, cid: str, uid: str) -> list[str]:
        """Who a typing hint from `uid` in `cid` goes to: the other active members who show
        presence and are not in a block with `uid` — and nobody when `uid` hides theirs (typing
        is a presence signal)."""
        self._member(cid, uid)
        if not self.settings(uid)["show_presence"]:
            return []
        partners = self.block_partners(uid)
        others = [u for u in self._active_user_ids(cid) if u != uid and u not in partners]
        st = self._settings_for(others)
        return [u for u in others if st[u]["show_presence"]]

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
        if not self.store.one("SELECT 1 AS x FROM conversations WHERE dm_key=?", (key,)):
            # no new direct message while either side has blocked the other (CI-78); an existing
            # one still opens — its history is the reader's — but nothing more can be sent in it
            self._guard_dm({"kind": "dm", "dm_key": key}, uid)
        ts = now()
        with self.store.tx() as t:
            conv = t.execute("SELECT * FROM conversations WHERE dm_key=?", (key,)).fetchone()
            if conv:
                conv = dict(conv)
                for who in (uid, other):
                    m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=?", (conv["id"], who)).fetchone()
                    if m is None:
                        t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify,updated_at) VALUES (?,?,'member',?,?,'all',?)",
                                  (conv["id"], who, ts, conv["last_seq"], ts))
                    elif m["left_at"] is not None:
                        # a person who left the organization and came back sees the DM from now on
                        t.execute("UPDATE conversation_members SET left_at=NULL, left_seq=NULL, joined_at=?, joined_seq=?, last_read_seq=?, updated_at=? WHERE conversation_id=? AND user_id=?",
                                  (ts, conv["last_seq"] + 1, conv["last_seq"], ts, conv["id"], who))
                return self.get(conv["id"], uid)
            cid = new_id("cv")
            t.execute("INSERT INTO conversations (id,organization_id,kind,dm_key,created_by,created_at,updated_at) VALUES (?,?,'dm',?,?,?,?)",
                      (cid, oid, key, uid, ts, ts))
            for who in (uid, other):
                t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify,updated_at) VALUES (?,?,'member',?,0,'all',?)", (cid, who, ts, ts))
        self.publish([other], {"type": "conversation", "conversation_id": cid, "change": "added"})
        return self.get(cid, uid)

    def create_group(self, oid: str, uid: str, *, name: str, member_ids: list[str], description: str | None = None,
                     workspace_id: str | None = None) -> dict:
        self._org_member(oid, uid)
        name = (name or "").strip()
        if not 1 <= len(name) <= MAX_NAME:
            raise ServiceError(400, "invalid_field", f"群名称需要 1–{MAX_NAME} 个字符", {"field": "name", "limit": MAX_NAME})
        description = (description or "").strip()[:MAX_DESCRIPTION] or None
        others = []
        for mid in dict.fromkeys(member_ids or []):
            if mid != uid:
                self._org_member(oid, mid)
                others.append(mid)
        if len(others) + 1 > MAX_GROUP_MEMBERS:
            raise ServiceError(400, "too_many_members", f"群成员最多 {MAX_GROUP_MEMBERS} 人", {"limit": MAX_GROUP_MEMBERS})
        if workspace_id:
            ws = self.ident.get_workspace(workspace_id)
            if not ws or ws.get("organization_id") != oid:
                raise ServiceError(400, "bad_workspace", "工作空间不属于这个组织", {"field": "workspace_id"})
        ts = now()
        cid = new_id("cv")
        with self.store.tx() as t:
            t.execute("INSERT INTO conversations (id,organization_id,kind,workspace_id,name,description,created_by,created_at,updated_at) VALUES (?,?,'group',?,?,?,?,?,?)",
                      (cid, oid, workspace_id, name, description, uid, ts, ts))
            t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify,updated_at) VALUES (?,?,'owner',?,0,'all',?)", (cid, uid, ts, ts))
            for mid in others:
                t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,notify,added_by,updated_at) VALUES (?,?,'member',?,0,'mentions',?,?)", (cid, mid, ts, uid, ts))
            self._system(t, cid, uid, {"event": "created", "name": name, "added": others})
        self.publish(others, {"type": "conversation", "conversation_id": cid, "change": "added"})
        return self.get(cid, uid)

    def update_group(self, cid: str, uid: str, *, name=None, description=None) -> dict:
        conv, _ = self._manager(cid, uid)
        fields, args = [], []
        if name is not None:
            name = str(name).strip()
            if not 1 <= len(name) <= MAX_NAME:
                raise ServiceError(400, "invalid_field", f"群名称需要 1–{MAX_NAME} 个字符", {"field": "name", "limit": MAX_NAME})
            fields.append("name=?"); args.append(name)
        if description is not None:
            if len(str(description).strip()) > MAX_DESCRIPTION:
                raise ServiceError(400, "invalid_field", f"群描述最多 {MAX_DESCRIPTION} 个字符", {"field": "description", "limit": MAX_DESCRIPTION})
            fields.append("description=?"); args.append(str(description).strip() or None)
        if fields:
            with self.store.tx() as t:
                t.execute(f"UPDATE conversations SET {', '.join(fields)}, updated_at=? WHERE id=?", (*args, now(), cid))
                if name is not None:
                    self._system(t, cid, uid, {"event": "renamed", "name": name})
                if description is not None:
                    self._system(t, cid, uid, {"event": "description", "cleared": args[-1] is None})
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

    def set_avatar(self, cid: str, uid: str, image_base64: str | None) -> dict:
        """A group's avatar, set (`image_base64`) or removed (None) by its owner/admins. The image
        is held to the platform's avatar rules (`avatar_image.canonical_avatar`: the PNG is sniffed,
        bounded and rebuilt; anything else is 400 `bad_avatar`) and the change leaves a system row
        「X 更换了群头像 / 移除了群头像」. Checked in this order: membership (404), role (403),
        archive (409), then the bytes — a stranger learns nothing from a bad image."""
        conv, _ = self._manager(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "已归档的群不能修改头像")
        image = None
        if image_base64 is not None:
            from .avatar_image import canonical_avatar
            image = canonical_avatar(image_base64)
        ts = now()
        with self.store.tx() as t:
            if image is None:
                if not t.execute("DELETE FROM conversation_avatars WHERE conversation_id=?", (cid,)).rowcount:
                    raise ServiceError(404, "no_avatar", "这个群还没有头像")
            else:
                t.execute("INSERT INTO conversation_avatars (conversation_id, image, updated_by, updated_at) VALUES (?,?,?,?) "
                          "ON CONFLICT(conversation_id) DO UPDATE SET image=excluded.image, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                          (cid, image, uid, ts))
            self._system(t, cid, uid, {"event": "avatar", "cleared": image is None})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def avatar_image(self, cid: str, uid: str) -> dict:
        """The group's avatar bytes for a current member (404 for anyone else, and when there is none)."""
        conv, _ = self._member(cid, uid)
        row = self.store.one("SELECT image, updated_at FROM conversation_avatars WHERE conversation_id=?", (cid,)) if conv["kind"] == "group" else None
        if not row:
            raise ServiceError(404, "no_avatar", "这个群还没有头像")
        return {"image": bytes(row["image"]), "updated_at": row["updated_at"]}

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

    def add_members(self, cid: str, uid: str, member_ids: list[str], *, history_from: str = "join") -> dict:
        """Add members (owner/admin). `history_from` is the adder's choice for *these* members:
        `join` (default) shows them what comes next; `all` shares the whole history — recorded on
        each membership (`history_from`) and said in the system row so the exception is visible."""
        if history_from not in HISTORY_FROM:
            raise ServiceError(400, "invalid_field", "history_from 只能是 join 或 all", {"field": "history_from", "allowed": list(HISTORY_FROM)})
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
                    raise ServiceError(400, "too_many_members", f"群成员最多 {MAX_GROUP_MEMBERS} 人", {"limit": MAX_GROUP_MEMBERS})
                last = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()["last_seq"]
                # history starts with the first message *after* the join (the system notice of
                # the join itself); everything up to `last` stays invisible to this member —
                # unless the adder shared the whole history (`all`: floor 0)
                floor = 0 if history_from == "all" else last + 1
                hist = "all" if history_from == "all" else None
                # the read cursor starts at the join notice itself (seq last+1, written below in
                # this transaction): a newcomer has nothing unread, whichever history they see
                if m:      # rejoining: history starts again from now (or from the beginning, if shared)
                    t.execute("UPDATE conversation_members SET left_at=NULL, left_seq=NULL, role='member', joined_at=?, joined_seq=?, last_read_seq=?, added_by=?, history_from=?, marked_unread=0, updated_at=? WHERE conversation_id=? AND user_id=?",
                              (ts, floor, last + 1, uid, hist, ts, cid, mid))
                else:
                    t.execute("INSERT INTO conversation_members (conversation_id,user_id,role,joined_at,joined_seq,last_read_seq,notify,added_by,history_from,updated_at) VALUES (?,?,'member',?,?,?,'mentions',?,?,?)",
                              (cid, mid, ts, floor, last + 1, uid, hist, ts))
                added.append(mid); count += 1
            if added:
                ev = {"event": "added", "added": added}
                if history_from == "all":
                    ev["history"] = "all"
                self._system(t, cid, uid, ev)
        if added:
            self.publish(added, {"type": "conversation", "conversation_id": cid, "change": "added"})
            self.publish([u for u in self._active_user_ids(cid) if u not in added], {"type": "conversation", "conversation_id": cid, "change": "updated"})
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
            raise ServiceError(409, "not_a_group", "私聊不能退出；可以设为静音，或屏蔽对方")
        with self.store.tx() as t:
            others = t.execute("SELECT COUNT(*) AS n FROM conversation_members WHERE conversation_id=? AND left_at IS NULL AND user_id<>?", (cid, uid)).fetchone()["n"]
            if me["role"] == "owner" and others:
                raise ServiceError(409, "owner_must_transfer", "群主退出前请先把群主交接给其他成员，或归档群聊")
            self._close(t, cid, uid)
            self._system(t, cid, uid, {"event": "left"})
            if not others:
                t.execute("UPDATE conversations SET archived_at=COALESCE(archived_at,?), updated_at=? WHERE id=?", (now(), now(), cid))
        self.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "removed"})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})

    def transfer(self, cid: str, uid: str, target: str) -> dict:
        self._manager(cid, uid, owner=True)
        with self.store.tx() as t:
            m = t.execute("SELECT * FROM conversation_members WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (cid, target)).fetchone()
            if not m or target == uid:
                raise ServiceError(404, "no_member", "该成员不在群里")
            t.execute("UPDATE conversation_members SET role='owner', updated_at=? WHERE conversation_id=? AND user_id=?", (now(), cid, target))
            t.execute("UPDATE conversation_members SET role='admin', updated_at=? WHERE conversation_id=? AND user_id=?", (now(), cid, uid))
            self._system(t, cid, uid, {"event": "transferred", "to": target})
        self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def set_role(self, cid: str, uid: str, target: str, role: str) -> dict:
        self._manager(cid, uid, owner=True)
        if role not in ("admin", "member") or target == uid:
            raise ServiceError(400, "invalid_field", "角色只能是 admin 或 member", {"field": "role"})
        n = self.store.execute("UPDATE conversation_members SET role=?, updated_at=? WHERE conversation_id=? AND user_id=? AND left_at IS NULL AND role<>'owner'", (role, now(), cid, target))
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

    def set_preferences(self, cid: str, uid: str, *, muted=None, pinned=None, notify=None, muted_until=None, clear_muted_until: bool = False) -> dict:
        """Per-membership switches. `muted_until` (a Unix time ahead of now) silences the
        conversation for a while — unread badge, inbox items and digests skip it until then;
        `muted` is the standing switch. Passing `muted_until: null` clears the timer."""
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
        if clear_muted_until:
            fields.append("muted_until=NULL")
        elif muted_until is not None:
            try:
                until = float(muted_until)
            except (TypeError, ValueError):
                raise ServiceError(400, "invalid_field", "muted_until 必须是时间戳（秒）", {"field": "muted_until"}) from None
            if not now() < until <= now() + 366 * 86400:
                raise ServiceError(400, "invalid_field", "muted_until 需要是未来一年内的时间", {"field": "muted_until"})
            fields.append("muted_until=?"); args.append(until)
        if fields:
            self.store.execute(f"UPDATE conversation_members SET {', '.join(fields)}, updated_at=? WHERE conversation_id=? AND user_id=?", (*args, now(), cid, uid))
            self.publish([uid], {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return self.get(cid, uid)

    def _close(self, t, cid: str, uid: str) -> None:
        last = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()["last_seq"]
        t.execute("UPDATE conversation_members SET left_at=?, left_seq=?, updated_at=? WHERE conversation_id=? AND user_id=? AND left_at IS NULL", (now(), last, now(), cid, uid))

    def withdraw_from_organization(self, oid: str, uid: str) -> int:
        """Called when someone leaves or is removed from an organization: every conversation of
        that organization closes for them at once — lists, history, streams and attachments — and
        a group they owned passes to the next person (see `depart`)."""
        n = self.depart(uid, organization_id=oid, reason="left_organization")
        if n:
            self.publish([uid], {"type": "conversation", "change": "withdrawn", "organization_id": oid})
        return n

    def depart(self, uid: str, *, organization_id: str | None = None, reason: str = "account_deleted") -> int:
        """`uid` leaves every conversation (of one organization, or all of them) without anyone
        acting: an account that was deleted, a person who left the organization. A group they
        owned is handed to the earliest-joined remaining admin, else the earliest-joined remaining
        member — a group is never left without an owner nobody can replace — with the hand-over and
        the departure written as system lines everyone can read; a group left with nobody is
        archived. A direct message closes quietly: the other side keeps the history. Idempotent:
        only active memberships are touched."""
        sql = ("SELECT m.conversation_id, m.role, c.kind FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id "
               "WHERE m.user_id=? AND m.left_at IS NULL")
        args: list = [uid]
        if organization_id is not None:
            sql += " AND c.organization_id=?"; args.append(organization_id)
        changed: list[str] = []
        with self.store.tx() as t:
            for r in t.execute(sql, tuple(args)).fetchall():
                cid = r["conversation_id"]
                if r["kind"] == "group":
                    others = t.execute("SELECT user_id, role FROM conversation_members WHERE conversation_id=? AND left_at IS NULL AND user_id<>? "
                                       "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined_at, user_id", (cid, uid)).fetchall()
                    if r["role"] == "owner" and others:
                        heir = others[0]["user_id"]
                        t.execute("UPDATE conversation_members SET role='owner', updated_at=? WHERE conversation_id=? AND user_id=?", (now(), cid, heir))
                        self._system(t, cid, uid, {"event": "transferred", "to": heir, "reason": reason})
                    self._close(t, cid, uid)
                    self._system(t, cid, uid, {"event": "left", "reason": reason})
                    if not others:
                        t.execute("UPDATE conversations SET archived_at=COALESCE(archived_at,?), updated_at=? WHERE id=?", (now(), now(), cid))
                else:
                    self._close(t, cid, uid)
                changed.append(cid)
        for cid in changed:
            self.publish(self._active_user_ids(cid), {"type": "conversation", "conversation_id": cid, "change": "updated"})
        return len(changed)

    # ------------------------------------------------------------------ reading
    def get(self, cid: str, uid: str) -> dict:
        conv, me = self._member(cid, uid)
        return self._conv_out(conv, me, uid)

    def unread_mentions(self, uid: str, cids: list[str] | None = None) -> dict[str, int]:
        """Per conversation: unread messages that mention `uid` — after the reader's floor
        (max(last_read_seq, joined_seq), the same floor as `unread`), still there (not recalled,
        removed or deleted), not deleted by this reader, and not from someone this reader blocked
        (their messages stay visible in a group, but raise no @ mark). One grouped query."""
        sql = ("SELECT x.conversation_id AS cid, COUNT(*) AS n FROM conversation_members m JOIN messages x ON x.conversation_id=m.conversation_id "
               "WHERE m.user_id=? AND m.left_at IS NULL AND x.seq>MAX(m.last_read_seq, m.joined_seq) AND x.kind='text' AND x.deleted_at IS NULL "
               "AND x.mentions IS NOT NULL AND instr(x.mentions, ?)>0 AND x.sender_id<>m.user_id "
               "AND NOT EXISTS (SELECT 1 FROM message_hides h WHERE h.message_id=x.id AND h.user_id=m.user_id) "
               "AND NOT EXISTS (SELECT 1 FROM message_blocks b WHERE b.user_id=m.user_id AND b.blocked_id=x.sender_id)")
        args: list = [uid, json.dumps(uid)]
        if cids is not None:
            if not cids:
                return {}
            sql += f" AND m.conversation_id IN ({','.join('?' for _ in cids)})"
            args += list(cids)
        return {r["cid"]: r["n"] for r in self.store.all(sql + " GROUP BY x.conversation_id", tuple(args))}

    def _me_out(self, me: dict) -> dict:
        return {"role": me["role"], "joined_seq": me["joined_seq"], "last_read_seq": me["last_read_seq"], "muted": bool(me["muted"]),
                "muted_until": me.get("muted_until"), "marked_unread": bool(me.get("marked_unread")), "pinned": bool(me["pinned"]),
                "notify": me["notify"], "history_from": me.get("history_from") or "join"}

    def _read_by(self, conv: dict, uid: str, members: list[dict], settings: dict[str, dict], partners: set[str] | None = None) -> list[dict] | None:
        """Other members' read positions, for a viewer who turned read receipts on, listing only
        members who turned theirs on and are not in a block with the viewer. None means "not
        shown" (the viewer's switch is off), so the shell never confuses "nobody read it" with
        "you cannot see who did"."""
        if not settings.get(uid, {}).get("read_receipts"):
            return None
        partners = partners if partners is not None else self.block_partners(uid)
        return [{"user_id": m["user_id"], "last_read_seq": m["last_read_seq"], "at": m.get("last_read_at")}
                for m in members if m["user_id"] != uid and m["user_id"] not in partners and settings.get(m["user_id"], {}).get("read_receipts")]

    def _conv_out(self, conv: dict, me: dict, uid: str, ctx: dict | None = None) -> dict:
        """One conversation as `uid` sees it. `ctx` carries what a list computes once for all rows
        (`partners`, `mine`: the reader's blocks; `mentions`: unread mentions per conversation)."""
        members = self.store.all("SELECT m.user_id, m.role, m.joined_at, m.last_read_seq, m.last_read_at, u.display_name, u.email FROM conversation_members m JOIN users u ON u.id=m.user_id "
                                 "WHERE m.conversation_id=? AND m.left_at IS NULL ORDER BY m.joined_at", (conv["id"],))
        peer = next((m for m in members if m["user_id"] != uid), None) if conv["kind"] == "dm" else None
        unread = max(0, conv["last_seq"] - max(me["last_read_seq"], me["joined_seq"]))
        last = self.store.one("SELECT * FROM messages WHERE conversation_id=? AND seq=?", (conv["id"], conv["last_seq"])) if conv["last_seq"] else None
        ids = [m["user_id"] for m in members]
        settings = self._settings_for([uid, *ids])
        partners = ctx["partners"] if ctx and "partners" in ctx else self.block_partners(uid)
        pres = self.presence_of(uid, ids, settings, partners)
        if ctx and "mentions" in ctx:
            mentions = ctx["mentions"].get(conv["id"], 0)
        else:
            mentions = self.unread_mentions(uid, [conv["id"]]).get(conv["id"], 0) if unread else 0
        blocked_by_me = False
        if peer:
            blocked_by_me = peer["user_id"] in ctx["mine"] if ctx and "mine" in ctx else self.has_blocked(uid, peer["user_id"])
        avatar = self.store.one("SELECT updated_at FROM conversation_avatars WHERE conversation_id=?", (conv["id"],)) if conv["kind"] == "group" else None
        return {"id": conv["id"], "organization_id": conv["organization_id"], "kind": conv["kind"], "workspace_id": conv["workspace_id"],
                "name": conv["name"] if conv["kind"] == "group" else (peer["display_name"] or peer["email"].split("@")[0] if peer else "—"),
                "description": conv["description"], "archived": bool(conv["archived_at"]), "created_by": conv["created_by"],
                "announcement": conv.get("announcement") or None, "announcement_at": conv.get("announcement_at"),
                "last_seq": conv["last_seq"], "last_message_at": conv["last_message_at"], "updated_at": conv["updated_at"],
                "me": self._me_out(me),
                "unread": unread, "unread_mentions": mentions,
                "peer": ({"user_id": peer["user_id"], "name": peer["display_name"] or peer["email"].split("@")[0], "presence": pres.get(peer["user_id"]),
                          "blocked_by_me": blocked_by_me} if peer else None),
                "members": [{"user_id": m["user_id"], "name": m["display_name"] or m["email"].split("@")[0], "role": m["role"], "presence": pres.get(m["user_id"])} for m in members],
                "member_count": len(members),
                "avatar": {"updated_at": avatar["updated_at"]} if avatar else None,
                "read_by": self._read_by(conv, uid, members, settings, partners),
                "last_message": self._preview(last, me, uid) if last else None}

    def _preview(self, msg: dict, me: dict, uid: str) -> dict | None:
        if msg["seq"] < me["joined_seq"]:
            return None
        hidden = bool(self.store.one("SELECT 1 AS x FROM message_hides WHERE message_id=? AND user_id=?", (msg["id"], uid)))
        gone = bool(msg["deleted_at"]) or hidden
        return {"seq": msg["seq"], "sender_id": msg["sender_id"], "sender": self._name(msg["sender_id"]), "kind": msg["kind"],
                "body": "" if gone else msg["body"][:120], "deleted": bool(msg["deleted_at"]), "recalled": bool(msg.get("recalled_at")),
                "hidden": hidden, "created_at": msg["created_at"], "attachment": bool(msg["attachment_id"]) and not gone}

    def list_conversations(self, uid: str, *, organization_id: str | None = None) -> list[dict]:
        where, args = "m.user_id=? AND m.left_at IS NULL", [uid]
        if organization_id:
            where += " AND c.organization_id=?"; args.append(organization_id)
        rows = self.store.all(f"SELECT c.*, m.role AS m_role, m.joined_seq AS m_joined_seq, m.last_read_seq AS m_last_read_seq, m.muted AS m_muted, "
                              f"m.pinned AS m_pinned, m.notify AS m_notify, m.muted_until AS m_muted_until, m.marked_unread AS m_marked_unread, "
                              f"m.history_from AS m_history_from, m.last_read_at AS m_last_read_at FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id "
                              f"WHERE {where} ORDER BY m.pinned DESC, COALESCE(c.last_message_at, c.created_at) DESC LIMIT 500", tuple(args))
        out = []
        ctx = {"partners": self.block_partners(uid), "mine": self.blocked_ids(uid), "mentions": self.unread_mentions(uid)}
        seen: dict[str, bool] = {}
        for r in rows:
            oid = r["organization_id"]
            if oid not in seen:
                seen[oid] = bool(current_membership(self.store, oid, uid))
            if not seen[oid]:
                continue                                           # organization access gone: not listed
            me = {k[2:]: r[k] for k in r.keys() if k.startswith("m_")}
            conv = {k: r[k] for k in r.keys() if not k.startswith("m_")}
            out.append(self._conv_out(conv, me, uid, ctx))
        return out

    def unread_total(self, uid: str) -> dict:
        """The badge: unread messages across the caller's conversations, silent ones excluded. A
        conversation the reader marked unread counts as one when it has no unread message."""
        rows = self.store.all("SELECT c.organization_id, c.last_seq, m.last_read_seq, m.joined_seq, m.muted, m.muted_until, m.marked_unread FROM conversations c JOIN conversation_members m ON m.conversation_id=c.id "
                              "WHERE m.user_id=? AND m.left_at IS NULL", (uid,))
        total, conversations = 0, 0
        seen_orgs: dict[str, bool] = {}
        ts = now()
        for r in rows:
            if is_muted(r, ts):
                continue
            oid = r["organization_id"]
            if oid not in seen_orgs:
                seen_orgs[oid] = bool(current_membership(self.store, oid, uid))
            if not seen_orgs[oid]:
                continue
            n = max(0, r["last_seq"] - max(r["last_read_seq"], r["joined_seq"]))
            if not n and r.get("marked_unread"):
                n = 1
            if n:
                total += n; conversations += 1
        return {"unread": total, "conversations": conversations}

    def _message_out(self, m: dict, reactions: dict | None = None, names: dict | None = None, *, viewer: str | None = None,
                     source_visible: bool | None = None) -> dict:
        """`viewer` is who this rendering is for: read receipts, recall/forward state and a card
        sent by an application (CI-75) are rendered per viewer — the full card for someone who may
        open the object, a neutral one otherwise — and the card is left out when there is no viewer
        (an event fanned out to everyone)."""
        names = names if names is not None else {}
        if m["sender_id"] not in names:
            names[m["sender_id"]] = self._name(m["sender_id"])
        gone = bool(m["deleted_at"])
        out = {"id": m["id"], "conversation_id": m["conversation_id"], "seq": m["seq"], "sender_id": m["sender_id"], "sender": names[m["sender_id"]],
               "kind": m["kind"], "body": "" if gone else m["body"], "reply_to": m["reply_to"], "mentions": _load(m["mentions"]) or [],
               "edited_at": m["edited_at"], "deleted": gone, "recalled": bool(m.get("recalled_at")), "created_at": m["created_at"], "client_id": m["client_id"],
               "reactions": (reactions or {}).get(m["id"], []), "attachment": None, "forwarded_from": None, "removed_by": None}
        if m["kind"] == "system":
            out["system"] = _load(m["body"]) or {}
            out["body"] = ""
        elif m.get("recalled_at"):
            # the marker readers see in place of the message: 「X 撤回了一条消息」
            out["system"] = {"event": "recalled", "actor": m["sender_id"]}
        elif m.get("removed_by"):
            if m["removed_by"] not in names:
                names[m["removed_by"]] = self._name(m["removed_by"])
            out["removed_by"] = {"user_id": m["removed_by"], "name": names[m["removed_by"]]}
            out["system"] = {"event": "removed", "actor": m["removed_by"]}
        if m["attachment_id"] and not gone:
            a = self.store.one("SELECT id, filename, size, content_type, status, duration_ms FROM message_attachments WHERE id=?", (m["attachment_id"],))
            if a:
                out["attachment"] = self._attachment_out(a)
        if m.get("forwarded_from") and not gone:
            fwd = _load(m["forwarded_from"]) or {}
            if fwd.get("sender_id") and fwd["sender_id"] not in names:
                names[fwd["sender_id"]] = self._name(fwd["sender_id"])
            visible = source_visible
            if visible is None:
                visible = bool(viewer) and bool(self.store.one("SELECT 1 AS x FROM conversation_members WHERE conversation_id=? AND user_id=? AND left_at IS NULL",
                                                                (fwd.get("conversation_id"), viewer)))
            out["forwarded_from"] = {"sender": names.get(fwd.get("sender_id"), "—"), "conversation_id": fwd.get("conversation_id") if visible else None}
        if m["reply_to"]:
            r = self.store.one("SELECT id, sender_id, body, deleted_at, recalled_at, seq FROM messages WHERE id=? AND conversation_id=?", (m["reply_to"], m["conversation_id"]))
            if r:
                out["reply"] = {"id": r["id"], "seq": r["seq"], "sender": self._name(r["sender_id"]), "body": "" if r["deleted_at"] else r["body"][:160],
                                "deleted": bool(r["deleted_at"]), "recalled": bool(r.get("recalled_at"))}
        from .messaging_bridge import decorate_message
        decorate_message(self.store, self.ident, out, None if gone else viewer)   # sender_kind, and the viewer's card
        return out

    @staticmethod
    def _attachment_out(a: dict) -> dict:
        out = {"id": a["id"], "filename": a["filename"], "size": a["size"], "content_type": a["content_type"], "inline": a["content_type"] in INLINE_TYPES}
        if a.get("status") == "rejected":
            # A rescan found these bytes infected (CI-90): the row stays so the conversation reads
            # the same, but nothing is served or shown inline; the download answers 410.
            out["inline"] = False
            out["rejected"] = True
        if a["content_type"] in VOICE_TYPES:
            out["voice"] = True
            out["duration_ms"] = a.get("duration_ms")
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

    _NOT_HIDDEN = "NOT EXISTS (SELECT 1 FROM message_hides h WHERE h.message_id=x.id AND h.user_id=?)"

    def history(self, cid: str, uid: str, *, before_seq: int | None = None, after_seq: int | None = None, limit: int = PAGE) -> dict:
        """A page of messages the caller may see (seq ≥ their joined_seq, not hidden for them).
        `before_seq` pages backwards for scrolling up; `after_seq` fetches what arrived since a
        known point. `read_by` follows the read-receipt rule (None = not shown)."""
        conv, me = self._member(cid, uid)
        limit = max(1, min(int(limit or PAGE), PAGE_MAX))
        floor = me["joined_seq"]
        nh = self._NOT_HIDDEN
        if after_seq is not None:
            rows = self.store.all(f"SELECT * FROM messages x WHERE x.conversation_id=? AND x.seq>? AND x.seq>=? AND {nh} ORDER BY x.seq LIMIT ?", (cid, int(after_seq), floor, uid, limit))
            more = len(rows) == limit
        else:
            top = int(before_seq) if before_seq is not None else conv["last_seq"] + 1
            rows = self.store.all(f"SELECT * FROM messages x WHERE x.conversation_id=? AND x.seq<? AND x.seq>=? AND {nh} ORDER BY x.seq DESC LIMIT ?", (cid, top, floor, uid, limit))
            rows = list(reversed(rows))
            more = bool(rows) and rows[0]["seq"] > floor and self.store.one(f"SELECT 1 FROM messages x WHERE x.conversation_id=? AND x.seq<? AND x.seq>=? AND {nh}", (cid, rows[0]["seq"], floor, uid)) is not None
        reactions = self._reactions_for([r["id"] for r in rows])
        names: dict[str, str] = {}
        members = self.store.all("SELECT user_id, last_read_seq, last_read_at FROM conversation_members WHERE conversation_id=? AND left_at IS NULL", (cid,))
        settings = self._settings_for([uid, *[m["user_id"] for m in members]])
        return {"items": [self._message_out(dict(r), reactions, names, viewer=uid) for r in rows], "has_more": more,
                "floor": floor, "last_seq": conv["last_seq"], "last_read_seq": me["last_read_seq"],
                "read_by": self._read_by(conv, uid, members, settings)}

    # ------------------------------------------------------------------ search (CI-74: FTS5)
    def search_info(self) -> dict:
        """`{engine, tokenizer, min_chars}` for this database, read once per service instance."""
        if self._search_info is None:
            with self.store.conn() as c:
                self._search_info = fts.describe(c)
        return self._search_info

    def search(self, uid: str, q: str, *, organization_id: str | None = None, limit: int = 50, conversation_id: str | None = None,
               sender_id: str | None = None, has_attachment: bool | None = None, since: float | None = None, until: float | None = None) -> dict:
        """Text search over what the caller may read: their active memberships, at or after
        their joined_seq, not hidden for them, not recalled/removed. Through the FTS5 index when
        it can answer the query (see `messaging_search.usable_for`), else the bounded LIKE it
        always had. Each hit carries `cid`, `seq` and a `snippet` with highlight `ranges`."""
        q = (q or "").strip()
        if len(q) < 2:
            raise ServiceError(400, "invalid_field", "搜索至少需要 2 个字符", {"field": "q"})
        info = self.search_info()
        use_fts = fts.usable_for(info, q)
        where, args = ["m.user_id=?", "m.left_at IS NULL", "x.seq>=m.joined_seq", "x.kind='text'", "x.deleted_at IS NULL", self._NOT_HIDDEN], [uid, uid]
        if organization_id:
            where.append("c.organization_id=?"); args.append(organization_id)
        if conversation_id:
            where.append("x.conversation_id=?"); args.append(conversation_id)
        if sender_id:
            where.append("x.sender_id=?"); args.append(sender_id)
        if has_attachment is True:
            where.append("x.attachment_id IS NOT NULL")
        elif has_attachment is False:
            where.append("x.attachment_id IS NULL")
        if since is not None:
            where.append("x.created_at>=?"); args.append(float(since))
        if until is not None:
            where.append("x.created_at<=?"); args.append(float(until))
        limit = max(1, min(int(limit or 50), 100))
        if use_fts:
            sql = (f"SELECT x.*, c.organization_id AS c_org, {fts.snippet_sql()} AS c_snip FROM {fts.TABLE} JOIN messages x ON x.rowid={fts.TABLE}.rowid "
                   f"JOIN conversation_members m ON m.conversation_id=x.conversation_id JOIN conversations c ON c.id=x.conversation_id "
                   f"WHERE {fts.TABLE} MATCH ? AND {' AND '.join(where)} ORDER BY x.created_at DESC LIMIT ?")
            rows = self.store.all(sql, (fts.match_expression(q), *args, limit))
        else:
            where.append("x.body LIKE ? ESCAPE '\\'"); args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
            sql = (f"SELECT x.*, c.organization_id AS c_org, NULL AS c_snip FROM messages x JOIN conversation_members m ON m.conversation_id=x.conversation_id "
                   f"JOIN conversations c ON c.id=x.conversation_id WHERE {' AND '.join(where)} ORDER BY x.created_at DESC LIMIT ?")
            rows = self.store.all(sql, (*args, limit))
        out, names, seen = [], {}, {}
        for r in rows:
            oid = r["c_org"]
            if oid not in seen:
                seen[oid] = bool(current_membership(self.store, oid, uid))
            if not seen[oid]:
                continue
            item = self._message_out({k: r[k] for k in r.keys() if k not in ("c_org", "c_snip")}, {}, names, viewer=uid)
            if r["c_snip"] is not None:
                text, ranges = fts.split_snippet(r["c_snip"])
            else:
                text, ranges = self._like_snippet(item["body"], q)
            item.update({"cid": item["conversation_id"], "snippet": text, "ranges": ranges})
            out.append(item)
        return {"items": out, "engine": "fts5" if use_fts else "like", "tokenizer": info.get("tokenizer") if use_fts else None,
                "fallback": (info.get("engine") == "fts5" and not use_fts)}

    @staticmethod
    def _like_snippet(body: str, q: str, width: int = 60) -> tuple[str, list[list[int]]]:
        low, ql = body.lower(), q.lower()
        i = low.find(ql)
        if i < 0:
            return body[:width * 2], []
        start = max(0, i - width)
        end = min(len(body), i + len(q) + width)
        text = ("…" if start else "") + body[start:end] + ("…" if end < len(body) else "")
        off = i - start + (1 if start else 0)
        return text, [[off, off + len(q)]]

    # ------------------------------------------------------------------ writing
    def send(self, cid: str, uid: str, *, body: str, client_id: str | None = None, reply_to: str | None = None,
             attachment_id: str | None = None, mention_ids: list[str] | None = None) -> tuple[dict, bool]:
        """Store one message; returns (message, created). A repeated `client_id` from the same
        sender returns the stored message with created=False — the retry after a lost
        acknowledgement that must not become a duplicate."""
        conv, me = self._member(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "会话已归档，不能再发消息")
        self._guard_dm(conv, uid)
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
                    return self._message_out(dict(dup), self._reactions_for([dup["id"]]), viewer=uid), False
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
            t.execute("UPDATE conversation_members SET last_read_seq=?, last_read_at=?, marked_unread=0, updated_at=? WHERE conversation_id=? AND user_id=?", (seq, ts, ts, cid, uid))
            if attachment_id:
                t.execute("UPDATE message_attachments SET message_id=?, expires_at=NULL WHERE id=?", (mid_, attachment_id))
            stored = dict(t.execute("SELECT * FROM messages WHERE id=?", (mid_,)).fetchone())
        out = self._message_out(stored, viewer=uid)
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
        """Edit one's own message within `EDIT_WINDOW_S`; `edited_at` marks it, nobody is
        re-notified. Past the window: 403 `edit_window_closed`."""
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        if m["sender_id"] != uid or m["kind"] != "text" or m["deleted_at"] or m["seq"] < me["joined_seq"]:
            raise ServiceError(403, "forbidden", "只能编辑自己的消息")
        self._guard_dm(conv, uid)            # an edit puts new words in front of the other side
        if EDIT_WINDOW_S and now() - float(m["created_at"]) > EDIT_WINDOW_S:
            raise ServiceError(403, "edit_window_closed", f"发出超过 {EDIT_WINDOW_S // 60} 分钟的消息不能再编辑", {"edit_window_s": EDIT_WINDOW_S})
        body = (body or "").replace("\r\n", "\n").strip()
        if not body or len(body) > MAX_BODY:
            raise ServiceError(400, "invalid_field", f"消息需要 1–{MAX_BODY} 个字符", {"field": "body", "limit": MAX_BODY})
        self.store.execute("UPDATE messages SET body=?, edited_at=? WHERE id=?", (body, now(), mid))
        stored = dict(self.store.one("SELECT * FROM messages WHERE id=?", (mid,)))
        self._publish_updated(stored, self._reactions_for([mid]))
        return self._message_out(stored, self._reactions_for([mid]), viewer=uid)

    def recall(self, mid: str, uid: str) -> dict:
        """Recall one's own message for everyone within `RECALL_WINDOW_S`: body, attachment,
        reactions and search tokens go; the row stays with `recalled_at` (and `deleted_at`, so
        every existing "is it gone" check holds) and readers see 「X 撤回了一条消息」."""
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(m["conversation_id"], uid)
        if m["kind"] != "text" or m["seq"] < me["joined_seq"]:
            raise ServiceError(404, "no_message", "找不到该消息")
        if m["sender_id"] != uid:
            raise ServiceError(403, "forbidden", "只能撤回自己的消息")
        if m["deleted_at"]:
            raise ServiceError(409, "already_gone", "这条消息已经撤回或删除")
        if RECALL_WINDOW_S and now() - float(m["created_at"]) > RECALL_WINDOW_S:
            raise ServiceError(403, "recall_window_closed", f"发出超过 {RECALL_WINDOW_S} 秒的消息不能再撤回，仍可以「删除（仅自己）」", {"recall_window_s": RECALL_WINDOW_S})
        ts = now()
        with self.store.tx() as t:
            t.execute("UPDATE messages SET recalled_at=?, deleted_at=?, body='', attachment_id=NULL WHERE id=?", (ts, ts, mid))
            t.execute("DELETE FROM message_reactions WHERE message_id=?", (mid,))
            if m["attachment_id"]:
                t.execute("DELETE FROM message_attachments WHERE id=?", (m["attachment_id"],))
        stored = dict(self.store.one("SELECT * FROM messages WHERE id=?", (mid,)))
        self._publish_updated(stored)
        return self._message_out(stored, viewer=uid)

    def remove(self, mid: str, uid: str) -> dict:
        """A group owner/admin removes a message for everyone (no time limit); the marker names
        them: 「由管理员 X 删除」. Not for direct messages — there is no manager in a DM."""
        m = self.store.one("SELECT * FROM messages WHERE id=?", (mid,))
        if not m:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._manager(m["conversation_id"], uid)
        if m["kind"] != "text" or m["seq"] < me["joined_seq"]:
            raise ServiceError(404, "no_message", "找不到该消息")
        if m["deleted_at"]:
            raise ServiceError(409, "already_gone", "这条消息已经撤回或删除")
        ts = now()
        with self.store.tx() as t:
            t.execute("UPDATE messages SET removed_by=?, deleted_at=?, body='', attachment_id=NULL WHERE id=?", (uid, ts, mid))
            t.execute("DELETE FROM message_reactions WHERE message_id=?", (mid,))
            if m["attachment_id"]:
                t.execute("DELETE FROM message_attachments WHERE id=?", (m["attachment_id"],))
        stored = dict(self.store.one("SELECT * FROM messages WHERE id=?", (mid,)))
        self._publish_updated(stored)
        return self._message_out(stored, viewer=uid)

    def hide(self, mid: str, uid: str) -> dict:
        """Delete for me: the row disappears from this reader's history, search and previews and
        from nobody else's. Idempotent; undone by `unhide`."""
        row = self.store.one("SELECT conversation_id, seq FROM messages WHERE id=?", (mid,))
        if not row:
            raise ServiceError(404, "no_message", "找不到该消息")
        conv, me = self._member(row["conversation_id"], uid)
        if row["seq"] < me["joined_seq"]:
            raise ServiceError(404, "no_message", "找不到该消息")
        if self.store.execute("INSERT OR IGNORE INTO message_hides (message_id, user_id, created_at) VALUES (?,?,?)", (mid, uid, now())):
            self.publish([uid], {"type": "message_hidden", "conversation_id": row["conversation_id"], "message_id": mid, "seq": row["seq"], "hidden": True})
        return {"message_id": mid, "conversation_id": row["conversation_id"], "seq": row["seq"], "hidden": True}

    def unhide(self, mid: str, uid: str) -> dict:
        row = self.store.one("SELECT conversation_id, seq FROM messages WHERE id=?", (mid,))
        if not row:
            raise ServiceError(404, "no_message", "找不到该消息")
        self._member(row["conversation_id"], uid)
        self.store.execute("DELETE FROM message_hides WHERE message_id=? AND user_id=?", (mid, uid))
        self.publish([uid], {"type": "message_hidden", "conversation_id": row["conversation_id"], "message_id": mid, "seq": row["seq"], "hidden": False})
        return {"message_id": mid, "conversation_id": row["conversation_id"], "seq": row["seq"], "hidden": False}

    def delete(self, mid: str, uid: str) -> dict:
        """Kept as a name for callers of the old API: since CI-74 `DELETE` is *delete for me*."""
        return self.hide(mid, uid)

    def _publish_updated(self, stored: dict, reactions: dict | None = None) -> None:
        self._publish_message(stored["conversation_id"], stored, "message_updated", reactions)

    def _publish_message(self, cid: str, stored: dict, kind: str, reactions: dict | None = None) -> None:
        """Fan a message event out to the conversation's current members. A forwarded message
        names its source conversation only to recipients who are in it, so the payload is split
        in two when that matters."""
        recipients = sorted(self._active_user_ids(cid))
        fwd = _load(stored.get("forwarded_from")) or {}
        if fwd.get("conversation_id") and not stored.get("deleted_at"):
            src = set(self._active_user_ids(fwd["conversation_id"]))
            ins, outs = [u for u in recipients if u in src], [u for u in recipients if u not in src]
            if ins:
                self.publish(ins, {"type": kind, "conversation_id": cid, "message": self._message_out(stored, reactions, source_visible=True)})
            if outs:
                self.publish(outs, {"type": kind, "conversation_id": cid, "message": self._message_out(stored, reactions, source_visible=False)})
            return
        self.publish(recipients, {"type": kind, "conversation_id": cid, "message": self._message_out(stored, reactions)})

    def forward(self, mid: str, uid: str, targets: list[str], *, client_id: str | None = None) -> list[dict]:
        """Forward a message the caller can read to conversations the caller can write to
        (≤ `MAX_FORWARD_TARGETS`). The copy carries `forwarded_from`; an attachment is re-bound as
        a new row on the same stored bytes, only when the forwarder may read it; a Docs link in
        the body is a card per reader at read time, as everywhere. `client_id` makes the whole
        request idempotent (`{client_id}-{n}` per target)."""
        ids = list(dict.fromkeys(str(t) for t in (targets or []) if t))
        if not ids:
            raise ServiceError(400, "invalid_field", "请选择要转发到的会话", {"field": "conversation_ids"})
        if len(ids) > MAX_FORWARD_TARGETS:
            raise ServiceError(400, "too_many_targets", f"一次最多转发到 {MAX_FORWARD_TARGETS} 个会话", {"field": "conversation_ids", "limit": MAX_FORWARD_TARGETS})
        if client_id is not None and (not isinstance(client_id, str) or not 1 <= len(client_id) <= 60 or not re.fullmatch(r"[A-Za-z0-9_-]+", client_id)):
            raise ServiceError(400, "invalid_field", "client_id 无效", {"field": "client_id"})
        src, src_conv, me = self._visible_message(mid, uid)
        if src["kind"] != "text" or src["deleted_at"]:
            raise ServiceError(404, "no_message", "找不到该消息")
        att = None
        if src["attachment_id"]:
            att = self.attachment_for_download(src["attachment_id"], uid)      # readable by the forwarder, or 404
        # every target is checked before anything is written: a refusal leaves nothing half-done
        convs = []
        for cid in ids:
            conv, _ = self._member(cid, uid)
            if conv["archived_at"]:
                raise ServiceError(409, "archived", "目标会话已归档，不能再发消息", {"conversation_id": cid})
            self._guard_dm(conv, uid)
            convs.append(conv)
        origin = json.dumps({"message_id": src["id"], "conversation_id": src["conversation_id"], "sender_id": src["sender_id"]})
        results = []
        for n, conv in enumerate(convs, start=1):
            cid = conv["id"]
            per_client = f"{client_id}-{n}" if client_id else None
            ts = now()
            with self.store.tx() as t:
                if per_client:
                    dup = t.execute("SELECT * FROM messages WHERE conversation_id=? AND sender_id=? AND client_id=?", (cid, uid, per_client)).fetchone()
                    if dup:
                        results.append({"conversation_id": cid, "message": self._message_out(dict(dup), viewer=uid), "created": False})
                        continue
                row = t.execute("SELECT last_seq FROM conversations WHERE id=?", (cid,)).fetchone()
                seq = row["last_seq"] + 1
                new_mid = new_id("msg")
                new_aid = None
                if att:
                    new_aid = new_id("att")
                    t.execute("INSERT INTO message_attachments (id,conversation_id,message_id,uploader_id,filename,size,blob,content_type,scan,status,created_at,expires_at,duration_ms) "
                              "VALUES (?,?,?,?,?,?,?,?,?,'accepted',?,NULL,?)",
                              (new_aid, cid, new_mid, uid, att["filename"], att["size"], att["blob"], att["content_type"],
                               json.dumps(att["scan"], ensure_ascii=False) if isinstance(att.get("scan"), (dict, list)) else att.get("scan"), ts, att.get("duration_ms")))
                t.execute("INSERT INTO messages (id,conversation_id,seq,sender_id,client_id,kind,body,attachment_id,forwarded_from,created_at) VALUES (?,?,?,?,?,'text',?,?,?,?)",
                          (new_mid, cid, seq, uid, per_client, src["body"], new_aid, origin, ts))
                t.execute("UPDATE conversations SET last_seq=?, last_message_at=?, updated_at=? WHERE id=?", (seq, ts, ts, cid))
                t.execute("UPDATE conversation_members SET last_read_seq=?, last_read_at=?, marked_unread=0, updated_at=? WHERE conversation_id=? AND user_id=?", (seq, ts, ts, cid, uid))
                stored = dict(t.execute("SELECT * FROM messages WHERE id=?", (new_mid,)).fetchone())
            self._publish_message(cid, stored, "message")
            results.append({"conversation_id": cid, "message": self._message_out(stored, viewer=uid), "created": True})
        return results

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
            self._guard_dm(conv, uid)        # adding a reaction reaches the other side; taking one back does not
            self.store.execute("INSERT OR IGNORE INTO message_reactions (message_id,user_id,emoji,created_at) VALUES (?,?,?,?)", (mid, uid, emoji, now()))
        else:
            self.store.execute("DELETE FROM message_reactions WHERE message_id=? AND user_id=? AND emoji=?", (mid, uid, emoji))
        self._publish_updated(dict(m), self._reactions_for([mid]))
        return self._message_out(dict(m), self._reactions_for([mid]), viewer=uid)

    def mark_read(self, cid: str, uid: str, seq: int) -> dict:
        """Move the reader's own cursor forward (never back, never past `last_seq`). Any read
        clears a "marked unread" flag. Other devices of the reader get `read`; members who
        turned read receipts on get `receipt` — only if the reader turned theirs on too."""
        conv, me = self._member(cid, uid)
        target = max(me["last_read_seq"], min(int(seq), conv["last_seq"]))
        ts = now()
        moved = target != me["last_read_seq"]
        if moved or me.get("marked_unread"):
            self.store.execute("UPDATE conversation_members SET last_read_seq=?, last_read_at=CASE WHEN ?<>last_read_seq THEN ? ELSE COALESCE(last_read_at, ?) END, marked_unread=0, updated_at=? "
                               "WHERE conversation_id=? AND user_id=?", (target, target, ts, ts, ts, cid, uid))
            self.publish([uid], {"type": "read", "conversation_id": cid, "last_read_seq": target, "marked_unread": False})
        if moved:
            settings = self._settings_for(self._active_user_ids(cid))
            if settings.get(uid, {}).get("read_receipts"):
                partners = self.block_partners(uid)
                audience = [u for u, s in settings.items() if u != uid and s["read_receipts"] and u not in partners]
                if audience:
                    self.publish(audience, {"type": "receipt", "conversation_id": cid, "user_id": uid, "last_read_seq": target, "at": ts})
        return {"conversation_id": cid, "last_read_seq": target, "marked_unread": False, "unread": max(0, conv["last_seq"] - max(target, me["joined_seq"]))}

    def mark_unread(self, cid: str, uid: str) -> dict:
        """Flag the conversation as unread for this reader without moving the read cursor back:
        it shows in the badge (as one) and in the list until the next read."""
        conv, me = self._member(cid, uid)
        self.store.execute("UPDATE conversation_members SET marked_unread=1, updated_at=? WHERE conversation_id=? AND user_id=?", (now(), cid, uid))
        self.publish([uid], {"type": "read", "conversation_id": cid, "last_read_seq": me["last_read_seq"], "marked_unread": True})
        return {"conversation_id": cid, "last_read_seq": me["last_read_seq"], "marked_unread": True,
                "unread": max(1, conv["last_seq"] - max(me["last_read_seq"], me["joined_seq"]))}

    # ------------------------------------------------------------------ resync (CI-74)
    def sync(self, uid: str, since: float, *, limit: int = 500) -> dict:
        """Conversation-level changes since a cursor, for a client that reconnected: memberships
        of the caller whose conversation changed (a message, a system row, a membership change),
        or whose own row changed (read cursor, marked unread, mute…), conversations the caller
        left (or was removed from) since, and messages recalled/removed since in what they can
        see. `cursor` is the server time to hand back next time. Never message bodies: the client
        fetches history per conversation with `after=`."""
        since = float(since or 0)
        cursor = now()
        limit = max(1, min(int(limit or 500), 500))
        rows = self.store.all("SELECT c.*, m.role AS m_role, m.joined_seq AS m_joined_seq, m.last_read_seq AS m_last_read_seq, m.muted AS m_muted, m.pinned AS m_pinned, "
                              "m.notify AS m_notify, m.muted_until AS m_muted_until, m.marked_unread AS m_marked_unread, m.history_from AS m_history_from, m.last_read_at AS m_last_read_at "
                              "FROM conversation_members m JOIN conversations c ON c.id=m.conversation_id "
                              "WHERE m.user_id=? AND m.left_at IS NULL AND (c.updated_at>? OR COALESCE(m.updated_at,0)>? OR m.joined_at>?) ORDER BY c.updated_at DESC LIMIT ?",
                              (uid, since, since, since, limit))
        convs, seen = [], {}
        for r in rows:
            oid = r["organization_id"]
            if oid not in seen:
                seen[oid] = bool(current_membership(self.store, oid, uid))
            if not seen[oid]:
                continue
            me = {k[2:]: r[k] for k in r.keys() if k.startswith("m_")}
            convs.append({"id": r["id"], "kind": r["kind"], "last_seq": r["last_seq"], "last_message_at": r["last_message_at"], "updated_at": r["updated_at"],
                          "archived": bool(r["archived_at"]), "me": self._me_out(me),
                          "unread": max(0, r["last_seq"] - max(me["last_read_seq"], me["joined_seq"]))})
        mentions = self.unread_mentions(uid, [x["id"] for x in convs if x["unread"]])
        for x in convs:
            x["unread_mentions"] = mentions.get(x["id"], 0)
        left = [r["conversation_id"] for r in self.store.all("SELECT conversation_id FROM conversation_members WHERE user_id=? AND left_at>? ORDER BY left_at LIMIT ?", (uid, since, limit))]
        gone = self.store.all(f"SELECT x.id, x.conversation_id, x.seq, x.recalled_at, x.removed_by FROM messages x JOIN conversation_members m ON m.conversation_id=x.conversation_id "
                              f"WHERE m.user_id=? AND m.left_at IS NULL AND x.deleted_at>? AND x.seq>=m.joined_seq ORDER BY x.deleted_at LIMIT ?", (uid, since, limit))
        return {"cursor": cursor, "since": since, "conversations": convs, "left": left,
                "recalled": [{"id": g["id"], "conversation_id": g["conversation_id"], "seq": g["seq"],
                              "how": "recalled" if g["recalled_at"] else ("removed" if g["removed_by"] else "deleted")} for g in gone],
                "truncated": len(rows) >= limit or len(gone) >= limit}

    # ------------------------------------------------------------------ attachments
    def attach(self, cid: str, uid: str, *, filename: str, data: bytes, scan: dict, duration_ms: int | None = None) -> dict:
        """Store an accepted upload as an unbound attachment (bound by `send`, or swept after an
        hour). Voice (CI-74): a sniffed audio type may declare `duration_ms`, checked against
        `MAX_VOICE_MS`; voice files have their own, smaller size limit."""
        conv, _ = self._member(cid, uid)
        if conv["archived_at"]:
            raise ServiceError(409, "archived", "会话已归档")
        self._guard_dm(conv, uid)            # an upload only exists to be sent
        if not data:
            raise ServiceError(400, "empty_file", "文件是空的")
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ServiceError(413, "file_too_large", f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限", {"limit": MAX_ATTACHMENT_BYTES})
        ctype = sniff_content_type(data, filename)
        voice = ctype in VOICE_TYPES
        if voice and len(data) > MAX_VOICE_BYTES:
            raise ServiceError(413, "file_too_large", f"语音超过 {MAX_VOICE_BYTES // (1024 * 1024)} MB 上限", {"limit": MAX_VOICE_BYTES, "kind": "voice"})
        if duration_ms is not None:
            try:
                duration_ms = int(duration_ms)
            except (TypeError, ValueError):
                raise ServiceError(400, "invalid_field", "duration_ms 必须是整数毫秒", {"field": "duration_ms"}) from None
            if not voice:
                raise ServiceError(400, "invalid_field", "只有语音附件可以声明时长", {"field": "duration_ms"})
            if not 0 < duration_ms <= MAX_VOICE_MS:
                raise ServiceError(400, "invalid_field", f"语音时长需要在 1 毫秒到 {MAX_VOICE_MS // 60000} 分钟之间", {"field": "duration_ms", "limit": MAX_VOICE_MS})
        import hashlib
        if quarantine.verdict(self.store, hashlib.sha256(data).hexdigest()):
            # Bytes under a standing quarantine verdict (CI-90) are not stored again, whether or
            # not this deployment scans uploads.
            raise ServiceError(400, "malware_detected", "该文件此前已被判定为恶意内容，已拒绝")
        blob = self.store.put_blob(data)
        aid = new_id("att")
        self.store.insert("message_attachments", {"id": aid, "conversation_id": cid, "message_id": None, "uploader_id": uid, "filename": filename[:200] or "file",
                                                  "size": len(data), "blob": blob, "content_type": ctype, "scan": json.dumps(scan or {}, ensure_ascii=False),
                                                  "status": "accepted", "created_at": now(), "expires_at": now() + ATTACHMENT_UNUSED_S, "duration_ms": duration_ms})
        out = self._attachment_out({"id": aid, "filename": filename[:200] or "file", "size": len(data), "content_type": ctype, "duration_ms": duration_ms})
        out["scan"] = scan
        return out

    def attachment_for_download(self, aid: str, uid: str) -> dict:
        """The attachment row, for a member who may read the message it hangs on — and only while
        its bytes are accepted. Rejected by a rescan (or on bytes whose quarantine verdict stands although
        this row is not marked yet — another application's rescan found them first, or a restored
        backup predates the verdict), it answers 410 to that member and 404 to everyone else, so
        the refusal says nothing to someone who could not have read it (CI-90). Download, inline
        display and forwarding all come through here."""
        a = self.store.one("SELECT * FROM message_attachments WHERE id=?", (aid,))
        if not a or a["status"] not in ("accepted", "rejected"):
            raise ServiceError(404, "no_attachment", "找不到该附件")
        conv, me = self._member(a["conversation_id"], uid)
        if a["message_id"]:
            m = self.store.one("SELECT id, seq, deleted_at FROM messages WHERE id=?", (a["message_id"],))
            if not m or m["deleted_at"] or m["seq"] < me["joined_seq"] or self.store.one("SELECT 1 AS x FROM message_hides WHERE message_id=? AND user_id=?", (m["id"], uid)):
                raise ServiceError(404, "no_attachment", "找不到该附件")
        elif a["uploader_id"] != uid:
            raise ServiceError(404, "no_attachment", "找不到该附件")
        if a["status"] == "rejected" or quarantine.verdict(self.store, a["blob"]):
            raise ServiceError(410, "rejected_malware", "该附件未通过安全检查，已被拒绝并隔离")
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
        notify modes, the standing mute and a running `muted_until`. The sender is never told
        about their own message, and nobody who blocked the sender is told about it (CI-78: the
        message stays visible in a shared group, but it does not reach the blocker's inbox)."""
        conv = self.store.one("SELECT * FROM conversations WHERE id=?", (cid,))
        rows = self.store.all("SELECT * FROM conversation_members WHERE conversation_id=? AND left_at IS NULL AND user_id<>?", (cid, message["sender_id"]))
        blockers = {r["user_id"] for r in self.store.all("SELECT user_id FROM message_blocks WHERE blocked_id=?", (message["sender_id"],))}
        out: dict[str, str] = {}
        ts = now()
        for m in rows:
            if is_muted(m, ts) or m["notify"] == "none" or m["user_id"] in blockers:
                continue
            if m["user_id"] in (message.get("mentions") or []):
                out[m["user_id"]] = "mention"
            elif conv["kind"] == "dm" and m["notify"] == "all":
                out[m["user_id"]] = "dm"
            elif conv["kind"] == "group" and m["notify"] == "all":
                out[m["user_id"]] = "group"
        return out
