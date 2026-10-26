"""The exact cost rollup (suite_schema/model_cost_rollup.py, billing/cost_rollup.py) on real Stores.

Isolated fixtures only: no keys, no provider traffic, never ``data/``. Bulk history is cloned from
one real Ledger dispatch with plain SQL (marked FIXTURE) so the triggers see exactly what a bulk
writer, an old release or a hand-written repair would write. Expected amounts are independent
arithmetic over the cloned rows, never values read back from the implementation under test.
"""
from __future__ import annotations

from fractions import Fraction
import json
import re
from pathlib import Path
import sqlite3

import pytest

from synwevo_core.billing import BillingError, Ledger, Rate, Unit, Usage
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.risk import BrakeTripped, RiskPolicy
from synwevo_core.store import Store
from tests.integration.test_billing_ledger import COST_UNIT, cost_price, evidence, fund, money  # noqa: F401
from tests.integration.test_model_cost_admission import exact, funded, policy, prepare, send, utc  # noqa: F401

BIG = 10**12
SCOPE = ("test", "fixture-provider", "fixture-provider-account")
DERIVED = ("model_cost_buckets", "model_cost_inflight", "model_cost_hold", "model_cost_exceptions")


def rollup():
    from synwevo_core.billing import cost_rollup
    return cost_rollup


def generous(**changes):
    return policy(**{"daily_budget": BIG, "weekly_budget": BIG, "monthly_budget": BIG, "unknown_budget": BIG, **changes})


def velocity_policy(*, route_cost=None, payer_cost=None, window=86_400, **changes):
    risk = RiskPolicy(allowlist_only=False, payer_window_s=window, payer_window_cost=payer_cost,
                      route_window_s=window, route_window_cost=route_cost, balance_warn=None, balance_close=None,
                      balance_poll_s=60, reconcile_tolerance=0, pending_alert=None)
    return generous(risk=risk, **changes)


def template(money, name, *, amount=7, settle=False, stop=False, usage=None, hold=20, deadline_seconds=500):
    """One real reservation, dispatch and admission (and usage or a local stop) to clone from."""
    store, ledger, _, _ = money
    _, ticket = prepare(money, name, hold=hold, deadline_seconds=deadline_seconds)
    admission = CostAdmission(ledger, current_policy=generous)
    send(admission, ticket, amount=amount)
    if settle:
        ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=usage))
    if stop:
        ledger.mark_usage_pending(ticket)
        assert admission.record_local_stop(ticket.attempt_key)
    return ticket.attempt_key


def clone(store, source, times, *, prefix, settled, call_status=None, stop_at=None, expires_after=120,
          reserved=None, usage_first=False, cost=None):
    """FIXTURE: ``len(times)`` copies of a real admitted call, one per admitted_at in ``times``,
    written with plain SQL (bulk, the way old code or an operator would). Each copy has its own
    call and attempt with valid foreign keys; ``settled`` copies the usage fact too; ``stop_at``
    records a local stop; ``cost`` overrides the copied (cost_n, cost_d)."""
    call = store.one("SELECT call_key FROM billing_attempts WHERE id=?", (source,))["call_key"]
    status = call_status or ("settled" if settled else "reserved")
    with store.tx() as t:
        t.execute("CREATE TEMP TABLE IF NOT EXISTS fixture_times(i INTEGER PRIMARY KEY, at INTEGER NOT NULL)")
        t.execute("DELETE FROM fixture_times")
        t.executemany("INSERT INTO fixture_times VALUES (?,?)", list(enumerate(times)))
        t.execute("INSERT INTO billing_calls(id,run_key,logical_call_id,context,account_id,price_id,hold_units,deadline,"
                  "status,created_at) SELECT ?||'-call-'||i,run_key,?||'-'||i,context,account_id,price_id,0,deadline,?,"
                  "created_at FROM fixture_times, billing_calls WHERE id=?", (prefix, prefix, status, call))
        t.execute("INSERT INTO billing_attempts(id,call_key,attempt_id,owner,fence,lease_until,environment,provider,"
                  "requested_model,cost_price_id,status,created_at,dispatched_at) SELECT ?||'-attempt-'||i,?||'-call-'||i,"
                  "?||'-'||i,owner,?||'-fence-'||i,lease_until,environment,provider,requested_model,cost_price_id,?,"
                  "created_at,dispatched_at FROM fixture_times, billing_attempts WHERE id=?",
                  (prefix, prefix, prefix, prefix, "settled" if settled else "usage_pending", source))

        def usage():
            n, d = cost if cost is not None else (None, None)
            t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                      "absorbed_d,late,created_at) SELECT ?||'-attempt-'||i,facts,outcome,COALESCE(?,cost_n),"
                      "COALESCE(?,cost_d),cost_unit,charge,absorbed_n,absorbed_d,late,created_at "
                      "FROM fixture_times, billing_usage WHERE attempt_key=?", (prefix, n, d, source))

        if settled and usage_first:
            usage()
        t.execute("INSERT INTO model_cost_admissions(attempt_key,environment,provider,account_ref,unit,reserved_units,"
                  "policy_revision,limits_json,admitted_at,expires_at) SELECT ?||'-attempt-'||i,environment,provider,"
                  "account_ref,unit,COALESCE(?,reserved_units),policy_revision,limits_json,at,at+? "
                  "FROM fixture_times, model_cost_admissions WHERE attempt_key=?", (prefix, reserved, expires_after, source))
        if settled and not usage_first:
            usage()
        if stop_at is not None:
            t.execute("INSERT INTO model_cost_endings(attempt_key,ending,recorded_at) "
                      "SELECT ?||'-attempt-'||i,'stopped_locally',? FROM fixture_times", (prefix, stop_at))


def derived_rows(store, scope=SCOPE):
    out = {}
    for table in DERIVED:
        rows = store.all(f"SELECT * FROM {table} WHERE environment=? AND provider=? AND account_ref=?", scope)
        out[table] = sorted(json.dumps(r, sort_keys=True) for r in rows)
    return out


def day_bucket(store, start, unit=COST_UNIT.code):
    return store.one("SELECT * FROM model_cost_buckets WHERE environment=? AND provider=? AND account_ref=? "
                     "AND dim='account' AND span=86400 AND start=? AND unit=? AND den=0", (*SCOPE, start, unit))


def value(row):
    from synwevo_core.suite_schema.model_cost_rollup import B, G
    return (row["bound_hi"] * B + row["bound_lo"] - row["rbound_hi"] * B - row["rbound_lo"]
            + row["cost_hi"] * B + row["cost_lo"] + row["cost_carry"] + Fraction(row["cost_frac"], G))


def clean(store, **kwargs):
    report = rollup().CostRollup.check(store, **kwargs)
    assert report.ok, report.summary()
    return report


# ---------------------------------------------------------------- 1-3: no limit grows with volume
def test_20000_settled_admissions_this_month_admit_with_exact_totals(funded):
    store, ledger, _, ticks = funded
    now = utc("2034-05-20T12:00:00")                   # a Saturday; week from 05-15, month from 05-01
    ticks[0] = now - 7200
    source = template(funded, "month-template", amount=20, settle=True)
    month, week, day = utc("2034-05-01T00:00:00"), utc("2034-05-15T00:00:00"), utc("2034-05-20T00:00:00")
    times = [month + (i * 997) % (now - 3600 - month) for i in range(19_999)]   # + the template: 20,000
    clone(store, source, times, prefix="fixture-month", settled=True)
    ticks[0] = now
    expected = {"daily": 19 * (1 + sum(t >= day for t in times)), "weekly": 19 * (1 + sum(t >= week for t in times)),
                "monthly": 19 * 20_000}
    admission = CostAdmission(ledger, current_policy=lambda: generous(daily_budget=expected["daily"] + 5))
    _, ticket = prepare(funded, "month-admit")
    send(admission, ticket, amount=5)                   # exactly at the daily budget
    snapshot = admission.snapshot()
    assert snapshot["records"] == 20_001
    assert [exact(snapshot, p) for p in ("daily", "weekly", "monthly")] == [
        expected["daily"] + 5, expected["weekly"] + 5, expected["monthly"] + 5]
    _, refused = prepare(funded, "month-over")
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, refused, amount=1)
    clean(store)


def test_12000_local_stops_within_a_day_admit_and_absorbed_is_exact(funded):
    store, ledger, _, ticks = funded
    now = utc("2034-05-17T12:00:00")                    # a Wednesday: the windows reach back past 24 h
    ticks[0] = now - 300
    source = template(funded, "stop-template", amount=7, stop=True, deadline_seconds=900)
    times = [now - 86_000 + (i * 7) % 85_000 for i in range(12_000)]
    clone(store, source, times, prefix="fixture-stop", settled=False, stop_at=now - 60)
    ticks[0] = now
    admission = CostAdmission(ledger, current_policy=lambda: generous(max_unknown=1))
    _, ticket = prepare(funded, "after-stops")
    send(admission, ticket, amount=3)
    snapshot = admission.snapshot()
    assert (snapshot["unknown_count"], snapshot["unknown_reserved_units"], snapshot["inflight"]) == (1, 3, 1)
    assert (snapshot["absorbed_unknown_count"], snapshot["absorbed_unknown_units"]) == (12_001, 7 * 12_001)
    assert exact(snapshot, "weekly") == 7 * 12_001 + 3
    clean(store)


