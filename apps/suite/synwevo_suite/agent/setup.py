"""The one-time "start using Synwevo Agent" step (send-and-go, D-5): what a person still has to
do before their first automatic paid run, and the terms acceptance it records.

The step is an inline card in the chat, not a dialog before every send. It shows only what is
missing: the separate model-provider data consent (recorded by ``agent.provider_consent.accept``,
unchanged), the one-time acceptance of the published terms, AI use rules and pricing (recorded
here, ``agent.terms.accept``, with the step's operation run as context), and — for the personal
payer — the personal spending limits, prefilled with the suggested values below and saved through
``agent.billing.budget.set``. Nothing here grants payment authority or moves money: the send
itself is admitted by the engine, which re-reads every one of these facts.
"""
from __future__ import annotations

from synwevo_core.agent_safety import PROVIDER_TERMS, ProviderConsents
from synwevo_core.billing import BillingError, Unit
from synwevo_core.billing.wallet_config import fen_to_units
from synwevo_core.errors import ServiceError
from synwevo_core.terms import TermsAcceptances, TermsVersionChanged, current_documents

#: Suggested personal spending limits, in fen (CNY 10 a day, 30 a week, 100 a month), shown
#: prefilled and editable in the start step; converted exactly into the sale unit. The owner can
#: change these; they are suggestions the person confirms, never a policy applied silently.
SUGGESTED_LIMITS_FEN = {"daily_budget": 1_000, "weekly_budget": 3_000, "monthly_budget": 10_000}


def suggested_limits(unit: Unit | None) -> dict | None:
    """The suggested limits in integer units of the sale unit (decimal strings), or None when the
    sale unit is not a CNY wallet unit (no exchange rate is ever assumed)."""
    if unit is None:
        return None
    try:
        return {name: str(fen_to_units(fen, unit)) for name, fen in SUGGESTED_LIMITS_FEN.items()}
    except BillingError:
        return None


def terms_status(store, user_id: str) -> dict:
    accepted = TermsAcceptances(store).accepted(user_id)
    documents = [{**d, "accepted": accepted[d["id"]]} for d in current_documents()]
    return {"complete": all(d["accepted"] for d in documents), "documents": documents}


def setup_status(agent, store, user_id: str) -> dict:
    """``agent.setup.get``: whether the start step is needed at all (a paid, governed model), and
    which of its items the person has completed, computed for the personal payer."""
    quotes = getattr(agent.provider, "quotes", None)
    required = quotes is not None
    credential = agent._credential(user_id)
    provider = None
    if credential is not None and credential.provider in PROVIDER_TERMS:
        status = ProviderConsents(store).status(user_id, credential.provider)
        provider = {"provider": credential.provider, "version": status["version"], "accepted": bool(status["accepted"]),
                    "accepted_version": status["accepted_version"]}
    terms = terms_status(store, user_id)
    limits = {"configured": False, "enabled": False, "revision": 0, "unit": None, "suggested": None}
    if required:
        unit = None
        try:
            offer = quotes.offer()
            unit = Unit(**offer["unit"])
        except BillingError:
            pass
        service = getattr(quotes.ledger, "payer_budgets", None)
        view = None
        if service is not None and unit is not None:
            try:
                view = next((b for b in service.read(user_id)["budgets"] if b["unit"]["code"] == unit.code), None)
            except BillingError:
                view = None
        limits = {"configured": bool(view and view["configured"]), "enabled": bool(view and view["enabled"]),
                  "revision": int(view["revision"]) if view else 0,
                  "unit": {"code": unit.code, "scale": unit.scale} if unit is not None else None,
                  "suggested": suggested_limits(unit)}
    complete = ((provider is None or provider["accepted"])
                and (not required or (terms["complete"] and limits["configured"] and limits["enabled"])))
    return {"required": required, "complete": complete, "provider_consent": provider, "terms": terms, "limits": limits}


def accept_terms(store, user_id: str, documents: list, *, context: str) -> dict:
    """``agent.terms.accept``: record the person's explicit acceptance of every document at its
    version in force. A stale or incomplete list records nothing and names the current versions."""
    versions = {}
    for item in documents:
        if item["id"] in versions:
            raise ServiceError(409, "terms_version_changed", "条款版本已更新，请查看并接受当前版本",
                               {"documents": current_documents()})
        versions[item["id"]] = item["version"]
    try:
        TermsAcceptances(store).accept(user_id, versions, context=context)
    except TermsVersionChanged as changed:
        raise ServiceError(409, "terms_version_changed", "条款版本已更新，请查看并接受当前版本",
                           {"documents": changed.documents}) from None
    return terms_status(store, user_id)
