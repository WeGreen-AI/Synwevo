"""Messaging HTTP ceremonies and the realtime stream.

Transport (docs/platform/messaging.md §3): one server-sent-events stream per signed-in browser
tab, served by an async generator so an open tab costs a queue and a coroutine — not a thread
from the pool the ordinary handlers share. Events are fanned out in-process (the deployment runs
one worker per role; a second worker would need a shared bus, recorded as the condition for
scaling out). The stream is a hint, never the truth: every reconnect and every "what did I miss"
goes back to the history API with the last sequence the client has, and the client falls back to
polling that API when the stream cannot be held open. Authorization is re-checked on the stream
at every heartbeat: a revoked session or a withdrawn membership ends it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from pathlib import Path

from fastapi import APIRouter, Body, File, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from fastapi.concurrency import run_in_threadpool

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.member_import import accept_upload
from synwevo_core.messaging import INLINE_TYPES, MAX_ATTACHMENT_BYTES, MAX_BODY, REACTIONS, Messaging
from synwevo_core.notifications import Notifier
from synwevo_core.store import now

log = logging.getLogger("synwevo.messaging")
HEARTBEAT_S = 25                # under Cloudflare's ~100 s idle cut and nginx's 300 s read timeout
QUEUE_MAX = 200                 # a tab that cannot keep up resyncs from history; nothing waits on it
MESSAGES_PER_MINUTE = 60
ATTACHMENTS_PER_HOUR = 30
GROUPS_PER_DAY = 20


class MessageHub:
    """Per-user event queues for the open streams of this process. `publish` may be called from
    any thread (the routes run in the thread pool); delivery happens on the event loop."""

    def __init__(self):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def subscribe(self, user_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._subs.setdefault(user_id, set()).add(q)
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

    def publish(self, users: list[str], event: dict) -> None:
        with self._lock:
            loop = self._loop
            targets = [q for u in users for q in self._subs.get(u, ())]
        if not loop or not targets:
            return
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


def build_router(store, ident: Identity, sessions, audit, *, notifier: Notifier) -> APIRouter:
    r = APIRouter(prefix="/api/auth/messaging", tags=["messaging"])
    hub = MessageHub()
    svc = Messaging(store, ident, publish=hub.publish)
    r.hub, r.messaging = hub, svc

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

    # ------------------------------------------------------------------ conversations
    @r.get("/conversations")
    def list_conversations(request: Request, organization_id: str = Query("")):
        return {"items": svc.list_conversations(uid_of(request), organization_id=organization_id or None)}

    @r.get("/unread-count")
    def unread(request: Request):
        return svc.unread_total(uid_of(request))

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
                _notify(mid, "conversation.added", {"actor": svc._name(uid), "conversation": out["name"]}, out)
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

    @r.post("/conversations/{cid}/members")
    def add_members(request: Request, cid: str, body: dict = Body(...)):
        uid = uid_of(request)
        ids = body.get("user_ids") or []
        if not isinstance(ids, list) or not ids or not all(isinstance(x, str) for x in ids) or len(ids) > 50:
            raise ServiceError(400, "invalid_field", "user_ids 必须是 1–50 个用户 id", {"field": "user_ids"})
        before = {m["user_id"] for m in svc.get(cid, uid)["members"]}
        out = svc.add_members(cid, uid, ids)
        added = [m["user_id"] for m in out["members"] if m["user_id"] not in before]
        audit(request, "messaging.group.add", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"added": added})
        for mid in added:
            _notify(mid, "conversation.added", {"actor": svc._name(uid), "conversation": out["name"]}, out)
        return out

    @r.delete("/conversations/{cid}/members/{member_id}")
    def remove_member(request: Request, cid: str, member_id: str):
        uid = uid_of(request)
        out = svc.remove_member(cid, uid, member_id)
        audit(request, "messaging.group.remove", actor_id=uid, object_kind="conversation", object_id=cid, result="ok", meta={"removed": member_id})
        _notify(member_id, "conversation.removed", {"actor": svc._name(uid), "conversation": out["name"]}, out, target_path="/messages")
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
        if set(body) - {"muted", "pinned", "notify"}:
            raise ServiceError(400, "invalid_field", "只能设置 muted、pinned、notify")
        return svc.set_preferences(cid, uid_of(request), muted=body.get("muted"), pinned=body.get("pinned"), notify=body.get("notify"))

    # ------------------------------------------------------------------ messages
    @r.get("/conversations/{cid}/messages")
    def history(request: Request, cid: str, before: int | None = Query(None, ge=1), after: int | None = Query(None, ge=0),
                limit: int = Query(50, ge=1, le=200)):
        return svc.history(cid, uid_of(request), before_seq=before, after_seq=after, limit=limit)

    def _notify(recipient: str, kind: str, params: dict, conv: dict, *, target_path: str | None = None, dedupe: str | None = None) -> None:
        try:
            notifier.emit(recipient, kind, params=params, organization_id=conv["organization_id"], object_kind="conversation", object_id=conv["id"],
                          dedupe_key=dedupe, target={"origin": "accounts", "path": target_path or f"/messages/{conv['id']}"})
        except Exception as e:  # noqa: BLE001 - an inbox item is a courtesy; the message is already stored
            log.warning("messaging inbox item skipped: %s", type(e).__name__)

    @r.post("/conversations/{cid}/messages", status_code=201)
    def send(request: Request, cid: str, body: dict = Body(...)):
        uid = uid_of(request)
        if set(body) - {"body", "client_id", "reply_to", "attachment_id", "mention_ids"}:
            raise ServiceError(400, "invalid_field", "消息包含不支持的字段")
        limited(uid, "msg", MESSAGES_PER_MINUTE, 60)
        mention_ids = body.get("mention_ids") or []
        if not isinstance(mention_ids, list) or len(mention_ids) > 50:
            raise ServiceError(400, "invalid_field", "mention_ids 无效", {"field": "mention_ids"})
        msg, created = svc.send(cid, uid, body=str(body.get("body") or ""), client_id=body.get("client_id"), reply_to=body.get("reply_to"),
                                attachment_id=body.get("attachment_id"), mention_ids=[str(x) for x in mention_ids])
        if created:
            conv = svc.get(cid, uid)
            preview = (msg["body"][:120] if msg["body"] else "") or ("[附件]" if msg.get("attachment") else "")
            for who, why in svc.recipients_for(cid, msg).items():
                reader = store.one("SELECT last_read_seq FROM conversation_members WHERE conversation_id=? AND user_id=?", (cid, who)) or {}
                label = svc._name(uid) if conv["kind"] == "dm" else conv["name"]
                _notify(who, {"dm": "conversation.dm", "mention": "conversation.mention", "group": "conversation.group"}[why],
                        {"sender": svc._name(uid), "conversation": label, "preview": preview}, conv,
                        dedupe=f"conv:{cid}:{who}:{why}:{reader.get('last_read_seq', 0)}")
        return {"message": msg, "created": created}

    @r.patch("/messages/{mid}")
    def edit(request: Request, mid: str, body: dict = Body(...)):
        return svc.edit(mid, uid_of(request), str(body.get("body") or ""))

    @r.delete("/messages/{mid}")
    def delete(request: Request, mid: str):
        uid = uid_of(request)
        out = svc.delete(mid, uid)
        audit(request, "messaging.message.delete", actor_id=uid, object_kind="message", object_id=mid, result="ok", meta={"own": out["sender_id"] == uid})
        return out

    @r.put("/messages/{mid}/reactions")
    def react(request: Request, mid: str, body: dict = Body(...)):
        return svc.react(mid, uid_of(request), str(body.get("emoji") or ""), body.get("on", True) is not False)

    @r.post("/conversations/{cid}/read")
    def mark_read(request: Request, cid: str, body: dict = Body(...)):
        try:
            seq = int(body.get("seq"))
        except (TypeError, ValueError):
            raise ServiceError(400, "invalid_field", "seq 必须是整数", {"field": "seq"}) from None
        return svc.mark_read(cid, uid_of(request), seq)

    @r.get("/search")
    def search(request: Request, q: str = Query("", max_length=200), organization_id: str = Query("")):
        return {"items": svc.search(uid_of(request), q, organization_id=organization_id or None)}

    # ------------------------------------------------------------------ attachments
    @r.post("/conversations/{cid}/attachments", status_code=201)
    async def upload_attachment(request: Request, cid: str, file: UploadFile = File(...)):
        uid = uid_of(request)
        limited(uid, "msg-att", ATTACHMENTS_PER_HOUR, 3600)
        data = await file.read(MAX_ATTACHMENT_BYTES + 1)
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ServiceError(413, "file_too_large", f"附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB 上限")
        filename = Path(file.filename or "file").name[:200] or "file"
        scan = await run_in_threadpool(accept_upload, filename, data)       # 400 infected / 503 scanner down / 413
        out = await run_in_threadpool(lambda: svc.attach(cid, uid, filename=filename, data=data, scan=scan))
        audit(request, "messaging.attachment.upload", actor_id=uid, object_kind="conversation", object_id=cid, result="ok",
              meta={"attachment_id": out["id"], "size": out["size"], "content_type": out["content_type"], "scan": (scan or {}).get("status")})
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

    @r.get("/stream")
    async def stream(request: Request):
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        sid, uid = state["id"], state["user_id"]
        if not await run_in_threadpool(_still_valid, sid, uid):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        q = hub.subscribe(uid)

        async def gen():
            try:
                yield b"retry: 3000\nevent: hello\ndata: {\"heartbeat_s\": " + str(HEARTBEAT_S).encode() + b"}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                        yield f"event: {ev.get('type', 'event')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
                    except asyncio.TimeoutError:
                        if not await run_in_threadpool(_still_valid, sid, uid):
                            yield b"event: end\ndata: {\"reason\": \"session\"}\n\n"
                            break
                        yield b": ping\n\n"
            finally:
                hub.unsubscribe(uid, q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    @r.get("/capabilities")
    def capabilities(request: Request):
        uid_of(request)
        return {"max_body": MAX_BODY, "max_attachment_bytes": MAX_ATTACHMENT_BYTES, "reactions": list(REACTIONS),
                "heartbeat_s": HEARTBEAT_S, "limits": {"messages_per_minute": MESSAGES_PER_MINUTE, "attachments_per_hour": ATTACHMENTS_PER_HOUR, "groups_per_day": GROUPS_PER_DAY}}

    return r
