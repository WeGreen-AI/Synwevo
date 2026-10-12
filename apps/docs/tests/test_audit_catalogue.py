"""The list of Synwevo Docs' audit actions equals what the source writes (G26, D7 §9).

The account service labels these codes from a copy it keeps as data; this scan is what keeps the
copy from falling behind the code: a new `audit(...)` literal must be added to
`synwevo_docs.audit_actions.ACTIONS` (and, by the integration test, to the account service's list).
"""
from __future__ import annotations

import pathlib
import re

import synwevo_docs
from synwevo_docs.audit_actions import ACTIONS

ROOT = pathlib.Path(synwevo_docs.__file__).parent

#: Fully written codes.
LITERAL = re.compile(r'"(docs\.[a-z_]+(?:\.[a-z_]+)+)"')
#: `App.audit("<short>", …)` prefixes the code with `docs.`.
SHORT = re.compile(r'\.audit\(\s*"([a-z_]+(?:\.[a-z_]+)+)"')
#: The webhook service writes `docs.webhook.<action>` from `_audit("<action>", …)` call sites.
WEBHOOK_STEM = re.compile(r'self\._audit\(\s*"([a-z_]+)"')


def written_in_source() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(LITERAL.findall(text))
        found |= {"docs." + s for s in SHORT.findall(text)}
        if path.name == "webhooks.py":
            found |= {"docs.webhook." + s for s in WEBHOOK_STEM.findall(text)}
    # `App.audit` itself writes f"docs.{action}" and the webhook service f"docs.webhook.{action}":
    # those are the writers, not actions.
    return found


def test_every_audit_action_the_source_writes_is_listed_once():
    found = written_in_source()
    listed = set(ACTIONS)
    assert len(ACTIONS) == len(listed), "a code is listed twice"
    assert found == listed, {"unlisted": sorted(found - listed), "stale": sorted(listed - found)}
    assert all(code.startswith("docs.") and code == code.lower() for code in ACTIONS)