def test_12000_rows_on_one_route_velocity_is_exact_and_brakes_only_above_the_threshold(funded):
    store, ledger, _, ticks = funded
    now = utc("2034-05-17T12:00:00")
    ticks[0] = now - 100
    source = template(funded, "route-template", amount=20, settle=True)
    times = [now - 86_399 + (i * 13) % 86_000 for i in range(12_000)]
    clone(store, source, times, prefix="fixture-route", settled=True)
    outside = [now - 86_400, now - 90_000]              # just outside the window: not counted
    clone(store, source, outside, prefix="fixture-old", settled=True)
    ticks[0] = now
    observed = 19 * 12_001                              # the clones plus the template, all inside
    threshold = observed + 11
    current = [velocity_policy(route_cost=threshold)]
    admission = CostAdmission(ledger, current_policy=lambda: current[0])
    _, first = prepare(funded, "route-at-threshold")
    send(admission, first, amount=11)                   # observed + 11 == threshold: admitted
    _, second = prepare(funded, "route-zero")
    send(admission, second, amount=0)                   # still exactly at the threshold
    _, third = prepare(funded, "route-over")
    with pytest.raises(BrakeTripped, match="risk_route_brake") as caught:
        send(admission, third, amount=1)
    assert caught.value.trip["observed_units"] == threshold + 1
    with store.tx() as t:
        payer, route = rollup().velocity(t, environment="test", provider="fixture-provider",
                                         account_ref="fixture-provider-account", unit=COST_UNIT,
                                         owner=funded[2][0], route="fixture-model-alias", now=now,
                                         risk=velocity_policy(route_cost=1, payer_cost=1).risk)
    assert route == payer == threshold
    clean(store)


# ---------------------------------------------------------------- 4-7: write paths
def test_late_usage_on_day_plus_three_updates_the_bucket_of_its_admission_day(funded):
    store, ledger, _, ticks = funded
    day = utc("2034-05-15T00:00:00")
    ticks[0] = day + 3600
    _, ticket = prepare(funded, "late")
    send(CostAdmission(ledger, current_policy=generous), ticket, amount=20)
    before = day_bucket(store, day)
    assert (before["n_adm"], before["n_res"], value(before)) == (1, 0, 20)
    ticks[0] = day + 3 * 86_400 + 60
    result = ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    assert result.late and result.cost == 19
    after = day_bucket(store, day)
    assert (after["n_adm"], after["n_res"], after["rbound_lo"], value(after)) == (1, 1, 20, 19)
    assert store.all("SELECT start FROM model_cost_buckets WHERE dim='account' AND environment='test'") == [{"start": day}]
    clean(store)


@pytest.mark.parametrize("settled", [False, True])
def test_monday_the_first_tail_refuses_a_pending_foreign_unit_but_not_a_settled_one(funded, settled):
    store, ledger, _, ticks = funded
    now = utc("2034-05-01T12:00:00")                    # Monday the 1st: every window starts today
    other = Unit("FIXTURE-OTHER-COST", 8)
    price = cost_price(price_id="fixture-other-unit", unit=other)
    ledger.publish_price(price)
    ticks[0] = now - 19 * 3600                          # 2034-04-30T17:00, inside [now-24h, today)
    _, closed = prepare(funded, "tail-closed")          # a closed unknown in the policy unit
    send(CostAdmission(ledger, current_policy=generous), closed, amount=6)
    ticks[0] = now - 18 * 3600
    _, foreign = prepare(funded, "tail-foreign", cost_id=price.price_id)
    ledger.dispatch(foreign)
    with store.tx() as t:                               # FIXTURE: an admission in another unit (old policy)
        t.execute("INSERT INTO model_cost_admissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (foreign.attempt_key, *SCOPE, other.code, 4, "fixture-old-unit", "{}", ticks[0], ticks[0] + 120))
    if settled:
        ledger.settle_trusted_usage(foreign.attempt_key, evidence(foreign))
    ticks[0] = now
    admission = CostAdmission(ledger, current_policy=generous)
    if not settled:
        with pytest.raises(BillingError, match="cost_unit_mismatch"):
            admission.snapshot()
        return
    snapshot = admission.snapshot()
    # The closed unknown of yesterday counts as a record (today's definition), not in any window.
    assert snapshot["records"] == 1 and snapshot["unknown_count"] == 0
    assert [exact(snapshot, p) for p in ("daily", "weekly", "monthly")] == [0, 0, 0]
    assert snapshot["rollup"]["open_faults"] == []
    _, ticket = prepare(funded, "after-tail")
    send(admission, ticket, amount=1)
    assert admission.snapshot()["records"] == 2
    clean(store)


@pytest.mark.parametrize("closing", ["ending", "release", "settlement"])
def test_a_hold_row_disappears_when_the_hold_closes(funded, closing):
    store, ledger, _, ticks = funded
    admission = CostAdmission(ledger, current_policy=generous)
    call, ticket = prepare(funded, "hold-" + closing)
    send(admission, ticket, amount=7)
    key = ticket.attempt_key
    hold = store.one("SELECT * FROM model_cost_hold WHERE attempt_key=?", (key,))
    assert hold["call_key"] == call and hold["deadline"] == ledger.call_status(call)["deadline"]
    assert store.one("SELECT 1 AS x FROM model_cost_inflight WHERE attempt_key=?", (key,))
    if closing == "ending":
        ledger.mark_usage_pending(ticket)
        admission.record_local_stop(key)
    elif closing == "release":
        ledger.mark_usage_pending(ticket)
        ticks[0] += 500
        assert ledger.expire_due() == 1
    else:
        ledger.settle_trusted_usage(key, evidence(ticket))
        assert store.one("SELECT 1 AS x FROM model_cost_inflight WHERE attempt_key=?", (key,)) is None
    assert store.one("SELECT 1 AS x FROM model_cost_hold WHERE attempt_key=?", (key,)) is None
    assert store.all("SELECT * FROM model_cost_rollup_deltas") == []
    clean(store)


@pytest.mark.cost_rollup_unchecked
def test_derived_tables_refuse_direct_writes_and_a_tampered_bucket_never_blocks_a_fact(funded):
    store, ledger, _, _ = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _, ticket = prepare(funded, "gate")
    send(admission, ticket, amount=7)
    for sql in ("UPDATE model_cost_buckets SET n_adm=0", "DELETE FROM model_cost_buckets",
                "INSERT INTO model_cost_buckets(environment,provider,account_ref,dim,member,span,start,unit,den) "
                "VALUES ('test','p','a','account','',86400,0,'U',0)",
                "DELETE FROM model_cost_inflight", "UPDATE model_cost_hold SET deadline=0",
                "INSERT INTO model_cost_exceptions VALUES ('k','test','p','a',0)"):
        with pytest.raises(sqlite3.IntegrityError, match="cost_rollup_derived"):
            store.execute(sql)
    # FIXTURE: hand-written deltas bypass the gate (the residual risk the verifier covers).
    with store.tx() as t:
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("UPDATE model_cost_buckets SET bound_lo=NULL,n_res='x',cost_frac=-1 WHERE environment='test'")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    result = ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))   # the fact still commits
    assert result.cost == 19 and store.one("SELECT 1 AS x FROM billing_usage")
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
        admission.snapshot()
    assert not rollup().CostRollup.check(store).ok


