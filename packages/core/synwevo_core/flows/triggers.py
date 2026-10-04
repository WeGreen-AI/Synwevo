"""Durable event consumption and timezone schedules. Wake-ups never substitute for a read."""
from __future__ import annotations

import json
import logging

from ..capabilities import canonical
from ..errors import ServiceError
from ..store import new_id
from .models import Policy, Trigger, WaitStep, next_occurrence

log = logging.getLogger("synwevo.flows")

#: Events decided record by record for one trigger in one tick. The decision reads the
#: application's records inside the tick's transaction; the rest waits for the next tick.
DECIDED_PER_TICK = 20


def refusal(code):
    return ServiceError(409, code, "流程触发条件或授权不可用，请检查设置")


class Triggers:
    @staticmethod
    def _check_scope(spec, policy):
        for step in spec.steps:
            refs = [step] if step.kind == "call" else ([step.read] if step.kind == "wait" else [])
            if step.kind == "call" and step.result_read:
                refs.append(step.result_read)
            for ref in refs:
                if ref.operation not in policy.tools or (ref.workspace_id and ref.workspace_id not in policy.workspaces):
                    raise refusal("flow_policy_scope")

    def create_trigger(self, flow_id, *, user_id, version, trigger, policy):
        try:
            spec = Trigger.model_validate(trigger)
            grant = Policy.model_validate(policy)
        except ValueError:
            raise refusal("flow_trigger_invalid") from None
        with self.store.tx() as t:
            self._flow(flow_id, user_id)
            definition = self._spec(flow_id, version)
            self._authorize_definition(definition, user_id)
            self._check_scope(definition, grant)
            ts = self.clock()
            if grant.expires_at is not None and grant.expires_at <= ts:
                raise refusal("flow_policy_expired")
            if spec.kind == "event":
                if spec.workspace_id not in grant.workspaces:
                    raise refusal("flow_policy_scope")
                if not self.caps.registry.app(spec.event_type.split(".")[0]):
                    raise refusal("app_unavailable")
                self.caps.check_workspace(user_id, spec.event_type.split(".")[0], spec.workspace_id, "read")
            else:
                try:
                    definition.parameters_for(spec.input)
                except ValueError:
                    raise refusal("flow_parameters_invalid") from None
            count = self.store.one("SELECT COUNT(*) AS n FROM flow_triggers WHERE user_id=?", (user_id,))["n"]
            if count >= 100:
                raise refusal("flow_trigger_quota")
            tid = new_id("flowtrigger")
            cursor = self.store.one("SELECT COALESCE(MAX(seq),0) AS n FROM app_events")["n"]
            nxt = next_occurrence(spec, ts) if spec.kind == "schedule" else None
            t.execute("INSERT INTO flow_triggers (id,flow_id,version,user_id,spec_json,policy_json,next_at,cursor,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (tid, flow_id, version, user_id, canonical(spec.model_dump(mode="json")),
                       canonical(grant.model_dump(mode="json")), nxt, cursor, ts, ts))
            return {"trigger_id": tid, "next_at": nxt, "enabled": True}

    def trigger(self, trigger_id, *, user_id):
        self._user(user_id)
        row = self.store.one("SELECT * FROM flow_triggers WHERE id=? AND user_id=?", (trigger_id, user_id))
        if not row:
            raise ServiceError(404, "flow_trigger_not_found", "触发器不存在")
        return row

    def triggers(self, flow_id, *, user_id):
        self._flow(flow_id, user_id)
        # Creation is capped at 100 per owner, so this list is complete and bounded.
        return {"triggers": [self._trigger_public(r) for r in self.store.all("SELECT * FROM flow_triggers WHERE flow_id=? AND user_id=? ORDER BY id", (flow_id, user_id))]}

    @staticmethod
    def _trigger_public(row):
        # `cursor` is this trigger's place in the event log, which counts every workspace's events:
        # shown, it would tell its owner how busy everyone else is. It stays the engine's.
        return {k: v for k, v in row.items() if k not in ("spec_json", "policy_json", "cursor")} | {
            "trigger": json.loads(row["spec_json"]), "policy": json.loads(row["policy_json"])}

    def trigger_view(self, trigger_id, *, user_id):
        return self._trigger_public(self.trigger(trigger_id, user_id=user_id))

    def deliveries(self, trigger_id, *, user_id, after="", limit=50):
        self.trigger(trigger_id, user_id=user_id)
        if not 1 <= limit <= 200:
            raise refusal("flow_page_invalid")
        rows = self.store.all("SELECT * FROM flow_trigger_deliveries WHERE trigger_id=? AND occurrence>? ORDER BY occurrence LIMIT ?",
                              (trigger_id, after, limit + 1))
        return {"deliveries": rows[:limit], "next": rows[limit - 1]["occurrence"] if len(rows) > limit else None}

    def set_trigger_enabled(self, trigger_id, *, user_id, enabled):
        with self.store.tx() as t:
            row = self.trigger(trigger_id, user_id=user_id)
            ts = self.clock()
            cursor, nxt = row["cursor"], row["next_at"]
            if enabled:
                # Only switching on reads the row: its owner can always switch off a trigger this
                # release cannot read (R3-FE-01).
                try:
                    spec, policy = self._trigger_row(row)
                except ValueError:
                    raise refusal("flow_trigger_invalid") from None
                self._authorize_definition(self._spec(row["flow_id"], row["version"]), user_id)
                if policy.expires_at is not None and ts >= policy.expires_at:
                    raise refusal("flow_policy_expired")
                if row["runs_used"] >= policy.max_runs:
                    raise refusal("flow_trigger_budget_exhausted")
                if not row["enabled"]:
                    cursor = self.store.one("SELECT COALESCE(MAX(seq),0) AS n FROM app_events")["n"]
                    nxt = next_occurrence(spec, ts) if spec.kind == "schedule" else None
            t.execute("UPDATE flow_triggers SET enabled=?,cursor=?,next_at=?,error_code=NULL,updated_at=? WHERE id=?",
                      (int(enabled), cursor, nxt, ts, trigger_id))
            if not enabled:
                for run in self.store.all("SELECT * FROM flow_runs WHERE trigger_id=? AND status IN "
                                          "('queued','running','waiting_input','waiting_approval','retrying','paused')", (trigger_id,)):
                    self._release(t, run, "blocked", code="flow_policy_revoked")
                    t.execute("UPDATE flow_runs SET fence=fence+1 WHERE id=?", (run["id"],))
                    self._history(t, run, "policy_revoked", "blocked", code="flow_policy_revoked")
            return {"trigger_id": trigger_id, "enabled": bool(enabled), "next_at": nxt}

    @staticmethod
    def _trigger_row(row):
        return Trigger.model_validate(json.loads(row["spec_json"])), Policy.model_validate(json.loads(row["policy_json"]))

    def _deliver(self, t, row, spec, policy, occurrence, *, event=None, skip=False):
        prior = self.store.one("SELECT 1 FROM flow_trigger_deliveries WHERE trigger_id=? AND occurrence=?", (row["id"], occurrence))
        if prior:
            return
        ts, rid, code, state = self.clock(), None, None, "skipped" if skip else "started"
        closed = False
        t.execute("SAVEPOINT flow_delivery")
        try:
            if not skip:
                if event:
                    # Before anything else is decided about the event: may its owner read this
                    # application here at all? If not, the receipt says why and nothing of the event.
                    closed = True
                    if not self.caps.registry.app(event["app"]):
                        raise refusal("app_unavailable")
                    self.caps.check_workspace(row["user_id"], event["app"], spec.workspace_id, "read")
                    closed = False
                if policy.expires_at is not None and ts >= policy.expires_at:
                    raise refusal("flow_policy_expired")
                # Read the counter in the same transaction for concurrent workers/event batches.
                used = self.store.one("SELECT runs_used FROM flow_triggers WHERE id=?", (row["id"],))["runs_used"]
                if used >= policy.max_runs:
                    raise refusal("flow_trigger_budget_exhausted")
                definition = self._spec(row["flow_id"], row["version"])
                self._check_scope(definition, policy)
                payload = self._trigger_input(spec.input, event)
                # Only identity/routing metadata survives. Business data must come from operations.
                routing = {k: event[k] for k in ("id", "type", "object_id", "workspace_id")} if event else None
                result = self._start(row["flow_id"], user_id=row["user_id"], version=row["version"], parameters=payload,
                                     idempotency_key=f"trigger:{row['id']}:{occurrence}", trigger_id=row["id"], event=routing,
                                     correlation_id=self._chain(event, row["user_id"]) if event else None,
                                     parent_run_id=event.get("run_id") if event else None,
                                     depth=int(event.get("depth", 0)) + 1 if event else 0)
                rid = result["id"]
                t.execute("UPDATE flow_triggers SET runs_used=runs_used+1 WHERE id=?", (row["id"],))
        except ServiceError as exc:
            t.execute("ROLLBACK TO flow_delivery")
            code, state = exc.code, "refused"
        t.execute("RELEASE flow_delivery")
        if closed:
            # The application is closed to the owner: an event there is not theirs to know about, so
            # the receipt carries the reason in place of the event's id — and, the reason being its
            # key, there is one such receipt however many events were refused, not one per event.
            occurrence = "closed:" + code
        t.execute("INSERT OR IGNORE INTO flow_trigger_deliveries VALUES (?,?,?,?,?,?)", (row["id"], occurrence, rid, state, code, ts))
        t.execute("UPDATE flow_triggers SET error_code=?,updated_at=? WHERE id=?", (code, ts, row["id"]))

    def _chain(self, event, owner):
        """The chain of events this one belongs to, for the runs it starts for ``owner``: what the
        chain has read (`_sources`) must follow into them. An application that records an event
        later, from its own worker, may name only the operation run it settles; that run still
        knows its correlation.

        A chain is one person's, and a correlation is something any caller may name. So the
        event's chain is the owner's only when the owner's own operation caused it, or — for an
        event an application records with no operation run (a submission, a decision) — when the
        owner already has runs under its correlation. Otherwise, another member naming one
        correlation on all their writes would gather every run the owner's triggers start into
        one chain (R3-FE-07): the event begins a chain of its own, named after it and shared by
        the runs it starts side by side."""
        origin = (self.store.one("SELECT user_id, correlation_id FROM op_runs WHERE id=?", (event["run_id"],))
                  if event.get("run_id") else None)
        if origin is not None:
            if origin["user_id"] == owner:
                return event.get("correlation_id") or origin["correlation_id"]
        elif event.get("correlation_id") and (
                self.store.one("SELECT 1 FROM op_runs WHERE correlation_id=? AND user_id=? LIMIT 1",
                               (event["correlation_id"], owner))
                or self.store.one("SELECT 1 FROM flow_runs WHERE correlation_id=? AND user_id=? LIMIT 1",
                                  (event["correlation_id"], owner))):
            return event["correlation_id"]
        return event["id"]

    def _readable(self, row, workspace_id, events):
        """The events of this batch the trigger's owner may know about now.

        Reading the application in the workspace is not always reading the record: a table has a
        reading role, a dataset is withheld from someone who lost its sources. The application
        says which (`EventFeed.visible`, the decision the machine event feed uses), and an event
        about a record its owner may not read does not exist for this trigger — no run, no receipt,
        nothing of the event in any row the owner can read. It is not delivered later either: the
        cursor moves on, and a record that becomes readable fires from then.

        What is not a hidden record stays with `_deliver`, which refuses with the reason as it
        always has: an owner who is no longer available, an application closed to them here, and
        every application that declares no per-record decision.
        """
        shown, by_app = set(), {}
        for ev in events:
            by_app.setdefault(ev["app"], []).append(ev)
        for app, batch in by_app.items():
            ids = {ev["id"] for ev in batch}
            feed = self.caps.registry.events(app)
            if feed is None or feed.visible is None:
                shown |= ids
                continue
            try:
                self._user(row["user_id"])
                self.caps.check_workspace(row["user_id"], app, workspace_id, "read")
            except ServiceError:
                shown |= ids
                continue
            try:
                shown |= feed.visible(row["user_id"], workspace_id, batch) & ids
            except ServiceError:
                pass                        # the decision was refused: nothing is shown
        return shown

    def _trigger_input(self, value, event):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                return event[value["$ref"].split(".")[1]]
            return {k: self._trigger_input(v, event) for k, v in value.items()}
        if isinstance(value, list):
            return [self._trigger_input(v, event) for v in value]
        return value

    def trigger_tick(self, *, limit=20):
        # Fair rotation (updated_at), bounded event pages. Cursor advances atomically with runs
        # and refusal receipts. A restart cannot lose an event between advance and insert.
        ts = self.clock()
        rows = self.store.all("SELECT id FROM flow_triggers WHERE enabled=1 AND COALESCE(error_code,'') NOT IN "
                              "('flow_policy_expired','flow_trigger_budget_exhausted') AND (next_at IS NULL OR next_at<=?) "
                              "ORDER BY updated_at,id LIMIT ?", (ts, limit))
        for item in rows:
            with self.store.tx() as t:
                row = self.store.one("SELECT * FROM flow_triggers WHERE id=? AND enabled=1", (item["id"],))
                if not row:
                    continue
                try:
                    spec, policy = self._trigger_row(row)
                except ValueError:                  # pydantic's ValidationError is a ValueError
                    # A row this release cannot read (damaged, or stored by another release): it is
                    # refused alone and says why, and keeps its place — its cursor and due time stay,
                    # so a release that reads it goes on from there. Every other trigger and run go
                    # on (R3-FE-01).
                    t.execute("UPDATE flow_triggers SET error_code='flow_trigger_invalid',updated_at=? WHERE id=?",
                              (ts, row["id"]))
                    continue
                if spec.kind == "schedule":
                    if row["next_at"] > ts:
                        continue
                    due = row["next_at"]
                    self._deliver(t, row, spec, policy, str(int(due)), skip=spec.misfire == "skip" and ts - due > spec.grace_s)
                    # Coalesce downtime to at most one occurrence, then move strictly ahead.
                    t.execute("UPDATE flow_triggers SET next_at=?,updated_at=? WHERE id=?", (next_occurrence(spec, ts), ts, row["id"]))
                else:
                    events = self.caps.events_after(row["cursor"], limit=100)
                    matched = [ev for ev in events
                               if (ev["type"] == spec.event_type and ev["app"] == spec.event_type.split(".")[0]
                                   and ev["version"] == 1 and ev["workspace_id"] == spec.workspace_id
                                   and (not spec.object_kind or ev["object_kind"] == spec.object_kind)
                                   and (not spec.object_id or ev["object_id"] == spec.object_id))]
                    upto = events[-1]["seq"] if events else None
                    feed = self.caps.registry.events(spec.event_type.split(".")[0])
                    if feed is not None and feed.visible is not None and len(matched) > DECIDED_PER_TICK:
                        matched = matched[:DECIDED_PER_TICK]
                        upto = matched[-1]["seq"]           # the rest is read again by the next tick
                    try:
                        readable = self._readable(row, spec.workspace_id, matched) if matched else set()
                    except Exception:  # noqa: BLE001 — an application's decision failed, not this engine
                        # Not a refusal: a fault in the application's own check. This trigger keeps its
                        # place and is tried again next tick — nothing is passed over, and nothing is
                        # delivered undecided — while the other triggers and every run carry on.
                        log.exception("flow trigger %s: the per-record decision of %s failed", row["id"],
                                      spec.event_type.split(".")[0])
                        t.execute("UPDATE flow_triggers SET updated_at=? WHERE id=?", (ts, row["id"]))
                        continue
                    for ev in matched:
                        if ev["id"] in readable:
                            self._deliver(t, row, spec, policy, ev["id"], event=ev)
                    if upto is not None:
                        t.execute("UPDATE flow_triggers SET cursor=? WHERE id=?", (upto, row["id"]))
                    t.execute("UPDATE flow_triggers SET updated_at=? WHERE id=?", (ts, row["id"]))
        self._wake_waits()

    def _wake_waits(self):
        # Subscriptions are only latency hints; every wait also has a durable polling deadline.
        with self.store.tx() as t:
            consumer = "flows.wait.v1"
            seq = self.caps.cursor(consumer)
            events = self.caps.events_after(seq, limit=200)
            if not events:
                return
            pairs = {(e["workspace_id"], e["type"]) for e in events if e["version"] == 1 and e["type"].startswith(e["app"] + ".")}
            runs = self.store.all("SELECT * FROM flow_runs WHERE status IN ('waiting_input','waiting_approval') ORDER BY due_at LIMIT 200")
            for run in runs:
                try:
                    _, step = self._stored_step(run)
                except ServiceError:
                    continue                                # it fails alone when its own check falls due
                if isinstance(step, WaitStep) and any((step.read.workspace_id, typ) in pairs for typ in step.event_types):
                    t.execute("UPDATE flow_runs SET due_at=MIN(due_at,?) WHERE id=?", (self.clock(), run["id"]))
            self.caps.advance(t, consumer, events[-1]["seq"])
