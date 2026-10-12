"""Platform governance of the personal Agent's content safety (`synwevo_core.agent_safety`).

* The keyword/pattern lists are platform configuration: read with ``platform.config.view``,
  published (a new immutable version with a reason) with ``platform.config.manage``.
* Reports and per-person stop counts are account governance: ``platform.users.view`` to read,
  ``platform.users.manage`` to close a report or suspend / restore a person's Agent access.

Administrators see no conversation here. A report carries the answer's text only when its
reporter chose to include it; a stop is a rule id, a direction and a length. Every change is
audited, with counts and identifiers, never rule bodies or message text.
"""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.agent_safety import AgentAccess, AgentReports, SafetyEvents, SafetyRules
from synwevo_core.audit_change import change
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class RulesPublish(_In):
    rules: list[dict] = Field(max_length=2000)
    reason: str = Field(min_length=4, max_length=500)
    expected_version: int = Field(ge=0)


class ReportDecision(_In):
    decision: Literal["resolved", "dismissed"]
    resolution: str | None = Field(default=None, max_length=1000)


class Suspend(_In):
    reason: str = Field(min_length=4, max_length=500)
    report_id: str | None = Field(default=None, pattern=r"^arp_[a-f0-9]{12}$")


class Lift(_In):
    reason: str = Field(min_length=4, max_length=500)


def build_router(store, audit) -> APIRouter:
    r = APIRouter(prefix="/api/admin/agent-safety", tags=["admin"])
    rules, reports, events, access = SafetyRules(store), AgentReports(store), SafetyEvents(store), AgentAccess(store)

    def actor(request, permission, *, dangerous=False):
        return require_platform(request, permission, store=store, audit=audit, dangerous=dangerous)

    def record(request, who, action, object_kind, object_id, meta):
        audit(request, action, actor_id=who["id"], object_kind=object_kind, object_id=object_id,
              workspace_given=True, result="ok", meta=meta)

    @r.get("/rules")
    def read_rules(request: Request):
        actor(request, "platform.config.view")
        return {**rules.current(), "history": rules.history()}

    @r.put("/rules")
    def publish_rules(request: Request, body: RulesPublish = Body(...)):
        who = actor(request, "platform.config.manage")
        with store.tx():
            who = actor(request, "platform.config.manage")
            out = rules.publish(who["id"], body.rules, reason=body.reason, expected_version=body.expected_version)
            record(request, who, "admin.agent_safety.rules_publish", "agent_safety_rules", str(out["version"]),
                   {"version": out["version"], "rules": len(out["rules"]), "reason": out["reason"]})
        return {**out, "history": rules.history()}

    @r.get("/reports")
    def list_reports(request: Request, status: str = Query("open", pattern="^(open|resolved|dismissed|all)$")):
        actor(request, "platform.users.view")
        items = reports.list(status=None if status == "all" else status)
        return {"items": [{**i, "suspended": access.active(i["user_id"]) is not None} for i in items]}

    @r.post("/reports/{report_id}/decide")
    def decide(request: Request, report_id: str, body: ReportDecision = Body(...)):
        who = actor(request, "platform.users.manage")
        with store.tx():
            who = actor(request, "platform.users.manage")
            out = reports.resolve(report_id, who["id"], decision=body.decision, resolution=body.resolution)
            record(request, who, "admin.agent_safety.report_decide", "agent_report", report_id,
                   {"decision": out["status"], "user_id": out["user_id"]})
        return out

    @r.get("/stops")
    def stops(request: Request, days: int = Query(7, ge=1, le=90)):
        """Per person: how many messages or answers the rules stopped — counts, never text."""
        actor(request, "platform.users.view")
        items = events.summary(since=time.time() - days * 86400)
        return {"days": days, "items": [{**i, "suspended": access.active(i["user_id"]) is not None} for i in items]}

    @r.get("/suspensions")
    def suspensions(request: Request):
        actor(request, "platform.users.view")
        return {"items": store.all("SELECT s.*, u.display_name, u.email FROM agent_access_suspensions s "
                                   "LEFT JOIN users u ON u.id=s.user_id WHERE s.lifted_at IS NULL ORDER BY s.created_at DESC")}

    @r.post("/users/{user_id}/suspend")
    def suspend(request: Request, user_id: str, body: Suspend = Body(...)):
        who = actor(request, "platform.users.manage")
        with store.tx():
            who = actor(request, "platform.users.manage")
            if not store.one("SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL", (user_id,)):
                raise ServiceError(404, "user_not_found", "账户不存在")
            was = bool(store.one("SELECT 1 FROM agent_access_suspensions WHERE user_id=? AND lifted_at IS NULL", (user_id,)))
            out = access.suspend(user_id, who["id"], reason=body.reason, report_id=body.report_id)
            # The administrator's reason is audited like every other account action's (the
            # account detail's timeline shows it); it is the administrator's words, not a message.
            record(request, who, "admin.agent_safety.suspend", "user", user_id,
                   {**change({"suspended": was}, {"suspended": True}),
                    "suspension_id": out["id"], "report_id": body.report_id, "reason": body.reason})
        return out

    @r.post("/users/{user_id}/lift")
    def lift(request: Request, user_id: str, body: Lift = Body(...)):
        who = actor(request, "platform.users.manage")
        with store.tx():
            who = actor(request, "platform.users.manage")
            out = access.lift(user_id, who["id"], reason=body.reason)
            record(request, who, "admin.agent_safety.lift", "user", user_id,
                   {**change({"suspended": True}, {"suspended": False}), "suspension_id": out["id"], "reason": body.reason})
        return out

    return r
