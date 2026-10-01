"""Real Store/Ledger recovery, with explicitly synthetic payments, routes and SSE fixtures.

No provider query API exists in these fixtures. Recovery receives no key; a forbidden
transport and provider authority callback fail if maintenance ever attempts a model send.
"""
from dataclasses import asdict, replace
import json
import sqlite3
import threading
from types import SimpleNamespace

import httpx
import pytest

from synwevo_core import workers
from synwevo_core.billing import BillingError, Ledger
from synwevo_core.store import Store
from synwevo_suite.agent.deepseek import Attempt
from synwevo_suite.agent.engine import Agent
from synwevo_suite.agent.gateway import MeteredDeepSeek
from synwevo_suite.agent.recovery import MeteringRecovery

from .test_agent_metering import STAMP, USAGE, gateway
from .test_agent_metering_failures import worker


def reopened(g, *, environment="test", namespace="agent"):
    """A fresh Store/gateway, as in another process; no payment or runtime permission."""
    def forbidden(*args, **kwargs):
        pytest.fail("Local recovery attempted provider I/O or current authorization")
    store = Store(g.store.root)
    ledger = Ledger(store, environment=environment, namespace=namespace, clock=lambda: g.ticks[0],
                    authorize=forbidden, max_hold_seconds=100)
    return MeteredDeepSeek(ledger, current_authority=forbidden, transport=httpx.MockTransport(forbidden))


def persisted(g, *, turn_id="fixture-run", step=1, evidence="trusted", completed=True,
              dispatch=True, linked=True, owner=None, status="failed", counters=(0, 0), steps=0):
    """Crash-boundary fixture: real reservation/dispatch, synthetic saved observation.

    The 50-unit fixture hold covers the 42-unit final usage. No network is necessary to
    construct backlog cases; the end-to-end crash test below exercises the actual SSE port.
    """
    context = replace(g.context, run_id=turn_id)
    run = g.model.open_run(context, route=g.route, budget=1000, max_calls=3, deadline=STAMP + 500)
    logical_id = f"step:{step}"
    key = f"fixture-{turn_id}-{step}"
    g.store.execute("INSERT INTO agent_metered_calls(id,run_key,logical_id,request_digest,created_at) VALUES (?,?,?,?,?)",
                    (key, run["run_key"], logical_id, "a" * 64, STAMP))
    call_key = g.ledger.reserve_call(context, logical_call_id=logical_id, amount=50,
                                    price_id=g.route.sale_price_id, deadline=STAMP + 100)
    ticket = g.ledger.prepare_attempt(call_key, attempt_id=key, owner="fixture-owner",
                                     cost_price_id=g.route.cost_price_id, requested_model=g.route.requested_model)
    if linked:
        g.store.execute("UPDATE agent_metered_calls SET call_key=?,attempt_key=? WHERE id=?",
                        (call_key, ticket.attempt_key, key))
    if dispatch:
        g.ledger.dispatch(ticket)
    if evidence is not None:
        attempt = Attempt(requested_model=g.route.requested_model, dispatch="observed",
                          provider_request_id="upstream-" + key, http_status=200, finish_reason="stop",
                          stream_complete=True, usage_reliability="trusted_terminal", raw_usage=USAGE)
        payload = {k: v for k, v in asdict(attempt).items() if k != "usage"}
        payload.update(completed=completed, confirmed_route=g.route.confirmed_route,
                       confirmation_ref=g.route.confirmation_ref, metered_at=STAMP)
        if evidence == "unreliable":
            payload["usage_reliability"] = "unreliable"
        if evidence == "invalid":
            payload["raw_usage"] = USAGE | {"prompt_tokens": -1}
        if evidence == "zero":
            payload["raw_usage"] = {k: 0 for k in USAGE if k != "completion_tokens_details"}
        encoded = "{broken-json" if evidence == "malformed" else json.dumps(payload)
        g.store.execute("INSERT INTO agent_model_observations VALUES (?,?,?,?)",
                        (key, ticket.attempt_key, encoded, STAMP))
    if not g.store.one("SELECT 1 FROM agent_turns WHERE id=?", (turn_id,)):
        g.store.execute(
            "INSERT INTO agent_turns(id,conversation_id,user_id,user_message_id,assistant_message_id,status,"
            "provider,credential,model,steps,input_tokens,output_tokens,usage_state,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'deepseek','platform',?,?,?,?,'pending',?,?)",
            (turn_id, "fixture-conversation", owner or context.actor_user_id, "fixture-user-message",
             "fixture-assistant-message", status, g.route.requested_model, steps, *counters, STAMP, STAMP))
    return SimpleNamespace(id=key, call_key=call_key, attempt_key=ticket.attempt_key, turn_id=turn_id)


