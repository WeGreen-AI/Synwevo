"""Organization payers through the real Suite factory and governed DeepSeek wiring.

Synthetic CNY prices/funds, fixture payment verifier, SSE through an httpx.MockTransport test
double only (the governed fixture fails on any real transport). No real provider or payment.
"""
import json

import pytest

from synwevo_core.billing import Ledger, PaymentReceipt, Unit
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_core.store import now

from . import test_agent_governed_factory as factory
from .test_agent_governed_factory import governed  # noqa: F401 - fixture reuse

CNY = Unit("CNY", 6)


@pytest.fixture
def cny(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([{"code": "CNY", "scale": 6}]))
    monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", "100")
    monkeypatch.setattr(factory, "UNIT", CNY)


def organization(g):
    people, store = g.suite.people, g.store
    created = Organizations(store, people.ident).create(people.uid("oscar"), "Fixture Payer Org", "Org WS", "fixture-org-agent")
    oid = created["organization"]["id"]
    store.insert("organization_memberships", {"organization_id": oid, "user_id": g.user["id"], "role": "member",
                                              "created_at": now(), "updated_at": now()})
    return oid


def fund_org(g, oid, units=5_000_000):
    receipt = PaymentReceipt("test", "fixture-payment", "fixture-merchant", "fixture-org-txn", "fixture-org-event",
                             "fixture-org-order", 500, "CNY", "fixture-inbox", "a" * 64)
    ledger = Ledger(g.store, environment="test", namespace="agent", verify_payment=lambda t, r: r == receipt)
    ledger.create_payment_order(order_id="fixture-org-order", payer_user_id=g.suite.people.uid("oscar"),
                                provider="fixture-payment", merchant_id="fixture-merchant", amount_minor=500,
                                currency="CNY", unit=CNY, credit_units=units, exchange_version="cny-fen-identity-v1",
                                payer_kind="organization", payer_org_id=oid)
    ledger.credit_verified_payment(receipt)


def grant(g, oid, *, enabled=True, limit=2_000_000):
    payers = g.provider.gateway.ledger.org_payers
    with g.store.tx():
        current = payers._grant(g.store._c(), oid, g.user["id"])
        return payers.set_member(oid, g.user["id"], unit=CNY, enabled=enabled, monthly_limit=limit,
                                 expected_revision=current["revision"] if current else 0, actor_id=g.suite.people.uid("oscar"))


def org_quote(g, oid):
    return g.agent.quote_turn(g.user, g.conversation["id"], "Read my forms", parent_id=None, regenerate_of=None,
                              budget=5000, max_calls=3, run_seconds=300, payer={"kind": "organization", "id": oid})


def balance(g, kind, owner):
    return g.provider.gateway.ledger.overview_for_payer(payer_kind=kind, payer_id=owner, units=(CNY,))["balances"][0]


def test_payers_lists_the_person_first_and_grants_the_welcome_allowance(cny, governed):  # noqa: F811
    g = governed
    factory.configure(g)
    headers = g.suite.sign_in("alice")
    r = g.suite.get("/api/suite/agent/payers", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True and body["currency"] == "CNY" and body["unit"] == {"code": "CNY", "scale": 6}
    assert body["payers"] == [{"kind": "user", "id": g.user["id"], "label": None, "available": "1000000", "frozen": False}]
    assert body["welcome"] == {"state": "granted"}
    assert g.suite.get("/api/suite/agent/payers", headers=headers).json()["welcome"] == {"state": "already"}


def test_an_organization_pays_only_with_its_grant_and_the_person_pays_nothing(cny, governed):  # noqa: F811
    g = governed
    factory.configure(g)
    oid = organization(g)
    fund_org(g, oid)
    with pytest.raises(ServiceError) as refused:
        org_quote(g, oid)
    assert refused.value.code == "billing_org_payer_not_granted", "没有组织的授权，成员不能选组织付款"
    grant(g, oid)
    headers = g.suite.sign_in("alice")
    listed = g.suite.get("/api/suite/agent/payers", headers=headers).json()["payers"]
    assert [p["kind"] for p in listed] == ["user", "organization"]
    assert listed[1]["label"] == "Fixture Payer Org" and listed[1]["remaining_this_month"] == "2000000"
    quote = org_quote(g, oid)
    assert quote["payer"] == {"kind": "organization", "id": oid}
    personal_before = balance(g, "user", g.user["id"])["booked"]
    tid = g.agent.start_turn(g.user, g.conversation["id"], "Read my forms", parent_id=None, regenerate_of=None,
                             quote_id=quote["id"])["turn_id"]
    g.agent._run(tid)
    turn = g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (tid,))
    assert turn["status"] == "succeeded", turn
    call = g.store.one("SELECT c.context,a.kind,a.owner FROM billing_calls c JOIN billing_accounts a ON a.id=c.account_id")
    assert (call["kind"], call["owner"]) == ("org_wallet", oid)
    assert json.loads(call["context"])["organization_id"] == oid, "计量记录付款方"
    assert g.store.one("SELECT user_id FROM billing_org_admissions")["user_id"] == g.user["id"]
    assert balance(g, "organization", oid)["used"] > 0
    assert balance(g, "user", g.user["id"])["booked"] == personal_before, "组织付款不动个人钱包"


def test_a_withdrawn_grant_stops_the_send_before_any_network(cny, governed):  # noqa: F811
    g = governed
    factory.configure(g)
    oid = organization(g)
    fund_org(g, oid)
    grant(g, oid)
    quote = org_quote(g, oid)
    tid = g.agent.start_turn(g.user, g.conversation["id"], "Read my forms", parent_id=None, regenerate_of=None,
                             quote_id=quote["id"])["turn_id"]
    grant(g, oid, enabled=False)
    g.agent._run(tid)
    assert g.seen == [], "撤回授权后不再发出模型请求"
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (tid,))["status"] in ("failed", "cancelled")
    assert balance(g, "organization", oid)["used"] == 0
