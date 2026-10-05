"""The one-time welcome allowance: off unless configured, once per account and per address.

Isolated fixture store, fixture CNY unit, no payment, no model, no network.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from synwevo_core.billing import Ledger, Unit
from synwevo_core.billing.welcome import WelcomeAllowance, abuse_key, normalized_address
from synwevo_core.billing.wallet_config import cny_unit, fen_to_units, units_from_env
from synwevo_core.identity import Identity
from synwevo_core.store import Store, now

CNY = Unit("CNY", 6)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    store = Store(tmp_path / "isolated-welcome-fixture")
    ident = Identity(store)
    ledger = Ledger(store, environment="test", namespace="agent")
    return store, ident, ledger


def person(store, ident, email, *, verified=True):
    user = ident.create_user(email, None, status="active", with_personal_workspace=False)
    if verified:
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
    return user["id"]


def wallet(ledger, uid):
    return ledger.balance_for_user(requester_user_id=uid, payer_user_id=uid, unit=CNY).booked


def test_nothing_is_granted_unless_the_host_configures_an_amount_and_a_cny_unit(world, monkeypatch):
    store, ident, ledger = world
    monkeypatch.delenv("SYNWEVO_BILLING_WELCOME_MINOR", raising=False)
    assert WelcomeAllowance.from_host(ledger, (CNY,)) is None
    monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", "100")
    assert WelcomeAllowance.from_host(ledger, (Unit("USD", 2),)) is None, "只有 CNY 钱包单位"
    assert WelcomeAllowance.from_host(ledger, (Unit("CNY", 1),)) is None, "精度至少到分"
    assert WelcomeAllowance.from_host(None, (CNY,)) is None
    for bad in ("0", "-1", "1.5", "0100", "10001", "abc", " "):
        monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", bad)
        assert WelcomeAllowance.from_host(ledger, (CNY,)) is None, bad
    monkeypatch.setenv("SYNWEVO_BILLING_WELCOME_MINOR", "100")
    welcome = WelcomeAllowance.from_host(ledger, (CNY,))
    assert welcome.amount == 1_000_000 and welcome.amount_minor == 100, "1 元 = 100 分 = 10^6 个 6 位精度单位"


def test_a_verified_account_gets_exactly_one_yuan_once(world):
    store, ident, ledger = world
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100)
    uid = person(store, ident, "first@example.test")
    assert welcome.ensure(uid)["state"] == "granted"
    assert wallet(ledger, uid) == fen_to_units(100, CNY)
    for _ in range(3):
        assert welcome.ensure(uid)["state"] == "already"
    assert wallet(ledger, uid) == 1_000_000
    row = store.one("SELECT kind,actor_id,reason,payer_kind FROM billing_grants WHERE payer_id=?", (uid,))
    assert row == {"kind": "welcome", "actor_id": "system:welcome",
                   "reason": "welcome allowance (one per account)", "payer_kind": "user"}


def test_unverified_inactive_and_application_accounts_get_nothing(world):
    store, ident, ledger = world
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100)
    pending = person(store, ident, "pending@example.test", verified=False)
    assert welcome.ensure(pending)["state"] == "email_unverified" and wallet(ledger, pending) == 0
    suspended = person(store, ident, "suspended@example.test")
    store.execute("UPDATE users SET status='suspended' WHERE id=?", (suspended,))
    assert welcome.ensure(suspended)["state"] == "ineligible"
    assert welcome.ensure("u_missing")["state"] == "ineligible"
    assert store.one("SELECT COUNT(*) AS n FROM billing_grants")["n"] == 0


def test_the_same_mailbox_on_a_new_account_does_not_pay_twice(world):
    store, ident, ledger = world
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100)
    first = person(store, ident, "Someone@Example.test")
    assert welcome.ensure(first)["state"] == "granted"
    # the first account is deleted; the mailbox registers again, with and without a +tag
    store.execute("UPDATE users SET email='deleted-1@invalid.test', status='deleted', deleted_at=? WHERE id=?", (now(), first))
    again = person(store, ident, "someone+again@example.test")
    assert normalized_address("someone+again@example.test") == normalized_address("Someone@Example.test")
    assert abuse_key("someone+again@example.test") == abuse_key("SOMEONE@example.test")
    assert welcome.ensure(again)["state"] == "already" and wallet(ledger, again) == 0


def test_a_daily_ceiling_bounds_a_burst_of_new_accounts(world):
    store, ident, ledger = world
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100, daily_cap=2)
    users = [person(store, ident, f"burst-{i}@example.test") for i in range(3)]
    assert [welcome.ensure(u)["state"] for u in users] == ["granted", "granted", "capacity"]
    assert wallet(ledger, users[2]) == 0


def test_concurrent_first_uses_grant_once(world):
    store, ident, ledger = world
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100)
    uid = person(store, ident, "racer@example.test")
    with ThreadPoolExecutor(8) as pool:
        states = list(pool.map(lambda _: welcome.ensure(uid)["state"], range(8)))
    assert states.count("granted") == 1 and set(states) <= {"granted", "already"}
    assert wallet(ledger, uid) == 1_000_000
    assert store.one("SELECT COUNT(*) AS n FROM billing_journal WHERE kind='grant'")["n"] == 1


def test_wallet_units_are_read_from_the_host_without_defaults(monkeypatch):
    monkeypatch.delenv("SYNWEVO_BILLING_WALLET_UNITS", raising=False)
    assert units_from_env() == ()
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([{"code": "CNY", "scale": 6}]))
    assert cny_unit(units_from_env()) == CNY
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", '[{"code":"CNY","scale":6,"code":"X"}]')
    assert units_from_env() == ()