# ---------------------------------------------------------------- 8-10: faults, rebuild, backfill
@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("kind", ["orphan", "malformed"])
def test_a_malformed_or_orphan_admission_commits_as_a_fault_and_refuses_its_scope(funded, kind):
    store, ledger, _, _ = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _, good = prepare(funded, "before-fault")
    send(admission, good, amount=1)
    _, dispatched = prepare(funded, "unadmitted")
    ledger.dispatch(dispatched)
    key = "fixture-orphan-attempt" if kind == "orphan" else dispatched.attempt_key
    admitted_at = 1 if kind == "orphan" else "soon"
    with store.conn() as c:                             # FIXTURE: foreign keys off, as a broken import would
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("INSERT INTO model_cost_admissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (key, *SCOPE, COST_UNIT.code, 3, "fixture", "{}", admitted_at, 100))
        finally:
            c.execute("PRAGMA foreign_keys=ON")
    fault = store.one("SELECT * FROM model_cost_rollup_faults WHERE id=?", (rollup().fact_fault_id(key, SCOPE),))
    assert fault["resolved_at"] is None and fault["code"] == f"fact_{kind}_admission"
    assert (fault["environment"], fault["provider"], fault["account_ref"]) == SCOPE
    _, refused = prepare(funded, "after-fault")
    count = store.one("SELECT COUNT(*) AS n FROM model_cost_admissions")["n"]
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
        send(admission, refused, amount=1)
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_admissions")["n"] == count
    assert store.one("SELECT status FROM billing_attempts WHERE id=?", (refused.attempt_key,))["status"] == "prepared"
    reopened = Store(store.root)                        # opening never refuses
    assert reopened.one("SELECT resolved_at FROM model_cost_rollup_faults WHERE id=?",
                        (rollup().fact_fault_id(key, SCOPE),))["resolved_at"] is None
    with pytest.raises(BillingError, match="cost_rollup_rebuild_refused") as caught:
        rollup().CostRollup.rebuild(store, environment="test", provider="fixture-provider",
                                    account_ref="fixture-provider-account", actor="fixture-operator",
                                    reason="fixture rebuild over a broken fact")
    assert key in caught.value.attempt_keys
    assert store.one("SELECT resolved_at FROM model_cost_rollup_faults WHERE id=?",
                     (rollup().fact_fault_id(key, SCOPE),))["resolved_at"] is None


@pytest.mark.cost_rollup_unchecked
def test_a_tampered_bucket_latches_only_its_scope_and_a_rebuild_restores_identical_rows(funded):
    store, ledger, _, ticks = funded
    a = CostAdmission(ledger, current_policy=generous)
    b = CostAdmission(ledger, current_policy=lambda: generous(account_ref="fixture-other-account"))
    for i, admission in enumerate((a, b, a)):
        _, ticket = prepare(funded, f"scope-{i}")
        send(admission, ticket, amount=5 + i)
        if i == 0:
            ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    original = derived_rows(store)
    CR = rollup().CostRollup

    def tamper():
        with store.tx() as t:                           # FIXTURE: a direct edit past the gate
            t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
            t.execute("UPDATE model_cost_buckets SET cost_lo=cost_lo+1 WHERE environment=? AND provider=? "
                      "AND account_ref=? AND dim='account'", SCOPE)
            t.execute("DELETE FROM model_cost_rollup_deltas")

    tamper()
    report = CR.verify(store)
    assert set(report.mismatched) == {SCOPE}
    _, refused = prepare(funded, "latched")
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
        send(a, refused, amount=1)
    _, other = prepare(funded, "other-scope")
    send(b, other, amount=1)                            # the other account is unaffected
    alert = store.all("SELECT * FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")
    assert len(alert) == 1
    result = CR.rebuild(store, environment="test", provider="fixture-provider",
                        account_ref="fixture-provider-account", actor="fixture-operator",
                        reason="fixture tamper repaired")
    assert result["resolved_faults"] and derived_rows(store) == original
    assert store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL") == []
    send(a, refused, amount=1)
    # A verify that read a snapshot before the rebuild is recorded as superseded, not latched.
    tamper()
    stale = CR.check(store)
    assert not stale.ok
    CR.rebuild(store, environment="test", provider="fixture-provider", account_ref="fixture-provider-account",
               actor="fixture-operator", reason="fixture second repair")
    assert CR.record(store, stale) == {SCOPE: "superseded"}
    assert store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL") == []
    assert store.one("SELECT status FROM model_cost_rollup_audits WHERE mode='verify' AND provider='fixture-provider' "
                     "AND account_ref='fixture-provider-account' ORDER BY rowid DESC LIMIT 1")["status"] == "superseded"
    clean(store)


@pytest.mark.parametrize("damage", ["drop_hold", "drop_buckets", "empty_inflight"])
def test_a_lost_derived_table_is_replayed_on_the_next_start(funded, damage):
    store, ledger, _, _ = funded
    admission = CostAdmission(ledger, current_policy=generous)
    for i in range(3):
        _, ticket = prepare(funded, f"replay-{i}")
        send(admission, ticket, amount=4 + i)
        if i == 1:
            ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket))
    original, before = derived_rows(store), admission.snapshot()
    with store.tx() as t:
        if damage == "empty_inflight":
            t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-damage')")
            t.execute("DELETE FROM model_cost_inflight")
            t.execute("DELETE FROM model_cost_rollup_deltas")
        else:
            t.execute("DROP TABLE " + {"drop_hold": "model_cost_hold", "drop_buckets": "model_cost_buckets"}[damage])
    assert store.one("SELECT value FROM meta WHERE key='model_cost_rollup_v1'") == {"value": "1"}
    reopened = Store(store.root)
    assert derived_rows(reopened) == original
    assert reopened.one("SELECT mode FROM model_cost_rollup_audits ORDER BY rowid DESC LIMIT 1")["mode"] == "backfill"
    reopened_ledger = Ledger(reopened, environment="test", namespace=ledger.namespace, clock=ledger.clock)
    again = CostAdmission(reopened_ledger, current_policy=generous).snapshot()
    assert {k: again[k] for k in ("daily", "unknown_count", "records")} == {k: before[k] for k in ("daily", "unknown_count", "records")}
    clean(reopened)


# ---------------------------------------------------------------- 11-13: exactness, plain SQL, latch
def _metered(funded, name, *, amount=1, cost_id="fixture-cost-v1", usage=None):
    store, ledger, _, _ = funded
    _, ticket = prepare(funded, name, cost_id=cost_id)
    top = 2**63 - 1
    send(CostAdmission(ledger, current_policy=lambda: generous(daily_budget=top, weekly_budget=top, monthly_budget=top)),
         ticket, amount=amount)
    if usage is not None:
        ledger.settle_trusted_usage(ticket.attempt_key, evidence(ticket, usage=usage))
    return ticket.attempt_key


def _raw_usage(store, key, n, d):
    """FIXTURE: a usage fact with an arbitrary stored cost, as a bulk import could write it."""
    with store.tx() as t:
        t.execute("UPDATE billing_attempts SET status='settled' WHERE id=?", (key,))
        t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                  "absorbed_d,late,created_at) VALUES (?,?,?,?,?,?,0,'0','1',0,0)",
                  (key, "{}", "completed", n, d, COST_UNIT.code))


def test_exact_sums_for_grid_rational_and_exception_costs(funded):
    store, ledger, _, _ = funded
    limit = 2**53
    # Ledger-made off-grid costs (class Q): 1/3, 1/7 and (3*2^53+1)/3, priced per single token.
    prices = {"third": Rate(1, 3), "seventh": Rate(1, 7), "rational": Rate(3 * limit + 1, 3)}
    expected = Fraction(0)
    for name, rate in prices.items():
        ledger.publish_price(cost_price(price_id="fixture-" + name, input_cached=rate))
        _metered(funded, name, cost_id="fixture-" + name, usage=Usage(0, 1, 0))
        expected += rate.fraction
    # Stored costs a bulk writer could leave: complements (Q), a 20-digit numerator and a padded
    # "007" (both class X, parsed exactly as before), and two on-grid decimals (class R).
    raw = {"two-thirds": ("2", "3"), "six-sevenths": ("6", "7"), "two-thirds-more": ("2", "3"),
           "twenty-digit": ("12345678901234567890", "10"), "padded": ("007", "1"),
           "grid": ("123456789012345678", "1000000000000000"), "grid-complement": ("876543210987654322", "1000000000000000")}
    for name, (n, d) in raw.items():
        _raw_usage(store, _metered(funded, name), n, d)
        expected += Fraction(int(n), int(d))
    assert expected == 3 + limit + 1234567890123456789 + 7 + 1000 and expected.denominator == 1
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_exceptions WHERE environment='test'")["n"] == 2
    assert {r["den"] for r in store.all("SELECT DISTINCT den FROM model_cost_buckets WHERE environment='test' "
                                        "AND dim='account'")} == {0, 3, 7}
    top = 2**63 - 1
    assert exact(CostAdmission(ledger, current_policy=lambda: generous(daily_budget=top)).snapshot(), "daily") == expected
    budget = int(expected) + 5

    def limits(daily):
        return policy(daily_budget=daily, weekly_budget=top, monthly_budget=top, unknown_budget=top)

    _, one = prepare(funded, "boundary")
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(CostAdmission(ledger, current_policy=lambda: limits(budget - 1)), one, amount=5)
    send(CostAdmission(ledger, current_policy=lambda: limits(budget)), one, amount=5)
    assert exact(CostAdmission(ledger, current_policy=lambda: limits(top)).snapshot(), "daily") == budget
    clean(store)


