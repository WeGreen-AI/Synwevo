"""Usage reads, CSV export, payer authority and the person's wallet settings over real ledger facts.

Isolated fixture, NOT real charging acceptance: the identity application is the production
factory (create_app) with the wallet configured by host variables; its Ledger runs the real
reserve -> dispatch -> trusted-settlement path with fixture prices, a fixture authorizer and a
fixture payment verifier. Nothing contacts a model, a payment provider or an existing data
directory. Organizations, memberships and custom roles are inserted as labelled fixture rows.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import time
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from synwevo_core.billing import (ChargeContext, Ledger, PaymentReceipt, PriceSnapshot, Rate, Unit, Usage,
                                 UsageEvidence)
from synwevo_core.billing.usage import neutralize
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_identity.app import create_app

from .platform_fixture import make_platform_admin

ROOT = "/api/auth/wallet"
UNIT = Unit("FIXTURE-CNY", 4)
PW = "wallet-usage-fixture-passphrase-123"
PRIVATE_EVIDENCE = "fixture-private-evidence-ref"
PRIVATE_COST = "fixture-private-cost-price"
MODELS = ("fixture-flash", "fixture-pro")
# Sale rates per token in smallest units: uncached 3, cached 1, output 5.
SALE = {model: PriceSnapshot(f"fixture-sale-{model}", "sale", UNIT, "fixture-provider", model,
                             Rate(3), Rate(1), Rate(5), "fixture-terms", 0, per_tokens=1) for model in MODELS}
COST = {model: PriceSnapshot(f"{PRIVATE_COST}-{model}", "cost", UNIT, "fixture-provider", model,
                             Rate(1), Rate(1), Rate(1), "fixture-cost-terms", 0, per_tokens=1) for model in MODELS}


@dataclass
class Env:
    app: Any
    client: TestClient
    store: Store
    ledger: Ledger
    users: dict[str, dict]

    def login(self, who: str) -> None:
        self.client.cookies.clear()
        result = self.client.post("/api/auth/login", json={"email": self.users[who]["email"], "password": PW})
        assert result.status_code == 200, result.text

    def csrf(self) -> dict:
        return {CSRF_HEADER: self.client.cookies.get(CSRF_COOKIE) or ""}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "isolated-wallet-usage"
    for name, value in {
        "SYNWEVO_DATA_DIR": str(root), "SYNWEVO_UI_AUTH": "session", "SYNWEVO_ROLE": "accounts",
        "SYNWEVO_COOKIE_SECURE": "0", "SYNWEVO_TEST_INSTANCE": "1", "SYNWEVO_MAIL_MODE": "disabled",
        "SYNWEVO_PUBLIC_URL": "http://testserver",
        "SYNWEVO_SECRET": "synthetic-wallet-usage-fixture-key-123456789",
        # The host configuration the production installer reads (wallet_routes.from_host).
        "SYNWEVO_BILLING_ENVIRONMENT": "test",
        "SYNWEVO_BILLING_WALLET_UNITS": json.dumps([{"code": UNIT.code, "scale": UNIT.scale}]),
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app(str(root), start_jobs=False)
    ident, store = app.state.identity, app.state.identity.store
    users = {}
    for name in ("alice", "bob", "carol", "dave", "erin", "admin"):
        email = f"wallet-usage-{name}@example.test"
        users[name] = ident.by_email(email) or ident.create_user(email, PW, status="active")
    make_platform_admin(store, users["admin"])
    ledger = Ledger(store, environment="test", namespace="agent",
                    authorize=lambda t, context, action: True,  # fixture authority
                    verify_payment=lambda t, receipt: receipt.evidence_ref == "fixture-verified")
    for model in MODELS:
        ledger.publish_price(SALE[model])
        ledger.publish_price(COST[model])
    with TestClient(app) as client:
        yield Env(app, client, store, ledger, users)


def fund(env: Env, who: str, units: int) -> None:
    """A fixture payment through the real order/credit path (verifier is a labelled fixture)."""
    order = uuid4().hex
    env.ledger.create_payment_order(order_id=order, payer_user_id=env.users[who]["id"], provider="fixture-pay",
                                    merchant_id="fixture-merchant", amount_minor=units, currency="CNY",
                                    unit=UNIT, credit_units=units, exchange_version="fixture-identity")
    env.ledger.credit_verified_payment(PaymentReceipt(
        "test", "fixture-pay", "fixture-merchant", "pay-" + order, "evt-" + order, order, units, "CNY",
        "fixture-verified", hashlib.sha256(order.encode()).hexdigest()))


def conversation(env: Env, who: str, title: str) -> str:
    cid = "conv-" + uuid4().hex[:12]
    env.store.execute("INSERT INTO agent_conversations(id,user_id,title,status,created_at,updated_at) "
                      "VALUES (?,?,?,'active',?,?)", (cid, env.users[who]["id"], title, time.time(), time.time()))
    return cid


def model_call(env: Env, who: str, cid: str, model: str, *, at: int | None = None,
               usage: Usage | None = Usage(100, 50, 20, 5), finish: str | None = "stop",
               hold: int = 5000) -> str:
    """One Agent call: a turn row (engine counters set to nonsense on purpose), the ledger's run,
    reservation, dispatch fence and — unless ``usage`` is None — the trusted settlement."""
    uid, turn = env.users[who]["id"], "turn-" + uuid4().hex[:12]
    env.store.execute("INSERT INTO agent_turns(id,conversation_id,user_id,user_message_id,assistant_message_id,status,"
                      "input_tokens,output_tokens,created_at,updated_at) VALUES (?,?,?,?,?,'succeeded',?,?,?,?)",
                      (turn, cid, uid, "m-" + turn, "a-" + turn, 999_999, 999_999, time.time(), time.time()))
    clock = (lambda: at) if at is not None else (lambda: int(time.time()))
    env.ledger.clock = clock
    try:
        context = ChargeContext(uid, uid, turn, "fixture-authorization")
        now = clock()
        run = env.ledger.open_run(context, unit=UNIT, budget=10**9, max_calls=5, max_attempts=5, deadline=now + 3600)
        call = env.ledger.reserve_call(context, logical_call_id="call-1", amount=hold,
                                       price_id=SALE[model].price_id, deadline=now + 600)
        ticket = env.ledger.prepare_attempt(call, attempt_id="attempt-1", owner="fixture-worker",
                                            cost_price_id=COST[model].price_id, requested_model=model)
        env.ledger.dispatch(ticket)
        if usage is not None:
            # The provider meters the call 7 s after dispatch; the ledger pins that as completed_at.
            env.ledger.clock = lambda: now + 7
            env.ledger.settle_trusted_usage(ticket.attempt_key, UsageEvidence(
                "fixture-provider", "fixture-request-" + turn, PRIVATE_EVIDENCE, usage, model, "fixture-cap", now + 7))
            if finish is not None:
                metered = "mc-" + turn
                with env.store.tx() as t:
                    t.execute("INSERT INTO agent_metered_runs(run_key,route) VALUES (?,?)", (run, "fixture-route"))
                    t.execute("INSERT INTO agent_metered_calls(id,run_key,logical_id,request_digest,call_key,attempt_key,"
                              "created_at) VALUES (?,?,?,?,?,?,?)", (metered, run, "call-1", "fixture-digest", call,
                                                                    ticket.attempt_key, now))
                    t.execute("INSERT INTO agent_model_observations(call_id,attempt_key,evidence,created_at) VALUES (?,?,?,?)",
                              (metered, ticket.attempt_key, json.dumps({"finish_reason": finish}), now + 7))
        return call
    finally:
        env.ledger.clock = lambda: int(time.time())


def organization(env: Env, name: str, members: dict[str, str], *, status: str = "active") -> str:
    oid = "org-" + uuid4().hex[:10]
    at = time.time()
    with env.store.tx() as t:
        t.execute("INSERT INTO organizations(id,name,status,created_by,created_at,updated_at,deleted_at) "
                  "VALUES (?,?,?,?,?,?,?)", (oid, name, status, env.users["carol"]["id"], at, at,
                                             at if status == "deleted" else None))
        for who, role in members.items():
            t.execute("INSERT INTO organization_memberships(organization_id,user_id,role,created_at,updated_at) "
                      "VALUES (?,?,?,?,?)", (oid, env.users[who]["id"], role, at, at))
    return oid


def billing_role(env: Env, oid: str, who: str) -> None:
    """Fixture custom role carrying the org-payments permission (org.billing.manage)."""
    rid = "role-" + uuid4().hex[:10]
    at = time.time()
    with env.store.tx() as t:
        t.execute("INSERT INTO organization_roles(id,organization_id,name,permissions,created_at,updated_at) "
                  "VALUES (?,?,?,?,?,?)", (rid, oid, "Finance (fixture)", json.dumps(["org.billing.manage"]), at, at))
        t.execute("INSERT INTO organization_role_assignments(organization_id,user_id,role_id,created_at) VALUES (?,?,?,?)",
                  (oid, env.users[who]["id"], rid, at))


def day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def noon(offset: int) -> int:
    d = datetime.now(timezone.utc).date() + timedelta(days=offset)
    return int(datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).timestamp())


def get(env: Env, path: str, **params) -> Any:
    response = env.client.get(path, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "private, no-store"
    return response.json()


# Expected sale of Usage(100 uncached, 50 cached, 20 output, 5 reasoning): 100*3 + 50*1 + 20*5.
CHARGE = 450


def test_usage_by_day_model_and_conversation_comes_from_the_ledger(env):
    fund(env, "alice", 100_000)
    trip, notes = conversation(env, "alice", "Plan a trip"), conversation(env, "alice", "Notes")
    model_call(env, "alice", trip, "fixture-flash", at=noon(-2))
    model_call(env, "alice", notes, "fixture-pro", at=noon(-2), usage=Usage(10, 0, 2, 0))
    model_call(env, "alice", trip, "fixture-pro")
    model_call(env, "alice", trip, "fixture-flash", usage=None, hold=700)  # still held
    env.login("alice")
    period = {"from": day(-6), "to": day(0), "tz": "UTC"}

    by_day = get(env, ROOT + "/usage", group="day", **period)
    assert by_day["payer"] == {"kind": "user", "id": env.users["alice"]["id"]}
    assert by_day["period"] == {"from": day(-6), "to": day(0), "timezone": "UTC"}
    assert [(b["key"], b["calls"], b["charged_minor"], b["held_minor"]) for b in by_day["buckets"]] == [
        (day(-2), 2, str(CHARGE + 40), "0"), (day(0), 2, str(CHARGE), "700")]
    total = by_day["totals"][0]
    assert (total["unit"], total["scale"], total["calls"], total["charged_minor"]) == (UNIT.code, 4, 4, str(2 * CHARGE + 40))
    # Token counters are the trusted settlement's, never the engine's (set to 999999 above).
    assert total["tokens"] == {"input_cached": 100, "input_uncached": 210, "output": 42, "reasoning": 10}

    by_model = get(env, ROOT + "/usage", group="model", **period)
    # Most spent first, so the chart and table lead with what cost the most.
    assert [(b["key"], b["calls"], b["charged_minor"]) for b in by_model["buckets"]] == [
        ("fixture-pro", 2, str(CHARGE + 40)), ("fixture-flash", 2, str(CHARGE))]

    by_conversation = get(env, ROOT + "/usage", group="conversation", **period)
    assert [(b["key"], b["title"], b["calls"]) for b in by_conversation["buckets"]] == [
        (trip, "Plan a trip", 3), (notes, "Notes", 1)]

    # The ledger agrees: the settled sales debit the wallet by exactly the charged total.
    balance = get(env, ROOT)
    assert balance["balances"][0]["used"] == str(2 * CHARGE + 40)
    # A period without calls is honestly empty, not padded with invented figures.
    empty = get(env, ROOT + "/usage", group="day", **{"from": day(-30), "to": day(-20), "tz": "UTC"})
    assert empty["buckets"] == [] and empty["totals"] == []


def test_calls_list_every_contract_field_with_cursor_pages_and_filters(env):
    fund(env, "alice", 100_000)
    trip = conversation(env, "alice", "Plan a trip")
    first = model_call(env, "alice", trip, "fixture-flash", at=noon(-1), finish="length")
    second = model_call(env, "alice", trip, "fixture-pro", at=noon(-1) + 60)
    held = model_call(env, "alice", trip, "fixture-flash", usage=None, hold=700)
    env.login("alice")
    response = env.client.get(ROOT + "/calls", params={"limit": "2", "from": day(-3), "to": day(0)})
    assert response.status_code == 200, response.text
    page = response.json()
    assert [row["id"] for row in page["items"]] == [held, second]
    assert page["next_cursor"] and page["models"] == ["fixture-flash", "fixture-pro"]
    settled = page["items"][1]
    assert settled["model"] == "fixture-pro" and settled["state"] == "settled"
    assert settled["price_version"] == SALE["fixture-pro"].price_id
    assert settled["charged_minor"] == str(CHARGE) and settled["held_minor"] == "0"
    assert settled["tokens"] == {"input_cached": 50, "input_uncached": 100, "output": 20, "reasoning": 5}
    assert settled["finish_reason"] == "stop" and settled["duration_s"] == 7
    assert settled["conversation_id"] == trip and settled["conversation_title"] == "Plan a trip"
    assert "band" in settled  # pinned by the pricing package; unknown (null) until it exists, never guessed
    assert page["items"][0]["state"] == "held" and page["items"][0]["held_minor"] == "700"
    assert page["items"][0]["tokens"] is None and page["items"][0]["charged_minor"] == "0"
    # Platform cost, provider request ids and evidence references never leave the server.
    for secret in (PRIVATE_COST, PRIVATE_EVIDENCE, "fixture-request-", "fixture-authorization", "cost"):
        assert secret not in response.text

    rest = get(env, ROOT + "/calls", limit="2", cursor=page["next_cursor"], **{"from": day(-3), "to": day(0)})
    assert [row["id"] for row in rest["items"]] == [first] and rest["next_cursor"] is None
    assert rest["items"][0]["finish_reason"] == "length"
    flash = get(env, ROOT + "/calls", model="fixture-flash", **{"from": day(-3), "to": day(0)})
    assert [row["id"] for row in flash["items"]] == [held, first]
    holding = get(env, ROOT + "/calls", state="held", **{"from": day(-3), "to": day(0)})
    assert [row["id"] for row in holding["items"]] == [held]
    for bad in ({"state": "refunded"}, {"cursor": "01"}, {"limit": "101"}, {"from": "2026-13-01"},
                {"from": day(-400), "to": day(0)}, {"tz": "Mars/Base"}, {"unknown": "1"}):
        refused = env.client.get(ROOT + "/calls", params=bad)
        assert refused.status_code == 400, (bad, refused.text)


def test_nobody_reads_another_persons_usage(env):
    fund(env, "alice", 100_000)
    model_call(env, "alice", conversation(env, "alice", "Private plans"), "fixture-flash")
    alice = env.users["alice"]["id"]
    for who in ("bob", "admin"):
        env.login(who)
        for path in ("", "/statement", "/usage", "/calls", "/calls.csv"):
            refused = env.client.get(ROOT + path, params={"payer_kind": "user", "payer_id": alice})
            assert refused.status_code == 403, (who, path, refused.text)
            assert "Private plans" not in refused.text
        # Their own reads are empty: nothing of Alice's leaks into another payer's projection.
        mine = get(env, ROOT + "/calls")
        assert mine["items"] == [] and mine["models"] == []
        assert get(env, ROOT + "/usage")["totals"] == []
    env.client.cookies.clear()
    assert env.client.get(ROOT + "/usage").status_code == 401


def test_organization_money_is_read_only_by_its_billing_managers(env):
    acme = organization(env, "Acme", {"carol": "owner", "dave": "admin", "erin": "member", "bob": "member"})
    billing_role(env, acme, "erin")
    other = organization(env, "Other", {"alice": "owner"})
    gone = organization(env, "Gone", {"carol": "owner"}, status="deleted")
    payer = {"payer_kind": "organization", "payer_id": acme}
    for who in ("carol", "erin"):
        env.login(who)
        assert get(env, ROOT, **payer)["payer"] == {"kind": "organization", "id": acme}
        assert get(env, ROOT + "/usage", group="member", **payer)["buckets"] == []
        assert get(env, ROOT + "/calls", **payer)["items"] == []
        assert get(env, ROOT + "/statement", before_seq="9223372036854775807", **payer)["items"] == []
        listed = get(env, ROOT + "/payers")["items"]
        assert listed[0] == {"kind": "user", "id": env.users[who]["id"], "name": None}
        assert {"kind": "organization", "id": acme, "name": "Acme"} in listed
        assert all(item["id"] not in (other, gone) for item in listed)
        # A member's conversations are theirs: an organization cannot group by conversation.
        assert env.client.get(ROOT + "/usage", params={"group": "conversation", **payer}).status_code == 400
    refusals = set()
    for who, oid in (("dave", acme), ("bob", acme), ("admin", acme), ("alice", acme), ("carol", gone),
                     ("carol", "org-does-not-exist")):
        env.login(who)
        for path in ("", "/statement", "/usage", "/calls", "/calls.csv"):
            refused = env.client.get(ROOT + path, params={"payer_kind": "organization", "payer_id": oid})
            assert refused.status_code == 403, (who, path, refused.text)
            refusals.add((refused.json()["error"], refused.json()["message"]))
        assert all(item["kind"] == "user" or item["id"] != oid for item in get(env, ROOT + "/payers")["items"])
    # Unknown, deleted and forbidden organizations get one indistinguishable answer.
    assert len(refusals) == 1
    env.login("alice")
    assert env.client.get(ROOT, params={"payer_kind": "team", "payer_id": acme}).status_code == 400
    assert env.client.get(ROOT, params={"payer_kind": "organization"}).status_code == 400


def test_organization_balance_reads_the_organization_wallet(env):
    kinds = env.store.one("SELECT sql FROM sqlite_master WHERE name='billing_accounts'")["sql"]
    if "org_wallet" not in kinds:
        pytest.skip("organization wallets arrive with the org-payments ledger change (account kind org_wallet)")
    acme = organization(env, "Acme", {"carol": "owner", "erin": "member"})
    at = time.time()
    with env.store.tx() as t:  # fixture organization wallet funded by synthetic balanced facts
        for kind, owner in (("org_wallet", acme), ("test_payment", "platform")):
            t.execute("INSERT OR IGNORE INTO billing_accounts VALUES (?,?,?,?,?)", (kind + owner, "test", UNIT.code, owner, kind))
            t.execute("INSERT OR IGNORE INTO billing_balances VALUES (?,0)", (kind + owner,))
        t.execute("INSERT INTO billing_journal(id,environment,namespace,voucher,unit,debit_account,credit_account,amount,"
                  "kind,reference,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  ("fixture-org-fund", "test", "fixture", "fixture-org-fund", UNIT.code, "test_paymentplatform",
                   "org_wallet" + acme, 100_000, "test_payment", "fixture", int(at)))
    env.login("carol")
    assert get(env, ROOT, payer_kind="organization", payer_id=acme)["balances"][0]["booked"] == "100000"


def test_csv_export_neutralises_spreadsheet_formulas(env):
    fund(env, "alice", 100_000)
    hostile = ['=HYPERLINK("http://example.invalid","x")', "+1+1", "-2", "@SUM(A1)", "  =cmd|' /C calc'!A0",
               "\t=1", "＝1", "|pipe", "%0A", "Plain title"]
    for title in hostile:
        model_call(env, "alice", conversation(env, "alice", title), "fixture-flash")
    env.login("alice")
    response = env.client.get(ROOT + "/calls.csv")
    assert response.status_code == 200, response.text
    assert response.headers["Content-Type"].startswith("text/csv")
    assert response.headers["Content-Disposition"].startswith('attachment; filename="synwevo-usage-')
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.content.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    header = rows[0]
    assert header[:4] == ["time", "call_id", "conversation_id", "conversation_title"]
    titles = [row[header.index("conversation_title")] for row in rows[1:]]
    assert sorted(titles) == sorted("'" + t if t != "Plain title" else t for t in hostile)
    for row in rows[1:]:
        for cell in row:
            assert not cell.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r", "|", "%", "＝")), cell
        assert row[header.index("charged")] == "0.045" or row[header.index("charged")] == "0.0450"
        assert row[header.index("state")] == "settled" and row[header.index("model")] == "fixture-flash"
    for secret in (PRIVATE_COST, PRIVATE_EVIDENCE, "fixture-request-"):
        assert secret not in response.text
    assert neutralize(None) == "" and neutralize(12) == "12" and neutralize("a=b") == "a=b"


def test_spending_limits_and_low_balance_reminder_are_the_persons_own_settings(env):
    fund(env, "alice", 10_000)
    env.login("alice")
    limits = get(env, ROOT + "/limits")
    budget = next(b for b in limits["budgets"] if b["unit"]["code"] == UNIT.code)
    assert budget["revision"] == 0 and budget["configured"] is False
    change = {"unit_code": UNIT.code, "expected_revision": 0, "enabled": True,
              "daily_budget": "1000", "weekly_budget": "5000", "monthly_budget": "20000"}
    saved = env.client.put(ROOT + "/limits", json=change, headers=env.csrf())
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 1 and saved.json()["daily_budget"] == "1000"
    stale = env.client.put(ROOT + "/limits", json=change, headers=env.csrf())
    assert stale.status_code == 409 and stale.json()["error"] == "limits_conflict"
    assert env.client.put(ROOT + "/limits", json=change).status_code == 403  # no CSRF header

    reminder = get(env, ROOT + "/reminder")["reminders"][0]
    assert reminder == {**reminder, "unit": UNIT.code, "configured": False, "enabled": False, "revision": 0,
                        "available": "10000", "below": False}
    body = {"unit": UNIT.code, "expected_revision": 0, "enabled": True, "threshold": "20000"}
    stored = env.client.put(ROOT + "/reminder", json=body, headers=env.csrf())
    assert stored.status_code == 200, stored.text
    assert stored.json()["below"] is True and stored.json()["threshold"] == "20000"
    assert env.client.put(ROOT + "/reminder", json=body, headers=env.csrf()).json()["error"] == "reminder_conflict"
    for bad in ({**body, "threshold": "-1"}, {**body, "threshold": "1.5"}, {**body, "unit": "OTHER"},
                {**body, "expected_revision": 1, "threshold": "9999999999999999"}):
        assert env.client.put(ROOT + "/reminder", json=bad, headers=env.csrf()).status_code in (400, 422), bad
    # Audited as who changed which setting, never with amounts.
    events = env.store.all("SELECT action,actor_id,meta FROM audit_events WHERE action LIKE 'wallet.%' ORDER BY at")
    assert [e["action"] for e in events] == ["wallet.limits.changed", "wallet.reminder.changed"]
    assert all(e["actor_id"] == env.users["alice"]["id"] for e in events)
    assert all(amount not in str(e["meta"]) for e in events for amount in ("1000", "5000", "20000"))
    # Bob's settings are separate; he cannot see Alice's threshold.
    env.login("bob")
    assert get(env, ROOT + "/reminder")["reminders"][0]["configured"] is False


def test_reminder_sends_one_notice_per_crossing(env):
    fund(env, "alice", 10_000)
    env.login("alice")
    body = {"unit": UNIT.code, "expected_revision": 0, "enabled": True, "threshold": "12000"}
    assert env.client.put(ROOT + "/reminder", json=body, headers=env.csrf()).status_code == 200
    sweeper = env.app.state.auth_sweeper
    assert sweeper.wallet_reminders is not None  # installed because the host configured the wallet

    def notices():
        return env.store.all("SELECT params FROM notifications WHERE user_id=? AND kind='wallet.low_balance'",
                             (env.users["alice"]["id"],))

    assert sweeper.run_once()["wallet_reminders"] == {"enabled": True, "checked": 1, "sent": 1, "rearmed": 0}
    params = json.loads(notices()[0]["params"])
    assert params == {"available": "1", "threshold": "1.2", "unit": UNIT.code}
    assert sweeper.run_once()["wallet_reminders"]["sent"] == 0 and len(notices()) == 1
    fund(env, "alice", 5_000)  # back above the line re-arms the reminder
    assert sweeper.run_once()["wallet_reminders"]["rearmed"] == 1
    model_call(env, "alice", conversation(env, "alice", "Spend"), "fixture-flash", usage=None, hold=4_000)
    assert sweeper.run_once()["wallet_reminders"]["sent"] == 1 and len(notices()) == 2
    rendered = env.app.state.notifier.render(
        env.store.one("SELECT * FROM notifications WHERE kind='wallet.low_balance' ORDER BY seq DESC LIMIT 1"), "en")
    assert rendered["title"] == "Your wallet balance is below your reminder"
    assert "1.1 FIXTURE-CNY" in rendered["body"]
    latest = env.store.one("SELECT target FROM notifications WHERE kind='wallet.low_balance' ORDER BY seq DESC LIMIT 1")
    target = latest["target"] if isinstance(latest["target"], dict) else json.loads(latest["target"])
    assert target == {"origin": "accounts", "path": "/account/wallet"}


def test_statement_reads_newest_first_for_recent_activity(env):
    for amount in (100, 200, 300):
        fund(env, "alice", amount)
    env.login("alice")
    newest = get(env, ROOT + "/statement", before_seq="9223372036854775807", limit="2")
    assert [row["delta"] for row in newest["items"]] == ["300", "200"]
    assert newest["next_after_seq"] is None and newest["next_before_seq"] == newest["items"][-1]["seq"]
    older = get(env, ROOT + "/statement", before_seq=newest["next_before_seq"], limit="2")
    assert [row["delta"] for row in older["items"]] == ["100"] and older["next_before_seq"] is None
    both = env.client.get(ROOT + "/statement", params={"before_seq": "5", "after_seq": "0"})
    assert both.status_code == 400


def test_statement_passes_through_an_entry_kind_this_release_does_not_know(env, monkeypatch):
    """Other billing packages add journal kinds (a welcome grant, a chargeback). Recent activity
    must keep listing them, named by the ledger, instead of failing the whole statement. The
    widened rule is still an allowlist of shape: a lowercase identifier, never free text."""
    from synwevo_core.billing.usage import PayerUsage
    fund(env, "alice", 100)
    env.login("alice")
    real = PayerUsage.statement

    def with_unseen_kind(self, *args, **kwargs):
        # Fixture: the real rows, plus one row of a kind this schema cannot store yet.
        rows = real(self, *args, **kwargs)
        return [{**rows[0], "id": "fixture-grant-entry", "kind": "grant"}, *rows]

    monkeypatch.setattr(PayerUsage, "statement", with_unseen_kind)
    newest = get(env, ROOT + "/statement", before_seq="9223372036854775807", limit="5")
    assert [row["kind"] for row in newest["items"]][:2] == ["grant", "test_payment"]

    def with_unsafe_kind(self, *args, **kwargs):
        rows = real(self, *args, **kwargs)
        return [{**rows[0], "kind": "<b>Grant</b>"}]

    monkeypatch.setattr(PayerUsage, "statement", with_unsafe_kind)
    refused = env.client.get(ROOT + "/statement", params={"before_seq": "9223372036854775807"})
    assert refused.status_code == 503 and "<b>" not in refused.text


def test_wallet_usage_schema_is_additive_and_idempotent(tmp_path):
    from synwevo_core import suite_schema
    assert suite_schema.MODULES.index("wallet_usage") > suite_schema.MODULES.index("billing")
    store = Store(str(tmp_path / "fixture-store"))
    index = store.one("SELECT sql FROM sqlite_master WHERE name='billing_calls_account_created'")
    assert index and "billing_calls(account_id,created_at)" in index["sql"]
    user = store.one("SELECT id FROM users LIMIT 1")
    store.execute("INSERT INTO billing_units VALUES (?,?)", (UNIT.code, UNIT.scale))
    if user is None:
        from synwevo_core.identity import Identity
        user = Identity(store).create_user("schema-fixture@example.test", PW, status="active")
    store.execute("INSERT INTO billing_wallet_reminders VALUES ('test',?,?,1,1,5,0,NULL)", (user["id"], UNIT.code))
    with store.conn() as c:
        suite_schema.apply(c)  # a second start re-applies every module without touching rows
    assert store.one("SELECT threshold FROM billing_wallet_reminders")["threshold"] == 5
    with pytest.raises(Exception):
        store.execute("INSERT INTO billing_wallet_reminders VALUES ('prod',?,?,1,1,5,0,NULL)", (user["id"], UNIT.code))
