"""The production starting point in docs/operations.md §11.3 obeys the rules of §11.2.

The documented cost policy is parsed from the runbook and published through the real validation;
every rule is then recomputed independently from DeepSeek's published facts (account concurrency
per model, CNY peak prices per million tokens) and the documented route ceilings. A policy that
validates but breaks a rule would, for example, refuse every call of a route or let in-flight
calls drain the upstream balance after the breaker closed.
"""
from fractions import Fraction
import json
import math
from pathlib import Path
import re

from synwevo_core.billing.cost_settings import validate_spec

RUNBOOK = Path(__file__).resolve().parents[2] / "docs" / "operations.md"
# DeepSeek, read 2026-10-05 (docs/operations.md §11.2): account concurrency per model.
CONCURRENCY = {"deepseek-flash": 2500, "deepseek-v4-pro": 500}
# CNY per million tokens at the peak rate: (cache hit, cache miss, output).
PEAK_CNY = {"deepseek-flash": (Fraction("0.04"), Fraction(2), Fraction(8)),
            "deepseek-v4-pro": (Fraction("0.30"), Fraction(9), Fraction(27))}
# The documented route assumptions of §11.3.
INPUT_CEILING, OUTPUT_CEILING = 131_072, 32_768


def documented_policy() -> dict:
    text = RUNBOOK.read_text(encoding="utf-8")
    block = re.search(r"<!-- model-risk-guidance:start -->\s*```json\n(.*?)```\s*<!-- model-risk-guidance:end -->",
                      text, re.S)
    assert block, "docs/operations.md §11.3 lost its marked starting-point policy"
    return json.loads(block.group(1))


def call_bound(model: str, scale: int) -> int:
    """The gateway's conservative per-call cost bound, in cost units, recomputed by hand."""
    hit, miss, output = PEAK_CNY[model]
    exact = (INPUT_CEILING * max(hit, miss) + OUTPUT_CEILING * output) / 10**6 * 10**scale
    return math.ceil(exact) + 1


def test_the_documented_starting_point_is_a_publishable_cost_policy():
    spec = validate_spec(documented_policy())
    policy = spec.policy("documented-starting-point")
    assert policy.enabled is True and policy.risk is not None and policy.traffic is not None
    # The first production enablement is staged: only allowlisted payers.
    assert policy.risk.allowlist_only is True


def test_the_documented_worked_bounds_match_the_gateway_formula():
    assert call_bound("deepseek-flash", 6) == 524_289
    assert call_bound("deepseek-v4-pro", 6) == 2_064_385
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "flash 524,289" in text and "v4-pro 2,064,385" in text


def test_the_documented_starting_point_obeys_every_rule():
    p = validate_spec(documented_policy()).policy("documented-starting-point")
    traffic, risk = p.traffic, p.risk
    worst = max(call_bound(model, p.unit.scale) for model in PEAK_CNY)
    # 1. All in-flight calls may land on the smallest-quota model; keep 20% headroom.
    assert p.max_inflight <= math.floor(Fraction(8, 10) * min(CONCURRENCY.values()))
    # 2. In-flight calls count as unknown usage too.
    assert p.max_unknown >= p.max_inflight
    # 3. Every enabled route's peak bound fits under the per-call cap.
    assert p.max_call_cost >= worst
    # 4. The unknown amount does not bind before the unknown count.
    assert p.unknown_budget >= p.max_unknown * p.max_call_cost
    # 5. One call's token estimate fits in a payer's minute.
    assert traffic.payer_tpm >= INPUT_CEILING + OUTPUT_CEILING
    # 6. Enforced by validation already; restated so the runbook cannot drift from it.
    assert traffic.payer_inflight <= p.max_inflight and traffic.payer_rpm <= traffic.platform_rpm
    assert traffic.payer_tpm <= traffic.platform_tpm and traffic.max_waiting_per_payer <= traffic.max_waiting
    # 7. Ordinary concurrent calls do not trip the brake.
    assert risk.payer_window_cost >= traffic.payer_inflight * p.max_call_cost
    assert risk.route_window_cost >= p.max_inflight * p.max_call_cost
    # 8. Calls already sent when the breaker closes cannot empty the upstream balance.
    assert risk.balance_close >= p.unknown_budget
    # 9. At least one day of spend between the warning and the stop.
    assert risk.balance_warn >= risk.balance_close + p.daily_budget
    # 10. Tolerance starts at one yuan; the exposure alert near half the unknown budget.
    assert risk.reconcile_tolerance == 10**p.unit.scale
    assert risk.pending_alert * 2 == p.unknown_budget
