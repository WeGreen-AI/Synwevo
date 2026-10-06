"""Messaging HTTP ceremonies and the realtime stream — Synwevo Message's API, `/api/messaging/…`.

Moved here from the account service in CI-66 (it was `/api/auth/messaging/…` there). The routes
import only the shared layer, so the move changed the prefix and nothing about the ceremonies;
the tables, ids and rules are `synwevo_core.messaging`'s and stayed where they were.

Transport (docs/platform/messaging.md §3, §7.8): one server-sent-events stream per signed-in
browser tab, served by an async generator so an open tab costs a queue and a coroutine — not a
thread from the pool the ordinary handlers share. Events are fanned out in-process (the deployment
runs one worker per role; a second worker would need a shared bus, recorded as the condition for
scaling out). Every event carries an `id:`; a reconnecting tab presents `Last-Event-ID` and gets
what it missed from a bounded in-memory ring — or a `resync` telling it the ring no longer reaches
back that far, after which it catches up through `GET /sync` and per-conversation history. The
stream is a hint, never the truth: authorization is re-checked on every replayed event and at
every heartbeat; a revoked session or a withdrawn membership ends it.

Ephemeral by design (CI-74): typing hints and presence changes go through the same hub but are
never stored and never replayed; a tab that was not connected did not miss anything real.

Not here: the moderation half (listing and reviewing reports). It needs `org.messaging.manage`
and a strict re-authentication on the *account* session, so it stays on the account service
(`synwevo_identity.messaging_reports_routes`); this application only accepts a report.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.concurrency import run_in_threadpool

from synwevo_core.avatar_image import MAX_AVATAR_BYTES, MAX_AVATAR_SIDE
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.member_import import accept_upload
from synwevo_core.messaging import (EDIT_WINDOW_S, HISTORY_FROM, INLINE_TYPES, MAX_ATTACHMENT_BYTES, MAX_BODY, MAX_FORWARD_TARGETS,
                                    MAX_VOICE_BYTES, MAX_VOICE_MS, REACTIONS, RECALL_WINDOW_S, VOICE_TYPES, Messaging, conversation_target)
from synwevo_core.messaging_presence import AWAY_S, ONLINE_S, STATES, Presence
from synwevo_core.notifications import Notifier
from synwevo_core.store import now

log = logging.getLogger("synwevo.messaging")
#: Where the API lives on the messages origin (and on `all`, the single-process deployment).
PREFIX = "/api/messaging"
#: Where it lived on the account origin before CI-66; served there only by `install_legacy`.
LEGACY_PREFIX = "/api/auth/messaging"
HEARTBEAT_S = 25                # under Cloudflare's ~100 s idle cut and nginx's 300 s read timeout
QUEUE_MAX = 200                 # a tab that cannot keep up resyncs from history; nothing waits on it
RING_MAX = 5000                 # events kept for Last-Event-ID replay, per process
MAX_STREAMS_PER_USER = 8        # the 9th tab is told `end{too_many_streams}`
MESSAGES_PER_MINUTE = 60
ATTACHMENTS_PER_HOUR = 30
GROUPS_PER_DAY = 20
REPORTS_PER_DAY = 20
#: Group avatar changes and block/unblock toggles (CI-78): ordinary use never comes near these.
AVATARS_PER_HOUR = 30
BLOCKS_PER_HOUR = 60
#: Typing hints: their own bucket, never counted against messages; a hint is stale after this.
TYPING_PER_10S = 20
TYPING_TTL_S = 6
#: Presence heartbeats: a visible page sends one a minute; a burst of tabs still fits.
PRESENCE_PER_MINUTE = 12
PRESENCE_INTERVAL_S = 60
#: The inbox items a message creates for a reader; reading the conversation reads them too.
CONVERSATION_ITEM_KINDS = ("conversation.dm", "conversation.mention", "conversation.group")


class Metrics:
    """Counters of this process, numbers only (no content, no ids), for `/readyz`."""

    def __init__(self):
        self._c: dict[str, int] = {k: 0 for k in ("send_failed", "stream_replays", "stream_resyncs", "stream_refused", "typing_events", "presence_events", "search_queries")}
        self._lock = threading.Lock()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._c[name] = self._c.get(name, 0) + n

    def snapshot(self, hub: "MessageHub", presence: Presence) -> dict:
        with self._lock:
            out = dict(self._c)
        out["stream_connections"] = hub.connections()
        out["presence_tracked"] = presence.tracked()
        out["ring_events"] = hub.ring_size()
        return out


class MessageHub:
    """Per-user event queues for the open streams of this process, and the ring they replay from.

    `publish` may be called from any thread (the routes run in the thread pool); delivery happens
    on the event loop. Every published event gets a process-unique, increasing `id` (seeded from
    the clock so ids keep rising across restarts) and an `at`. Non-ephemeral events also enter a
    bounded ring; `replay` hands a reconnecting user the ones addressed to them after a cursor,
    or None when the cursor is older than what the ring still holds (the client must resync).
    """

    def __init__(self, *, ring_max: int = RING_MAX, max_per_user: int = MAX_STREAMS_PER_USER):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()
        self._next_id = int(time.time() * 1000)
        self._evicted_up_to = self._next_id - 1     # ids ≤ this may be gone; a cursor below it cannot be replayed
        self._ring: deque[tuple[int, frozenset, dict]] = deque()
        self._ring_max, self._max_per_user = ring_max, max_per_user
        self._once: deque[str] = deque(maxlen=2000)
        self._once_set: set[str] = set()

    def subscribe(self, user_id: str) -> asyncio.Queue | None:
        """A queue for one more stream of `user_id`, or None when they already hold the maximum."""
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            have = self._subs.setdefault(user_id, set())
            if len(have) >= self._max_per_user:
                if not have:
                    del self._subs[user_id]
                return None
            have.add(q)
        return q

    def unsubscribe(self, user_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subs.get(user_id)
            if subs:
                subs.discard(q)
                if not subs:
                    del self._subs[user_id]

    def connections(self) -> int:
        with self._lock:
            return sum(len(s) for s in self._subs.values())

    def connections_of(self, user_id: str) -> int:
        with self._lock:
            return len(self._subs.get(user_id, ()))

    def current_id(self) -> int:
        with self._lock:
            return self._next_id - 1

    def ring_size(self) -> int:
        with self._lock:
            return len(self._ring)

    def publish(self, users: list[str], event: dict, *, ephemeral: bool = False) -> dict:
        with self._lock:
            eid = self._next_id
            self._next_id += 1
            event = {**event, "id": eid, "at": now()}
            if not ephemeral:
                if len(self._ring) >= self._ring_max:
                    self._evicted_up_to = self._ring.popleft()[0]
                self._ring.append((eid, frozenset(users), event))
            loop = self._loop
            targets = [q for u in users for q in self._subs.get(u, ())]
        if not loop or not targets:
            return event

        def deliver():
            for q in targets:
                if q.full():
                    try:
                        q.get_nowait()          # drop the oldest; the client resyncs from history
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(event)
        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:                    # loop closed (shutdown)
            pass
        return event

    def publish_once(self, key: str, users: list[str], event: dict) -> dict | None:
        """`publish`, unless this process already published under `key` (bounded memory): several
        open tabs of one person may notice the same lost membership at their heartbeats, and the
        person should be told once."""
        with self._lock:
            if key in self._once_set:
                return None
            if len(self._once) == self._once.maxlen:
                self._once_set.discard(self._once[0])
            self._once.append(key)
            self._once_set.add(key)
        return self.publish(users, event)

    def replay(self, user_id: str, last_id: int) -> list[dict] | None:
        """Events addressed to `user_id` with id > `last_id`, or None when `last_id` is older than
        the ring reaches (or from another process: ahead of everything this one issued)."""
        with self._lock:
            if last_id < self._evicted_up_to or last_id >= self._next_id:
                return None
            return [ev for eid, users, ev in self._ring if eid > last_id and user_id in users]


def build_router(store, ident: Identity, sessions, audit, *, notifier: Notifier,
                 prefix: str = PREFIX, legacy_guard: bool = False) -> APIRouter:
    """The messaging API. `legacy_guard` is for the account origin's old prefix: every route then
    answers 410 from the moment Synwevo Message has registered, naming its address — also while
    it is disabled — so conversations have exactly one write source at any time."""
    def moved(request: Request) -> None:
        # One primary-key read per request, uncached: the switch has to take effect the moment
        # the messages process registers itself, not a cache lifetime later.
        from synwevo_core.apps import messages_registered_url
        url = messages_registered_url(store)
        if url:
            raise ServiceError(410, "moved", f"消息已迁到 Synwevo Message（{url}），此地址不再提供会话接口",
                               {"messages_url": url})

    r = APIRouter(prefix=prefix, tags=["messaging"], dependencies=[Depends(moved)] if legacy_guard else None)
    hub = MessageHub()
    presence = Presence()
    metrics = Metrics()
    svc = Messaging(store, ident, publish=hub.publish, presence=presence)
    r.hub, r.messaging, r.presence, r.metrics = hub, svc, presence, metrics
    r.metrics_snapshot = lambda: metrics.snapshot(hub, presence)

    def session(request: Request) -> dict:
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if (not current or current["revoked_at"] is not None or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current

    def uid_of(request: Request) -> str:
        return session(request)["user_id"]

    def limited(uid: str, bucket: str, limit: int, window_s: int) -> None:
        allowed, wait = ident.window_allows(f"{bucket}:{uid}", limit=limit, window_s=window_s)
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, int(wait or 60))})

    def _org(body: dict) -> str:
        oid = str(body.get("organization_id") or "").strip()
        if not oid:
            raise ServiceError(400, "invalid_field", "请指定组织", {"field": "organization_id"})
        return oid

    def _bool(body: dict, key: str):
        v = body.get(key)
        if v is None:
            return None
        if not isinstance(v, bool):
            raise ServiceError(400, "invalid_field", f"{key} 必须是 true 或 false", {"field": key})
        return v

    # ------------------------------------------------------------------ conversations
    @r.get("/organizations")
    def organizations(request: Request):
        """The caller's organizations — the scope a conversation is started in. On the account
        origin the page read `/api/auth/organizations`; that route is the account service's and
        is refused here by role, so the application answers the same question from the shared
        layer (id, name, status, the caller's role — nothing about members)."""
        from synwevo_core.organizations import Organizations
        items = Organizations(store, ident).list_for_user(uid_of(request))
        return {"items": items, "total": len(items)}

    @r.get("/conversations")
    def list_conversations(request: Request, organization_id: str = Query("")):
        return {"items": svc.list_conversations(uid_of(request), organization_id=organization_id or None)}

    # /unread-count is answered by the shared session router (synwevo_auth_client.session_routes)
    # on every origin, so the platform bar's badge works on the portal and in Docs as well.

    @r.get("/link-card")
    def link_card(request: Request, url: str = Query(..., max_length=2048)):
        """The card for a link to a Docs project or job, as *this* reader may see it: no access,
        no such object and not a Docs link all answer 404 alike. Rate-limited per reader because
        each card is a membership check."""
        uid = uid_of(request)
        limited(uid, "msg-card", 600, 60)
        from synwevo_core.docs_links import DocsLinkCards
        card = DocsLinkCards(store, ident).card(uid, url)
        if not card:
            raise ServiceError(404, "no_card", "没有可显示的卡片")
        return card

    @r.get("/directory")
    def directory(request: Request, organization_id: str = Query(...), q: str = Query("", max_length=80)):
        return {"items": svc.directory(organization_id, uid_of(request), q)}

    @r.post("/dm", status_code=201)
    def open_dm(request: Request, body: dict = Body(...)):
        uid = uid_of(request)
        return svc.open_dm(_org(body), uid, str(body.get("user_id") or ""))

    @r.post("/groups", status_code=201)
    def create_group(request: Request, body: dict = Body(...)):
        uid = uid_of(request)
        limited(uid, "msg-group", GROUPS_PER_DAY, 86400)
        ids = body.get("member_ids") or []
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ServiceError(400, "invalid_field", "member_ids 必须是用户 id 列表", {"field": "member_ids"})
        out = svc.create_group(_org(body), uid, name=str(body.get("name") or ""), member_ids=ids,
                               description=body.get("description"), workspace_id=body.get("workspace_id") or None)
        audit(request, "messaging.group.create", actor_id=uid, object_kind="conversation", object_id=out["id"], result="ok",
              meta={"organization_id": out["organization_id"], "members": out["member_count"]})
        for mid in ids:
            if mid != uid:
                _notify(mid, "conversation.added", {"actor": svc._name(uid), "conversation": out["name"]}, out, actor=uid)
        return out

    @r.get("/conversations/{cid}")
    def get_conversation(request: Request, cid: str):
        return svc.get(cid, uid_of(request))

    @r.patch("/conversations/{cid}")
    def update_group(request: Request, cid: str, body: dict = Body(...)):
        if set(body) - {"name", "description"}:
            raise ServiceError(400, "invalid_field", "只能修改 name 和 description")
        uid = uid_of(request)
        out = svc.update_group(cid, uid, name=body.get("name"), description=body.get("description"))
        audit(request, "messaging.group.update", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"fields": sorted(body)})
        return out

    @r.put("/conversations/{cid}/announcement")
    def set_announcement(request: Request, cid: str, body: dict = Body(...)):
        """The group's pinned announcement (managers). Members are told once through the inbox
        as a digest item; clearing it tells nobody."""
        if set(body) - {"text"} or not (body.get("text") is None or isinstance(body.get("text"), str)):
            raise ServiceError(400, "invalid_field", "只能设置 text", {"field": "text"})
        uid = uid_of(request)
        out = svc.set_announcement(cid, uid, body.get("text"))
        audit(request, "messaging.group.announcement", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"cleared": not out["announcement"]})
        if out["announcement"]:
            stamp = int(out["announcement_at"] or now())
            for m in out["members"]:
                if m["user_id"] != uid:
                    _notify(m["user_id"], "conversation.announcement", {"actor": svc._name(uid), "conversation": out["name"], "excerpt": out["announcement"][:120]}, out,
                            dedupe=f"announcement:{cid}:{stamp}:{m['user_id']}", actor=uid)
        return out

    # ------------------------------------------------------------------ group avatar (CI-78)
    @r.put("/conversations/{cid}/avatar")
    def put_avatar(request: Request, cid: str, body: dict = Body(...)):
        """A group's avatar (owner/admin): `{image_base64}` — a PNG held to the platform's avatar
        rules (≤ 256 KB, ≤ 512 × 512, rebuilt without metadata; anything else 400 `bad_avatar`).
        Leaves the system row 「X 更换了群头像」; members' lists update through the stream."""
        if set(body) != {"image_base64"} or not isinstance(body.get("image_base64"), str):
            raise ServiceError(400, "invalid_field", "只能提交 image_base64", {"field": "image_base64"})
        uid = uid_of(request)
        limited(uid, "msg-avatar", AVATARS_PER_HOUR, 3600)
        out = svc.set_avatar(cid, uid, body["image_base64"])
        audit(request, "messaging.group.avatar", actor_id=uid, object_kind="conversation", object_id=cid, result="ok")
        return out

    @r.delete("/conversations/{cid}/avatar")
    def delete_avatar(request: Request, cid: str):
        uid = uid_of(request)
        limited(uid, "msg-avatar", AVATARS_PER_HOUR, 3600)
        out = svc.set_avatar(cid, uid, None)
        audit(request, "messaging.group.avatar.remove", actor_id=uid, object_kind="conversation", object_id=cid, result="ok")
        return out

    @r.get("/conversations/{cid}/avatar")
    def get_avatar(request: Request, cid: str):
        """The avatar, to current members only (404 otherwise). `private, no-cache` with an ETag:
        a browser may keep it but asks again every time, so a member who left stops getting it."""
        a = svc.avatar_image(cid, uid_of(request))
        etag = '"' + hashlib.sha256(a["image"]).hexdigest()[:32] + '"'
        headers = {"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff", "ETag": etag,
                   "Content-Security-Policy": "default-src 'none'; sandbox"}
        if etag in [x.strip() for x in (request.headers.get("if-none-match") or "").split(",")]:
            return Response(status_code=304, headers=headers)
        return Response(a["image"], media_type="image/png", headers=headers)

    @r.post("/conversations/{cid}/members")
    def add_members(request: Request, cid: str, body: dict = Body(...)):
        """Add members (owner/admin). `history_from`: `join` (default) or `all` — the adder's
        choice, recorded per membership and said in the system row."""
        uid = uid_of(request)
        if set(body) - {"user_ids", "history_from"}:
            raise ServiceError(400, "invalid_field", "只能提交 user_ids 和 history_from")
        ids = body.get("user_ids") or []
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids) or len(ids) > 50:
            raise ServiceError(400, "invalid_field", "user_ids 必须是 1–50 个用户 id", {"field": "user_ids"})
        history_from = body.get("history_from") or "join"
        if history_from not in HISTORY_FROM:
            raise ServiceError(400, "invalid_field", "history_from 只能是 join 或 all", {"field": "history_from", "allowed": list(HISTORY_FROM)})
        before = {m["user_id"] for m in svc.get(cid, uid)["members"]}
        out = svc.add_members(cid, uid, ids, history_from=history_from)
        added = [m["user_id"] for m in out["members"] if m["user_id"] not in before]
        audit(request, "messaging.group.add", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"added": added, "history_from": history_from})
        for mid in added:
            _notify(mid, "conversation.added", {"actor": svc._name(uid), "conversation": out["name"]}, out, actor=uid)
        return out

    @r.delete("/conversations/{cid}/members/{member_id}")
    def remove_member(request: Request, cid: str, member_id: str):
        uid = uid_of(request)
        out = svc.remove_member(cid, uid, member_id)
        audit(request, "messaging.group.remove", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"removed": member_id})
        _notify(member_id, "conversation.removed", {"actor": svc._name(uid), "conversation": out["name"]}, out, to_list=True)
        return out

    @r.post("/conversations/{cid}/leave")
    def leave(request: Request, cid: str):
        uid = uid_of(request)
        svc.leave(cid, uid)
        audit(request, "messaging.group.leave", actor_id=uid, object_kind="conversation", object_id=cid, result="ok")
        return {"ok": True}

    @r.post("/conversations/{cid}/transfer")
    def transfer(request: Request, cid: str, body: dict = Body(...)):
        uid = uid_of(request)
        out = svc.transfer(cid, uid, str(body.get("user_id") or ""))
        audit(request, "messaging.group.transfer", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"to": body.get("user_id")})
        return out

    @r.put("/conversations/{cid}/members/{member_id}/role")
    def set_role(request: Request, cid: str, member_id: str, body: dict = Body(...)):
        uid = uid_of(request)
        out = svc.set_role(cid, uid, member_id, str(body.get("role") or ""))
        audit(request, "messaging.group.role", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"user_id": member_id, "role": body.get("role")})
        return out

    @r.post("/conversations/{cid}/archive")
    def archive(request: Request, cid: str, body: dict = Body(default={})):
        uid = uid_of(request)
        archived = body.get("archived", True) is not False
        out = svc.archive(cid, uid, archived)
        audit(request, "messaging.group.archive" if archived else "messaging.group.unarchive", actor_id=uid, object_kind="conversation", object_id=cid, result="ok")
        return out

    @r.put("/conversations/{cid}/preferences")
    def preferences(request: Request, cid: str, body: dict = Body(...)):
        """`muted`, `pinned`, `notify` as before; `muted_until` (CI-74) is a Unix time ahead of
        now, or null to clear the timer."""
        if set(body) - {"muted", "pinned", "notify", "muted_until"}:
            raise ServiceError(400, "invalid_field", "只能设置 muted、pinned、notify、muted_until")
        clear = "muted_until" in body and body["muted_until"] is None
        return svc.set_preferences(cid, uid_of(request), muted=body.get("muted"), pinned=body.get("pinned"), notify=body.get("notify"),
                                   muted_until=None if clear else body.get("muted_until"), clear_muted_until=clear)

    @r.post("/conversations/{cid}/unread")
    def mark_unread(request: Request, cid: str):
        """Mark as unread (CI-74): a flag on the membership, cleared by the next read; the badge
        counts the conversation as one while it has no unread message."""
        return svc.mark_unread(cid, uid_of(request))

    # ------------------------------------------------------------------ settings (CI-74)
    @r.get("/settings")
    def get_settings(request: Request):
        return svc.settings(uid_of(request))

    @r.put("/settings")
    def put_settings(request: Request, body: dict = Body(...)):
        """`show_presence` (default on) and `read_receipts` (default off); both mutual. Turning
        presence off tells the people who could see it that it is no longer shown (`null`)."""
        if set(body) - {"show_presence", "read_receipts"}:
            raise ServiceError(400, "invalid_field", "只能设置 show_presence 和 read_receipts")
        uid = uid_of(request)
        before = svc.settings(uid)
        audience = svc.presence_audience(uid) if before["show_presence"] else []
        out = svc.set_settings(uid, show_presence=_bool(body, "show_presence"), read_receipts=_bool(body, "read_receipts"))
        if before["show_presence"] and not out["show_presence"] and audience:
            hub.publish(audience, {"type": "presence", "user_id": uid, "state": None}, ephemeral=True)
            metrics.inc("presence_events")
        audit(request, "messaging.settings", actor_id=uid, object_kind="user", object_id=uid, result="ok", meta={"fields": sorted(body)})
        return out

    # ------------------------------------------------------------------ blocking (CI-78)
    @r.get("/blocks")
    def list_blocks(request: Request):
        """The people the caller blocked (never who blocked the caller)."""
        return {"items": svc.blocks(uid_of(request))}

    def _presence_between(a: str, b: str) -> None:
        """After a block or an unblock, each side's open tabs learn what they may now see of the
        other: `null` while blocked, the real state after (only if they share a conversation). The
        event is the same one a hidden presence produces, so it tells the blocked person nothing."""
        if b not in set(svc.co_members(a)):
            return
        for viewer, other in ((a, b), (b, a)):
            state = svc.presence_of(viewer, [other])[other]
            hub.publish([viewer], {"type": "presence", "user_id": other, "state": state}, ephemeral=True)
            metrics.inc("presence_events")

    @r.put("/blocks/{user_id}")
    def block(request: Request, user_id: str):
        """Block someone who shares an organization with the caller (yourself or an application
        account: 400; anyone else: 404). Idempotent. The blocked person is not told."""
        uid = uid_of(request)
        limited(uid, "msg-block", BLOCKS_PER_HOUR, 3600)
        out, created = svc.block(uid, user_id)
        if created:
            _presence_between(uid, user_id)
            audit(request, "messaging.block", actor_id=uid, object_kind="user", object_id=user_id, result="ok")
        return out

    @r.delete("/blocks/{user_id}")
    def unblock(request: Request, user_id: str):
        uid = uid_of(request)
        limited(uid, "msg-block", BLOCKS_PER_HOUR, 3600)
        out, removed = svc.unblock(uid, user_id)
        if removed:
            _presence_between(uid, user_id)
            audit(request, "messaging.unblock", actor_id=uid, object_kind="user", object_id=user_id, result="ok")
        return out

    # ------------------------------------------------------------------ typing & presence (CI-74; nothing stored)
    @r.post("/conversations/{cid}/typing")
    def typing(request: Request, cid: str, body: dict = Body(default={})):
        """A typing hint to the other members' open streams: `state` start (default) or stop.
        Its own rate bucket, never counted against messages; `expires_in_s` says when a receiver
        must drop it if no `stop` arrives. Nothing is stored, audited or replayed."""
        uid = uid_of(request)
        state = body.get("state", "start")
        if state not in ("start", "stop"):
            raise ServiceError(400, "invalid_field", "state 只能是 start 或 stop", {"field": "state"})
        limited(uid, "msg-typing", TYPING_PER_10S, 10)
        targets = svc.typing_targets(cid, uid)                                  # 404 for a non-member
        if targets:
            hub.publish(targets, {"type": "typing", "conversation_id": cid, "user_id": uid, "name": svc._name(uid), "state": state,
                                  "expires_in_s": TYPING_TTL_S}, ephemeral=True)
            metrics.inc("typing_events")
        return {"sent": bool(targets), "state": state, "expires_in_s": TYPING_TTL_S}

    def _presence_definition() -> dict:
        return {"online_s": presence.online_s, "away_s": presence.away_s, "interval_s": PRESENCE_INTERVAL_S, "states": list(STATES)}

    def _publish_presence(uid: str, state: str | None) -> None:
        audience = svc.presence_audience(uid)
        if audience:
            hub.publish(audience, {"type": "presence", "user_id": uid, "state": state}, ephemeral=True)
            metrics.inc("presence_events")

    @r.post("/presence")
    def heartbeat(request: Request):
        """A heartbeat from a *visible* page (the client sends none while hidden). Records the
        time in memory; answers the definition the state is derived from. A person who hides
        their presence is not tracked at all (`shown: false`)."""
        uid = uid_of(request)
        limited(uid, "msg-presence", PRESENCE_PER_MINUTE, 60)
        if not svc.settings(uid)["show_presence"]:
            return {"shown": False, "state": None, **_presence_definition()}
        before, after = presence.heartbeat(uid)
        if before != after:
            _publish_presence(uid, after)
        return {"shown": True, "state": after, **_presence_definition()}

    @r.get("/presence")
    def presence_snapshot(request: Request, user_ids: str = Query("", max_length=8000)):
        """States of people who share a conversation with the caller (others are simply absent),
        as the caller may see them (`null` = not shown). For the periodic batch pull."""
        uid = uid_of(request)
        ids = [x for x in dict.fromkeys(s.strip() for s in user_ids.split(",")) if x][:200]
        peers = set(svc.co_members(uid))
        ids = [x for x in ids if x in peers]
        return {"presence": svc.presence_of(uid, ids), **_presence_definition()}

    def _sweep_presence() -> None:
        for who, state in presence.sweep():
            _publish_presence(who, state)

    # ------------------------------------------------------------------ messages
    @r.get("/conversations/{cid}/messages")
    def history(request: Request, cid: str, before: int | None = Query(None, ge=1), after: int | None = Query(None, ge=0),
                limit: int = Query(50, ge=1, le=200)):
        return svc.history(cid, uid_of(request), before_seq=before, after_seq=after, limit=limit)

    def _notify(recipient: str, kind: str, params: dict, conv: dict, *, to_list: bool = False, dedupe: str | None = None,
                actor: str | None = None) -> None:
        """An inbox item pointing at the conversation (or at the list, for someone who was
        removed): on the messages origin while the application is registered, else the account
        origin's old paths (`conversation_target`). An item whose `actor` the recipient blocked is
        not written (CI-78) — the group still shows what happened; the inbox does not."""
        if actor and svc.has_blocked(recipient, actor):
            return
        try:
            notifier.emit(recipient, kind, params=params, organization_id=conv["organization_id"], object_kind="conversation", object_id=conv["id"],
                          dedupe_key=dedupe, target=conversation_target(store, None if to_list else conv["id"]))
        except Exception as e:  # noqa: BLE001 - an inbox item is a courtesy; the message is already stored
            log.warning("messaging inbox item skipped: %s", type(e).__name__)

    def _tell_readers(cid: str, uid: str, msg: dict) -> None:
        """The inbox items a stored message creates (`recipients_for` decides who and why)."""
        conv = svc.get(cid, uid)
        preview = (msg["body"][:120] if msg["body"] else "") or ("[附件]" if msg.get("attachment") else "")
        for who, why in svc.recipients_for(cid, msg).items():
            reader = store.one("SELECT last_read_seq FROM conversation_members WHERE conversation_id=? AND user_id=?", (cid, who)) or {}
            label = svc._name(uid) if conv["kind"] == "dm" else conv["name"]
            _notify(who, {"dm": "conversation.dm", "mention": "conversation.mention", "group": "conversation.group"}[why],
                    {"sender": svc._name(uid), "conversation": label, "preview": preview}, conv,
                    dedupe=f"conv:{cid}:{who}:{why}:{reader.get('last_read_seq', 0)}")

    @r.post("/conversations/{cid}/messages", status_code=201)
    def send(request: Request, cid: str, body: dict = Body(...)):
        uid = uid_of(request)
        try:
            if set(body) - {"body", "client_id", "reply_to", "attachment_id", "mention_ids", "mention_all"}:
                raise ServiceError(400, "invalid_field", "消息包含不支持的字段")
            limited(uid, "msg", MESSAGES_PER_MINUTE, 60)
            mention_ids = body.get("mention_ids") or []
            if not isinstance(mention_ids, list) or len(mention_ids) > 50:
                raise ServiceError(400, "invalid_field", "mention_ids 无效", {"field": "mention_ids"})
            mention_all = body.get("mention_all", False)
            if not isinstance(mention_all, bool):
                raise ServiceError(400, "invalid_field", "mention_all 只能是 true 或 false", {"field": "mention_all"})
            msg, created = svc.send(cid, uid, body=str(body.get("body") or ""), client_id=body.get("client_id"), reply_to=body.get("reply_to"),
                                    attachment_id=body.get("attachment_id"), mention_ids=[str(x) for x in mention_ids], mention_all=mention_all)
        except ServiceError:
            metrics.inc("send_failed")
            raise
        if created:
            _tell_readers(cid, uid, msg)
        return {"message": msg, "created": created}

    @r.patch("/messages/{mid}")
    def edit(request: Request, mid: str, body: dict = Body(...)):
        return svc.edit(mid, uid_of(request), str(body.get("body") or ""))

    @r.delete("/messages/{mid}")
    def delete_for_me(request: Request, mid: str):
        """Delete for me (CI-74): hides the message from this reader only. Recall (sender, within
        the window) and remove (group managers) are the ways to take a message away for everyone."""
        return svc.hide(mid, uid_of(request))

    @r.post("/messages/{mid}/unhide")
    def unhide(request: Request, mid: str):
        """Undo a delete-for-me (the shell offers it for a few seconds)."""
        return svc.unhide(mid, uid_of(request))

    @r.post("/messages/{mid}/recall")
    def recall(request: Request, mid: str):
        """Recall for everyone: the sender, within `recall_window_s`."""
        uid = uid_of(request)
        out = svc.recall(mid, uid)
        audit(request, "messaging.message.recall", actor_id=uid, object_kind="message", object_id=mid, result="ok")
        return out

    @r.post("/messages/{mid}/remove")
    def remove(request: Request, mid: str):
        """Remove for everyone: a group owner/admin, any time; the marker names them."""
        uid = uid_of(request)
        out = svc.remove(mid, uid)
        audit(request, "messaging.message.remove", actor_id=uid, object_kind="message", object_id=mid, result="ok", meta={"sender_id": out["sender_id"]})
        return out

    @r.post("/messages/{mid}/forward", status_code=201)
    def forward(request: Request, mid: str, body: dict = Body(...)):
        """Forward one message to ≤ `forward_targets` conversations the caller can write to.
        Counts against the message rate limit once per target; `client_id` makes it idempotent."""
        if set(body) - {"conversation_ids", "client_id"}:
            raise ServiceError(400, "invalid_field", "只能提交 conversation_ids 和 client_id")
        uid = uid_of(request)
        ids = body.get("conversation_ids") or []
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids):
            raise ServiceError(400, "invalid_field", "conversation_ids 必须是会话 id 列表", {"field": "conversation_ids"})
        for _ in range(max(1, min(len(ids), MAX_FORWARD_TARGETS))):
            limited(uid, "msg", MESSAGES_PER_MINUTE, 60)
        try:
            items = svc.forward(mid, uid, ids, client_id=body.get("client_id"))
        except ServiceError:
            metrics.inc("send_failed")
            raise
        audit(request, "messaging.message.forward", actor_id=uid, object_kind="message", object_id=mid, result="ok",
              meta={"targets": [x["conversation_id"] for x in items if x["created"]]})
        for x in items:
            if x["created"]:
                _tell_readers(x["conversation_id"], uid, x["message"])
        return {"items": items}

    @r.post("/messages/{mid}/report", status_code=201)
    def report(request: Request, mid: str, body: dict = Body(...)):
        """A member reports a message they can see. The organization's messaging managers get an
        actionable inbox item pointing at the organization page on the account origin, where the
        report is reviewed; the reporter is told when it is. Rate-limited per reporter."""
        if set(body) - {"reason", "note"}:
            raise ServiceError(400, "invalid_field", "只能提交 reason 和 note")
        uid = uid_of(request)
        limited(uid, "msg-report", REPORTS_PER_DAY, 86400)
        # Admission/rate limiting may precede a withdrawal. The report, audit and every
        # reviewer's to-do must become visible together: review cannot close a report and
        # then be overtaken by a late pending to-do from this request.
        with store.tx():
            current = sessions.current(getattr(request.state, "session", None))
            if not current:
                raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
            uid = current["user_id"]
            out = svc.report(mid, uid, str(body.get("reason") or ""), body.get("note"))
            audit(request, "messaging.message.report", actor_id=uid, object_kind="message", object_id=mid, result="ok",
                  meta={"report_id": out["id"], "reason": out["reason"], "organization_id": out["organization_id"]})
            for manager in svc.messaging_managers(out["organization_id"]):
                if manager != uid:
                    notifier.emit(manager, "conversation.reported", params={"who": out["reporter"]["name"], "conversation": out["conversation"]["name"] or out["sender"]["name"]},
                                  organization_id=out["organization_id"], object_kind="message_report", object_id=out["id"], dedupe_key=f"report:{out['id']}:{manager}",
                                  actionable=True, target={"origin": "accounts", "path": f"/organizations/{out['organization_id']}?report={out['id']}"})
        return out

    # Listing and reviewing reports are not here: see the module docstring.

    @r.put("/messages/{mid}/reactions")
    def react(request: Request, mid: str, body: dict = Body(...)):
        return svc.react(mid, uid_of(request), str(body.get("emoji") or ""), body.get("on", True) is not False)

    @r.post("/conversations/{cid}/read")
    def mark_read(request: Request, cid: str, body: dict = Body(...)):
        try:
            seq = int(body.get("seq"))
        except (TypeError, ValueError):
            raise ServiceError(400, "invalid_field", "seq 必须是整数", {"field": "seq"}) from None
        uid = uid_of(request)
        out = svc.mark_read(cid, uid, seq)
        if out["unread"] == 0:
            # Reading the conversation to its end is reading the inbox items about it: the bell
            # used to keep saying "X 给你发了私信" after the chat had been read, and only the
            # dispatcher's send-time re-check kept that from being mailed. An inbox read and
            # nothing more — no approval, no action is completed here.
            out["notifications_read"] = notifier.mark_object_read(uid, "conversation", cid, kinds=CONVERSATION_ITEM_KINDS)
        return out

    @r.get("/search")
    def search(request: Request, q: str = Query("", max_length=200), organization_id: str = Query(""), cid: str = Query(""),
               sender: str = Query("", alias="from"), has_attachment: int | None = Query(None, ge=0, le=1),
               since: float | None = Query(None, ge=0), until: float | None = Query(None, ge=0), limit: int = Query(50, ge=1, le=100)):
        """Search what the caller may read (CI-74: FTS5 when the index can answer, else LIKE;
        `engine`/`fallback` say which). Filters: `cid`, `from` (user id), `has_attachment`,
        `since`/`until` (Unix seconds)."""
        metrics.inc("search_queries")
        return svc.search(uid_of(request), q, organization_id=organization_id or None, limit=limit, conversation_id=cid or None,
                          sender_id=sender or None, has_attachment=None if has_attachment is None else bool(has_attachment), since=since, until=until)

    @r.get("/sync")
    def sync(request: Request, since: float = Query(0, ge=0)):
        """Conversation-level changes since a cursor (CI-74) — what a reconnecting tab needs to
        know which conversations to refetch, which to drop and which messages vanished. Bodies are
        never here; the client fetches history per conversation with `after=`."""
        return svc.sync(uid_of(request), since)

    # ------------------------------------------------------------------ attachments
    @r.post("/conversations/{cid}/attachments", status_code=201)
    async def upload_attachment(request: Request, cid: str, file: UploadFile = File(...), duration_ms: str | None = Form(None)):
        """One file through the acceptance chain. A voice message (audio/webm, ogg, mp4, aac by
        the bytes) may declare `duration_ms`, checked server-side; the type is never the client's."""
        uid = uid_of(request)
        limited(uid, "msg-att", ATTACHMENTS_PER_HOUR, 3600)
        data = await file.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ServiceError(413, "file_too_large", f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限", {"limit": MAX_ATTACHMENT_BYTES})
        filename = Path(file.filename or "file").name[:200] or "file"
        dur = None
        if duration_ms not in (None, ""):
            try:
                dur = int(duration_ms)
            except (TypeError, ValueError):
                raise ServiceError(400, "invalid_field", "duration_ms 必须是整数毫秒", {"field": "duration_ms"}) from None
        scan = await run_in_threadpool(accept_upload, filename, data)       # 400 infected or encrypted / 503 scanner down / 413
        out = await run_in_threadpool(lambda: svc.attach(cid, uid, filename=filename, data=data, scan=scan, duration_ms=dur))
        audit(request, "messaging.attachment.upload", actor_id=uid, object_kind="conversation", object_id=cid, result="ok",
              meta={"attachment_id": out["id"], "size": out["size"], "content_type": out["content_type"], "scan": (scan or {}).get("status"),
                    "voice": bool(out.get("voice"))})
        return out

    @r.get("/attachments/{aid}")
    def download(request: Request, aid: str, inline: int = Query(0, ge=0, le=1)):
        a = svc.attachment_for_download(aid, uid_of(request))
        data = store.get_blob(a["blob"])
        ctype = a["content_type"] if (inline and a["content_type"] in INLINE_TYPES) else ("application/octet-stream" if inline else a["content_type"])
        from urllib.parse import quote
        disposition = "inline" if inline and a["content_type"] in INLINE_TYPES else "attachment"
        return Response(data, media_type=ctype, headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(a['filename'])}",
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "X-Content-SHA256": a["blob"],
            "Content-Security-Policy": "sandbox; default-src 'none'"})

    # ------------------------------------------------------------------ the stream
    def _still_valid(sid: str, uid: str) -> bool:
        s = store.one("SELECT revoked_at, idle_expires_at, absolute_expires_at FROM sessions WHERE id=?", (sid,))
        u = ident.get_user(uid)
        ts = now()
        return bool(s and s["revoked_at"] is None and s["idle_expires_at"] > ts and s["absolute_expires_at"] > ts and u and u["status"] == "active")

    def _still_allowed(uid: str, events: list[dict]) -> list[dict]:
        """Replay only what the reconnecting person may still see: an event about a conversation
        they are no longer in (removed, left, organization lost) is dropped, whatever the ring
        remembers — except the `removed` notice itself, which is what tells the tab to drop the
        conversation. `withdrawn` events name an organization and are kept."""
        ok: dict[str, bool] = {}
        out = []
        for ev in events:
            cid = ev.get("conversation_id")
            if cid and not (ev.get("type") == "conversation" and ev.get("change") == "removed"):
                if cid not in ok:
                    try:
                        svc._member(cid, uid)
                        ok[cid] = True
                    except ServiceError:
                        ok[cid] = False
                if not ok[cid]:
                    continue
            out.append(ev)
        return out

    def _sse(ev: dict) -> bytes:
        return f"id: {ev['id']}\nevent: {ev.get('type', 'event')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()

    def _lost_memberships(uid: str, known: set[str]) -> set[str]:
        """Heartbeat re-check (docs/platform/messaging.md §7.10): conversations the tab knew that
        the person may no longer see — closed by this process or another (organization removal on
        the account service, moderation) — are announced as `conversation removed`, once per lost
        membership across all of the person's tabs. Returns what is visible now."""
        current = set(svc.visible_conversation_ids(uid))
        for cid in sorted(known - current):
            closed = svc.membership_closed_at(cid, uid)
            hub.publish_once(f"lost:{uid}:{cid}:{int(closed or 0)}", [uid],
                             {"type": "conversation", "conversation_id": cid, "change": "removed", "detected": "heartbeat"})
        return current

    @r.get("/stream")
    async def stream(request: Request, last_event_id: int | None = Query(None, ge=0)):
        """`text/event-stream`. Every event has `id:`; `hello` carries the current id so a tab
        that reconnects presents `Last-Event-ID` (the header the browser sends, or the query for a
        fresh `EventSource`) and gets the events it missed — or `resync` when the ring no longer
        reaches back that far (then `GET /sync` + history `after=`). Typing and presence are never
        replayed. Heartbeats re-check the session and sweep presence."""
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        sid, uid = state["id"], state["user_id"]
        if not await run_in_threadpool(_still_valid, sid, uid):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        header = request.headers.get("last-event-id")
        cursor = int(header) if header and header.isdigit() else last_event_id
        headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
        q = hub.subscribe(uid)
        if q is None:
            metrics.inc("stream_refused")

            async def refuse():
                yield b"retry: 30000\nevent: end\ndata: {\"reason\": \"too_many_streams\", \"max\": " + str(MAX_STREAMS_PER_USER).encode() + b"}\n\n"
            return StreamingResponse(refuse(), media_type="text/event-stream", headers=headers)

        async def gen():
            last_sent: int | None = None
            try:
                known = set(await run_in_threadpool(svc.visible_conversation_ids, uid))
                current = hub.current_id()
                hello = {"heartbeat_s": HEARTBEAT_S, "last_event_id": current, "typing_ttl_s": TYPING_TTL_S, "presence": _presence_definition(),
                         "replay": cursor is not None}
                yield b"retry: 3000\nid: " + str(current).encode() + b"\nevent: hello\ndata: " + json.dumps(hello, ensure_ascii=False).encode() + b"\n\n"
                if cursor is not None:
                    missed = hub.replay(uid, cursor)
                    if missed is None:
                        metrics.inc("stream_resyncs")
                        yield b"event: resync\ndata: {\"reason\": \"cursor_expired\"}\n\n"
                    else:
                        missed = await run_in_threadpool(_still_allowed, uid, missed)
                        if missed:
                            metrics.inc("stream_replays")
                        for ev in missed:
                            yield _sse(ev)
                            last_sent = ev["id"]
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                        if last_sent is not None and ev.get("id", 0) <= last_sent:
                            continue                                    # already replayed from the ring
                        yield _sse(ev)
                        last_sent = ev["id"]
                    except asyncio.TimeoutError:
                        if not await run_in_threadpool(_still_valid, sid, uid):
                            yield b"event: end\ndata: {\"reason\": \"session\"}\n\n"
                            break
                        yield b": ping\n\n"
                        known = await run_in_threadpool(_lost_memberships, uid, known)
                        await run_in_threadpool(_sweep_presence)
            finally:
                hub.unsubscribe(uid, q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)

    @r.get("/capabilities")
    def capabilities(request: Request):
        uid = uid_of(request)
        return {"max_body": MAX_BODY, "max_attachment_bytes": MAX_ATTACHMENT_BYTES, "reactions": list(REACTIONS),
                "heartbeat_s": HEARTBEAT_S,
                "limits": {"messages_per_minute": MESSAGES_PER_MINUTE, "attachments_per_hour": ATTACHMENTS_PER_HOUR, "groups_per_day": GROUPS_PER_DAY,
                           "reports_per_day": REPORTS_PER_DAY, "typing_per_10s": TYPING_PER_10S, "presence_per_minute": PRESENCE_PER_MINUTE,
                           "forward_targets": MAX_FORWARD_TARGETS, "streams_per_user": MAX_STREAMS_PER_USER,
                           "avatars_per_hour": AVATARS_PER_HOUR, "blocks_per_hour": BLOCKS_PER_HOUR},
                "recall_window_s": RECALL_WINDOW_S, "edit_window_s": EDIT_WINDOW_S,
                "voice": {"types": list(VOICE_TYPES), "max_bytes": MAX_VOICE_BYTES, "max_duration_ms": MAX_VOICE_MS},
                "search": {**svc.search_info(), "filters": ["cid", "from", "has_attachment", "since", "until"], "max_limit": 100},
                "presence": _presence_definition(), "typing_ttl_s": TYPING_TTL_S,
                "stream": {"event_ids": True, "replay_ring": RING_MAX, "ephemeral": ["typing", "presence"]},
                "history_from": list(HISTORY_FROM), "settings": svc.settings(uid),
                "group_avatar": {"types": ["image/png"], "max_bytes": MAX_AVATAR_BYTES, "max_side": MAX_AVATAR_SIDE},
                "blocks": True}

    return r