def test_repeated_int64_bounds_and_costs_carry_across_limbs(funded):
    store, ledger, _, ticks = funded
    top = 2**63 - 1
    source = template(funded, "int64-template", amount=0)
    clone(store, source, [ticks[0] - 10, ticks[0] - 9, ticks[0] - 8], prefix="fixture-int64", settled=False,
          reserved=top)
    for i in range(2):
        _raw_usage(store, f"fixture-int64-attempt-{i}", str(top), "1")
    snapshot = CostAdmission(ledger, current_policy=generous).snapshot()
    assert exact(snapshot, "daily") == 3 * top           # two exact costs of 2^63-1 plus one bound of 2^63-1
    assert snapshot["unknown_reserved_units"] == top and snapshot["unknown_count"] == 2
    row = day_bucket(store, ticks[0] - ticks[0] % 86_400)
    assert (row["bound_hi"], row["rbound_hi"], row["cost_hi"]) == (3 * top // 10**15, 2 * top // 10**15, 2 * top // 10**15)
    clean(store)


@pytest.mark.parametrize("n,d,admission_code,risk_counts", [
    ("-5", "1", "invalid_cost_fact", Fraction(-5)), ("x", "1", "invalid_cost_fact", None),
    ("5", "0", "invalid_cost_fact", None), ("007", "1", None, Fraction(7))])
def test_exception_facts_keep_each_consumers_validity_rule(funded, n, d, admission_code, risk_counts):
    store, ledger, _, ticks = funded
    key = _metered(funded, "exception")
    _raw_usage(store, key, n, d)
    admission = CostAdmission(ledger, current_policy=generous)
    if admission_code:
        with pytest.raises(BillingError, match=admission_code):
            admission.snapshot()
    else:
        assert exact(admission.snapshot(), "daily") == Fraction(int(n), int(d))
    with store.tx() as t:
        args = dict(environment="test", provider="fixture-provider", account_ref="fixture-provider-account",
                    unit=COST_UNIT, owner=funded[2][0], route="fixture-model-alias", now=ticks[0],
                    risk=velocity_policy(route_cost=1, payer_cost=1).risk)
        if risk_counts is None:
            with pytest.raises(BillingError, match="invalid_cost_fact"):
                rollup().velocity(t, **args)
            with pytest.raises(BillingError, match="invalid_cost_fact"):
                rollup().reference_velocity(t, **args)
        else:
            assert rollup().velocity(t, **args) == rollup().reference_velocity(t, **args) == (risk_counts, risk_counts)
    clean(store)


@pytest.mark.parametrize("usage_first", [False, True])
def test_facts_written_with_plain_sql_give_an_exact_rollup(funded, usage_first):
    store, ledger, _, ticks = funded
    now = ticks[0]
    source = template(funded, "plain-template", amount=9, settle=True)
    clone(store, source, [now - 86_400 * k - k for k in range(1, 40)], prefix="fixture-plain", settled=True,
          usage_first=usage_first, cost=("10", "3"))
    clone(store, source, [now - 30, now - 20], prefix="fixture-open", settled=False)
    report = clean(store, since=0)
    assert report.facts == 1 + 39 + 2
    with store.tx() as t:
        got = rollup().counters(t, environment="test", provider="fixture-provider",
                                account_ref="fixture-provider-account", unit=COST_UNIT, now=now, records=True)
        want = rollup().reference_counters(t, environment="test", provider="fixture-provider",
                                           account_ref="fixture-provider-account", unit=COST_UNIT, now=now)
    for field in ("windows", "totals", "unknown_reserved_units", "unknown_count", "inflight", "records",
                  "absorbed_unknown_units", "absorbed_unknown_count"):
        assert got[field] == want[field], field


@pytest.mark.cost_rollup_unchecked
def test_after_refusal_latches_a_read_path_inconsistency(funded):
    store, ledger, users, _ = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _, settled = prepare(funded, "latch-settled")
    send(admission, settled, amount=7)
    ledger.settle_trusted_usage(settled.attempt_key, evidence(settled))
    with store.tx() as t:                               # FIXTURE: one admission too many in today's row
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("UPDATE model_cost_buckets SET n_adm=n_adm+1 WHERE environment='test' AND dim='account'")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    _, ticket = prepare(funded, "latch-refused")
    with pytest.raises(BillingError, match="cost_rollup_inconsistent") as caught:
        send(admission, ticket, amount=1)
    error = caught.value
    assert isinstance(error, rollup().RollupInconsistent)
    assert error.scope == SCOPE and error.check == "parity"
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []        # rolled back with the dispatch
    admission.after_refusal(error)
    faults = store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL")
    assert len(faults) == 1 and faults[0]["code"] == "read_parity"
    assert store.all("SELECT * FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")
    admission.after_refusal(error)                      # idempotent
    assert len(store.all("SELECT * FROM model_cost_rollup_faults")) == 1
    # A latch computed before a newer rebuild is not written.
    rollup().CostRollup.rebuild(store, environment="test", provider="fixture-provider",
                                account_ref="fixture-provider-account", actor="fixture-operator",
                                reason="fixture parity repaired")
    admission.after_refusal(error)
    assert store.all("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL") == []
    send(admission, ticket, amount=1)


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("check,tamper", [
    ("structure", "UPDATE model_cost_buckets SET rbound_lo=bound_lo+1 WHERE environment='test' AND dim='account'"),
    ("stale", "INSERT INTO model_cost_inflight SELECT attempt_key,environment,provider,account_ref,expires_at+86400 "
              "FROM model_cost_admissions WHERE attempt_key IN (SELECT attempt_key FROM billing_usage)"),
    ("parity", "UPDATE model_cost_buckets SET n_adm=n_adm+1 WHERE environment='test' AND dim='account'"),
    ("absorbed", "UPDATE model_cost_buckets SET bound_lo=bound_lo-7 WHERE environment='test' AND dim='account'"),
])
def test_each_read_path_check_refuses_with_its_name(funded, check, tamper):
    store, ledger, _, _ = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _, settled = prepare(funded, "checked-settled")
    send(admission, settled, amount=7)
    ledger.settle_trusted_usage(settled.attempt_key, evidence(settled))
    _, open_ = prepare(funded, "checked-open")
    send(admission, open_, amount=7)
    with store.tx() as t:                               # FIXTURE: a derived row edited past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute(tamper)
        t.execute("DELETE FROM model_cost_rollup_deltas")
    with pytest.raises(rollup().RollupInconsistent) as caught:
        admission.snapshot()
    assert caught.value.check == check and caught.value.code == "cost_rollup_inconsistent"
    assert not rollup().CostRollup.check(store).ok


# ---------------------------------------------------------------- schedule, prune, CLI
@pytest.mark.cost_rollup_unchecked
def test_the_risk_monitor_verifies_the_rollup_without_a_policy_and_latches_a_mismatch(funded):
    from synwevo_core.billing.risk_monitor import RiskMonitor
    store, ledger, users, ticks = funded
    _, ticket = prepare(funded, "monitored")
    send(CostAdmission(ledger, current_policy=generous), ticket, amount=7)
    monitor = RiskMonitor(store, environment="test", clock=lambda: ticks[0])
    first = monitor.run_once()
    assert not first.configured and not first.failures
    assert first.ran == ["cost_rollup_verify", "cost_rollup_prune"]
    assert monitor.run_once().ran == []                 # the daily run also counts as this hour's
    ticks[0] += 3600
    assert monitor.run_once().ran == ["cost_rollup_verify_recent"]
    assert monitor.run_once().ran == []
    with store.tx() as t:                               # FIXTURE: a bucket edited past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("UPDATE model_cost_buckets SET bound_lo=bound_lo+1 WHERE environment='test' AND dim='route'")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    ticks[0] += 3600
    assert monitor.run_once().ran == ["cost_rollup_verify_recent"]
    fault = store.one("SELECT * FROM model_cost_rollup_faults WHERE resolved_at IS NULL")
    assert fault["code"] == "verify_mismatch" and (fault["environment"], fault["provider"], fault["account_ref"]) == SCOPE
    runs = store.all("SELECT mode,status FROM model_cost_rollup_audits WHERE environment='test' AND provider='' "
                     "ORDER BY rowid")
    assert [r["mode"] for r in runs] == ["daily", "recent", "recent"] and runs[-1]["status"] == "mismatch"


def test_prune_removes_only_rows_that_can_never_be_read_again(funded):
    store, ledger, _, ticks = funded
    admission = CostAdmission(ledger, current_policy=generous)
    day0 = ticks[0]
    _, old = prepare(funded, "old")
    send(admission, old, amount=7)
    ledger.mark_usage_pending(old)
    ticks[0] = day0 + 3 * 86_400                        # three days later: the old velocity rows are past H
    _, new = prepare(funded, "new")
    send(admission, new, amount=5)
    before = store.one("SELECT COUNT(*) AS n FROM model_cost_buckets WHERE environment='test' AND dim<>'account'")["n"]
    counts = rollup().CostRollup.prune(store, batch=2)
    # Six velocity rows (3 spans, payer and route) of the old admission, and its expired in-flight
    # row; its hold row already left when the ledger released the call at its deadline.
    assert counts == {"buckets": 6, "inflight": 1, "hold": 0}
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_buckets WHERE environment='test' AND dim<>'account'")["n"] == before - 6
    assert len(store.all("SELECT * FROM model_cost_buckets WHERE environment='test' AND dim='account'")) == 2
    assert store.all("SELECT attempt_key FROM model_cost_inflight WHERE environment='test'") == [{"attempt_key": new.attempt_key}]
    # Late usage for the old admission never recreates a pruned velocity row.
    result = ledger.settle_trusted_usage(old.attempt_key, evidence(old))
    assert result.late
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_buckets WHERE environment='test' AND dim<>'account'")["n"] == before - 6
    assert rollup().CostRollup.prune(store) == {"buckets": 0, "inflight": 0, "hold": 0}
    clean(store)


def test_cost_rollup_cli_verifies_and_rebuilds_an_isolated_data_directory(funded, tmp_path):
    import os
    import subprocess
    import sys

    store, ledger, _, _ = funded
    _, ticket = prepare(funded, "cli")
    send(CostAdmission(ledger, current_policy=generous), ticket, amount=7)
    env = {**os.environ, "SYNWEVO_DATA_DIR": str(store.root), "SYNWEVO_PLATFORM_REEXEC": ""}
    script = str(Path(__file__).resolve().parents[2] / "scripts" / "platform.py")

    def run(*args):
        return subprocess.run([sys.executable, script, "cost-rollup", *args], env=env, capture_output=True,
                              text=True, timeout=120)

    verified = run("verify", "--all")
    assert verified.returncode == 0, verified.stderr
    refused = run("rebuild", "--environment", "test", "--provider", "fixture-provider")
    assert refused.returncode == 2 and "--account-ref" in refused.stderr
    rebuilt = run("rebuild", "--environment", "test", "--provider", "fixture-provider", "--account-ref",
                  "fixture-provider-account", "--reason", "fixture operator rebuild rehearsal")
    assert rebuilt.returncode == 0, rebuilt.stderr
    assert json.loads(rebuilt.stdout[rebuilt.stdout.index("{"):])["facts"] == 1
    assert store.one("SELECT actor FROM model_cost_rollup_rebuilds WHERE environment='test'")["actor"].startswith("operator:")
    clean(store)


# ---------------------------------------------------------------- review findings (2026-10-26)
@pytest.mark.parametrize("n,d", [("\x0099999999", "1"), ("7\x00junk", "1"), ("5", "1\x00"), ("123", "3\x007")],
                         ids=["nul-first", "nul-inside", "nul-denominator", "nul-in-denominator"])
def test_a_cost_text_with_an_embedded_nul_is_an_exception_fact_for_sql_and_python(funded, n, d):
    """SQLite's GLOB, length() and CAST stop at the first NUL, Python's parsers do not: the triggers
    must class such a cost X like the verifier, so the admission refuses invalid_cost_fact as before."""
    store, ledger, users, ticks = funded
    _metered(funded, "nul-real", usage=Usage(2, 1, 3))
    key = _metered(funded, "nul-fact", amount=1000)
    _raw_usage(store, key, n, d)                        # FIXTURE: an import could write it
    assert rollup().cost_class(n, d) == "X"
    assert store.one("SELECT attempt_key FROM model_cost_exceptions WHERE attempt_key=?", (key,)) == {"attempt_key": key}
    row = day_bucket(store, ticks[0] - ticks[0] % 86_400)
    assert (row["n_adm"], row["n_res"], row["n_exc"]) == (2, 2, 1)
    with pytest.raises(BillingError, match="invalid_cost_fact"):
        CostAdmission(ledger, current_policy=generous).snapshot()
    with store.tx() as t:
        args = dict(environment="test", provider="fixture-provider", account_ref="fixture-provider-account",
                    unit=COST_UNIT, owner=users[0], route="fixture-model-alias", now=ticks[0],
                    risk=velocity_policy(route_cost=10**9, payer_cost=10**9).risk)
        for fn in (rollup().velocity, rollup().reference_velocity):
            with pytest.raises(BillingError, match="invalid_cost_fact"):
                fn(t, **args)
    clean(store)
    original = derived_rows(store)
    rollup().CostRollup.rebuild(store, environment="test", provider="fixture-provider",
                                account_ref="fixture-provider-account", actor="fixture-operator",
                                reason="fixture rebuild over a NUL cost")
    assert derived_rows(store) == original


def test_producer_triggers_older_than_their_version_are_recreated_and_the_facts_replayed(funded):
    import re

    from synwevo_core.suite_schema import model_cost_rollup as schema
    store, ledger, _, ticks = funded
    key = _metered(funded, "old-trigger", amount=1000)
    with store.tx() as t:                               # FIXTURE: the producers as the first release wrote them
        for name in schema.PRODUCERS:
            sql = t.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()[0]
            t.execute(f"DROP TRIGGER {name}")
            t.execute(re.sub(r"instr\([^()]*,char\(0\)\)=0 AND ", "", sql))
        t.execute("DELETE FROM meta WHERE key=?", (schema.TRIGGERS_KEY,))
    _raw_usage(store, key, "\x0099999999", "1")         # classed R (cost 0) by the older SQL
    assert store.one("SELECT COUNT(*) AS n FROM model_cost_exceptions WHERE environment='test'")["n"] == 0
    reopened = Store(store.root)
    for name in schema.PRODUCERS:
        sql = reopened.one("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,))["sql"]
        assert "char(0)" in sql, name
    assert reopened.one("SELECT value FROM meta WHERE key=?", (schema.TRIGGERS_KEY,)) == {"value": schema.TRIGGERS_VERSION}
    assert reopened.one("SELECT attempt_key FROM model_cost_exceptions WHERE environment='test'") == {"attempt_key": key}
    assert reopened.one("SELECT mode FROM model_cost_rollup_audits ORDER BY rowid DESC LIMIT 1")["mode"] == "backfill"
    clean(reopened)
    again = Store(store.root)                           # replays once, not on every start
    assert again.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE mode='backfill'")["n"] == \
        reopened.one("SELECT COUNT(*) AS n FROM model_cost_rollup_audits WHERE mode='backfill'")["n"]


def _snapshot_tool():
    """The backup/restore script (apps/docs/scripts/snapshot.py), loaded by path: it is a script."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("fixture_snapshot_tool", ROOT / "apps" / "docs" / "scripts" / "snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _backup(snapshot, data, out):
    from types import SimpleNamespace
    assert snapshot.create(SimpleNamespace(data=str(data), out=str(out))) == 0
    (archive,) = out.glob("*.tar.gz")
    return archive


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("kind", ["orphan", "malformed", "far_expiry", "future"])
def test_a_broken_admission_keeps_its_scope_refused_until_a_backup_from_before_it_is_restored(
        funded, tmp_path, monkeypatch, capsys, kind):
    """Facts cannot be edited (immutable_cost_admission) and only a write outside the product (an import
    with foreign keys off, hand SQL) can break one. The product fails closed: the scope refuses, and no
    rebuild, start, ended call or later verify clears it (each refusal changes nothing). The way out is
    the backup taken before that write (docs/platform/model-risk.md §13 step 3): its restore rehearsal
    passes, one taken after the write fails it, and the restored store admits again."""
    from types import SimpleNamespace

    from tests.integration.test_billing_ledger import fixture_authorize, fixture_verify
    store, ledger, users, ticks = funded
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(store.root))
    CR, snapshot = rollup().CostRollup, _snapshot_tool()
    admission = CostAdmission(ledger, current_policy=generous)
    send(admission, prepare(funded, "before-damage")[1], amount=1)
    before = _backup(snapshot, store.root, tmp_path / "backup-before")
    _, dispatched = prepare(funded, "unadmitted")
    ledger.dispatch(dispatched)
    key = "fixture-orphan-attempt" if kind == "orphan" else dispatched.attempt_key
    now = ticks[0]
    admitted_at, expires_at = {"orphan": (1, 100), "malformed": ("soon", 100), "far_expiry": (now, now + 2 * 86_400),
                               "future": (now + 10 * 86_400, now + 12 * 86_400)}[kind]
    _broken_admission(store, key, admitted_at, expires_at)
    code = "fact_orphan_admission" if kind == "orphan" else "fact_malformed_admission"
    assert store.one("SELECT code FROM model_cost_rollup_faults WHERE id=?", (rollup().fact_fault_id(key, SCOPE),)) == \
        {"code": code}
    with pytest.raises(sqlite3.IntegrityError, match="immutable_cost_admission"):
        store.execute("DELETE FROM model_cost_admissions WHERE attempt_key=?", (key,))

    tries = []

    def refused():
        tries.append(1)
        rows, faults = derived_rows(store), store.all("SELECT * FROM model_cost_rollup_faults ORDER BY id")
        with pytest.raises(rollup().RebuildRefused) as caught:
            CR.rebuild(store, reason="fixture rebuild over a broken fact", clock=lambda: ticks[0], **_REBUILD)
        assert caught.value.attempt_keys == [key]
        assert derived_rows(store) == rows and store.all("SELECT * FROM model_cost_rollup_faults ORDER BY id") == faults
        assert _open_fault_scopes(store) == {SCOPE}
        with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
            send(admission, prepare(funded, f"refused-{len(tries)}")[1], amount=1)

    refused()
    ticks[0] = now + 501                                # the unadmitted call's hold ends (deadline now + 500)
    ledger.expire_due()
    refused()
    assert _open_fault_scopes(Store(store.root)) == {SCOPE}            # a start keeps the latch
    for kwargs in ({"mode": "daily"}, {"since": now - 2 * 86_400, "mode": "recent"}, {"all": True}):
        report = CR.verify(store, now=ticks[0], clock=lambda: ticks[0], **kwargs)  # recorded (the orphan is in 1970)
        assert {d["check"] for d in report.scopes[SCOPE].differences} <= {"fact_fault"}, report.summary()
    assert [(d["check"], d["attempt_key"]) for d in report.scopes[SCOPE].differences] == [("fact_fault", key)]
    refused()
    # The recovery: the backup from before the broken write. One taken after it fails the rehearsal.
    after = _backup(snapshot, store.root, tmp_path / "backup-after")
    capsys.readouterr()
    assert snapshot.verify(SimpleNamespace(archive=str(after), journal_from=str(store.root))) == 1
    assert "✗ 成本汇总与成本事实一致" in capsys.readouterr().out
    assert snapshot.verify(SimpleNamespace(archive=str(before), journal_from=str(store.root))) == 0
    into = tmp_path / "restored"
    assert snapshot.restore(SimpleNamespace(archive=str(before), into=str(into), journal_from=str(store.root))) == 0
    restored = Store(into)
    assert restored.all("SELECT * FROM model_cost_rollup_faults") == []
    assert restored.one("SELECT COUNT(*) AS n FROM model_cost_admissions")["n"] == 1
    restored_ledger = Ledger(restored, environment="test", namespace=ledger.namespace, authorize=fixture_authorize,
                             verify_payment=fixture_verify, clock=lambda: ticks[0], max_hold_seconds=1000)
    world = (restored, restored_ledger, users, ticks)
    send(CostAdmission(restored_ledger, current_policy=generous), prepare(world, "after-restore")[1], amount=1)
    clean(restored, now=ticks[0])


def test_an_admission_expiring_when_it_is_admitted_verifies_and_rebuilds(funded):
    """I1's lower bound (expires_at > admitted_at) is not a rollup fault: such an admission is never in
    flight for the rollup or for the oracle, which agree; the verifier reports no difference for it."""
    store, ledger, _, ticks = funded
    source = template(funded, "i1-template", amount=5)
    clone(store, source, [ticks[0] - 30], prefix="fixture-i1", settled=False, expires_after=0)
    assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    report = rollup().CostRollup.verify(store, now=ticks[0], all=True)
    assert report.ok, report.summary()
    rollup().CostRollup.rebuild(store, environment="test", provider="fixture-provider",
                                account_ref="fixture-provider-account", actor="fixture-operator",
                                reason="fixture rebuild over an I1 lower-bound admission")
    _, ticket = prepare(funded, "after-i1")
    send(CostAdmission(ledger, current_policy=generous), ticket, amount=1)


def _broken_admission(store, key, admitted_at, expires_at, *, as_text=False):
    """FIXTURE: a broken admission in SCOPE written with foreign keys off, as a broken import would."""
    _raw_admission(store, key, SCOPE, admitted_at, expires_at, as_text=as_text)


_REBUILD = dict(environment="test", provider="fixture-provider", account_ref="fixture-provider-account",
                actor="fixture-operator")
_KW = dict(environment="test", provider="fixture-provider", account_ref="fixture-provider-account")
_NAMED = [(b"fixture-blob-key", False, "blob:x'" + b"fixture-blob-key".hex().upper() + "'"),
          ("k" * 201, False, "text:x'" + (b"k" * 128).hex().upper() + "'…(201 bytes)"),
          ("fixture\x00nul", False, "text:x'" + b"fixture\x00nul".hex().upper() + "'"),
          ("-fixture-dash", False, "-fixture-dash"),
          ("fixture key\n", False, "text:x'" + b"fixture key\n".hex().upper() + "'"),
          ("blob:x'6B'", False, "text:x'" + b"blob:x'6B'".hex().upper() + "'"),
          (b"fixture-caf\xe9", True, "text:x'" + b"fixture-caf\xe9".hex().upper() + "'")]


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("key,as_text,label", _NAMED, ids=["blob", "long", "nul", "dash", "newline", "spelled-like-a-name",
                                                           "not-utf8"])
def test_every_broken_admission_is_named_by_the_rebuild_and_the_command_line(funded, capsys, key, as_text, label):
    """A broken import can write any key (a BLOB, a long or non-printable or non-UTF-8 text, one spelled
    like another key's name): the rebuild's refusal and the command line name each, one per line and
    distinctly, so the operator can find the write that broke the scope; nothing is changed."""
    store, _, _, ticks = funded
    mod = rollup()
    _broken_admission(store, key, ticks[0] - 10, ticks[0] + 100, as_text=as_text)    # no attempt: an orphan
    faults = store.all("SELECT * FROM model_cost_rollup_faults")
    assert len(faults) == 1 and faults[0]["resolved_at"] is None
    with pytest.raises(mod.RebuildRefused) as caught:
        mod.CostRollup.rebuild(store, reason="fixture rebuild over a broken fact", **_REBUILD)
    assert caught.value.attempt_keys == [label]
    argv = ["rebuild", "--data", str(store.root), "--environment", "test", "--provider", "fixture-provider",
            "--account-ref", "fixture-provider-account", "--reason", "fixture: the import row is broken",
            "--actor", "fixture-operator"]
    assert mod.main(argv) == 1
    err = capsys.readouterr().err
    assert [line[2:] for line in err.splitlines() if line.startswith("  ")] == [label], err
    assert "备份" in err and "--accept" not in err
    assert store.all("SELECT * FROM model_cost_rollup_faults") == faults
    labels = [named for _, _, named in _NAMED]
    assert len(set(labels)) == len(labels)


# ---------------------------------------------------------------- undecodable values, fault ids (2026-10-26)
OTHER = ("test", "fixture-provider", "fixture-other-account")
#: A TEXT value that is not UTF-8 (Latin-1 "café"): SQLite stores it unchecked, Python's sqlite3
#: raises OperationalError when it decodes one.
UNDECODABLE = b"fixture-caf\xe9"


def _raw_admission(store, key, scope, admitted_at, expires_at, *, as_text=False):
    """FIXTURE: an admission a broken import writes (foreign keys off), with any key; ``as_text``
    stores the key's bytes as TEXT (CAST), which is how a non-UTF-8 text gets in."""
    value = "CAST(? AS TEXT)" if as_text else "?"
    with store.conn() as c:
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute(f"INSERT INTO model_cost_admissions VALUES ({value},?,?,?,?,?,?,?,?,?)",
                      (key, *scope, COST_UNIT.code, 3, "fixture", "{}", admitted_at, expires_at))
        finally:
            c.execute("PRAGMA foreign_keys=ON")


def _undecodable_cost(store, key):
    """FIXTURE: a usage fact whose cost numerator is a non-UTF-8 TEXT (class X for SQL)."""
    with store.tx() as t:
        t.execute("UPDATE billing_attempts SET status='settled' WHERE id=?", (key,))
        t.execute("INSERT INTO billing_usage(attempt_key,facts,outcome,cost_n,cost_d,cost_unit,charge,absorbed_n,"
                  "absorbed_d,late,created_at) VALUES (?,'{}','completed',CAST(? AS TEXT),'1',?,0,'0','1',0,0)",
                  (key, b"7\xe9", COST_UNIT.code))


def _hex_label(kind, raw):
    return f"{kind}:x'{raw.hex().upper()}'"


def _open_fault_scopes(store):
    return {(r["environment"], r["provider"], r["account_ref"]) for r in store.all(
        "SELECT environment,provider,account_ref FROM model_cost_rollup_faults WHERE resolved_at IS NULL")}


@pytest.mark.cost_rollup_unchecked
def test_an_undecodable_orphan_key_latches_its_scope_and_every_tool_names_it(funded, capsys):
    """The fault id, the rebuild's refusal, the command line, the verifier and a start's replay read an
    attempt key by its SQLite type and bytes, so each names a non-UTF-8 key instead of raising
    OperationalError (which left the scope unnamed and a replay unable to finish)."""
    from synwevo_core.suite_schema import model_cost_rollup as schema
    store, ledger, _, ticks = funded
    mod, CR = rollup(), rollup().CostRollup
    now = ticks[0]
    send(CostAdmission(ledger, current_policy=generous), prepare(funded, "before-undecodable")[1], amount=1)
    _raw_admission(store, UNDECODABLE, SCOPE, now - 10, now + 100, as_text=True)
    label = _hex_label("text", UNDECODABLE)
    assert store.all("SELECT id,code FROM model_cost_rollup_faults") == [
        {"id": mod.fact_fault_id(("text", UNDECODABLE), SCOPE), "code": "fact_orphan_admission"}]
    rows = derived_rows(store)
    with pytest.raises(mod.RebuildRefused) as caught:
        CR.rebuild(store, reason="fixture rebuild over an undecodable key", **_REBUILD)
    assert caught.value.attempt_keys == [label]
    argv = ["rebuild", "--data", str(store.root), "--environment", "test", "--provider", "fixture-provider",
            "--account-ref", "fixture-provider-account", "--reason", "fixture: the import row is broken",
            "--actor", "fixture-operator"]
    assert mod.main(argv) == 1
    err = capsys.readouterr().err
    assert label in err and "OperationalError" not in err, err
    assert derived_rows(store) == rows and _open_fault_scopes(store) == {SCOPE}
    for kwargs in ({"all": True}, {"mode": "daily"}, {"since": now - 2 * 86_400, "mode": "recent"}):
        report = CR.check(store, now=now, **kwargs)
        assert [(d["check"], d["attempt_key"]) for d in report.scopes[SCOPE].differences] == [("fact_fault", label)]
    with store.tx() as t:                               # FIXTURE: an older trigger version, so a start replays
        t.execute("UPDATE meta SET value='fixture-older' WHERE key=?", (schema.TRIGGERS_KEY,))
    reopened = Store(store.root)
    audit = reopened.one("SELECT differences FROM model_cost_rollup_audits WHERE mode='backfill' ORDER BY rowid DESC LIMIT 1")
    assert json.loads(audit["differences"])["faults"] == [label]
    assert _open_fault_scopes(reopened) == {SCOPE}
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
        send(CostAdmission(ledger, current_policy=generous), prepare(funded, "after-undecodable")[1], amount=1)


@pytest.mark.cost_rollup_unchecked
@pytest.mark.parametrize("where", ["key", "cost"])
def test_one_scopes_undecodable_value_is_that_scopes_fault_and_the_other_scopes_are_still_checked(funded, where):
    """A non-UTF-8 value in one provider account's facts (a well-formed fact's attempt key, or a class-X
    cost text) no longer stops the verifier for the whole environment: that account is latched (audited,
    alerted), every other account is still checked (here a tampered bucket is found), the risk monitor
    reports no failed step and a rebuild of the broken account refuses instead of raising."""
    from synwevo_core.billing.risk_monitor import RiskMonitor
    store, ledger, _, ticks = funded
    now = ticks[0]
    CR = rollup().CostRollup
    send(CostAdmission(ledger, current_policy=lambda: generous(account_ref=OTHER[2])), prepare(funded, "other")[1],
         amount=3)
    if where == "key":      # attempt, call and wallet rows present: a well-formed fact for SQL
        clone(store, template(funded, "undecodable-template", amount=5, settle=True), [now - 30],
              prefix=b"\xff", settled=True)
        label = _hex_label("text", b"\xff-attempt-0")
        assert store.one("SELECT COUNT(*) AS n FROM model_cost_admissions WHERE hex(attempt_key)=?",
                         (b"\xff-attempt-0".hex().upper(),))["n"] == 1
        assert store.all("SELECT * FROM model_cost_rollup_faults") == []
    else:
        _undecodable_cost(store, _metered(funded, "undecodable-cost"))
    with store.tx() as t:                               # FIXTURE: the other account's bucket edited past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        assert t.execute("UPDATE model_cost_buckets SET n_adm=n_adm+5 WHERE environment=? AND provider=? "
                         "AND account_ref=? AND dim='account'", OTHER).rowcount
        t.execute("DELETE FROM model_cost_rollup_deltas")
    for kwargs in ({"all": True}, {"mode": "daily"}, {"since": now - 2 * 86_400, "mode": "recent"}):
        report = CR.check(store, environment="test", now=now, **kwargs)
        assert set(report.mismatched) == {SCOPE, OTHER}, report.summary()
        assert "bucket" in {d["check"] for d in report.scopes[OTHER].differences}
        mine = report.scopes[SCOPE].differences
        if where == "key":
            assert [(d["check"], d["attempt_key"]) for d in mine] == [("undecodable_key", label)]
        else:
            assert [d["check"] for d in mine] == ["verify_error"] and "decode" in mine[0]["detail"]
    result = RiskMonitor(store, environment="test", clock=lambda: ticks[0]).run_once()
    assert result.failures == [] and "cost_rollup_verify" in result.ran
    assert _open_fault_scopes(store) == {SCOPE, OTHER}
    alerted = {json.loads(r["meta"])["account_ref"] for r in store.all(
        "SELECT meta FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")}
    assert alerted == {SCOPE[2], OTHER[2]}
    with pytest.raises(rollup().RebuildRefused) as caught:
        CR.rebuild(store, reason="fixture rebuild over an undecodable value", clock=lambda: ticks[0], **_REBUILD)
    if where == "key":
        assert caught.value.attempt_keys == [label]
    else:
        assert [d["check"] for d in caught.value.differences] == ["verify_error"]
    for account in (SCOPE[2], OTHER[2]):
        with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
            send(CostAdmission(ledger, current_policy=lambda a=account: generous(account_ref=a)),
                 prepare(funded, "refused-" + account)[1], amount=1)


@pytest.mark.cost_rollup_unchecked
def test_the_read_path_refuses_an_undecodable_value_with_a_named_check_that_latches(funded):
    """Admission and the velocity brake read class-X cost texts back: one that is not UTF-8 refuses with
    cost_rollup_inconsistent (check ``undecodable``), latched after the refusal like the other read-path
    checks, instead of an unhandled OperationalError on the dispatch path."""
    store, ledger, users, ticks = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _undecodable_cost(store, _metered(funded, "undecodable-cost"))
    _, ticket = prepare(funded, "after-undecodable-cost")
    with pytest.raises(BillingError, match="cost_rollup_inconsistent") as caught:
        send(admission, ticket, amount=1)
    error = caught.value
    assert isinstance(error, rollup().RollupInconsistent) and (error.scope, error.check) == (SCOPE, "undecodable")
    admission.after_refusal(error)
    assert [r["code"] for r in store.all("SELECT code FROM model_cost_rollup_faults WHERE resolved_at IS NULL")] == \
        ["read_undecodable"]
    with store.tx() as t:
        with pytest.raises(rollup().RollupInconsistent) as velocity:
            rollup().velocity(t, **_KW, unit=COST_UNIT, owner=users[0], route="fixture-model-alias", now=ticks[0],
                              risk=velocity_policy(route_cost=10**9, payer_cost=10**9).risk)
    assert velocity.value.check == "undecodable"


@pytest.mark.cost_rollup_unchecked
def test_fault_ids_tell_key_types_and_scopes_apart(funded):
    """SQLite's || turns a BLOB into the TEXT of the same bytes, and the old id had no scope: a BLOB and
    a TEXT key with the same bytes, in one account or in two, shared one fault row, so the second broken
    admission latched nothing. Now each has its own row in its own account, and the verifier finds it."""
    store, ledger, _, ticks = funded
    mod = rollup()
    now = ticks[0]
    broken = [("fixture-k", SCOPE), (b"fixture-k", SCOPE), ("fixture-k2", SCOPE), (b"fixture-k2", OTHER)]
    for i, (key, scope) in enumerate(broken):
        _raw_admission(store, key, scope, now - 40 + i, now + 100)
    rows = store.all("SELECT id,environment,provider,account_ref,code FROM model_cost_rollup_faults "
                     "WHERE resolved_at IS NULL ORDER BY id")
    assert sorted((r["id"], (r["environment"], r["provider"], r["account_ref"])) for r in rows) == \
        sorted((mod.fact_fault_id(key, scope), scope) for key, scope in broken)
    assert len({r["id"] for r in rows}) == 4 and {r["code"] for r in rows} == {"fact_orphan_admission"}
    assert _open_fault_scopes(store) == {SCOPE, OTHER}
    for account in (SCOPE[2], OTHER[2]):
        with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
            send(CostAdmission(ledger, current_policy=lambda a=account: generous(account_ref=a)),
                 prepare(funded, "after-" + account)[1], amount=1)
    report = mod.CostRollup.check(store, now=now, all=True)
    for scope, labels in ((SCOPE, ["blob:x'" + b"fixture-k".hex().upper() + "'", "fixture-k", "fixture-k2"]),
                          (OTHER, ["blob:x'" + b"fixture-k2".hex().upper() + "'"])):
        found = report.scopes[scope].differences
        assert sorted(d["attempt_key"] for d in found) == sorted(labels), found
        assert {d["check"] for d in found} == {"fact_fault"}                # no fact_fault_missing


@pytest.mark.cost_rollup_unchecked
def test_a_scope_named_by_undecodable_text_is_reported_and_every_other_scope_is_still_checked(funded):
    """A broken import can write an account name that is not UTF-8. No read can ever name that scope
    (every read binds UTF-8 text), so its facts reach no limit; the scan of scopes reads names as type
    and bytes, reports it as the store-wide issue undecodable_scope and goes on checking the others."""
    store, ledger, _, ticks = funded
    now = ticks[0]
    CR = rollup().CostRollup
    send(CostAdmission(ledger, current_policy=generous), prepare(funded, "named-scope")[1], amount=2)
    with store.conn() as c:                             # FIXTURE: foreign keys off, the account name as raw bytes
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            c.execute("INSERT INTO model_cost_admissions VALUES ('fixture-undecodable-scope','test','fixture-provider',"
                      "CAST(? AS TEXT),?,3,'fixture','{}',?,?)", (UNDECODABLE, COST_UNIT.code, now - 10, now + 100))
        finally:
            c.execute("PRAGMA foreign_keys=ON")
    with store.tx() as t:                               # FIXTURE: the named scope's bucket edited past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        assert t.execute("UPDATE model_cost_buckets SET n_adm=n_adm+1 WHERE environment=? AND provider=? "
                         "AND account_ref=? AND dim='account'", SCOPE).rowcount
        t.execute("DELETE FROM model_cost_rollup_deltas")
    for kwargs in ({"all": True}, {"mode": "daily"}, {"since": now - 2 * 86_400, "mode": "recent"}):
        report = CR.check(store, environment="test", now=now, **kwargs)
        assert report.issues == ["undecodable_scope"] and list(report.scopes) == [SCOPE], report.summary()
        assert "bucket" in {d["check"] for d in report.scopes[SCOPE].differences}
    assert CR.record(store, report) == {SCOPE: "mismatch"}
    run = store.one("SELECT status,differences FROM model_cost_rollup_audits WHERE mode='recent' AND provider='' "
                    "ORDER BY rowid DESC LIMIT 1")
    assert run["status"] == "mismatch" and json.loads(run["differences"])["issues"] == ["undecodable_scope"]
    assert rollup().CostRollup.prune(store)["inflight"] == 0          # prune skips the scope it cannot name


def test_scopes_whose_parts_contain_colons_latch_independently(funded):
    store = funded[0]
    mod, CR = rollup(), rollup().CostRollup
    s1, s2 = ("test", "acme:eu", "main"), ("test", "acme", "eu:main")
    with store.tx() as t:
        seq = mod.rebuild_seq(t, s1)                    # the global backfill covers every scope
    report = mod.VerifyReport("verify", 0, None)
    for scope in (s1, s2):
        result = mod.ScopeReport(scope, 0, seq)
        result.differ("bucket", key=["fixture"])
        report.scopes[scope] = result
    assert CR.record(store, report) == {s1: "mismatch", s2: "mismatch"}

    def open_scopes():
        return {(r["environment"], r["provider"], r["account_ref"]) for r in store.all(
            "SELECT environment,provider,account_ref FROM model_cost_rollup_faults WHERE resolved_at IS NULL")}

    assert open_scopes() == {s1, s2}
    s3, s4 = ("test", "beta:eu", "main"), ("test", "beta", "eu:main")
    assert CR.latch(store, mod.RollupInconsistent(s3, "parity", seq)) == "latched"
    assert CR.latch(store, mod.RollupInconsistent(s4, "parity", seq)) == "latched"
    assert CR.latch(store, mod.RollupInconsistent(s4, "parity", seq)) == "already_latched"
    assert open_scopes() == {s1, s2, s3, s4}
    assert len(report.summary()["mismatched"]) == 2


VALUES = ("n_adm", "n_res", "n_cross", "n_exc", "bound_hi", "bound_lo", "rbound_hi", "rbound_lo", "cost_hi", "cost_lo",
          "cost_carry", "cost_frac")


@pytest.mark.cost_rollup_unchecked
def test_a_tampered_velocity_bucket_in_a_scope_without_admissions_is_found(funded):
    store, ledger, _, ticks = funded
    now = ticks[0]
    CR = rollup().CostRollup
    with store.tx() as t:                               # FIXTURE: the gate opened by hand (the residual risk)
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("INSERT INTO model_cost_buckets(environment,provider,account_ref,dim,member,span,start,unit,den,"
                  + ",".join(VALUES) + ") VALUES (?,?,?,'route','fixture-model-alias',3600,?,?,0,1,0,0,0,0,999,0,0,0,0,0,0)",
                  (*SCOPE, now - now % 3600, COST_UNIT.code))
        t.execute("DELETE FROM model_cost_rollup_deltas")
    for kwargs in ({"all": True}, {"mode": "daily"}, {"since": now - 2 * 86_400, "mode": "recent"}):
        report = CR.check(store, now=now, **kwargs)
        assert not report.ok and report.scopes[SCOPE].differences[0]["check"] == "bucket", report.summary()
    assert CR.record(store, report) == {SCOPE: "mismatch"}
    _, ticket = prepare(funded, "first-admission")
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):  # the latch, not risk_route_brake
        send(CostAdmission(ledger, current_policy=lambda: velocity_policy(route_cost=1000)), ticket, amount=5)


