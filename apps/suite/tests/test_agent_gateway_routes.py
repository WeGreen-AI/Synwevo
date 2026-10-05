"""Governed DeepSeek route rules on the real Suite factory (FIXTURE transport, synthetic prices).

Covers the gateway package: per-route reasoning efforts. No real DeepSeek endpoint or key.
"""
import json
import time

import pytest

from synwevo_core.billing.provider_settings import ProviderSpec, RouteSpec
from synwevo_suite.agent.gateway import Route

from .test_agent_governed_factory import configure, fund, governed, publish, start  # noqa: F401 - fixture import
from .test_agent_metering import complete


def route_fields(**changes):
    fields = {"requested_model": "fixture-model", "confirmed_route": "fixture-route", "confirmation_ref": "fixture:route",
              "cost_basis_ref": "fixture:cost", "account_confirmation_ref": "fixture:account",
              "sale_price_id": "fixture-sale", "cost_price_id": "fixture-cost", "input_token_ceiling": 300,
              "output_token_ceiling": 64, "valid_until": int(time.time()) + 3600}
    fields.update(changes)
    return fields


def test_route_efforts_are_validated_canonical_and_default_to_every_level():
    assert RouteSpec.model_validate(route_fields()).reasoning_efforts == ["none", "low", "high", "max"]
    assert RouteSpec.model_validate(route_fields(reasoning_efforts=["max", "none"])).reasoning_efforts == ["none", "max"]
    for invalid in ([], ["medium"], ["high", "high"], ["high", "max", "low", "none", "high"], "high"):
        with pytest.raises(ValueError):
            RouteSpec.model_validate(route_fields(reasoning_efforts=invalid))
    # A version stored before the field existed keeps its meaning (every level allowed).
    stored = json.dumps({"enabled": False, "route": route_fields()})
    assert ProviderSpec.model_validate_json(stored).route.reasoning_efforts == ["none", "low", "high", "max"]
    # A restored run snapshot (JSON list) equals the live route (tuple), so quotes stay valid.
    live = Route(version="v1", **{k: v for k, v in route_fields(reasoning_efforts=["high", "low"]).items()
                                  if k != "account_confirmation_ref"})
    assert live.reasoning_efforts == ("low", "high")
    assert Route(**json.loads(json.dumps(live.__dict__))) == live


@pytest.mark.parametrize("wanted,allowed,chosen", [
    ("max", ("none", "high"), "high"), ("high", ("none", "low", "high", "max"), "high"),
    ("low", ("high", "max"), "high"), ("max", ("none",), "none"), ("low", ("none",), "none"),
    ("high", ("low", "max"), "low"),    # equally near: the lower, cheaper level
    ("low", ("none", "high"), "none"), ("none", ("low", "max"), "low"),
])
def test_the_nearest_level_the_route_offers_is_chosen(wanted, allowed, chosen):
    from synwevo_suite.agent.deepseek import route_effort
    assert route_effort(wanted, allowed) == chosen


def test_the_persons_effort_maps_onto_the_levels_the_route_offers(governed):
    # A person's setting (low/medium/high/xhigh/max) is a preference; the governed route decides
    # which DeepSeek levels exist. No setting selects "none", so a thinking-off route must still
    # serve everybody instead of refusing every turn with advice that cannot work.
    g = governed
    configure(g)
    fund(g)
    spec = json.loads(json.dumps(g.spec))
    spec["route"]["reasoning_efforts"] = ["none", "high"]
    publish(g, {"action": "keep"}, spec)
    g.store.execute("INSERT INTO agent_settings(user_id,effort,updated_at) VALUES (?,?,?)", (g.user["id"], "max", time.time()))
    tid = start(g)
    g.agent._run(tid)
    assert g.store.one("SELECT status FROM agent_turns WHERE id=?", (tid,))["status"] == "succeeded"
    body = json.loads(g.seen[-1].content)
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"

    spec["route"]["reasoning_efforts"] = ["none"]   # Thinking off for everyone.
    publish(g, {"action": "keep"}, spec)
    g.store.execute("DELETE FROM agent_settings WHERE user_id=?", (g.user["id"],))  # The default, "medium".
    g.replies = [complete().replace(b"fixture-upstream-1", b"fixture-upstream-2")]
    tid = start(g)
    g.agent._run(tid)
    turn = g.store.one("SELECT * FROM agent_turns WHERE id=?", (tid,))
    assert turn["status"] == "succeeded", turn["error_code"]
    body = json.loads(g.seen[-1].content)
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body


def test_an_unknown_effort_value_still_fails_the_turn_unsent_and_unreserved(governed):
    g = governed
    configure(g)
    fund(g)
    g.store.execute("INSERT INTO agent_settings(user_id,effort,updated_at) VALUES (?,?,?)",
                    (g.user["id"], "fixture-unknown", time.time()))
    tid = start(g)
    g.agent._run(tid)
    turn = g.store.one("SELECT * FROM agent_turns WHERE id=?", (tid,))
    assert turn["status"] == "failed" and turn["error_code"] == "model_effort_unavailable"
    assert turn["usage_state"] == "known"
    assert g.seen == [] and g.store.all("SELECT * FROM billing_calls") == []
