"""Real policy revisions and dispatch consumer; isolated money facts, no external I/O."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from synwevo_core.billing import BillingError, Ledger, Unit
from synwevo_core.billing.admission import CostAdmission
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store
from tests.integration.test_billing_ledger import money, cost_price, sale_price, fund, evidence
from tests.integration.test_model_cost_admission import prepare, send


def spec(**changes):
    return {"enabled": True, "unit": {"code": "TESTCOST", "scale": 8},
            "daily_budget": "20", "weekly_budget": "100", "monthly_budget": "100",
            "unknown_budget": "100", "max_inflight": 10, "max_unknown": 10, **changes}


@pytest.fixture
def configured(money):
    store, ledger, _, ticks = money
    ledger.publish_price(cost_price(price_id="fixture-deepseek-policy", provider="deepseek", unit=Unit("TESTCOST", 8)))
    ledger.publish_price(sale_price(price_id="fixture-deepseek-sale", provider="deepseek"))
    return money, CostSettings(store, environment="test", clock=lambda: ticks[0])


def publish(money, settings, values, revision):
    with settings.store.tx():
        return settings.append(values, expected_revision=revision, actor_id=money[2][0],
                               reason="Isolated cost policy test", operation_id=f"fixture-cost-operation-{revision}")


def test_unconfigured_reads_do_not_create_policy_or_funds(configured):
    money, settings = configured
    state = settings.read()
    assert state["revision"] == 0 and state["spec"] is None
    assert state["units"] == [{"code": "TESTCOST", "scale": 8}]
    assert state["account_ref"] == "platform-deepseek"
    with pytest.raises(BillingError, match="cost_not_configured"):
        settings.policy()
    assert money[0].all("SELECT * FROM model_cost_policy_versions") == []
    assert money[0].all("SELECT * FROM billing_journal") == []


def test_real_dispatch_reads_new_policy_without_restart(configured):
    money, settings = configured
    fund(money, 1000)
    publish(money, settings, spec(), 0)
    admission = CostAdmission(money[1], current_policy=settings.policy)
    _, first = prepare(money, "policy-first", cost_id="fixture-deepseek-policy", sale_id="fixture-deepseek-sale")
    send(admission, first, amount=20)
    publish(money, settings, spec(enabled=False), 1)
    _, second = prepare(money, "policy-second", cost_id="fixture-deepseek-policy", sale_id="fixture-deepseek-sale")
    with pytest.raises(BillingError, match="cost_admission_disabled"):
        send(admission, second, amount=1)
    result = money[1].settle_trusted_usage(first.attempt_key, replace(evidence(first), provider="deepseek"))
    assert result.cost == 19  # Disabled new admission never blocks incurred settlement.
    publish(money, settings, spec(daily_budget="19"), 2)
    with pytest.raises(BillingError, match="cost_daily_budget_exceeded"):
        send(admission, second, amount=1)
    publish(money, settings, spec(), 3)
    send(admission, second, amount=1)
    assert [row["policy_revision"] for row in money[0].all("SELECT policy_revision FROM model_cost_admissions ORDER BY rowid")] == [
        "model-cost:test:1", "model-cost:test:4"]


def test_writer_transaction_and_rollback_preserve_version(configured):
    money, settings = configured
    with pytest.raises(RuntimeError, match="Store.tx"):
        settings.append(spec(), expected_revision=0, actor_id=money[2][0], reason="Isolated policy change", operation_id="fixture-op")
    with pytest.raises(RuntimeError, match="fixture rollback"):
        with settings.store.tx():
            settings.append(spec(), expected_revision=0, actor_id=money[2][0], reason="Isolated policy change", operation_id="fixture-op")
            raise RuntimeError("fixture rollback")
    assert settings.read()["revision"] == 0
    assert publish(money, settings, spec(), 0)["revision"] == 1


def test_two_instances_cannot_overwrite_each_others_revision(configured):
    money, settings = configured
    publish(money, settings, spec(), 0)
    others = [CostSettings(Store(settings.store.root), environment="test", clock=settings.clock) for _ in range(2)]
    def change(i):
        try:
            publish(money, others[i], spec(daily_budget=str(21+i)), 1)
            return "published"
        except ServiceError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, range(2)))
    assert sorted(results) == ["cost_revision_conflict", "published"]
    assert settings.read()["revision"] == 2


@pytest.mark.parametrize("value", [True, 1, "-1", "01", "1.0", "1e2", "9223372036854775808"])
def test_budget_validation_is_exact_and_sanitized(configured, value):
    _, settings = configured
    with pytest.raises(ServiceError) as exc:
        settings.preview(spec(daily_budget=value), expected_revision=0)
    assert exc.value.code == "cost_invalid"
    assert settings.read()["revision"] == 0


def test_published_cost_unit_and_live_environment_are_required(configured):
    money, settings = configured
    with pytest.raises(ServiceError, match="成本价格"):
        settings.preview(spec(unit={"code": "UNKNOWN", "scale": 8}), expected_revision=0)
    live = CostSettings(money[0], environment="live", clock=settings.clock)
    assert live.read()["units"] == []
    with pytest.raises(ServiceError) as exc:
        live.preview(spec(), expected_revision=0)
    assert exc.value.code == "cost_unit_unavailable"
    with pytest.raises(ServiceError):
        CostSettings(money[0], environment=None)


def test_changing_unit_cannot_hide_pending_platform_exposure(configured):
    money, settings = configured
    fund(money, 1000)
    publish(money, settings, spec(), 0)
    _, ticket = prepare(money, "unit-pending", cost_id="fixture-deepseek-policy", sale_id="fixture-deepseek-sale")
    send(CostAdmission(money[1], current_policy=settings.policy), ticket, amount=20)
    money[1].publish_price(cost_price(price_id="fixture-other-cost", provider="deepseek", unit=Unit("OTHERCOST", 8)))
    with pytest.raises(ServiceError) as exc:
        publish(money, settings, spec(unit={"code": "OTHERCOST", "scale": 8}), 1)
    assert exc.value.code == "cost_exposure_conflict" and settings.read()["revision"] == 1
    assert publish(money, settings, spec(enabled=False), 1)["revision"] == 2


def test_history_is_immutable_paginated_and_copy_creates_new_version(configured):
    money, settings = configured
    first = publish(money, settings, spec(), 0)
    publish(money, settings, spec(enabled=False), 1)
    publish(money, settings, first["spec"], 2)
    page = settings.history(limit=2)
    assert [row["revision"] for row in page["items"]] == [3, 2]
    assert page["next_before_revision"] == 2
    assert [row["revision"] for row in settings.history(before_revision=2)["items"]] == [1]
    for sql in ("DELETE FROM model_cost_policy_versions", "UPDATE model_cost_policy_versions SET reason='changed'",
                "INSERT OR REPLACE INTO model_cost_policy_versions SELECT * FROM model_cost_policy_versions"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable_cost_policy"):
            money[0].execute(sql)
    assert settings.read()["revision"] == 3 and settings.read()["spec"] == first["spec"]
