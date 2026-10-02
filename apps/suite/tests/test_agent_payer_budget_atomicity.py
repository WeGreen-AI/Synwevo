"""Persistence-stage payer-policy faults through the real governed factory/capabilities.

Every database is the suite's isolated fixture. Faults happen after real event/audit INSERTs,
not in Call.emit; an independent read-only Store observes only committed state.
"""
import pytest

from synwevo_core.capabilities import Capabilities
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store

from .test_agent_governed_factory import governed, configure
from .test_agent_payer_budgets import body, update


OP = "agent.billing.budget.set"
EVENT = "agent.payer_budget.changed"


def _changed_budget(revision):
    return {**body(revision), "enabled": False, "daily_budget": "17"}


def _committed_policy_facts(store):
    return {
        "versions": store.all("SELECT * FROM billing_payer_budget_versions ORDER BY revision"),
        "events": store.all("SELECT * FROM app_events WHERE type=? ORDER BY seq", (EVENT,)),
        "audit": store.all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (OP,)),
        "successes": store.all("SELECT * FROM op_runs WHERE op=? AND status='succeeded' ORDER BY id", (OP,)),
    }


@pytest.fixture
def atomic_budget(governed, request):
    g = governed
    configure(g, payer_budget=False)
    revision = request.param
    if revision:
        assert update(g, key="fixture-existing-policy")["output"]["revision"] == revision
    observer = Store(g.store.root, migrate=False)
    before = _committed_policy_facts(observer)
    announced = []
    g.suite.caps.listeners.append(lambda events: announced.extend(events))
    return g, revision, observer, before, announced


def _assert_uncommitted_insert(t, call, fixture):
    g, revision, observer, before, _ = fixture
    assert t.in_transaction
    assert t.execute("SELECT MAX(revision) FROM billing_payer_budget_versions WHERE user_id=?",
                     (g.user["id"],)).fetchone()[0] == revision + 1
    assert t.execute("SELECT status FROM op_runs WHERE id=?", (call.run_id,)).fetchone()[0] == "succeeded"
    assert _committed_policy_facts(observer) == before
    assert observer.one("SELECT 1 FROM op_runs WHERE id=?", (call.run_id,)) is None


def _assert_failure_rolled_back(fixture, run_ids, key):
    g, revision, observer, before, announced = fixture
    assert len(run_ids) == 1  # Proves every assertion in the injected fault was reached.
    assert _committed_policy_facts(observer) == before
    assert announced == []
    failed = observer.one("SELECT * FROM op_runs WHERE id=?", (run_ids[0],))
    assert failed["status"] == "failed" and failed["error_code"] == "operation_failed"
    assert failed["output"] is None and failed["object_id"] is None
    assert failed["idempotency_key"] == key
    with pytest.raises(ServiceError) as error:
        update(g, _changed_budget(revision), key=key)
    assert error.value.code == "idempotent_failure"
    assert _committed_policy_facts(observer) == before

    # A fresh attempt uses the same expected revision: failure consumed no revision or event.
    result = update(g, _changed_budget(revision), key=key + "-fresh")
    assert result["status"] == "succeeded"
    assert result["output"]["revision"] == revision + 1
    assert not result["output"]["enabled"] and result["output"]["daily_budget"] == "17"
    after = _committed_policy_facts(observer)
    for field in ("versions", "events", "audit", "successes"):
        assert len(after[field]) == len(before[field]) + 1
    assert len(announced) == 1 and announced[0]["type"] == EVENT
    assert g.seen == []
    assert observer.all("SELECT * FROM billing_journal") == []
    assert observer.all("SELECT * FROM billing_attempts") == []


@pytest.mark.parametrize("atomic_budget", [0, 1], indirect=True, ids=["first-policy", "existing-policy"])
def test_event_persistence_failure_rolls_back_policy_event_and_success_receipt(atomic_budget, monkeypatch):
    g, revision, _, _, _ = atomic_budget
    original = Capabilities._write_events
    reached = []

    def write_then_fail(self, t, call):
        original(self, t, call)
        if call.op.id == OP:
            _assert_uncommitted_insert(t, call, atomic_budget)
            assert t.execute("SELECT COUNT(*) FROM app_events WHERE type=? AND run_id=?",
                             (EVENT, call.run_id)).fetchone()[0] == 1
            reached.append(call.run_id)
            raise RuntimeError("fixture fault after event INSERT")

    key = "fixture-event-persistence-failure"
    with monkeypatch.context() as fault:
        fault.setattr(Capabilities, "_write_events", write_then_fail)
        with pytest.raises(ServiceError) as error:
            update(g, _changed_budget(revision), key=key)
        assert error.value.code == "operation_failed"
    _assert_failure_rolled_back(atomic_budget, reached, key)


@pytest.mark.parametrize("atomic_budget", [0, 1], indirect=True, ids=["first-policy", "existing-policy"])
def test_audit_persistence_failure_rolls_back_policy_event_audit_and_success_receipt(atomic_budget, monkeypatch):
    g, revision, _, _, _ = atomic_budget
    original = Capabilities._audit
    reached = []

    def audit_then_fail(self, t, call, status):
        original(self, t, call, status)
        if call.op.id == OP:
            _assert_uncommitted_insert(t, call, atomic_budget)
            assert t.execute("SELECT COUNT(*) FROM app_events WHERE type=? AND run_id=?",
                             (EVENT, call.run_id)).fetchone()[0] == 1
            assert t.execute("SELECT COUNT(*) FROM audit_events WHERE action=? AND json_extract(meta,'$.run_id')=?",
                             (OP, call.run_id)).fetchone()[0] == 1
            reached.append(call.run_id)
            raise RuntimeError("fixture fault after audit INSERT")

    key = "fixture-audit-persistence-failure"
    with monkeypatch.context() as fault:
        fault.setattr(Capabilities, "_audit", audit_then_fail)
        with pytest.raises(ServiceError) as error:
            update(g, _changed_budget(revision), key=key)
        assert error.value.code == "operation_failed"
    _assert_failure_rolled_back(atomic_budget, reached, key)