def usage(g, turn_id="fixture-run"):
    return g.store.one("SELECT usage_state,input_tokens,output_tokens FROM agent_turns WHERE id=?", (turn_id,))


@pytest.mark.parametrize("crash_kind", ["engine_failure", "process_crash"])
def test_restart_recovers_real_persisted_terminal_usage_and_public_projection(gateway, worker, monkeypatch, crash_kind):
    g, (old_agent, turn, conv) = gateway, worker
    def crash(*args, **kwargs):
        raise sqlite3.OperationalError("fixture settlement crash with private detail")
    with monkeypatch.context() as patch:
        patch.setattr(g.ledger, "settle_trusted_usage", crash)
        if crash_kind == "process_crash":
            with pytest.raises(sqlite3.OperationalError):
                old_agent._run(turn["turn_id"])
            assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (turn["turn_id"],))["status"] == "running"
        else:
            old_agent._run_safely(turn["turn_id"])
    assert usage(g, turn["turn_id"])["usage_state"] == "pending"
    assert len(g.seen) == 1 and g.store.one("SELECT 1 FROM agent_model_observations")
    g.authority[0] = False  # Historical consumption remains recoverable after revocation.
    old_agent.close()
    fresh_gateway = reopened(g)
    agent = Agent(fresh_gateway.store, old_agent.identity, old_agent.caps, old_agent.provider)
    agent.recover()  # Existing startup interruption only; never resubmits the model/tool.
    recovery = MeteringRecovery(gateway=fresh_gateway)
    result = recovery.run_once()
    assert not result.failures and len(result.calls) == len(result.usages) == 1
    assert result.calls[0].charge == 42 and not result.calls[0].late
    assert result.usages[0].input_tokens == 13 and result.usages[0].output_tokens == 7
    assert not recovery.run_once().calls and not recovery.run_once().usages
    try:
        public = agent.conversation_json(g.context.actor_user_id,
            agent.conversation(g.context.actor_user_id, conv["id"]), full=True)
        projected = next(m["turn"]["usage"] for m in public["messages"] if m.get("turn"))
        assert projected == {"input": 13, "output": 7, "state": "known"}
        reconciled = [event for event in agent.events_after(g.context.actor_user_id, conv["id"], 0)
                      if event["type"] == "usage.reconciled"]
        assert len(reconciled) == 1 and reconciled[0]["data"] == {"turn_id": turn["turn_id"]}
    finally:
        agent.close()
    assert len(g.seen) == 1 and g.store.all("SELECT * FROM agent_tool_calls") == []
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == 1
    assert g.balance().booked == 958 and g.balance().held == 0


def test_partial_engine_counter_is_replaced_by_all_steps(gateway):
    g = gateway
    first = persisted(g, counters=(13, 7), steps=1)
    g.model._settle(first.id)
    persisted(g, step=2)
    report = MeteringRecovery(gateway=reopened(g)).run_once()
    assert len(report.usages) == 1 and not report.failures
    assert usage(g) == {"usage_state": "known", "input_tokens": 26, "output_tokens": 14}
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == 2


