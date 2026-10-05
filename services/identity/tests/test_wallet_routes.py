"""Isolated HTTP wallet projections, NOT real payment or charging acceptance.

The real identity factory, password login, session gate and Ledger projections run against
temporary Stores. Synthetic journal facts exercise the real balanced-entry/cache triggers;
they are not verified payment receipts. Fixture prices/usage/holds never contact a model,
payment provider, browser, or existing data directory. Only this test mounts the router.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
import json
import sqlite3
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synwevo_core.billing import (ChargeContext, Ledger, PriceSnapshot, Rate, Unit,
                                 Usage, UsageEvidence)
from synwevo_core.billing.models import MAX_INTEGER
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, now
from synwevo_identity.app import create_app
from synwevo_identity.wallet_routes import build_router, from_host

from .platform_fixture import make_platform_admin

ROOT = "/api/auth/wallet"
STATEMENT = ROOT + "/statement"
UNIT = Unit("FIXTURE-WALLET", 6)
OTHER_UNIT = Unit("FIXTURE-OTHER", 2)
EMPTY_UNIT = Unit("FIXTURE-EMPTY", 18)
COST_UNIT = Unit("FIXTURE-PRIVATE-COST", 8)
UNITS = (UNIT, OTHER_UNIT, EMPTY_UNIT)
PW = "wallet-http-fixture-passphrase-123"
PRIVATE = "fixture-private-chat-space-secret-evidence"


def no_wallet_audit(*args: Any, **kwargs: Any) -> None:
    """Assertion hook: a read must not copy wallet details into the audit log."""
    raise AssertionError("wallet projection unexpectedly audited")


@dataclass
class WalletFixture:
    app: FastAPI
    client: TestClient
    ident: Identity
    sessions: Sessions
    store: Store
    ledger: Ledger
    users: list[dict[str, Any]]

    @property
    def uid(self) -> str:
        return self.users[0]["id"]

    def login(self, index: int = 0) -> None:
        self.client.cookies.clear()
        user = self.users[index]
        result = self.client.post("/api/auth/login", json={"email": user["email"], "password": PW})
        assert result.status_code == 200, result.text


@pytest.fixture
def wallet_factory(tmp_path, monkeypatch) -> Iterator[Callable[..., WalletFixture]]:
    root = tmp_path / "isolated-wallet-projection"
    for name, value in {
        "SYNWEVO_DATA_DIR": str(root), "SYNWEVO_UI_AUTH": "session",
        "SYNWEVO_ROLE": "accounts", "SYNWEVO_COOKIE_SECURE": "0",
        "SYNWEVO_TEST_INSTANCE": "1", "SYNWEVO_MAIL_MODE": "disabled",
        "SYNWEVO_PUBLIC_URL": "http://testserver",
        "SYNWEVO_SECRET": "synthetic-wallet-http-fixture-key-123456789",
    }.items():
        monkeypatch.setenv(name, value)

    with ExitStack() as stack:
        def factory(*, environment: str = "test", configured: bool = True,
                    units: Sequence[Unit] = UNITS) -> WalletFixture:
            # The host is always the actual identity HTTP application, not a test FastAPI.
            app = create_app(str(root), start_jobs=False)
            ident, sessions = app.state.identity, app.state.sessions
            store = ident.store
            # Register only fixture unit definitions; a configured unit is not a funded wallet.
            for unit in UNITS:
                if not store.one("SELECT 1 FROM billing_units WHERE code=?", (unit.code,)):
                    store.execute("INSERT INTO billing_units VALUES (?,?)", (unit.code, unit.scale))
            users = []
            for index in range(3):
                email = f"wallet-fixture-{index}@example.test"
                user = ident.by_email(email) or ident.create_user(email, PW, status="active")
                users.append(user)
            ledger = Ledger(store, environment=environment, namespace="fixture-wallet")
            router = build_router(ident, sessions, app.state.mailer, no_wallet_audit,
                                  ledger=ledger if configured else None, units=units)
            count = len(app.router.routes)
            app.include_router(router)
            # Explicit test-only installation before a possible already-built SPA catch-all.
            mounted = app.router.routes[count:]
            del app.router.routes[count:]
            app.router.routes[:0] = mounted
            client = stack.enter_context(TestClient(app))
            fixture = WalletFixture(app, client, ident, sessions, store, ledger, users)
            fixture.login()
            return fixture
        yield factory


@pytest.fixture
def wallet(wallet_factory) -> WalletFixture:
    return wallet_factory()


def journal(wallet: WalletFixture, delta: int, *, uid: str | None = None, unit: Unit = UNIT,
            namespace: str = "fixture-wallet", seq: int | None = None) -> str:
    """Insert labelled synthetic immutable facts, never an assigned balance or payment proof.

    The actual schema trigger produces both entry legs and updates the balance cache.
    No production route exposes this helper, payment callback, or arbitrary adjustment.
    """
    owner, mode, store = uid or wallet.uid, wallet.ledger.environment, wallet.store
    kind = "sale" if delta < 0 else "test_payment" if mode == "test" else "paid"
    with store.tx() as tx:
        if not tx.execute("SELECT 1 FROM billing_units WHERE code=?", (unit.code,)).fetchone():
            tx.execute("INSERT INTO billing_units VALUES (?,?)", (unit.code, unit.scale))

        def account(account_owner: str, account_kind: str) -> str:
            row = tx.execute("SELECT id FROM billing_accounts WHERE environment=? AND unit=? AND owner=? AND kind=?",
                             (mode, unit.code, account_owner, account_kind)).fetchone()
            if row:
                return row[0]
            key = uuid4().hex
            tx.execute("INSERT INTO billing_accounts VALUES (?,?,?,?,?)",
                       (key, mode, unit.code, account_owner, account_kind))
            tx.execute("INSERT INTO billing_balances VALUES (?,0)", (key,))
            return key

        personal = account(owner, "wallet")
        platform = account("platform", "revenue" if delta < 0 else kind)
        debit, credit = (personal, platform) if delta < 0 else (platform, personal)
        key = uuid4().hex
        tx.execute("INSERT INTO billing_journal(rowid,id,environment,namespace,voucher,unit,debit_account,"
                   "credit_account,amount,kind,reference,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (seq, key, mode, namespace, "fixture:" + key, unit.code, debit, credit,
                    abs(delta), kind, PRIVATE, 1_790_000_000))
        return key


def hold(wallet: WalletFixture, amount: int, *, settle: bool = False) -> str:
    """Synthetic service-level authorization/price/usage; no model or payment request."""
    ledger = wallet.ledger
    ledger.authorize = lambda tx, context, action: (
        context.actor_user_id == wallet.uid == context.payer_user_id
        and context.authorization_ref == "fixture-wallet-approval"
    )
    sale = PriceSnapshot("fixture-wallet-sale", "sale", UNIT, "fixture-provider", "fixture-model",
                         Rate(1), Rate(1), Rate(1), "fixture-terms", 0, per_tokens=1)
    ledger.publish_price(sale)
    uid, run, deadline = wallet.uid, uuid4().hex, int(now()) + 600
    context = ChargeContext(uid, uid, run, "fixture-wallet-approval", workspace_id=PRIVATE)
    ledger.open_run(context, unit=UNIT, budget=amount, max_calls=1, max_attempts=1, deadline=deadline)
    call = ledger.reserve_call(context, logical_call_id="fixture-call", amount=amount,
                               price_id=sale.price_id, deadline=deadline)
    if settle:
        ledger.publish_price(PriceSnapshot("fixture-wallet-cost", "cost", COST_UNIT, "fixture-provider",
                                           "fixture-model", Rate(77), Rate(77), Rate(77), PRIVATE, 0, per_tokens=1))
        ticket = ledger.prepare_attempt(call, attempt_id="fixture-attempt", owner="fixture-worker",
                                         cost_price_id="fixture-wallet-cost", requested_model=PRIVATE)
        ledger.dispatch(ticket)  # Local state transition only; this test sends no network call.
        ledger.settle_trusted_usage(ticket.attempt_key,
                                   UsageEvidence("fixture-provider", "fixture-request-" + run, PRIVATE,
                                                 Usage(amount, 0, 0), "fixture-model", "fixture-capability", int(now())))
    return call


def projected(wallet: WalletFixture) -> dict[str, Any]:
    response = wallet.client.get(ROOT)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()


def test_atomic_overview_multiple_units_settled_held_and_empty(wallet):
    journal(wallet, 100)
    hold(wallet, 7, settle=True)
    hold(wallet, 20)
    journal(wallet, 45, unit=OTHER_UNIT)
    journal(wallet, -9, unit=OTHER_UNIT, namespace="fixture-second-app")
    journal(wallet, 777, uid=wallet.users[1]["id"])

    result = projected(wallet)
    assert set(result) == {"environment", "payer", "payer_user_id", "balances", "used_through_seq"}
    assert result["environment"] == "test" and result["payer_user_id"] == wallet.uid
    assert result["payer"] == {"kind": "user", "id": wallet.uid}
    assert result["balances"] == [
        {"unit": UNIT.code, "scale": 6, "booked": "93", "used": "7", "held": "20", "available": "73", "frozen": False},
        {"unit": OTHER_UNIT.code, "scale": 2, "booked": "36", "used": "9", "held": "0", "available": "36", "frozen": False},
        {"unit": EMPTY_UNIT.code, "scale": 18, "booked": "0", "used": "0", "held": "0", "available": "0", "frozen": False},
    ]
    assert result["used_through_seq"] == "4"
    assert PRIVATE not in str(result) and COST_UNIT.code not in str(result)


def test_empty_configured_wallet_is_real_zero_without_creating_facts(wallet):
    result = projected(wallet)
    assert result["used_through_seq"] == "0"
    assert len(result["balances"]) == len(UNITS)
    assert all(row[field] == "0" for row in result["balances"]
               for field in ("booked", "used", "held", "available"))
    assert wallet.client.get(STATEMENT).json()["items"] == []
    for table in ("billing_accounts", "billing_journal", "billing_prices", "billing_orders"):
        assert wallet.store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


@pytest.mark.parametrize("index", [1, 2], ids=["other-user", "platform-admin"])
def test_other_users_and_platform_admins_cannot_select_payer(wallet, index):
    victim_journal = journal(wallet, 913)
    attacker_uid = wallet.users[index]["id"]
    journal(wallet, 11, uid=attacker_uid)
    if index == 2:
        make_platform_admin(wallet.store, attacker_uid)
    wallet.login(index)
    own = projected(wallet)
    assert own["payer_user_id"] == attacker_uid and own["balances"][0]["booked"] == "11"
    entries = wallet.client.get(STATEMENT).json()["items"]
    assert [row["delta"] for row in entries] == ["11"]
    assert victim_journal not in str(entries)
    for path in (ROOT, STATEMENT):
        for field in ("payer_user_id", "user_id", "payer", "actor_user_id"):
            denied = wallet.client.get(path, params={field: wallet.uid})
            assert denied.status_code == 400 and denied.json()["error"] == "bad_fields"
            assert wallet.uid not in denied.text and victim_journal not in denied.text
    # Guessing a global cursor only filters the attacker's own statement.
    assert wallet.client.get(STATEMENT, params={"after_seq": "0"}).json()["items"] == entries
    assert wallet.client.get(ROOT + "/" + wallet.uid).status_code == 404


def test_host_environment_separates_same_users_and_is_not_request_controlled(wallet_factory, monkeypatch):
    test_host = wallet_factory(environment="test")
    live_host = wallet_factory(environment="live")
    test_id = journal(test_host, 123)
    live_id = journal(live_host, 456)
    journal(live_host, -12)
    # Changing a process setting or sending a client header cannot switch the bound Ledger.
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "live")
    for fixture, amount, used, expected_id, excluded_id in (
        (test_host, "123", "0", test_id, live_id),
        (live_host, "444", "12", live_id, test_id),
    ):
        result = projected(fixture)
        assert result["environment"] == fixture.ledger.environment
        assert result["balances"][0]["booked"] == amount
        assert result["balances"][0]["used"] == used
        rows = fixture.client.get(STATEMENT, headers={"X-Billing-Environment": "live"}).json()["items"]
        assert expected_id in str(rows) and excluded_id not in str(rows)
        for path in (ROOT, STATEMENT):
            denied = fixture.client.get(path, params={"environment": "live"})
            assert denied.status_code == 400 and denied.json()["error"] == "bad_fields"


@pytest.mark.parametrize("settings", [
    {"configured": False}, {"units": ()}, {"units": (UNIT, UNIT)},
], ids=["no-ledger", "no-units", "duplicate-units"])
def test_unconfigured_is_unavailable_not_fake_zero(wallet_factory, settings):
    wallet = wallet_factory(**settings)
    for path in (ROOT, STATEMENT):
        response = wallet.client.get(path)
        assert response.status_code == 503 and response.json()["error"] == "wallet_unavailable"
        assert "balances" not in response.json() and "items" not in response.json()
    wallet.client.cookies.clear()
    assert wallet.client.get(ROOT).status_code == 401


def test_stable_pagination_across_namespaces_units_gaps_and_appends(wallet):
    first = journal(wallet, 30)
    journal(wallet, 900, uid=wallet.users[1]["id"])
    second = journal(wallet, -3, namespace="fixture-second-app")
    third = journal(wallet, 8, unit=OTHER_UNIT)
    response = wallet.client.get(STATEMENT, params={"limit": "2"})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    page = response.json()
    assert set(page) == {"environment", "payer", "payer_user_id", "items", "next_after_seq", "next_before_seq"}
    assert page["next_before_seq"] is None  # set only on a newest-first page
    assert [row["id"] for row in page["items"]] == [first, second]
    assert [row["seq"] for row in page["items"]] == ["1", "3"]
    assert page["next_after_seq"] == "3"
    appended = journal(wallet, -1, unit=OTHER_UNIT)
    tail = wallet.client.get(STATEMENT, params={"after_seq": page["next_after_seq"], "limit": "2"}).json()
    assert [row["id"] for row in tail["items"]] == [third, appended]
    assert tail["next_after_seq"] is None
    assert wallet.client.get(STATEMENT, params={"limit": "2"}).json() == page
    empty = wallet.client.get(STATEMENT, params={"after_seq": tail["items"][-1]["seq"]}).json()
    assert empty["items"] == [] and empty["next_after_seq"] is None
    assert wallet.client.get(STATEMENT, params={"after_seq": str(MAX_INTEGER)}).json()["items"] == []


def test_pagination_defaults_bounds_and_strict_sanitized_refusals(wallet):
    for _ in range(105):
        journal(wallet, 1)
    assert len(wallet.client.get(STATEMENT).json()["items"]) == 50
    assert len(wallet.client.get(STATEMENT, params={"limit": "100"}).json()["items"]) == 100
    for field, values in {
        "after_seq": ("", "-1", "+1", " 1", "1 ", "01", "1.0", "1e3", "true", "１", str(MAX_INTEGER + 1), "9" * 5000),
        "limit": ("", "0", "101", "-1", "+1", "01", "1.5", "NaN", "false", " 2", PRIVATE),
    }.items():
        for value in values:
            result = wallet.client.get(STATEMENT, params={field: value})
            assert result.status_code == 400 and result.json()["error"] == "bad_pagination"
            assert PRIVATE not in result.text
    for query in ("after_seq=0&after_seq=1", "limit=1&limit=2", "cursor=1", "unit=other"):
        result = wallet.client.get(STATEMENT + "?" + query)
        assert result.status_code == 400 and result.json()["error"] == "bad_fields"
    assert wallet.client.get(ROOT + "?limit=1").status_code == 400


def test_big_integer_amounts_and_cursors_round_trip_as_decimal_strings(wallet):
    base = 2**53 + 9
    amount = 2**62 + 123
    used = 2**53 + 31
    journal(wallet, amount, seq=base)
    journal(wallet, -used)
    hold(wallet, 17)
    result = projected(wallet)
    assert result["balances"][0] == {
        "unit": UNIT.code, "scale": 6, "booked": str(amount - used), "used": str(used),
        "held": "17", "available": str(amount - used - 17), "frozen": False,
    }
    assert result["used_through_seq"] == str(base + 1)
    first = wallet.client.get(STATEMENT, params={"limit": "1"}).json()
    assert first["items"][0]["delta"] == str(amount)
    assert first["next_after_seq"] == first["items"][0]["seq"] == str(base)
    second = wallet.client.get(STATEMENT, params={"after_seq": first["next_after_seq"]}).json()
    assert second["items"][0]["seq"] == str(base + 1)
    assert second["items"][0]["delta"] == str(-used)


def test_response_allowlist_excludes_cost_context_evidence_and_other_users(wallet):
    journal(wallet, 100)
    hold(wallet, 5, settle=True)
    other_id = journal(wallet, 973, uid=wallet.users[1]["id"])
    response = wallet.client.get(STATEMENT)
    assert response.status_code == 200
    entries = response.json()["items"]
    assert [entry["delta"] for entry in entries] == ["100", "-5"]
    for row in entries:
        assert set(row) == {"seq", "id", "kind", "unit", "scale", "delta", "created_at"}
    assert wallet.store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 1
    for text in (response.text, str(projected(wallet))):
        for private in (PRIVATE, COST_UNIT.code, other_id, wallet.users[1]["id"], "cost_n", "facts"):
            assert private not in text


@pytest.mark.parametrize("change", ["suspend", "delete", "revoke", "expire"])
def test_current_identity_and_session_changes_refuse_previously_logged_in_user(wallet, change):
    journal(wallet, 71)
    assert wallet.client.get(STATEMENT).status_code == 200
    if change == "suspend":
        wallet.store.execute("UPDATE users SET status='suspended' WHERE id=?", (wallet.uid,))
    elif change == "delete":
        wallet.store.execute("UPDATE users SET deleted_at=? WHERE id=?", (now(), wallet.uid))
    elif change == "revoke":
        wallet.sessions.revoke_all_for_user(wallet.uid, "fixture-revocation")
    else:
        wallet.store.execute("UPDATE sessions SET absolute_expires_at=? WHERE user_id=?", (now() - 1, wallet.uid))
    for path in (ROOT, STATEMENT):
        result = wallet.client.get(path)
        assert result.status_code in (401, 403)
        assert "balances" not in result.json() and "items" not in result.json()


def test_session_revoked_while_ledger_reads_is_refused_at_return(wallet, monkeypatch):
    journal(wallet, 71)
    original = wallet.ledger.statement_for_user

    def revoke_after_real_read(**kwargs):
        result = original(**kwargs)
        wallet.sessions.revoke_all_for_user(wallet.uid, "fixture-during-read")
        return result

    # A fault/race hook around the real projection, never a replacement balance or auth.
    monkeypatch.setattr(wallet.ledger, "statement_for_user", revoke_after_real_read)
    response = wallet.client.get(STATEMENT)
    assert response.status_code == 401 and response.json()["error"] == "unauthorized"
    assert "items" not in response.json()


def test_storage_failure_is_sanitized(wallet, monkeypatch, caplog):
    def storage_failure(**kwargs):
        raise ValueError(PRIVATE)

    monkeypatch.setattr(wallet.ledger, "statement_for_user", storage_failure)
    response = wallet.client.get(STATEMENT)
    assert response.status_code == 503 and response.json()["error"] == "wallet_unavailable"
    assert PRIVATE not in response.text and PRIVATE not in caplog.text


@pytest.mark.parametrize("units", [(Unit("FIXTURE-UNREGISTERED", 6),), (Unit(UNIT.code, 5),)])
def test_unknown_unit_or_scale_mismatch_is_unavailable(wallet_factory, units):
    wallet = wallet_factory(units=units)
    response = wallet.client.get(ROOT)
    assert response.status_code == 503 and response.json()["error"] == "wallet_unavailable"
    assert units[0].code not in response.text and "unit_scale_conflict" not in response.text


def test_overview_uses_one_locked_snapshot_and_route_does_not_reread_money(wallet, monkeypatch):
    journal(wallet, 100)
    journal(wallet, -5)
    hold(wallet, 20)
    other_store = Store(wallet.store.root)
    competing = replace(wallet, store=other_store)
    original_balance = wallet.ledger._balance
    lock_checks = []

    def balance_while_writer_competes(tx, account):
        result = original_balance(tx, account)
        assert tx.in_transaction
        # Store connections are thread-local; set the timeout in the HTTP worker thread.
        with other_store.conn() as connection:
            connection.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            journal(competing, -7)
        lock_checks.append(account)
        return result

    monkeypatch.setattr(wallet.ledger, "_balance", balance_while_writer_competes)
    original_overview = wallet.ledger.overview_for_user
    calls = []

    def append_after_atomic_snapshot(**kwargs):
        result = original_overview(**kwargs)
        calls.append(kwargs)
        # The writer can now commit, before the route serializes. Every response field must
        # still come from the original snapshot; independently fetching used/balance fails.
        journal(competing, -7)
        return result

    def no_separate_money_read(**kwargs):
        raise AssertionError("overview route must not page statements or separately read balances")

    monkeypatch.setattr(wallet.ledger, "overview_for_user", append_after_atomic_snapshot)
    monkeypatch.setattr(wallet.ledger, "statement_for_user", no_separate_money_read)
    monkeypatch.setattr(wallet.ledger, "balance_for_user", no_separate_money_read)
    result = projected(wallet)
    assert result["balances"][0] == {
        "unit": UNIT.code, "scale": 6, "booked": "95", "used": "5", "held": "20", "available": "75",
        "frozen": False,
    }
    assert result["used_through_seq"] == "2"
    assert len(lock_checks) == 1
    assert calls == [{"requester_user_id": wallet.uid, "payer_user_id": wallet.uid, "units": UNITS}]
    assert wallet.store.one("SELECT COUNT(*) AS n FROM billing_journal")["n"] == 3


def test_only_get_routes_exist_and_reads_do_not_create_money_facts(wallet):
    journal(wallet, 20)
    before = wallet.store.all("SELECT * FROM billing_journal ORDER BY rowid")
    for path in (ROOT, STATEMENT):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = wallet.client.request(method, path, json={"balance": "999999"},
                                             headers={CSRF_HEADER: wallet.client.cookies.get(CSRF_COOKIE) or ""})
            assert response.status_code == 405
    projected(wallet)
    assert wallet.client.get(STATEMENT).status_code == 200
    assert wallet.store.all("SELECT * FROM billing_journal ORDER BY rowid") == before
    assert wallet.store.one("SELECT COUNT(*) AS n FROM billing_orders")["n"] == 0


def test_from_host_explicit_units_and_environment_have_no_write_authority(wallet, monkeypatch):
    before = wallet.store.all("SELECT * FROM billing_units ORDER BY code")
    for mode in ("test", "live"):
        monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", mode)
        monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([
            {"code": "FIXTURE-NOT-REGISTERED", "scale": 0}, {"code": EMPTY_UNIT.code, "scale": 18},
        ]))
        ledger, units = from_host(wallet.store)
        assert isinstance(ledger, Ledger) and ledger.store is wallet.store
        assert ledger.environment == mode and ledger.namespace == "personal-agent"
        assert ledger.authorize is None and ledger.verify_payment is None
        assert units == (Unit("FIXTURE-NOT-REGISTERED", 0), EMPTY_UNIT)
        monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "invalid")
        assert ledger.environment == mode
    assert wallet.store.all("SELECT * FROM billing_units ORDER BY code") == before
    assert wallet.store.one("SELECT COUNT(*) AS n FROM billing_journal")["n"] == 0
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([
        {"code": f"FIXTURE-UNIT-{index}", "scale": index} for index in range(16)
    ]))
    ledger, units = from_host(wallet.store)
    assert ledger is not None and len(units) == 16


def test_from_host_missing_invalid_ambiguous_and_excess_settings_fail_closed(wallet, monkeypatch, caplog):
    good = json.dumps([{"code": UNIT.code, "scale": UNIT.scale}])
    for mode in (None, "", "TEST", "production", "test ", PRIVATE):
        if mode is None:
            monkeypatch.delenv("SYNWEVO_BILLING_ENVIRONMENT", raising=False)
        else:
            monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", mode)
        monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", good)
        assert from_host(wallet.store) == (None, ())
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")
    invalid = [None, "", "not-json-" + PRIVATE, "null", "{}", "[]", "true", "[1]",
               '[{"code":"X","scale":1,"scale":2}]',
               '[{"code":"X","code":"Y","scale":1}]', "[" * 2000 + "]" * 2000,
               " " * 16385]
    invalid.extend(json.dumps(value) for value in (
        [{"code": "X"}], [{"scale": 6}], [{"code": "X", "scale": 6, "secret": PRIVATE}],
        [{"code": "X", "scale": 6}, {"code": "X", "scale": 2}],
        [{"code": str(index), "scale": 6} for index in range(17)],
    ))
    invalid.extend(json.dumps([{"code": "X", "scale": scale}])
                   for scale in (-1, 19, True, 1.0, "6", None, float("nan")))
    invalid.extend(json.dumps([{"code": code, "scale": 6}])
                   for code in ("", " ", "X ", "X\nY", "x" * 201, True, 17, None))
    for value in invalid:
        if value is None:
            monkeypatch.delenv("SYNWEVO_BILLING_WALLET_UNITS", raising=False)
        else:
            monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", value)
        assert from_host(wallet.store) == (None, ())
    assert PRIVATE not in caplog.text


def test_default_factory_exposes_host_bound_wallet_without_fixture_router(wallet, monkeypatch):
    journal(wallet, 765)
    monkeypatch.setenv("SYNWEVO_BILLING_ENVIRONMENT", "test")
    monkeypatch.setenv("SYNWEVO_BILLING_WALLET_UNITS", json.dumps([{"code": UNIT.code, "scale": UNIT.scale}]))
    app = create_app(str(wallet.store.root), start_jobs=False)
    with TestClient(app) as client:
        client.cookies.update(wallet.client.cookies)
        overview = client.get(ROOT)
        assert overview.status_code == 200, overview.text
        assert overview.json()["balances"] == [{"unit": UNIT.code, "scale": 6, "booked": "765",
                                                "used": "0", "held": "0", "available": "765", "frozen": False}]
        assert client.get(STATEMENT).json()["items"][0]["delta"] == "765"
