"""Moderation of reported messages — the half of messaging that stays on the account service.

Since CI-66 conversations are served by Synwevo Message on its own origin (`/api/messaging/…`,
`apps/messages`). Reviewing a report is different in kind from reading a conversation: it needs
`org.messaging.manage` in the organization and a **strict, fresh re-authentication on the account
session** — the same ceremony as deciding an approval or removing a member, performed by the
organization page on the account origin. That ceremony belongs to the account service, so these
two routes keep their old address (`/api/auth/messaging/reports…`) and reuse the shared service
layer; the reviewer never enters the conversation, and the snapshot they see was fixed when the
report was made.

What the reviewer cannot get here: the live push of the deletion to tabs streaming on the
messages origin. The event fan-out is per process, so a removed message shows as deleted in an
open thread at its next resync (the client resyncs on every reconnect and on any conversation
event), not the same instant.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.messaging import REPORT_REASONS, Messaging, conversation_target
from synwevo_core.notifications import Notifier
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import Sessions
from synwevo_core.store import now

log = logging.getLogger("synwevo.messaging")
REAUTH_SCOPE = "admin"          # reviewing a report is an organization write like deciding an approval


def build_router(store, ident: Identity, sessions, audit, *, notifier: Notifier) -> APIRouter:
    r = APIRouter(prefix="/api/auth/messaging", tags=["messaging"])
    svc = Messaging(store, ident)          # no fan-out here: open streams live in the messages process
    orgs = Organizations(store, ident)

    def session(request: Request) -> dict:
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if (not current or current["revoked_at"] is not None or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current

    def _moderator(request: Request, oid: str, *, sensitive: bool = False) -> str:
        s = session(request)
        if sensitive and not Sessions.reauth_fresh(s, REAUTH_SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": REAUTH_SCOPE})
        orgs.require(oid, s["user_id"], permission="org.messaging.manage", active=True)
        return s["user_id"]

    @r.get("/reports")
    def list_reports(request: Request, organization_id: str = Query(...), status: str = Query("open", pattern="^(open|removed|dismissed|all)$")):
        """Reports of an organization, for holders of org.messaging.manage: the reported message's
        snapshot, sender, reporter and reason — never the rest of the conversation."""
        _moderator(request, organization_id)
        return {"items": svc.reports_for_organization(organization_id, status=None if status == "all" else status), "reasons": list(REPORT_REASONS)}

    @r.post("/reports/{rid}/review")
    def review_report(request: Request, rid: str, body: dict = Body(...)):
        current = svc.report_get(rid)
        uid = _moderator(request, current["organization_id"], sensitive=True)
        out = svc.review_report(rid, uid, str(body.get("decision") or ""), body.get("note"))
        audit(request, "messaging.report.review", actor_id=uid, object_kind="message_report", object_id=rid, result="ok",
              meta={"decision": out["status"], "message_id": out["message_id"], "organization_id": out["organization_id"]})
        try:
            notifier.emit(out["reporter"]["user_id"], f"conversation.report_{out['status']}", params={"note": out["decision_note"] or ""},
                          organization_id=out["organization_id"], object_kind="message_report", object_id=rid, dedupe_key=f"report:{rid}:reviewed",
                          target=conversation_target(store, out["conversation_id"]))
        except Exception as e:  # noqa: BLE001
            log.warning("report outcome inbox item skipped: %s", type(e).__name__)
        return out

    return r