def test_settlement_committed_before_gateway_marker_is_idempotent(gateway, monkeypatch):
    g = gateway
    call = persisted(g)
    execute = g.store.execute
    def crash_marker(sql, args=()):
        if "SET metering_state='settled'" in sql:
            raise sqlite3.OperationalError("fixture after settlement commit")
        return execute(sql, args)
    with monkeypatch.context() as patch:
        patch.setattr(g.store, "execute", crash_marker)
        with pytest.raises(sqlite3.OperationalError):
            g.model._settle(call.id)
    report = MeteringRecovery(gateway=reopened(g)).run_once()
    assert not report.failures and len(report.usages) == 1
    assert g.balance().booked == 958
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == 1


@pytest.mark.parametrize("completed", [True, False])
def test_expired_trusted_usage_records_cost_without_late_charge(gateway, completed):
    g = gateway
    persisted(g, completed=completed)
    g.ticks[0] += 100
    report = MeteringRecovery(gateway=reopened(g)).run_once()
    assert not report.failures and report.calls[0].late and report.calls[0].charge == 0
    assert usage(g)["usage_state"] == "known"
    assert g.store.all("SELECT * FROM billing_settlements") == []
    assert g.balance().booked == 1000 and g.balance().held == 0


def test_platform_fault_usage_is_visible_without_sale(gateway):
    g = gateway
    persisted(g, completed=False)
    report = MeteringRecovery(gateway=reopened(g)).run_once()
    assert not report.failures and report.calls[0].outcome == "platform_fault"
    assert report.calls[0].charge == 0 and not report.calls[0].late
    assert usage(g)["input_tokens"] == 13 and usage(g)["usage_state"] == "known"
    assert g.balance().booked == 1000 and g.balance().held == 0


@pytest.mark.parametrize("evidence", [None, "unreliable", "invalid", "malformed"])
def test_unknown_usage_releases_only_at_deadline_and_never_becomes_zero(gateway, evidence):
    g = gateway
    call = persisted(g, evidence=evidence)
    recovery = MeteringRecovery(gateway=reopened(g))
    recovery.run_once()
    assert g.store.one("SELECT status FROM billing_calls WHERE id=?", (call.call_key,))["status"] == "reserved"
    assert usage(g)["usage_state"] == "pending"
    g.ticks[0] += 100
    expired = recovery.run_once()
    assert g.store.one("SELECT status FROM billing_calls WHERE id=?", (call.call_key,))["status"] == "released"
    assert g.store.one("SELECT metering_state FROM agent_metered_calls")["metering_state"] == "released"
    assert usage(g)["usage_state"] == "pending" and g.store.all("SELECT * FROM billing_usage") == []
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_events WHERE kind='released'")["n"] == 1
    assert len(expired.calls) == 1 and expired.calls[0].state == "released"
    assert bool(expired.failures) == (evidence in ("invalid", "malformed"))
    assert g.seen == []


def test_trusted_explicit_zero_can_be_known(gateway):
    persisted(gateway, evidence="zero")
    report = MeteringRecovery(gateway=reopened(gateway)).run_once()
    assert not report.failures and len(report.usages) == 1
    assert usage(gateway) == {"usage_state": "known", "input_tokens": 0, "output_tokens": 0}


def test_orphan_pre_dispatch_hold_expires_without_repairing_request_fences(gateway):
    g = gateway
    call = persisted(g, linked=False, dispatch=False, evidence=None)
    g.ticks[0] += 100
    result = MeteringRecovery(gateway=reopened(g)).run_once()
    assert result.calls[0].state == "released" and not result.failures
    assert g.store.one("SELECT status FROM billing_attempts WHERE id=?", (call.attempt_key,))["status"] == "released"
    assert g.store.one("SELECT call_key,attempt_key FROM agent_metered_calls") == {"call_key": None, "attempt_key": None}
    assert usage(g)["usage_state"] == "pending"


