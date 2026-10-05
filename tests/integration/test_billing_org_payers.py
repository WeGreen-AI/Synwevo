"""Organization payers in the ledger: the organization's grant and monthly limits decide.

Isolated fixture store, fixture CNY unit and price, fixture payment verifier; no network.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import json

import pytest

from synwevo_core.billing import (BillingError, ChargeContext, Ledger, PaymentReceipt, PriceSnapshot, Rate, Unit,
                                 Usage, UsageEvidence)
from synwevo_core.billing.org_payers import OrgPayers
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store, now

UNIT = Unit("CNY", 2)
START = 2_000_000_000


@pytest.fixture
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-org-payer-fixture")
    ident = Identity(store)
    users = {}
    for key in ("owner", "member", "outsider"):
        u = ident.create_user(f"{key}@example.test", None, status="active", with_personal_workspace=False)
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        users[key] = u["id"]
    created = Organizations(store, ident).create(users["owner"], "Fixture Org", "Fixture WS", "fixture-org-payers")
    oid, wid = created["organization"]["id"], created["workspace"]["id"]
    store.insert("organization_memberships", {"organization_id": oid, "user_id": users["member"], "role": "member",
                                              "created_at": now(), "updated_at": now()})
    ticks = [START]
    receipt = PaymentReceipt("test", "fixture-pay", "fixture-merchant", "pi-org", "evt-org", "order-org", 1000, "CNY",
                             "fixture-inbox", "a" * 64)
    ledger = Ledger(store, environment="test", namespace="agent", clock=lambda: ticks[0], max_hold_seconds=1000,
                    authorize=lambda t, c, a: True,   # fixture: business authority is tested in the Agent suite
                    verify_payment=lambda t, r: r == receipt)
    ledger.org_payers = OrgPayers(ledger)
    ledger.publish_price(PriceSnapshot("fixture-sale", "sale", UNIT, "fixture-provider", "fixture-model",
                                       Rate(1), Rate(1), Rate(1), "fixture-terms", START, per_tokens=1))
    ledger.publish_price(PriceSnapshot("fixture-cost", "cost", UNIT, "fixture-provider", "fixture-model",
                                       Rate(0), Rate(0), Rate(0), "fixture-terms", START, per_tokens=1))
    ledger.create_payment_order(order_id="order-org", payer_user_id=users["owner"], provider="fixture-pay",
                                merchant_id="fixture-merchant", amount_minor=1000, currency="CNY", unit=UNIT,
                                credit_units=1000, exchange_version="cny-fen-identity-v1",
                                payer_kind="organization", payer_org_id=oid)
    ledger.credit_verified_payment(receipt)
    return {"store": store, "ledger": ledger, "users": users, "oid": oid, "wid": wid, "ticks": ticks,
            "payers": ledger.org_payers}


def grant(org, *, enabled=True, limit=300, user="member"):
    payers, store = org["payers"], org["store"]
    with store.tx():
        current = next(m["revision"] for m in payers.summary(org["oid"], UNIT)["members"] if m["user_id"] == org["users"][user])
        return payers.set_member(org["oid"], org["users"][user], unit=UNIT, enabled=enabled, monthly_limit=limit,
                                 expected_revision=current, actor_id=org["users"]["owner"])


def ctx(org, run, *, user="member", workspace=None):
    uid = org["users"][user]
    return ChargeContext(uid, uid, run, "fixture-approval", workspace, payer_kind="organization", organization_id=org["oid"])


def reserve(org, run, amount, *, base=START, **kw):
    ledger = org["ledger"]
    context = ctx(org, run, **kw)
    ledger.open_run(context, unit=UNIT, budget=10**6, max_calls=5, max_attempts=5, deadline=base + 900)
    return ledger.reserve_call(context, logical_call_id="c-" + run, amount=amount, price_id="fixture-sale",
                               deadline=base + 500)


def settle(org, call, amount, run):
    ledger = org["ledger"]
    ticket = ledger.prepare_attempt(call, attempt_id="a-" + run, owner="w", cost_price_id="fixture-cost",
                                    requested_model="fixture-model")
    ledger.dispatch(ticket)
    ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence("fixture-provider", "req-" + run, "ev-" + run,
                                Usage(amount, 0, 0), "fixture-model", "cap", START))


def org_wallet(org):
    return org["ledger"].overview_for_payer(payer_kind="organization", payer_id=org["oid"], units=(UNIT,))["balances"][0]


def test_without_the_organizations_grant_a_member_cannot_charge_it(org):
    with pytest.raises(BillingError, match="org_payer_not_granted"):
        reserve(org, "r1", 10)
    with pytest.raises(BillingError, match="org_payer_not_member"):
        reserve(org, "r2", 10, user="outsider")
    grant(org, enabled=True, limit=300)
    reserve(org, "r3", 10)
    assert org_wallet(org)["held"] == 10


def test_the_monthly_member_limit_counts_spend_and_holds(org):
    grant(org, limit=300)
    call = reserve(org, "r1", 200)
    settle(org, call, 150, "r1")
    assert org_wallet(org)["booked"] == 850
    reserve(org, "r2", 150)                      # 150 spent + 150 held = 300
    with pytest.raises(BillingError, match="org_member_monthly_limit_exceeded"):
        reserve(org, "r3", 1)
    # a new UTC month starts a new allowance (the old hold lapsed at its deadline, the spend is last month's)
    from synwevo_core.billing.org_payers import month_start
    later = month_start(month_start(START) + 32 * 86400) + 60
    org["ticks"][0] = later
    reserve(org, "r4", 300, base=later)
    with pytest.raises(BillingError, match="org_member_monthly_limit_exceeded"):
        reserve(org, "r5", 1, base=later)


def test_withdrawing_the_grant_stops_the_next_send(org):
    grant(org, limit=300)
    call = reserve(org, "r1", 50)
    ledger = org["ledger"]
    ticket = ledger.prepare_attempt(call, attempt_id="a1", owner="w", cost_price_id="fixture-cost",
                                    requested_model="fixture-model")
    grant(org, enabled=False)
    with pytest.raises(BillingError, match="org_payer_not_granted"):
        ledger.dispatch(ticket)


def test_a_workspace_limit_bounds_calls_attributed_to_it(org):
    grant(org, limit=1000)
    payers, store = org["payers"], org["store"]
    with store.tx():
        payers.set_workspace(org["oid"], org["wid"], unit=UNIT, monthly_limit=100, expected_revision=0,
                             actor_id=org["users"]["owner"])
    reserve(org, "r1", 80, workspace=org["wid"])
    with pytest.raises(BillingError, match="org_workspace_monthly_limit_exceeded"):
        reserve(org, "r2", 30, workspace=org["wid"])
    reserve(org, "r3", 30)                      # not attributed to that workspace
    admissions = store.all("SELECT user_id,workspace_id,grant_revision FROM billing_org_admissions ORDER BY rowid")
    assert admissions[0] == {"user_id": org["users"]["member"], "workspace_id": org["wid"], "grant_revision": 1}


def test_payers_for_lists_what_is_left_and_the_summary_names_members(org):
    grant(org, limit=300)
    call = reserve(org, "r1", 100)
    payers = org["payers"].payers_for(org["users"]["member"], UNIT)
    assert payers == [{"organization_id": org["oid"], "name": "Fixture Org", "grant_revision": 1, "monthly_limit": 300,
                       "spent_this_month": 0, "held": 100, "remaining_this_month": 200, "wallet_available": 900,
                       "frozen": False}]
    assert org["payers"].payers_for(org["users"]["outsider"], UNIT) == []
    summary = org["payers"].summary(org["oid"], UNIT)
    member = next(m for m in summary["members"] if m["user_id"] == org["users"]["member"])
    assert member["enabled"] and member["held"] == 100 and summary["wallet"]["available"] == 900
    # No workspace limits are offered: no organization-paid run is attributed to a workspace yet.
    assert "workspaces" not in summary
    assert call
