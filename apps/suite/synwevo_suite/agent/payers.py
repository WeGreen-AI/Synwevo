"""Who may pay for this person's Agent runs: GET /api/suite/agent/payers (org-payments).

The shared contract for the chat (package chat-ux): the payers the signed-in person may charge,
each with what is left —

  {"configured": bool, "environment", "currency": "CNY", "unit": {code, scale} | null,
   "payers": [
     {"kind": "user", "id": <user id>, "label": null, "available": "<units>", "frozen": bool},
     {"kind": "organization", "id": <org id>, "label": <organization name>,
      "available": "<org wallet units available>", "frozen": bool,
      "monthly_limit": "<units>", "spent_this_month": "<units>", "held": "<units>",
      "remaining_this_month": "<units>"} ...],
   "welcome": {"state": ...} | null}

Amounts are decimal strings of integer wallet units (``unit.scale`` decimal places), as on the
wallet page. The personal payer is always listed first and is the default. Opening the Agent is
its first use, so this is also where the one-time welcome allowance is granted when configured.
Nothing here grants payment authority: a run's quote names its payer and the ledger rechecks the
organization's grant and limits at every reservation and send.
"""
from __future__ import annotations

from fastapi import Request

from synwevo_core.billing import BillingError
from synwevo_core.billing.wallet_config import cny_unit
from synwevo_core.errors import ServiceError


def payers(agent, user_id: str) -> dict:
    quotes = getattr(agent.provider, "quotes", None)
    if quotes is None:
        return {"configured": False, "environment": None, "currency": "CNY", "unit": None, "payers": [], "welcome": None}
    ledger = quotes.ledger
    unit = cny_unit(getattr(agent.provider, "wallet_units", ()))
    if unit is None:
        return {"configured": False, "environment": ledger.environment, "currency": "CNY", "unit": None,
                "payers": [], "welcome": None}
    welcome = quotes.welcome.ensure(user_id) if quotes.welcome is not None else None
    try:
        mine = ledger.overview_for_user(requester_user_id=user_id, payer_user_id=user_id, units=(unit,))["balances"][0]
    except BillingError:
        raise ServiceError(503, "wallet_unavailable", "个人钱包暂不可用，请稍后再试") from None
    out = [{"kind": "user", "id": user_id, "label": None, "available": str(mine["available"]), "frozen": mine["frozen"]}]
    if ledger.org_payers is not None:
        for org in ledger.org_payers.payers_for(user_id, unit):
            out.append({"kind": "organization", "id": org["organization_id"], "label": org["name"],
                        "available": str(org["wallet_available"]), "frozen": org["frozen"],
                        "monthly_limit": str(org["monthly_limit"]), "spent_this_month": str(org["spent_this_month"]),
                        "held": str(org["held"]), "remaining_this_month": str(org["remaining_this_month"])})
    return {"configured": True, "environment": ledger.environment, "currency": "CNY",
            "unit": {"code": unit.code, "scale": unit.scale}, "payers": out,
            "welcome": {"state": welcome["state"]} if welcome else None}


def install(ctx, agent) -> None:
    from ..common.web import signed_in

    @ctx.api.get("/api/suite/agent/payers", tags=["agent"])
    def list_payers(request: Request):
        """The payers this person may charge for an Agent run, with remaining limits."""
        user = signed_in(request)
        ctx.capabilities.authorize(ctx.registry.get("agent.conversation.list"), user["id"], None)
        return payers(agent, user["id"])