def test_call_limit_also_bounds_expiry_hidden_inside_settlement(gateway):
    g = gateway
    for i in range(5):
        persisted(g, turn_id=f"fixture-batch-{i}")
    g.ticks[0] += 100
    recovery = MeteringRecovery(gateway=reopened(g), batch_size=2)
    one = recovery.run_once()
    assert one.calls_scanned == 2 and one.turns_scanned <= 2 and not one.failures
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='released'")["n"] == 2
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 2
    two, three = recovery.run_once(), recovery.run_once()
    assert two.calls_scanned == 2 and three.calls_scanned == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_calls WHERE status='released'")["n"] == 5
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 5


def test_bad_row_is_isolated_and_keyset_does_not_starve_later_rows(gateway):
    g = gateway
    persisted(g, turn_id="fixture-bad", evidence="malformed")
    good = persisted(g, turn_id="fixture-good")
    recovery = MeteringRecovery(gateway=reopened(g), batch_size=1)
    first, second = recovery.run_once(), recovery.run_once()
    assert len(first.failures) == 1 and first.failures[0].error_type == "JSONDecodeError"
    assert len(second.calls) == 1 and second.calls[0].call_id == good.id
    recovery.run_once()  # Wrap back to the failed record, without losing future eligibility.
    assert recovery.run_once().failures
    assert "broken-json" not in repr(first)


def test_one_bad_record_does_not_abort_the_same_round(gateway):
    g = gateway
    persisted(g, turn_id="fixture-bad-in-round", evidence="invalid")
    good = persisted(g, turn_id="fixture-good-in-round")
    result = MeteringRecovery(gateway=reopened(g)).run_once()
    assert result.calls_scanned == 2 and len(result.failures) == 1
    assert len(result.calls) == 1 and result.calls[0].call_id == good.id
    assert len(result.usages) == 1 and result.usages[0].turn_id == good.turn_id
    assert usage(g, "fixture-bad-in-round")["usage_state"] == "pending"


@pytest.mark.parametrize("expired", [False, True])
def test_two_instances_on_one_database_settle_and_project_once(gateway, monkeypatch, expired):
    g = gateway
    persisted(g)
    if expired:
        g.ticks[0] += 100
    recoveries = [MeteringRecovery(gateway=reopened(g)) for _ in range(2)]
    barrier = threading.Barrier(2, timeout=5)
    for recovery in recoveries:
        scan = recovery._scan_calls
        def simultaneous_scan(scan=scan):
            rows = scan()
            barrier.wait()  # Both have selected the same row before either starts settlement.
            return rows
        monkeypatch.setattr(recovery, "_scan_calls", simultaneous_scan)
    reports = []
    def run(recovery):
        reports.append(recovery.run_once())
    threads = [threading.Thread(target=run, args=(r,)) for r in recoveries]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert len(reports) == 2 and all(not r.failures for r in reports)
    assert sum(len(r.usages) for r in reports) == 1
    assert usage(g) == {"usage_state": "known", "input_tokens": 13, "output_tokens": 7}
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == (0 if expired else 1)
    assert g.balance().booked == (1000 if expired else 958)
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_events WHERE kind='released'")["n"] == int(expired)
    assert g.store.one("SELECT COUNT(*) AS n FROM agent_events WHERE type='usage.reconciled'")["n"] == 1


@pytest.mark.parametrize("status", ["queued", "running", "waiting_confirmation"])
def test_live_turn_is_not_overwritten(gateway, status):
    persisted(gateway, status=status)
    report = MeteringRecovery(gateway=reopened(gateway)).run_once()
    assert len(report.calls) == 1 and not report.usages
    assert usage(gateway)["usage_state"] == "pending"
    assert gateway.store.all("SELECT * FROM agent_events") == []


