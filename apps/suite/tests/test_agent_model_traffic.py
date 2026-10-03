"""Model-call traffic through the deployed composition; fixture prices, funds and SSE only."""
from dataclasses import asdict
import uuid

import pytest

from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.errors import ServiceError
from .test_agent_governed_factory import governed, configure, fund, start, UNIT  # noqa: F401
from .test_agent_metering import complete

TRAFFIC = {"platform_rpm": 100, "platform_tpm": 1_000_000, "payer_rpm": 1, "payer_tpm": 1_000_000,
           "payer_inflight": 2, "wait_seconds": 0, "max_waiting": 10, "max_waiting_per_payer": 5}


def limit(g, **traffic):
    settings = CostSettings(g.store, environment="test")
    with g.store.tx():
        settings.append({"enabled": True, "unit": asdict(UNIT), "daily_budget": "1000000",
                         "weekly_budget": "1000000", "monthly_budget": "1000000", "unknown_budget": "1000000",
                         "max_inflight": 4, "max_unknown": 4, "traffic": {**TRAFFIC, **traffic}},
                        expected_revision=settings.read()["revision"], actor_id=g.user["id"],
                        reason="Fixture traffic limits", operation_id=uuid.uuid4().hex)


def finished(g, tid):
    return g.store.one("SELECT status,error_code FROM agent_turns WHERE id=?", (tid,))


def clocked(g, monkeypatch):
    """A fixture clock for the money/traffic services; each waiting poll lets one second pass."""
    ticks = [g.provider.gateway.ledger._now()]
    monkeypatch.setattr(g.provider.gateway.ledger, "clock", lambda: ticks[0])
    monkeypatch.setattr(g.provider.gateway.traffic, "sleep", lambda _: ticks.__setitem__(0, ticks[0] + 1))
    return ticks


def test_a_second_call_in_the_same_minute_is_not_sent_or_reserved(governed):
    g = governed; configure(g); fund(g); limit(g)
    first = start(g)
    g.agent._run(first)
    assert finished(g, first)["status"] == "succeeded" and len(g.seen) == 1
    second = start(g)
    g.agent._run(second)
    assert dict(finished(g, second)) == {"status": "failed", "error_code": "billing_traffic_wait_timeout"}
    assert len(g.seen) == 1  # Nothing reached the provider.
    assert not g.store.one("SELECT 1 FROM billing_calls WHERE status='reserved'")
    states = [r["state"] for r in g.store.all("SELECT state FROM model_traffic_claims ORDER BY rowid")]
    assert states == ["finished", "abandoned"]


def test_a_waiting_call_proceeds_when_the_minute_passes_and_is_sent_once(governed, monkeypatch):
    g = governed; configure(g); fund(g); limit(g, wait_seconds=120)
    ticks = clocked(g, monkeypatch)
    g.agent._run(start(g))
    began = ticks[0]
    g.replies = [complete().replace(b"fixture-upstream-1", b"fixture-upstream-2")]
    second = start(g)
    g.agent._run(second)
    assert finished(g, second)["status"] == "succeeded" and len(g.seen) == 2
    assert ticks[0] - began >= 60  # It waited for the window instead of failing.
    row = g.store.one("SELECT actual_tokens,est_tokens FROM model_traffic_claims ORDER BY rowid DESC LIMIT 1")
    assert row["actual_tokens"] is not None and row["actual_tokens"] < row["est_tokens"]


def test_stopping_while_waiting_sends_nothing(governed, monkeypatch):
    g = governed; configure(g); fund(g); limit(g, wait_seconds=120)
    clocked(g, monkeypatch)
    g.agent._run(start(g))
    second = start(g)
    g.agent.cancel(g.user["id"], second)
    g.agent._run(second)
    assert finished(g, second)["status"] == "cancelled" and len(g.seen) == 1
    assert not g.store.one("SELECT 1 FROM model_traffic_claims WHERE state IN ('waiting','granted')")


def test_a_full_queue_refuses_the_message_before_accepting_it(governed):
    g = governed; configure(g); fund(g); limit(g, max_waiting_per_payer=1)
    now = g.provider.gateway.ledger._now()
    g.store.execute("INSERT INTO model_traffic_claims(id,environment,account_ref,payer_user_id,est_tokens,state,"
                    "enqueued_at,wait_until) VALUES ('traffic_fixture01','test','platform-deepseek',?,10,'waiting',?,?)",
                    (g.user["id"], now, now + 300))
    before = g.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"]
    with pytest.raises(ServiceError) as err:
        start(g)
    assert err.value.status == 429 and err.value.code == "model_queue_full"
    assert err.value.detail["retry_after"] >= 1
    assert g.store.one("SELECT COUNT(*) AS n FROM agent_messages")["n"] == before