@pytest.mark.cost_rollup_unchecked
def test_a_stray_delta_is_reported_even_when_no_scope_exists(funded):
    store = funded[0]
    CR = rollup().CostRollup
    with store.tx() as t:                               # FIXTURE: a hand-written delta, the gate open at rest
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-stray')")
    report = CR.check(store, all=True)
    assert report.scopes == {} and report.issues == ["deltas_not_empty"] and not report.ok
    assert report.summary()["issues"] == ["deltas_not_empty"]
    CR.record(store, report)
    run = store.one("SELECT status,differences FROM model_cost_rollup_audits WHERE mode='verify' AND provider='' "
                    "ORDER BY rowid DESC LIMIT 1")
    assert run["status"] == "mismatch" and json.loads(run["differences"])["issues"] == ["deltas_not_empty"]
    assert store.all("SELECT * FROM audit_events WHERE action='billing.cost.rollup_inconsistent'")


@pytest.mark.cost_rollup_unchecked
def test_a_velocity_bucket_with_a_negative_value_refuses_as_velocity_structure(funded):
    store, ledger, users, ticks = funded
    _, ticket = prepare(funded, "velocity-structure")
    send(CostAdmission(ledger, current_policy=generous), ticket, amount=7)
    with store.tx() as t:                               # FIXTURE: a velocity row edited past the gate
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("UPDATE model_cost_buckets SET bound_lo=-5 WHERE environment='test' AND dim='route' AND span=3600")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    with store.tx() as t:
        with pytest.raises(rollup().RollupInconsistent) as caught:
            rollup().velocity(t, environment="test", provider="fixture-provider", account_ref="fixture-provider-account",
                              unit=COST_UNIT, owner=users[0], route="fixture-model-alias", now=ticks[0],
                              risk=velocity_policy(route_cost=10**9, payer_cost=10**9).risk)
    assert caught.value.check == "velocity_structure"