@pytest.mark.parametrize("environment,namespace", [("live", "agent"), ("test", "other")])
def test_environment_and_namespace_are_isolated(gateway, environment, namespace):
    g = gateway
    persisted(g)
    g.ticks[0] += 100
    report = MeteringRecovery(gateway=reopened(g, environment=environment, namespace=namespace)).run_once()
    assert report.calls_scanned == report.turns_scanned == 0
    assert g.store.one("SELECT status FROM billing_calls")["status"] == "reserved"
    assert usage(g)["usage_state"] == "pending"


@pytest.mark.parametrize("case", ["wrong_owner", "missing_step", "legacy_steps", "unresolved_step", "personal_key", "unfenced_call"])
def test_incomplete_or_mismatched_turn_projection_stays_pending(gateway, case):
    g = gateway
    persisted(g, step=2 if case == "missing_step" else 1,
              owner="fixture-someone-else" if case == "wrong_owner" else None,
              steps=2 if case == "legacy_steps" else 0)
    if case == "unresolved_step":
        persisted(g, step=2, evidence=None)
    if case == "personal_key":
        g.store.execute("UPDATE agent_turns SET credential='personal'")
    if case == "unfenced_call":
        g.ledger.reserve_call(g.context, logical_call_id="fixture-outside-gateway", amount=50,
                              price_id=g.route.sale_price_id, deadline=STAMP + 100)
    result = MeteringRecovery(gateway=reopened(g)).run_once()
    assert not result.usages and usage(g)["usage_state"] == "pending"
    assert g.store.all("SELECT * FROM agent_events") == []
    if case == "wrong_owner":
        assert result.failures[0].phase == "turns"


def test_settled_marker_without_public_projection_is_found_after_restart(gateway):
    g = gateway
    call = persisted(g)
    g.model._settle(call.id)
    result = MeteringRecovery(gateway=reopened(g)).run_once()
    assert result.calls_scanned == 0 and len(result.usages) == 1


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_reconciled_event_is_owner_scoped_minimal_and_not_repeated(gateway, status):
    g = gateway
    call = persisted(g, status=status)
    recovery = MeteringRecovery(gateway=reopened(g))
    assert len(recovery.run_once().usages) == 1
    assert not recovery.run_once().usages
    assert not MeteringRecovery(gateway=reopened(g)).run_once().usages
    events = g.store.all("SELECT conversation_id,user_id,turn_id,type,body,created_at FROM agent_events")
    assert len(events) == 1
    event = events[0]
    assert {key: event[key] for key in ("conversation_id", "user_id", "turn_id", "type")} == {
        "conversation_id": "fixture-conversation", "user_id": g.context.actor_user_id,
        "turn_id": call.turn_id, "type": "usage.reconciled"}
    assert json.loads(event["body"]) == {"turn_id": call.turn_id}
    turn = g.store.one("SELECT status,updated_at FROM agent_turns WHERE id=?", (call.turn_id,))
    assert turn["status"] == status and event["created_at"] == turn["updated_at"]


def test_event_write_failure_rolls_back_projection_and_retry_emits_once(gateway):
    g = gateway
    persisted(g, counters=(4, 2))
    # A fixture-only SQLite trigger fails inside the real Store transaction, after the
    # counter update. It proves atomicity without replacing Store or Ledger methods.
    g.store.execute("CREATE TEMP TRIGGER fixture_reconcile_event_failure BEFORE INSERT ON agent_events "
                    "WHEN NEW.type='usage.reconciled' BEGIN SELECT RAISE(ABORT,'fixture event write failure'); END")
    recovery = MeteringRecovery(gateway=g.model)
    failed = recovery.run_once()
    assert len(failed.failures) == 1 and failed.failures[0].phase == "turns"
    assert not failed.usages and g.store.all("SELECT * FROM agent_events") == []
    assert usage(g) == {"usage_state": "pending", "input_tokens": 4, "output_tokens": 2}
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == 1
    g.store.execute("DROP TRIGGER fixture_reconcile_event_failure")
    retried = recovery.run_once()
    assert not retried.failures and len(retried.usages) == 1
    assert usage(g) == {"usage_state": "known", "input_tokens": 13, "output_tokens": 7}
    assert g.store.one("SELECT COUNT(*) AS n FROM agent_events WHERE type='usage.reconciled'")["n"] == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_settlements")["n"] == 1


