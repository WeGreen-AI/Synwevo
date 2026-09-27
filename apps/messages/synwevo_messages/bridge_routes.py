"""Application linkage on the messages origin (CI-75): object conversations and the worker that
turns Synwevo Docs' outbox rows into cards.

Everything decided here comes from the shared layer (`synwevo_core.messaging_bridge`): which
object a path names, who may open it, who is in its conversation, what a card says to whom. This
module is the HTTP surface plus the one process-level fact — the worker runs *here*, in the
process that owns the event fan-out, so a card written by the Docs process reaches open streams.

Routes (all under `/api/messaging`, the caller signed in on this origin):

* `GET  /objects/{app}/{kind}/{id}/conversation` — the linked conversation, or 404.
* `POST /objects/{app}/{kind}/{id}/conversation` — the linked conversation, created on first call
  (the caller becomes owner; everyone who may open the object becomes a member) or joined by a
  caller who may open the object but is not yet in it. 404 when the caller cannot open the
  object — never 403, never the title.
* `GET  /conversations/{cid}/object` — what an object conversation is about, as this reader may
  see it (the full summary with a link, or a neutral one).

The shell's path contract: `/objects/{app}/{kind}/{id}` on this origin calls the POST above and
navigates to `/c/{conversation_id}` (docs/platform/messaging.md §8).
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request

from synwevo_core.errors import ServiceError
from synwevo_core.http import Platform
from synwevo_core.messaging_bridge import APPS, BridgeWorker, CONVERSATION_KINDS, ObjectConversations, ensure_app_account
from synwevo_core.store import now

#: Object conversations a person may open or create per hour: each is a membership check and,
#: the first time, a group creation for everyone who may open the object.
OBJECT_OPENS_PER_HOUR = 60


def install(api: FastAPI, plat: Platform, router: APIRouter, *, start_jobs: bool = True) -> BridgeWorker:
    """Mount the bridge routes next to the messaging router and start the outbox worker.

    `router` is what `routes.build_router` returned: its `messaging` service and its `hub` are
    the write path and the fan-out the worker must share. The Docs application account is created
    (once per database) here, at the start of the process that sends its cards.
    """
    store, ident = plat.store, plat.identity
    svc, hub = router.messaging, router.hub
    for app_id in APPS:
        ensure_app_account(store, ident, app_id)
    worker = BridgeWorker(store, ident, svc, publish=hub.publish, start=start_jobs)
    api.state.bridge_worker = worker
    api.state.shutdown_hooks.append(worker.stop)
    objects = ObjectConversations(store, ident, svc)

    r = APIRouter(prefix=router.prefix, tags=["messaging-bridge"])

    def uid_of(request: Request) -> str:
        # The same check the messaging routes make: a live session row, re-read on every call.
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if not current or current["revoked_at"] is not None or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts:
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current["user_id"]

    def _check(app: str, kind: str, oid: str) -> None:
        if app not in APPS or kind not in CONVERSATION_KINDS or not 1 <= len(oid) <= 64:
            # An unknown application or kind is the same nothing as an object one may not open.
            raise ServiceError(404, "no_object", "找不到该对象，或你没有权限查看它")

    @r.get("/objects/{app}/{kind}/{oid}/conversation")
    def get_object_conversation(request: Request, app: str, kind: str, oid: str):
        _check(app, kind, oid)
        return objects.get(app, kind, oid, uid_of(request))

    @r.post("/objects/{app}/{kind}/{oid}/conversation")
    def open_object_conversation(request: Request, app: str, kind: str, oid: str):
        _check(app, kind, oid)
        uid = uid_of(request)
        allowed, wait = ident.window_allows(f"msg-object:{uid}", limit=OBJECT_OPENS_PER_HOUR, window_s=3600)
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": max(1, int(wait or 60))})
        out = objects.open(app, kind, oid, uid)
        if out["created"] or out["joined"]:
            plat.audit(request, "messaging.object.open", actor_id=uid, object_kind="conversation", object_id=out["conversation"]["id"], result="ok",
                       meta={"app": app, "kind": out["object"]["kind"], "object_id": out["object"]["id"], "created": out["created"], "joined": out["joined"],
                             "organization_id": out["conversation"]["organization_id"]})
        return out

    @r.get("/conversations/{cid}/object")
    def conversation_object(request: Request, cid: str):
        uid = uid_of(request)
        svc.get(cid, uid)                                   # membership: 404 for anyone else
        obj = objects.object_of(cid, uid)
        if not obj:
            raise ServiceError(404, "no_object", "这个会话不是关于某个对象的讨论")
        return obj

    api.include_router(r)
    return worker