def test_verify_memory_does_not_grow_with_the_facts_it_reads(funded):
    """CostRollup.check streams the facts: its Python peak at 20,001 facts stays near the one at 2,001
    (the monitor runs it inside the portal process, docs/platform/model-risk.md §8)."""
    import tracemalloc

    store, ledger, _, ticks = funded
    now = ticks[0]
    source = template(funded, "memory-template", amount=20, settle=True)

    def history(prefix, count):
        old = [now - 3 * 86_400 - (i * 7919) % (27 * 86_400) for i in range(count // 2)]
        recent = [now - 3600 - i for i in range(count - count // 2)]       # dense velocity rows
        clone(store, source, old + recent, prefix=prefix, settled=True)

    def peak():
        tracemalloc.start()
        try:
            report = rollup().CostRollup.check(store, all=True, now=now)
            return tracemalloc.get_traced_memory()[1], report
        finally:
            tracemalloc.stop()

    history("fixture-mem-a", 2_000)
    small, report = peak()
    assert report.ok and report.facts == 2_001
    history("fixture-mem-b", 18_000)
    large, report = peak()
    assert report.ok and report.facts == 20_001
    assert large < 1.5 * small + 2**20, (small, large)


def test_a_store_opened_while_another_process_holds_the_writer_lock_waits_for_it(funded, monkeypatch):
    """A backfill holds the writer lock longer than busy_timeout (48 s per million admissions): a role
    process opening the Store meanwhile waits for it instead of failing with 'database is locked'."""
    import threading
    import time

    import synwevo_core.store as store_module
    store = funded[0]
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_MS", 200)
    holder = sqlite3.connect(store.db_path, isolation_level=None, check_same_thread=False, timeout=5)
    holder.execute("BEGIN IMMEDIATE")                   # FIXTURE: another process's long migration
    release = threading.Timer(1.2, lambda: holder.execute("COMMIT"))
    release.start()
    try:
        started = time.monotonic()
        reopened = Store(store.root)
        waited = time.monotonic() - started
    finally:
        release.join()
        holder.close()
    assert waited >= 1.0
    assert reopened.one("SELECT value FROM meta WHERE key='model_cost_rollup_v1'") == {"value": "1"}


# ---------------------------------------------------------------- the runbook, as production runs it
ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = re.compile(r"docker compose -f /opt/synwevo/config/compose\.yaml (exec -T portal|run --rm --no-deps -T portal) "
                        r"python -m synwevo_core\.billing\.cost_rollup ([^<`\n]*(?:<[a-z_]+>[^<`\n]*)*)</dev/null")


@pytest.mark.cost_rollup_unchecked
def test_the_runbook_gives_the_production_container_form_and_each_command_runs(funded, tmp_path):
    """docs/platform/model-risk.md §13 as an operator copies it: the role container's module form
    (production has no workspace venv on the host), run --rm --no-deps while services are stopped,
    against SYNWEVO_DATA_DIR as the container sets it; and the alert points at that runbook."""
    import os
    import subprocess
    import sys

    from synwevo_core.notifications import CATALOGUE
    runbook = (ROOT / "docs" / "platform" / "model-risk.md").read_text(encoding="utf-8")
    section = runbook[runbook.index("## 13."):]
    commands = PRODUCTION.findall(section)
    assert {args.split()[0] for _, args in commands} == {"verify", "rebuild"}, commands
    assert any(kind.startswith("run") and args.startswith("rebuild") for kind, args in commands)
    blocks = re.findall(r"```bash\n(.*?)```", section, re.S)
    assert blocks and not any("scripts/platform.py" in block for block in blocks)   # no host-venv command
    assert "SYNWEVO_DATA_DIR: /data" in (ROOT / "infra" / "production" / "compose.yaml").read_text(encoding="utf-8")
    body = CATALOGUE["billing.cost.rollup_inconsistent"]["body"]
    assert all("model-risk.md §13" in text and "platform.py" not in text for text in body.values()), body

    store, ledger, _, ticks = funded
    admission = CostAdmission(ledger, current_policy=generous)
    _, good = prepare(funded, "runbook")
    send(admission, good, amount=1)
    values = {"environment": "test", "provider": "fixture-provider", "account_ref": SCOPE[2],
              "reason": "fixture runbook rehearsal: the source was checked"}
    env = {**os.environ, "SYNWEVO_DATA_DIR": str(store.root)}

    def run_all():
        outcomes = []
        for _, args in commands:
            argv = []
            for word in re.findall(r'"[^"]*"|\S+', args.strip()):
                word = word.strip('"')
                word = re.sub(r"<([a-z_]+)>", lambda m: values[m.group(1)], word)
                argv.append({"live": "test", "deepseek": "fixture-provider"}.get(word, word))
            done = subprocess.run([sys.executable, "-m", "synwevo_core.billing.cost_rollup", *argv], env=env,
                                  cwd=tmp_path, capture_output=True, text=True, timeout=120)
            outcomes.append((argv[0], done.returncode, done.stderr))
        return outcomes

    # Step 2: a derived row edited past the gate. verify shows it, the rebuild repairs it, verify confirms.
    with store.tx() as t:                               # FIXTURE: the gate opened by hand
        t.execute("INSERT INTO model_cost_rollup_deltas(kind) VALUES ('fixture-tamper')")
        t.execute("UPDATE model_cost_buckets SET n_adm=n_adm+1 WHERE environment='test' AND dim='account'")
        t.execute("DELETE FROM model_cost_rollup_deltas")
    outcomes = run_all()
    assert [(action, code) for action, code, _ in outcomes] == [("verify", 1), ("rebuild", 0), ("verify", 0)], outcomes
    send(admission, prepare(funded, "after-rebuild")[1], amount=1)
    # Step 3: a broken fact. The rebuild names it and changes nothing; the account stays refused.
    _, dispatched = prepare(funded, "runbook-broken")
    ledger.dispatch(dispatched)
    with store.conn() as c:                             # FIXTURE: a malformed admission, as a broken import
        c.execute("INSERT INTO model_cost_admissions VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (dispatched.attempt_key, *SCOPE, COST_UNIT.code, 3, "fixture", "{}", ticks[0], ticks[0] + 3 * 86_400))
    outcomes = run_all()
    assert [(action, code) for action, code, _ in outcomes] == [("verify", 1), ("rebuild", 1), ("verify", 1)], outcomes
    assert dispatched.attempt_key in outcomes[1][2] and "§13" in outcomes[1][2] and "备份" in outcomes[1][2]
    with pytest.raises(BillingError, match="cost_rollup_inconsistent"):
        send(admission, prepare(funded, "after-runbook")[1], amount=1)
    assert "accept" not in section.lower()


# ---------------------------------------------------------------- the benchmark measures what it says
def _bench():
    import importlib.util
    spec = importlib.util.spec_from_file_location("fixture_bench_cost_admission", ROOT / "scripts" / "bench_cost_admission.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_benchmark_calendar_is_the_one_its_docstring_and_evidence_state():
    from datetime import datetime, timezone
    bench = _bench()
    month, now = (datetime.fromtimestamp(v, timezone.utc) for v in (bench.MONTH, bench.NOW))
    assert (month.isoformat(), month.strftime("%A")) == ("2034-05-01T00:00:00+00:00", "Monday")
    assert (now.isoformat(), now.strftime("%A")) == ("2034-05-20T12:00:00+00:00", "Saturday")
    readme = (ROOT / "docs" / "agent-work" / "evidence" / "2026-10-25-cost-rollup" / "README.md").read_text(encoding="utf-8")
    assert "2034-05-01" in bench.__doc__ and "2034-05-20T12:00" in bench.__doc__ and "2034-05-20T12:00" in readme


def test_the_benchmark_measures_the_live_write_path_and_the_monitors_daily_verify(tmp_path, monkeypatch):
    """A tiny run of the benchmark: the trigger cost is measured as live writers pay it (one admission
    and one usage per transaction, interleaved with a copy without the producer triggers), and the
    daily verify as the risk monitor runs it, with its peak memory in a child process."""
    monkeypatch.delenv("SYNWEVO_DATA_DIR", raising=False)
    monkeypatch.delenv("DOCWEAVE_DATA_DIR", raising=False)
    bench = _bench()
    result = bench.run(3_000, samples=3, dispatches=5, seed=7, work_parent=tmp_path, pairs=200, live_pairs=40)
    live = result["triggers_live"]
    assert live["pairs"] == 40 and live["per_transaction"] is True
    assert {"extra_stmt_us_p50", "extra_locked_us_p50", "extra_locked_us_p95"} <= set(live)
    daily = result["verify_daily"]
    assert daily["ok"] and daily["facts"] >= 3_000 and daily["peak_rss_mb"] > 0
    assert result["triggers"]["bulk"] is True
    assert result["history"]["first"] >= bench.MONTH and result["history"]["last"] < bench.NOW
