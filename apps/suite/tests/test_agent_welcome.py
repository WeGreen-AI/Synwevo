"""The welcome allowance at the person's first Agent use, through the real Suite factory.

Synthetic prices, fixture CNY unit, no network (the governed fixture refuses any transport).
"""
import json

import pytest

from synwevo_core.billing import Unit

from .test_agent_governed_factory import configure, governed  # noqa: F401 - fixture reuse

CNY = Unit("CNY", 6)


@pytest.fixture
def welcome_host(monkeypatch):
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([{"code": "CNY", "scale": 6}]))
    monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", "100")


def cny(g):
    uid = g.user["id"]
    return g.provider.gateway.ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=CNY).booked


def quote(g):
    return g.agent.quote_turn(g.user, g.conversation["id"], "Hello", parent_id=None, regenerate_of=None,
                              budget=5000, max_calls=3, run_seconds=300)


def test_the_first_quote_grants_one_yuan_once(welcome_host, governed):  # noqa: F811
    g = governed
    configure(g)
    assert g.store.all("SELECT * FROM billing_grants") == []
    quote(g)
    assert cny(g) == 1_000_000, "首次使用 Agent 时赠送 1 元"
    quote(g)
    quote(g)
    assert cny(g) == 1_000_000
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_grants WHERE kind='welcome'")["n"] == 1
    assert g.seen == []


def test_no_allowance_without_host_configuration(governed):  # noqa: F811
    g = governed
    configure(g)
    quote(g)
    assert g.store.all("SELECT * FROM billing_grants") == []
