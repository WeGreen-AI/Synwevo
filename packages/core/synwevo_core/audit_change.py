"""The unified before/after record of a state change (admin coverage G24, "统一前后值").

Every administration action that changes state puts the same three keys in its audit `meta`:
`before` and `after`, two snapshots over the same keys, and `changed`, the keys whose value
differs. A reader (the audit page, an export, a reviewer) then shows a change the same way for
every action instead of knowing each action's own fields. Actions that do not change state (a
mail sent again, a diagnostic) carry none of the three.
"""
from __future__ import annotations

from collections.abc import Mapping


def change(before: Mapping[str, object], after: Mapping[str, object]) -> dict:
    """`{"before": …, "after": …, "changed": […]}` over the union of both snapshots' keys, sorted.

    A key missing on one side reads as `None` there, so a value that appears or disappears is a
    change like any other; `changed` is empty when nothing differs (the action then recorded a
    no-op, which is still a fact worth keeping)."""
    keys = sorted(set(before) | set(after))
    return {"before": {k: before.get(k) for k in keys}, "after": {k: after.get(k) for k in keys},
            "changed": [k for k in keys if before.get(k) != after.get(k)]}