def test_projection_refuses_overlarge_attempt_history(gateway, monkeypatch):
    g = gateway
    persisted(g)
    persisted(g, step=2)
    recovery = MeteringRecovery(gateway=reopened(g))
    monkeypatch.setattr(recovery, "MAX_TURN_CALLS", 1)
    result = recovery.run_once()
    assert len(result.calls) == 2 and not result.usages
    assert usage(g)["usage_state"] == "pending"


def test_periodic_worker_survives_scan_error_and_stops_promptly(gateway, monkeypatch):
    g = gateway
    persisted(g)
    recovery = MeteringRecovery(gateway=reopened(g), interval_seconds=0.01)
    scan, done, calls = recovery._scan_calls, threading.Event(), []
    def intermittent_scan():
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("fixture transient failure; private text")
        return scan()
    sync = recovery._sync_usage
    def projected(row):
        fact = sync(row)
        if fact:
            done.set()
        return fact
    monkeypatch.setattr(recovery, "_scan_calls", intermittent_scan)
    monkeypatch.setattr(recovery, "_sync_usage", projected)
    assert recovery.last_round is None and not recovery.running
    recovery.start()
    thread = recovery._thread
    assert recovery.start()._thread is thread and recovery in workers.live()
    try:
        assert done.wait(5)
    finally:
        assert recovery.close(wait=2)
    assert len(calls) >= 2 and recovery.run_once().stopped
    assert recovery.close()
    with pytest.raises(RuntimeError, match="closed"):
        recovery.start()


def test_stop_and_overlap_finish_only_current_record(gateway, monkeypatch):
    g = gateway
    persisted(g, turn_id="fixture-stop-1")
    persisted(g, turn_id="fixture-stop-2")
    recovery = MeteringRecovery(gateway=reopened(g))
    entered, proceed = threading.Event(), threading.Event()
    original = recovery._recover_call
    def blocked(row):
        entered.set()
        assert proceed.wait(5)
        return original(row)
    monkeypatch.setattr(recovery, "_recover_call", blocked)
    recovery.start()
    try:
        assert entered.wait(5)
        assert recovery.run_once().busy
        assert not recovery.close(wait=0)
    finally:
        proceed.set()
        assert recovery.close(wait=2)
    assert recovery.last_round.stopped and recovery.last_round.calls_scanned == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM billing_usage")["n"] == 1


def test_round_budget_stops_before_next_record(gateway, monkeypatch):
    g = gateway
    persisted(g, turn_id="fixture-budget-1")
    persisted(g, turn_id="fixture-budget-2")
    recovery = MeteringRecovery(gateway=reopened(g), round_seconds=1)
    monotonic = [0.0]
    monkeypatch.setattr("synwevo_suite.agent.recovery.time.monotonic", lambda: monotonic[0])
    original = recovery._recover_call
    def advance(row):
        fact = original(row)
        monotonic[0] += 2
        return fact
    monkeypatch.setattr(recovery, "_recover_call", advance)
    report = recovery.run_once()
    assert report.budget_exhausted and report.calls_scanned == 1 and report.turns_scanned == 0
    assert recovery.run_once().calls_scanned == 1


@pytest.mark.parametrize("options", [
    {"batch_size": 0}, {"batch_size": 501}, {"batch_size": True}, {"interval_seconds": 0},
    {"interval_seconds": float("inf")}, {"round_seconds": float("nan")}, {"round_seconds": False},
])
def test_invalid_bounds_are_rejected(gateway, options):
    with pytest.raises(ValueError):
        MeteringRecovery(gateway=gateway.model, **options)
