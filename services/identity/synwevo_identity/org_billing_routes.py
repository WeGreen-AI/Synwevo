"""An organization's payments for its members: wallet, grants, limits and statement.

Who: a person who currently holds ``org.billing.manage`` in the active organization (owners hold
it; others by a custom role). Organization admins do not hold it by default: paying is a
decision about money, not about membership. Checked again inside the transaction of every change.

  GET /api/auth/organizations/{oid}/billing
      the organization wallet (booked/held/available/frozen), every member's grant (enabled,
      monthly limit, spent and held this UTC month)
  PUT /api/auth/organizations/{oid}/billing/members/{uid}
      {enabled, monthly_limit_minor, expected_revision}: let a member charge the organization
  GET /api/auth/organizations/{oid}/billing/statement?after_seq=&limit=
      the organization wallet's journal (top-ups, grants, sales, chargebacks) and, for each sale,
      which member it was for — amounts and metadata, never conversation content

No per-workspace limit is offered. The ledger can bound calls attributed to a workspace
(``OrgPayers.check``), but no organization-paid Agent run is attributed to one: a conversation may
reach any of the person's workspaces, and a workspace the member merely named would not stop a
call that works in another. A limit set here would bind nothing, so there is none to set until
runs are attributed (docs/platform/wallet-payments.md).

Amounts in requests are fen (CNY minor units); responses carry integer wallet units as decimal
strings with the unit's scale, like the personal wallet. Top-ups use the wallet top-up routes with
``payer_kind=organization`` (payment_routes.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request, Response

from synwevo_core.billing import BillingError, Ledger
from synwevo_core.billing.org_payers import OrgPayers
from synwevo_core.billing.wallet_config import cny_unit, environment_from_env, fen_to_units, units_from_env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.org_roles import OrgRoles
from synwevo_core.sessions import Sessions

PERMISSION = "org.billing.manage"
MAX_LIMIT_MINOR = 100_000_000      # CNY 1,000,000 a month


def _units(value: int) -> str:
    return str(value)


def build_router(ident: Identity, sessions: Sessions, audit) -> APIRouter:
    router = APIRouter(prefix="/api/auth/organizations/{oid}/billing", tags=["organization"])
    store = ident.store
    roles = OrgRoles(store)
    environment = environment_from_env()
    unit = cny_unit(units_from_env())
    ledger = Ledger(store, environment=environment, namespace="org-billing") if environment else None
    payers = OrgPayers(ledger) if ledger else None

    def manager(request: Request, oid: str) -> dict:
        live = sessions.current(getattr(request.state, "session", None))
        user = ident.get_user(live["user_id"]) if live else None
        if not user or user["status"] != "active" or user.get("deleted_at") is not None:
            raise ServiceError(401, "unauthorized", "请先登录可用账户")
        org = store.one("SELECT status,deleted_at FROM organizations WHERE id=?", (oid,))
        if not org or org["deleted_at"] is not None or not roles.has(oid, user["id"], PERMISSION):
            raise ServiceError(403, "org_billing_forbidden", "你没有管理该组织付费的权限")
        if org["status"] != "active":
            raise ServiceError(409, "organization_unavailable", "该组织当前不可用")
        if payers is None or unit is None:
            raise ServiceError(503, "org_billing_not_configured", "组织付费尚未开放")
        return user

    def limit_of(body: dict, *, nullable: bool) -> int | None:
        value = body.get("monthly_limit_minor")
        if value is None and nullable:
            return None
        if type(value) is not int or not 0 <= value <= MAX_LIMIT_MINOR:
            raise ServiceError(400, "org_billing_invalid", "额度须为 0 至 1000000 元之间的金额")
        return fen_to_units(value, unit)

    def revision_of(body: dict) -> int:
        value = body.get("expected_revision")
        if type(value) is not int or value < 0:
            raise ServiceError(400, "org_billing_invalid", "额度须为 0 至 1000000 元之间的金额")
        return value

    def refusal(exc: BillingError) -> ServiceError:
        if exc.code == "org_payer_conflict":
            return ServiceError(409, "org_billing_conflict", "付费设置已被他人修改，请刷新后重试")
        if exc.code == "org_payer_not_member":
            return ServiceError(409, "org_billing_not_member", "只能授权本组织的有效成员用组织付费")
        return ServiceError(409, "organization_unavailable", "该组织当前不可用")

    def private(response: Response) -> None:
        response.headers["Cache-Control"] = "private, no-store"

    def view(oid: str) -> dict:
        summary = payers.summary(oid, unit)
        wallet = summary["wallet"]
        return {"organization_id": oid, "environment": environment, "currency": "CNY", "unit": summary["unit"],
                "month_start": summary["month_start"],
                "wallet": {**{k: _units(wallet[k]) for k in ("booked", "held", "available")}, "frozen": wallet["frozen"]},
                "members": [{**m, **{k: _units(m[k]) for k in ("monthly_limit", "spent_this_month", "held")}}
                            for m in summary["members"]]}

    @router.get("")
    def read(oid: str, request: Request, response: Response):
        manager(request, oid)
        private(response)
        return view(oid)

    @router.put("/members/{uid}")
    def set_member(oid: str, uid: str, request: Request, response: Response, body: dict = Body(...)):
        user = manager(request, oid)
        if set(body) - {"enabled", "monthly_limit_minor", "expected_revision"} or type(body.get("enabled")) is not bool:
            raise ServiceError(400, "org_billing_invalid", "额度须为 0 至 1000000 元之间的金额")
        limit, revision = limit_of(body, nullable=False), revision_of(body)
        try:
            with store.tx():
                user = manager(request, oid)
                saved = payers.set_member(oid, uid, unit=unit, enabled=body["enabled"], monthly_limit=limit,
                                          expected_revision=revision, actor_id=user["id"])
                audit(request, "org.billing.member_grant", actor_id=user["id"], object_kind="organization",
                      object_id=oid, workspace_given=True, result="ok",
                      meta={"member_id": uid, "enabled": body["enabled"], "monthly_limit_minor": body["monthly_limit_minor"],
                            "revision": saved["revision"]})
        except BillingError as exc:
            raise refusal(exc) from None
        private(response)
        return view(oid)

    @router.get("/statement")
    def statement(oid: str, request: Request, response: Response,
                  after_seq: int = Query(0, ge=0, le=2**63 - 1), limit: int = Query(50, ge=1, le=100)):
        manager(request, oid)
        rows = ledger.statement_for_payer(payer_kind="organization", payer_id=oid, after_seq=after_seq, limit=limit + 1)
        page = rows[:limit]
        attribution = {}
        ids = [r["id"] for r in page if r["kind"] == "sale"]
        if ids:
            marks = ",".join("?" * len(ids))
            for r in store.all("SELECT s.journal_id,a.user_id,u.email FROM billing_settlements s "
                               "JOIN billing_org_admissions a ON a.call_key=s.call_key LEFT JOIN users u ON u.id=a.user_id "
                               f"WHERE s.journal_id IN ({marks})", tuple(ids)):
                attribution[r["journal_id"]] = {"member_id": r["user_id"], "member_email": r["email"]}
        private(response)
        return {"organization_id": oid, "environment": environment, "unit": {"code": unit.code, "scale": unit.scale},
                "items": [{"seq": str(r["seq"]), "id": r["id"], "kind": r["kind"], "delta": str(r["delta"]),
                           "created_at": r["created_at"], **attribution.get(r["id"], {})} for r in page],
                "next_after_seq": str(page[-1]["seq"]) if len(rows) > limit else None}

    return router
