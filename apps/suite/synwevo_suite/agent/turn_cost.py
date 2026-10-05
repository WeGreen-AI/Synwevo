"""What each Agent answer cost, read from the money ledger only (shared contract "per-turn cost").

The figure a person sees beside an answer is the sum of the ledger's sale journal entries for the
calls of that turn's run, and the token breakdown is the settled usage those entries charged for.
Nothing here reads the engine's own counters (``agent_turns.input_tokens``), estimates from text,
or computes a price: a turn without a ledger run has no cost, and a call whose usage is still
unknown is reported as ``pending`` with its hold, never as a guess.

No cost price, platform margin, provider request ID, evidence, credential or prompt leaves this
module; only the person's own charge for their own turn.
"""
from __future__ import annotations

import json

#: How many turn IDs one query carries (SQLite's default variable limit is far higher).
_CHUNK = 200


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), _CHUNK):
        yield values[start:start + _CHUNK]


def _marks(values) -> str:
    return ",".join("?" * len(values))


def _band_column(store) -> str | None:
    """The time band is pinned on the attempt by the pricing package ("band"). Until that column
    exists no band is claimed: None means "not recorded", never a default band."""
    columns = {row["name"] for row in store.all("PRAGMA table_info(billing_attempts)")}
    return "band" if "band" in columns else None


def turn_costs(store, *, environment: str, namespace: str, user_id: str, turn_ids,
               active=frozenset()) -> dict[str, dict]:
    """``{turn_id: cost}`` for the person's own turns that have a ledger run in this environment.

    cost = {charged_minor, currency, scale, tokens: {input_cached, input_uncached, output, reasoning},
            model, band, state, held_minor}

    * ``charged_minor`` — decimal string, the sum of this run's sale journal amounts (smallest unit).
    * ``state`` — ``pending`` while any attempt's usage is unknown and its call still reserved
      (``usage_pending``, or ``dispatch_unknown`` once the turn is no longer active), else ``held``
      while a call still holds funds (a request in flight for an ``active`` turn), else ``settled``.
    * ``tokens`` — settled usage facts behind the charge; reasoning is part of output, not added.
    * ``band`` — the pinned time band of the charged attempts ("mixed" when they differ), or None.
    """
    turn_ids = [t for t in dict.fromkeys(turn_ids) if isinstance(t, str) and t]
    if not turn_ids:
        return {}
    band = _band_column(store)
    out: dict[str, dict] = {}
    for chunk in _chunks(turn_ids):
        runs = store.all(
            "SELECT r.id AS run_key, r.run_id, r.unit, r.context, u.scale, m.route FROM billing_runs r "
            "JOIN billing_units u ON u.code=r.unit LEFT JOIN agent_metered_runs m ON m.run_key=r.id "
            f"WHERE r.environment=? AND r.namespace=? AND r.run_id IN ({_marks(chunk)})",
            (environment, namespace, *chunk))
        for run in runs:
            context = json.loads(run["context"])
            # The turn ID is the run ID; the actor must be this person, whoever pays.
            if context.get("actor_user_id") != user_id:
                continue
            route = json.loads(run["route"]) if run["route"] else {}
            out[run["run_id"]] = _cost(store, run, route, band, in_flight=run["run_id"] in active)
    return out


def _cost(store, run, route, band_column, *, in_flight: bool) -> dict:
    calls = store.all("SELECT id, status, hold_units FROM billing_calls WHERE run_key=?", (run["run_key"],))
    keys = [c["id"] for c in calls]
    charged, held = 0, 0
    tokens = {"input_cached": 0, "input_uncached": 0, "output": 0, "reasoning": 0}
    models, bands = [], set()
    unknown = False
    if keys:
        marks = _marks(keys)
        for row in store.all(
                "SELECT j.amount FROM billing_settlements s JOIN billing_journal j ON j.id=s.journal_id "
                f"WHERE j.kind='sale' AND s.call_key IN ({marks})", keys):
            charged += int(row["amount"])
        for row in store.all(
                "SELECT u.facts FROM billing_settlements s JOIN billing_usage u ON u.attempt_key=s.attempt_key "
                f"WHERE s.call_key IN ({marks})", keys):
            usage = json.loads(row["facts"]).get("usage") or {}
            tokens["input_uncached"] += int(usage.get("input_uncached") or 0)
            tokens["input_cached"] += int(usage.get("input_cached") or 0)
            tokens["output"] += int(usage.get("output") or 0)
            tokens["reasoning"] += int(usage.get("reasoning") or 0)
        pick = f", a.{band_column} AS band" if band_column else ""
        reserved = {c["id"] for c in calls if c["status"] == "reserved"}
        for row in store.all(f"SELECT a.call_key, a.status, a.requested_model{pick} FROM billing_attempts a "
                             f"WHERE a.call_key IN ({marks}) ORDER BY a.created_at", keys):
            models.append(row["requested_model"])
            # While the turn runs, dispatch_unknown is the request in flight; afterwards it is
            # consumption nobody has proven, exactly like usage_pending.
            if row["call_key"] in reserved and (row["status"] == "usage_pending"
                                                 or (row["status"] == "dispatch_unknown" and not in_flight)):
                unknown = True
            if band_column and row["status"] == "settled" and row["band"]:
                bands.add(row["band"])
        held = sum(int(c["hold_units"]) for c in calls if c["status"] == "reserved")
    state = "pending" if unknown else "held" if held else "settled"
    return {"charged_minor": str(charged), "currency": run["unit"], "scale": run["scale"],
            "tokens": tokens, "model": route.get("requested_model") or (models[-1] if models else None),
            "band": None if not bands else bands.pop() if len(bands) == 1 else "mixed",
            "state": state, "held_minor": str(held)}
