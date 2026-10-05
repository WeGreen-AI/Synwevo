"""The one-time welcome allowance: off unless configured, once per account and per address.

Isolated fixture store, fixture CNY unit, no payment, no model, no network.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
from queue import Queue
from threading import Barrier, BrokenBarrierError

import pytest

from synwevo_core.billing import BillingError, Ledger, Unit
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


def _welcome_grant(ledger, uid, **kwargs):
    return ledger.grant(grant_id="welcome:" + uid, kind="welcome", payer_kind="user", payer_id=uid,
                        unit=CNY, amount=1_000_000, reason="fixture welcome allowance", actor_id="system:welcome",
                        abuse_key="fixture-address:" + uid, **kwargs)


def _welcome_racer(root, uid, start, counts, results, entrypoint):
    """FIXTURE: separate connections, with a rendezvous after reading the daily count.
    The database writer lock serialises this read with issuance: after the rendezvous times
    out and the first writer commits, the other must read the committed count.
    """
    store = Store(root)
    ledger = Ledger(store, environment="test", namespace="agent")
    original = ledger.count_grants

    def read_together(**kwargs):
        with store.conn() as connection:
            assert connection.in_transaction, "daily capacity must be checked in the grant transaction"
        count = original(**kwargs)
        try:
            counts.wait(timeout=1)
        except BrokenBarrierError:
            pass
        return count

    ledger.count_grants = read_together
    start.wait(timeout=15)
    if entrypoint == "allowance":
        state = WelcomeAllowance(ledger, unit=CNY, amount_minor=100, daily_cap=1).ensure(uid)["state"]
    else:
        try:
            _welcome_grant(ledger, uid, welcome_daily_cap=1)
            state = "granted"
        except BillingError as exc:
            assert exc.code == "welcome_capacity"
            state = "capacity"
    results.put(state)


@pytest.mark.parametrize("concurrency", ["threads", "processes"])
@pytest.mark.parametrize("entrypoint", ["allowance", "ledger"])
def test_daily_cap_is_shared_by_concurrent_accounts_and_stores(world, concurrency, entrypoint):
    store, ident, ledger = world
    users = [person(store, ident, f"concurrent-cap-{i}@example.test") for i in range(2)]
    if concurrency == "threads":
        start, counts, results = Barrier(3), Barrier(2), Queue()
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(_welcome_racer, store.root, uid, start, counts, results, entrypoint) for uid in users]
            start.wait(timeout=15)
            for task in tasks:
                task.result(timeout=15)
    else:
        context = multiprocessing.get_context("spawn")
        start, counts, results = context.Barrier(3), context.Barrier(2), context.Queue()
        processes = [context.Process(target=_welcome_racer, args=(store.root, uid, start, counts, results, entrypoint))
                     for uid in users]
        try:
            for process in processes:
                process.start()
            start.wait(timeout=15)
            for process in processes:
                process.join(timeout=15)
                assert process.exitcode == 0
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
    states = [results.get(timeout=5) for _ in users]
    if concurrency == "processes":
        results.close()
        results.join_thread()
    assert sorted(states) == ["capacity", "granted"]
    assert sorted(wallet(ledger, uid) for uid in users) == [0, 1_000_000]
    assert store.one("SELECT COUNT(*) AS n FROM billing_grants WHERE kind='welcome'")["n"] == 1
    assert store.one("SELECT COUNT(*) AS n FROM billing_journal WHERE kind='grant'")["n"] == 1


def test_a_failed_grant_rolls_back_and_does_not_consume_capacity(world, monkeypatch):
    store, ident, ledger = world
    uid = person(store, ident, "retry-grant@example.test")
    welcome = WelcomeAllowance(ledger, unit=CNY, amount_minor=100, daily_cap=1)
    post = ledger._post

    def fail_after_post(*args, **kwargs):
        post(*args, **kwargs)
        raise OSError("fixture: failed grant transaction")

    with monkeypatch.context() as patch:
        patch.setattr(ledger, "_post", fail_after_post)
        with pytest.raises(OSError, match="fixture"):
            welcome.ensure(uid)
    assert ledger.count_grants(kind="welcome", since=0) == 0
    assert wallet(ledger, uid) == 0
    other = WelcomeAllowance(Ledger(store, environment="test", namespace="agent"), unit=CNY,
                             amount_minor=100, daily_cap=1)
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(other.ensure, uid).result(timeout=10)["state"] == "granted"
    assert wallet(ledger, uid) == 1_000_000


def test_ledger_cap_uses_a_rolling_day_and_replays_do_not_consume_capacity(world):
    store, ident, ledger = world
    users = [person(store, ident, f"rolling-cap-{i}@example.test") for i in range(2)]
    ticks = [2_000_000_000]
    ledger.clock = lambda: ticks[0]
    first = _welcome_grant(ledger, users[0], welcome_daily_cap=1)
    # Even after the administrator closes new issuance, replay is the same grant.
    assert _welcome_grant(ledger, users[0], welcome_daily_cap=0) == {**first, "created": False}
    ticks[0] += 86400
    with pytest.raises(BillingError, match="^welcome_capacity$"):
        _welcome_grant(ledger, users[1], welcome_daily_cap=1)
    assert wallet(ledger, users[1]) == 0
    ticks[0] += 1
    assert _welcome_grant(ledger, users[1], welcome_daily_cap=1)["created"] is True
    assert ledger.count_grants(kind="welcome", since=0) == 2


def test_ledger_cap_is_environment_wide_and_optional_for_existing_callers(world):
    store, ident, ledger = world
    first = person(store, ident, "legacy-grant@example.test")
    second = person(store, ident, "other-grant@example.test")
    # Existing callers omit the cap; their welcome grants still count against a later cap.
    _welcome_grant(ledger, first)
    other = Ledger(Store(store.root), environment="test", namespace="other-grant-issuer")
    with pytest.raises(BillingError, match="^welcome_capacity$"):
        _welcome_grant(other, second, welcome_daily_cap=1)
    live = Ledger(store, environment="live", namespace="other-grant-issuer")
    assert _welcome_grant(live, second, welcome_daily_cap=1)["created"] is True
    assert wallet(ledger, second) == 0


@pytest.mark.parametrize("cap", [-1, True, 1.5, "1", 2**63])
def test_ledger_welcome_cap_requires_a_nonnegative_integer(world, cap):
    store, ident, ledger = world
    uid = person(store, ident, "invalid-cap@example.test")
    with pytest.raises(BillingError, match="^invalid_integer$"):
        _welcome_grant(ledger, uid, welcome_daily_cap=cap)
    assert ledger.count_grants(kind="welcome", since=0) == 0


def test_welcome_cap_zero_refuses_new_grants_and_is_not_an_admin_grant_option(world):
    store, ident, ledger = world
    uid = person(store, ident, "closed-cap@example.test")
    assert WelcomeAllowance(ledger, unit=CNY, amount_minor=100, daily_cap=0).ensure(uid) == {"state": "capacity"}
    with pytest.raises(BillingError, match="^invalid_grant$"):
        ledger.grant(grant_id="admin-cap", kind="admin", payer_kind="user", payer_id=uid, unit=CNY,
                     amount=1_000_000, reason="fixture admin grant", actor_id="fixture-admin", welcome_daily_cap=1)
    assert ledger.count_grants(kind="welcome", since=0) == 0
    assert store.all("SELECT * FROM billing_journal") == []


def test_wallet_units_are_read_from_the_host_without_defaults(monkeypatch):
    monkeypatch.delenv("SYNWEVO_BILLING_WALLET_UNITS", raising=False)
    assert units_from_env() == ()
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([{"code": "CNY", "scale": 6}]))
    assert cny_unit(units_from_env()) == CNY
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", '[{"code":"CNY","scale":6,"code":"X"}]')
    assert units_from_env() == ()
