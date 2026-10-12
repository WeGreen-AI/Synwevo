"""The list of the workplace applications' audit actions equals what the source writes (G26, D7 §9)."""
from __future__ import annotations

import pathlib
import re

import synwevo_suite
from synwevo_core import flows
from synwevo_suite.common.audit_actions import ACTIONS

LITERAL = re.compile(r"""["'](suite\.[a-z_]+(?:\.[a-z_]+)+)["']""")
#: `_record(r, "<kind>")` without a call writes `suite.approvals.<kind>`; only the engine's own
#: transitions (the sweeper) run without one.
APPROVALS_ENGINE_KINDS = ("stage.reminder", "stage.escalated")


def written_in_source() -> set[str]:
    found: set[str] = set()
    for root in (pathlib.Path(synwevo_suite.__file__).parent, pathlib.Path(flows.__file__).parent):
        for path in root.rglob("*.py"):
            found |= set(LITERAL.findall(path.read_text(encoding="utf-8")))
    found |= {"suite.approvals." + kind for kind in APPROVALS_ENGINE_KINDS}
    return found


def test_every_audit_action_the_source_writes_is_listed_once():
    found = written_in_source()
    listed = set(ACTIONS)
    assert len(ACTIONS) == len(listed), "a code is listed twice"
    assert found == listed, {"unlisted": sorted(found - listed), "stale": sorted(listed - found)}
    assert all(code.startswith("suite.") and code == code.lower() for code in ACTIONS)


def test_the_approvals_engine_kinds_are_the_ones_recorded_without_a_call():
    """The sweeper is the only path that records without a `Call`; its kinds are the audited ones."""
    source = (pathlib.Path(synwevo_suite.__file__).parent / "approvals" / "service.py").read_text(encoding="utf-8")
    without_call = re.findall(r'self\._record\(r,\s*"([a-z_.]+)"(?:,\s*\{[^}]*\})?\)', source)
    assert set(without_call) == set(APPROVALS_ENGINE_KINDS), without_call
