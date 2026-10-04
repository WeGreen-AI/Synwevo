"""One persisted step per lease, with database-atomic calls and conservative external recovery.

An external dispatch is considered started at its committed ``executing`` marker. Cancellation
fences completion and prevents later steps; it cannot retract an already started external call.
No worker may dispatch that marker again, even when no op receipt survived a crash.
"""
from __future__ import annotations

import json
from typing import Any

from .. import data_flow
from ..capabilities import MAX_DEPTH, canonical, fingerprint, record_event
from ..errors import ServiceError
from ..store import new_id, now
from .models import (BranchStep, CallStep, Definition, FailStep, Policy, Read, WaitStep, bounded, references)
from .triggers import Triggers

ACTIVE = ("queued", "running", "waiting_input", "waiting_approval", "retrying")
# Where a run stays until its owner acts. No worker claims these again (`claim` selects ACTIVE):
# "paused" by the owner, "blocked" by a refusal (401/403), "unknown" after a dispatched call
# whose outcome was not confirmed. `resume` re-queues them, `cancel` ends them.
PARKED = ("paused", "blocked", "unknown")
TERMINAL = ("succeeded", "partial", "failed", "cancelled")
# Every state a row of flow_runs can hold: what a reader that lists runs by state has to expect.
STATES = ACTIVE + PARKED + TERMINAL
# A step is complete when its operation reached a definite outcome with a result. "partial" (for
# example a DataPrep run that finished with row issues) completes it too and stays visible to
# branches as outcome.<step>; the run that ends after it ends partial, never plain succeeded.
COMPLETE = ("succeeded", "partial")
# The operation itself ended the receipt: a definite outcome, so on_error applies. Unlike a lost
# acknowledgement this is not an uncertain dispatch and is never "unknown".
DEFINITE = {"failed": "flow_operation_failed", "cancelled": "flow_operation_cancelled"}


def error(code, status=409):
    return ServiceError(status, code, "流程无法继续，请检查运行状态、权限或业务记录")


def decode(value, default=None):
    return json.loads(value) if isinstance(value, str) else (default if value is None else value)


class LostLease(Exception):
    """A newer worker, pause, cancellation or revocation owns the next transition."""


class AttemptFailed(ServiceError):
    def __init__(self, original, attempt):
        super().__init__(original.status, original.code, original.message)
        self.attempt = attempt
        self.reader = getattr(original, "reader", None)


class ResultReadFailed(ServiceError):
    """A step's result read ran and failed. The failure is that workspace's answer: the step
    keeps the reader, so the run counts where it read (Flows._sources)."""

    def __init__(self, original, reader):
        super().__init__(original.status, original.code, original.message)
        self.reader = reader


class Flows(Triggers):
    def __init__(self, store, capabilities, *, clock=now, lease_s=60):
        self.store, self.caps, self.clock = store, capabilities, clock
        self.lease_s = max(5, min(lease_s, 300))

    def _user(self, user_id):
        user = self.caps.identity.get_user(user_id)
        if not user or user["status"] != "active":
            raise error("account_unavailable", 401)
        app = self.store.one("SELECT status FROM apps WHERE id='agent'")
        if not self.caps.registry.app("agent") or not app or app["status"] != "active":
            raise error("app_unavailable", 403)
        return user

    def _flow(self, flow_id, user_id):
        self._user(user_id)
        row = self.store.one("SELECT * FROM flow_definitions WHERE id=? AND user_id=?", (flow_id, user_id))
        if not row:
            raise error("flow_not_found", 404)
        return row

    def _owned_run(self, run_id, user_id):
        self._user(user_id)
        row = self.store.one("SELECT * FROM flow_runs WHERE id=? AND user_id=?", (run_id, user_id))
        if not row:
            raise error("flow_run_not_found", 404)
        return row

    def _spec(self, flow_id, version):
        row = self.store.one("SELECT spec_json FROM flow_versions WHERE flow_id=? AND version=?", (flow_id, version))
        if not row:
            raise error("flow_version_not_found", 404)
        return Definition.model_validate(decode(row["spec_json"]))

    def _stored_step(self, run):
        """The run's pinned definition and the step it is at — or, for a row that no longer fits
        its definition (a damaged row, a version a later or earlier release stored that this one
        cannot read), a refusal for that run alone. It must never escape a worker's loop: one such
        row would stop every person's runs (CR-048 DF-B4)."""
        try:
            spec = self._spec(run["flow_id"], run["version"])
            return spec, next(st for st in spec.steps if st.id == run["step_id"])
        except (StopIteration, ValueError) as exc:          # pydantic's ValidationError is a ValueError
            raise error("flow_definition_invalid", 409) from exc

    def _operation(self, ref, user_id, *, read=False):
        op = self.caps.registry.get(ref.operation)
        if not op or op.version != ref.operation_version:
            raise error("flow_operation_version_unavailable")
        if op.need == "personal" or not op.unattended:
            # A personal operation has no workspace, so no transfer question can be asked of it:
            # as a step it would take workspace content into personal records (a memory, a
            # conversation) or hand personal records, whatever they hold, to a workspace.
            raise error("person_required", 403)
        if op.writes_elsewhere:
            # Scope cannot be proven from the explicit target when an operation has implicit
            # second destinations. Compose separate, explicitly targeted operations instead.
            raise error("flow_indirect_target_unsupported", 403)
        if isinstance(ref, CallStep) and ref.result_read and ref.result_read.workspace_id != ref.workspace_id:
            # The receipt of one workspace drives a read in another: the same transfer question.
            data_flow.check(self.store, user_id=user_id, source=ref.workspace_id, target=ref.result_read.workspace_id)
        if read and (op.side_effect != "none" or op.mode != "sync"):
            raise error("flow_read_required")
        self.caps.authorize(op, user_id, ref.workspace_id)
        return op

    def _refs(self, spec):
        for step in spec.steps:
            if isinstance(step, CallStep):
                yield step, False
                if step.result_read:
                    yield step.result_read, True
            if isinstance(step, WaitStep):
                yield step.read, True

    def _authorize_definition(self, spec, user_id):
        self._user(user_id)
        for ref, read in self._refs(spec):
            self._operation(ref, user_id, read=read)

    def create(self, *, user_id, name, definition):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise error("flow_name_invalid", 422)
        spec = self._parse_definition(definition)
        self._authorize_definition(spec, user_id)
        fid, ts = new_id("flow"), self.clock()
        with self.store.tx() as t:
            t.execute("INSERT INTO flow_definitions VALUES (?,?,?,1,?)", (fid, user_id, name, ts))
            t.execute("INSERT INTO flow_versions VALUES (?,1,?,?)", (fid, canonical(spec.model_dump(mode="json")), ts))
        return {"flow_id": fid, "version": 1}

    @staticmethod
    def _parse_definition(value):
        try:
            bounded(value)
            return Definition.model_validate(value)
        except (ValueError, TypeError, RecursionError):
            raise error("flow_definition_invalid", 422) from None

    def publish(self, flow_id, *, user_id, definition, expected_version):
        spec = self._parse_definition(definition)
        self._authorize_definition(spec, user_id)
        with self.store.tx() as t:
            row = self._flow(flow_id, user_id)
            if row["latest_version"] != expected_version:
                raise error("flow_version_conflict")
            version = expected_version + 1
            t.execute("INSERT INTO flow_versions VALUES (?,?,?,?)",
                      (flow_id, version, canonical(spec.model_dump(mode="json")), self.clock()))
            t.execute("UPDATE flow_definitions SET latest_version=? WHERE id=?", (version, flow_id))
        return {"flow_id": flow_id, "version": version}

    def definition(self, flow_id, *, user_id, version):
        row = self._flow(flow_id, user_id)
        return {"flow_id": flow_id, "name": row["name"], "version": version,
                "definition": self._spec(flow_id, version).model_dump(mode="json")}

    def list(self, *, user_id, after="", limit=50):
        self._user(user_id)
        if not 1 <= limit <= 200:
            raise error("flow_page_invalid", 422)
        rows = self.store.all("SELECT id,name,latest_version,created_at FROM flow_definitions "
                              "WHERE user_id=? AND id>? ORDER BY id LIMIT ?", (user_id, after, limit + 1))
        return {"flows": rows[:limit], "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def start(self, flow_id, *, user_id, version, parameters, idempotency_key,
              correlation_id=None, parent_run_id=None, depth=0):
        with self.store.tx():
            return self._start(flow_id, user_id=user_id, version=version, parameters=parameters,
                               idempotency_key=idempotency_key, correlation_id=correlation_id,
                               parent_run_id=parent_run_id, depth=depth)

    def _start(self, flow_id, *, user_id, version, parameters, idempotency_key,
               correlation_id=None, parent_run_id=None, depth=0, trigger_id=None, event=None):
        self._flow(flow_id, user_id)
        spec = self._spec(flow_id, version)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise error("bad_idempotency_key", 422)
        if not 0 <= depth < MAX_DEPTH:
            raise error("run_depth_exceeded")
        try:
            spec.parameters_for(parameters)
        except (ValueError, TypeError, RecursionError):
            raise error("flow_parameters_invalid", 422) from None
        digest = fingerprint({"version": version, "parameters": parameters})
        prior = self.store.one("SELECT * FROM flow_runs WHERE user_id=? AND flow_id=? AND idempotency_key=?",
                               (user_id, flow_id, idempotency_key))
        if prior:
            if prior["input_digest"] != digest:
                raise error("idempotency_conflict")
            return self._summary(prior) | {"replayed": True}
        self._authorize_definition(spec, user_id)
        ts, rid = self.clock(), new_id("flowrun")
        with self.store.tx() as t:
            t.execute("INSERT INTO flow_runs (id,flow_id,version,user_id,params_json,input_digest,idempotency_key,"
                      "status,step_id,correlation_id,parent_run_id,depth,trigger_id,event_json,deadline,due_at,created_at,updated_at) "
                      "VALUES (?,?,?,?,?,?,?,'queued',?,?,?,?,?,?,?,?,?,?)",
                      (rid, flow_id, version, user_id, canonical(parameters), digest, idempotency_key,
                       spec.steps[0].id, correlation_id or rid, parent_run_id, depth, trigger_id,
                       canonical(event) if event else None, ts + spec.timeout_s, ts, ts, ts))
            row = self.store.one("SELECT * FROM flow_runs WHERE id=?", (rid,))
            self._history(t, row, "started", "queued")
        return self._summary(row) | {"replayed": False}

    @staticmethod
    def _summary(row):
        return {k: row[k] for k in ("id", "flow_id", "version", "status", "step_id", "error_code",
                                    "correlation_id", "parent_run_id", "depth", "calls_used", "deadline",
                                    "created_at", "updated_at", "finished_at")}

    def carried(self, run_ids, *, user_id) -> list[str]:
        """Workspaces whose content these runs of this person may reflect (``_sources``). Not
        only a step's value: which steps ran, a run's state and its error code were decided on
        what the run read, so a reader that tracks sources counts them for a run's progress too."""
        found, chains, specs = set(), set(), {}
        for run_id in dict.fromkeys(run_ids):
            row = self.store.one("SELECT * FROM flow_runs WHERE id=? AND user_id=?", (run_id, user_id))
            if not row:
                continue
            # The chain's sources are the same for every run of it: read once per chain, not once
            # per run (a page of runs is mostly one chain).
            if row["correlation_id"] not in chains:
                chains.add(row["correlation_id"])
                found |= self._sources(row)
            if row["status"] in ("failed", "blocked", "unknown") and row["step_id"]:
                # The step the run stopped at answered with its error code: its places count too,
                # though _sources leaves the current step's failure out for a step being retried.
                version = (row["flow_id"], row["version"])
                if version not in specs:
                    specs[version] = {st.id: st for st in self._spec(*version).steps}
                found |= self._places(specs[version].get(row["step_id"]))
        return sorted(found - {None})

    def run(self, run_id, *, user_id, after=0, limit=100, outputs=True):
        """``outputs=False`` leaves the steps' business snapshots out: the answer of a personal
        operation carries no source workspace, so a reader that tracks sources (the Agent's
        model) must not be given workspace content through it."""
        if not 1 <= limit <= 200 or after < 0:
            raise error("flow_page_invalid", 422)
        row = self._owned_run(run_id, user_id)
        history = self.store.all("SELECT * FROM flow_history WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                                (run_id, after, limit + 1))
        steps = []
        try:
            spec = self._spec(row["flow_id"], row["version"])
        except (ValueError, ServiceError):
            spec = None                                     # a damaged row still shows what it holds
        positions = {s.id: n for n, s in enumerate(spec.steps)} if spec else {}
        stored_steps = self.store.all("SELECT * FROM flow_steps WHERE run_id=?", (run_id,))
        for step in sorted(stored_steps, key=lambda s: (positions.get(s["step_id"], len(positions)), s["step_id"])):
            public = {k: step[k] for k in ("step_id", "status", "attempt", "checks", "op_run_id", "error_code")}
            definition = spec.steps[positions[step["step_id"]]] if step["step_id"] in positions else None
            receipt = self._prior_op(row, definition, step) if isinstance(definition, CallStep) else None
            public["operation_status"] = receipt["status"] if receipt else None
            if receipt:
                public["op_run_id"] = receipt["id"]
            public.update(output=None, snapshot_hidden=bool(step["value_json"]))
            if outputs and step["value_json"] is not None:
                try:
                    current = self._source(row, step, charge=False)
                    if fingerprint(current) == fingerprint(decode(step["value_json"])):
                        public.update(output=current, snapshot_hidden=False)
                except ServiceError:
                    pass  # Metadata remains visible; business snapshots require live ACLs.
            steps.append(public)
        return self._summary(row) | {"steps": steps, "history": history[:limit],
                                    "next": history[limit - 1]["seq"] if len(history) > limit else None}

    def runs(self, flow_id, *, user_id, after="", limit=50):
        self._flow(flow_id, user_id)
        if not 1 <= limit <= 200:
            raise error("flow_page_invalid", 422)
        rows = self.store.all("SELECT * FROM flow_runs WHERE flow_id=? AND user_id=? AND id>? ORDER BY id LIMIT ?",
                              (flow_id, user_id, after, limit + 1))
        return {"runs": [self._summary(r) for r in rows[:limit]],
                "next": rows[limit - 1]["id"] if len(rows) > limit else None}

    def _history(self, t, run, type_, status, *, step=None, code=None):
        ts = self.clock()
        t.execute("INSERT INTO flow_history (run_id,step_id,type,status,attempt,op_run_id,error_code,fence,created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (run["id"], step["step_id"] if step else run["step_id"], type_, status,
                   step["attempt"] if step else None, step["op_run_id"] if step else None, code, run["fence"], ts))
        # Personal outbox carries routing metadata, never target workspace content or parameters.
        ev = record_event(t, type_="agent.flow." + type_, app="agent", workspace_id=None,
                          object_kind="flow_run", object_id=run["id"], user_id=run["user_id"],
                          run_id=run["id"], correlation_id=run["correlation_id"],
                          causation_id=run["parent_run_id"], depth=run["depth"],
                          data={"status": status, "step_id": run["step_id"], "error_code": code})
        self.store.after_commit(lambda: self.caps.announce([ev]))

    def _policy(self, run):
        self._user(run["user_id"])
        if run["trigger_id"]:
            trigger = self.store.one("SELECT * FROM flow_triggers WHERE id=?", (run["trigger_id"],))
            if not trigger or not trigger["enabled"]:
                raise error("flow_policy_revoked", 403)
            policy = Policy.model_validate(decode(trigger["policy_json"]))
            if policy.expires_at is not None and self.clock() >= policy.expires_at:
                raise error("flow_policy_expired", 403)
            self._check_scope(self._spec(run["flow_id"], run["version"]), policy)

    def _charge(self, run):
        spec = self._spec(run["flow_id"], run["version"])
        changed = self.store.execute("UPDATE flow_runs SET calls_used=calls_used+1 WHERE id=? AND calls_used<?",
                                     (run["id"], spec.max_calls))
        if not changed:
            raise error("flow_budget_exhausted")
        run["_charges"] = run.get("_charges", 0) + 1

    def _inspect(self, ref, user_id, *, run=None):
        self._operation(ref, user_id, read=True)
        if run:
            self._charge(run)
        if hasattr(self.caps, "inspect"):
            value = self.caps.inspect(ref.operation, ref.input, user_id=user_id, workspace_id=ref.workspace_id)
        else:
            # Old baseline: a real audited read, never a handler bypass. Integration's inspect
            # avoids accumulating read op_runs while an approval is waiting.
            result = self.caps.invoke(ref.operation, ref.input, user_id=user_id, workspace_id=ref.workspace_id,
                                      via="flow", correlation_id=run["correlation_id"] if run else None,
                                      parent_run_id=run["id"] if run else None,
                                      depth=run["depth"] + 1 if run else 0)
            if result["status"] != "succeeded":
                raise error("flow_read_incomplete")
            value = result["output"]
        try:
            bounded(value, 1048576)
        except (ValueError, TypeError, RecursionError):
            raise error("flow_result_too_large") from None
        return value

    def _source(self, run, step, *, charge):
        if step["status"] not in COMPLETE:
            raise error("flow_source_unavailable")
        reader = decode(step["reader_json"])
        if reader:
            return self._inspect(Read.model_validate(reader), run["user_id"], run=run if charge else None)
        if step["op_run_id"]:
            op_row = self.store.one("SELECT * FROM op_runs WHERE id=? AND user_id=?", (step["op_run_id"], run["user_id"]))
            op = self.caps.registry.get(op_row["op"]) if op_row else None
            # Never treat workspace membership alone as an object-level result guard.
            if not op or not getattr(op, "result_guard", None):
                raise error("flow_result_unverifiable", 403)
            if charge:
                self._charge(run)
            result = self.caps.run(step["op_run_id"], user_id=run["user_id"])
            if result.get("snapshot_hidden"):
                raise error("flow_result_unverifiable", 403)
            return result["output"]
        return decode(step["value_json"])

    @staticmethod
    def _path(value, path):
        for part in path:
            try:
                value = value[int(part)] if isinstance(value, list) and part.isdecimal() else value[part]
            except (TypeError, KeyError, IndexError, ValueError):
                raise error("flow_reference_missing", 422) from None
        return value

    @staticmethod
    def _places(step):
        """Where a step runs and reads, as its definition names them."""
        if isinstance(step, CallStep):
            return {step.workspace_id} | ({step.result_read.workspace_id} if step.result_read else set())
        return {step.read.workspace_id} if isinstance(step, WaitStep) else set()

    def _sources(self, run):
        """Every workspace whose content this run may carry.

        Which value came from where is not traced: a literal re-read of a workspace the run
        wrote into, a branch or a wait that decided on a value, and a run started by an event
        of another all carry content with no reference to follow. So a workspace counts once
        a step has read it (the stored reader: only reads that happened), once another step
        took a value or an outcome from a step there, once the run went on from a failure
        there, and when the event that started the run came from it. The same holds for every
        run of this person in the same chain of events: they share the correlation.
        """
        chain = (run["correlation_id"], run["user_id"])
        found = {r["place"] for r in self.store.all(
            "SELECT DISTINCT json_extract(event_json,'$.workspace_id') AS place FROM flow_runs "
            "WHERE correlation_id=? AND user_id=? AND event_json IS NOT NULL", chain)}
        # What any write of the chain carried, by whoever made it — an Agent's write whose event
        # started this chain included: its run record keeps the sources it was asked for.
        for r in self.store.all("SELECT DISTINCT delegation FROM op_runs WHERE correlation_id=? AND user_id=? "
                                "AND delegation LIKE '%transfer_from%'", chain):
            found.update((decode(r["delegation"], {}) or {}).get("transfer_from") or ())
        specs = {}
        # Distinct facts, not one row per step: a chain of events can be thousands of runs long.
        # A handled failure is an answer too; the step being run again has not given one yet.
        for row in self.store.all(
                "SELECT DISTINCT r.flow_id, r.version, s.step_id, json_extract(s.reader_json,'$.workspace_id') AS place, "
                "s.status='failed' AND NOT (r.id=? AND s.step_id=?) AS failed FROM flow_runs r "
                "JOIN flow_steps s ON s.run_id=r.id WHERE r.correlation_id=? AND r.user_id=?",
                (run["id"], run["step_id"], *chain)):
            found.add(row["place"])
            version = (row["flow_id"], row["version"])
            if version not in specs:
                specs[version] = {s.id: s for s in self._spec(*version).steps}
            step = specs[version][row["step_id"]]
            used = {path[1] for path in references(step.model_dump(mode="json")) if path[0] in ("steps", "outcome")}
            if row["failed"] and step.on_error:
                # A handled failure is an answer from where the step ran. A result read it never
                # reached told it nothing; a read that did happen, also one that failed, is
                # counted from its stored reader.
                found.add(step.workspace_id if isinstance(step, CallStep)
                          else step.read.workspace_id if isinstance(step, WaitStep) else None)
            for other in used:
                found |= self._places(specs[version].get(other))
        return found - {None}

    def _transfer(self, run, target, *, external=False, also=()):
        """Ask whether what this run may carry may become part of ``target``. Returns the
        sources asked: their IDs are the provenance of a write."""
        sources = sorted((self._sources(run) | set(also)) - {target, None})
        for source in sources:
            # Membership in two spaces does not grant transfer: the owner must still be
            # allowed to carry each source's content there, every time.
            data_flow.check(self.store, user_id=run["user_id"], source=source, target=target, external=external)
        return sources

    @staticmethod
    def _takes(run, value, *, own=()):
        """Whether an input takes a value the run obtained: a step's value or outcome, a
        receipt, or parameters an event filled in. A literal, or a parameter the person gave
        at the start, was fixed before the run read anything."""
        roots = {path[0] for path in references(value)} - set(own)
        if not run["event_json"]:
            roots.discard("params")
        return bool(roots)

    def _carried(self, run, step, op):
        """The transfer question of a call step, asked before it runs. A write takes along
        whatever the run has read, with or without a reference: the path that led to it was
        decided on that. A read is asked only when its input takes a value from the run, so
        reading two workspaces to compare them is no transfer."""
        carried = []
        if op.side_effect != "none":
            carried = self._transfer(run, step.workspace_id, external=op.side_effect == "external")
        elif self._takes(run, step.input):
            self._transfer(run, step.workspace_id)
        if step.result_read and step.result_read.workspace_id != step.workspace_id:
            # The receipt of one workspace drives a read in another, with all the step was given.
            self._transfer(run, step.result_read.workspace_id, also={step.workspace_id})
        return carried

    def _resolve(self, run, value, **extra):
        if isinstance(value, dict):
            if set(value) == {"$ref"}:
                path = value["$ref"].split(".")
                if path[0] == "steps":
                    step = self.store.one("SELECT * FROM flow_steps WHERE run_id=? AND step_id=?", (run["id"], path[1]))
                    if not step or step["status"] not in COMPLETE:
                        raise error("flow_reference_missing", 422)
                    saved = self._path(decode(step["value_json"]), path[2:])
                    current = self._path(self._source(run, step, charge=True), path[2:])
                    if fingerprint(saved) != fingerprint(current):
                        raise error("flow_source_changed", 403)
                    return saved
                if path[0] == "outcome":
                    done = self.store.one("SELECT status FROM flow_steps WHERE run_id=? AND step_id=?", (run["id"], path[1]))
                    if len(path) != 2 or not done or done["status"] not in COMPLETE:
                        raise error("flow_reference_missing", 422)
                    return done["status"]
                roots = {"params": decode(run["params_json"]), **extra}
                if path[0] not in roots:
                    raise error("flow_reference_missing", 422)
                return self._path(roots[path[0]], path[1:])
            return {k: self._resolve(run, v, **extra) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(run, v, **extra) for v in value]
        return value

    def _predicate(self, run, predicate, **extra):
        try:
            left = self._resolve(run, predicate.left, **extra)
        except ServiceError as exc:
            if predicate.op == "exists" and exc.code == "flow_reference_missing":
                return False
            raise
        right = self._resolve(run, predicate.right, **extra)
        if predicate.op == "exists":
            return left is not None
        if predicate.op == "eq":
            return fingerprint(left) == fingerprint(right)
        if predicate.op == "ne":
            return fingerprint(left) != fingerprint(right)
        if not isinstance(right, list):
            raise error("flow_condition_invalid", 422)
        return any(fingerprint(left) == fingerprint(item) for item in right)

    def claim(self, *, worker_id=None):
        """Public worker seam for multi-process execution/fault injection; never a user API."""
        ts = self.clock()
        with self.store.tx() as t:
            row = self.store.one("SELECT * FROM flow_runs WHERE status IN ('queued','running','waiting_input',"
                                  "'waiting_approval','retrying') AND due_at<=? AND lease_until<=? "
                                  "ORDER BY due_at,id LIMIT 1", (ts, ts))
            if not row:
                return None
            owner = worker_id or new_id("lease")
            t.execute("UPDATE flow_runs SET lease_owner=?,lease_until=?,fence=fence+1,status='running',updated_at=? WHERE id=?",
                      (owner, ts + self.lease_s, ts, row["id"]))
            return self.store.one("SELECT * FROM flow_runs WHERE id=?", (row["id"],))

    def _fenced(self, claim):
        row = self.store.one("SELECT * FROM flow_runs WHERE id=?", (claim["id"],))
        if not row or row["fence"] != claim["fence"] or row["lease_owner"] != claim["lease_owner"] or row["status"] != "running" or row["lease_until"] <= self.clock():
            raise LostLease()
        return row

    def _release(self, t, run, status, *, due=None, code=None, next_step=None):
        ts = self.clock()
        t.execute("UPDATE flow_runs SET status=?,step_id=COALESCE(?,step_id),error_code=?,due_at=?,"
                  "lease_owner=NULL,lease_until=0,updated_at=?,finished_at=? WHERE id=? AND fence=?",
                  (status, next_step, code, ts if due is None else due, ts, ts if status in TERMINAL else None,
                   run["id"], run["fence"]))

    def _advance(self, t, run, spec, step, *, target=None):
        index = next(i for i, s in enumerate(spec.steps) if s.id == step.id)
        target = target or step.next or (spec.steps[index + 1].id if index + 1 < len(spec.steps) else "end")
        state = "queued"
        if target == "end":
            failed = self.store.one("SELECT 1 FROM flow_steps WHERE run_id=? AND status IN ('failed','partial')", (run["id"],))
            state = "partial" if failed else "succeeded"
        self._release(t, run, state, next_step=target)
        self._history(t, run, "advanced", state)

    def _step_row(self, run):
        return self.store.one("SELECT * FROM flow_steps WHERE run_id=? AND step_id=?", (run["id"], run["step_id"]))

    def process(self, claim):
        """Execute a claim once. A stale fence cannot dispatch even a transactional operation."""
        run, committed = None, False
        try:
            external = None
            with self.store.tx() as t:
                run = self._fenced(claim)
                self._policy(run)
                if self.clock() >= run["deadline"]:
                    raise error("flow_deadline_exceeded")
                spec, step = self._stored_step(run)
                ts = self.clock()
                t.execute("INSERT OR IGNORE INTO flow_steps (run_id,step_id,status,started_at,updated_at) VALUES (?,?,'queued',?,?)",
                          (run["id"], step.id, ts, ts))
                saved = self._step_row(run)
                # Store's nested tx is reentrant, not a savepoint. An operation which fails
                # after writing must not accidentally commit its partial writes in our tx.
                t.execute("SAVEPOINT flow_dispatch")
                try:
                    if isinstance(step, FailStep):
                        raise error(step.code, 422)
                    elif isinstance(step, BranchStep):
                        chosen = step.then if self._predicate(run, step.condition) else step.otherwise
                        t.execute("UPDATE flow_steps SET status='succeeded',value_json=?,updated_at=? WHERE run_id=? AND step_id=?",
                                  (canonical({"target": chosen}), ts, run["id"], step.id))
                        self._advance(t, run, spec, step, target=chosen)
                    elif isinstance(step, WaitStep):
                        self._wait(t, run, spec, step, saved)
                    else:
                        external = self._call(t, run, spec, step, saved)
                    t.execute("RELEASE flow_dispatch")
                except Exception:
                    t.execute("ROLLBACK TO flow_dispatch")
                    t.execute("RELEASE flow_dispatch")
                    raise
            committed = True
            if external:
                self._external(claim, *external)
        except LostLease:
            return False
        except ServiceError as exc:
            self._failure(claim, exc, charges=run.get("_charges", 0) if run and not committed else 0)
        return True

    def _wait(self, t, run, spec, step, saved):
        if self.clock() >= saved["started_at"] + step.timeout_s:
            raise error("flow_wait_timeout")
        if self._takes(run, step.read.input):
            self._transfer(run, step.read.workspace_id)
        ref = step.read.model_copy(update={"input": self._resolve(run, step.read.input)})
        record = self._inspect(ref, run["user_id"], run=run)
        if step.reject and self._predicate(run, step.reject, record=record):
            raise error("flow_wait_rejected", 422)
        ready = self._predicate(run, step.until, record=record)
        state = "succeeded" if ready else step.state
        t.execute("UPDATE flow_steps SET status=?,checks=checks+1,reader_json=?,value_json=?,updated_at=? WHERE run_id=? AND step_id=?",
                  (state, canonical(ref.model_dump(mode="json")), canonical(record) if ready else None,
                   self.clock(), run["id"], step.id))
        if ready:
            self._history(t, run, "step_completed", state, step=self._step_row(run))
            self._advance(t, run, spec, step)
        else:
            self._release(t, run, state, due=min(self.clock() + step.poll_s, saved["started_at"] + step.timeout_s, run["deadline"]))
            if saved["status"] != state:
                self._history(t, run, "waiting", state)

    def _invoke(self, run, step, payload, key, carried):
        delegation = {"kind": "flow", "flow_id": run["flow_id"], "version": run["version"],
                      "run_id": run["id"], "step_id": step.id, "trigger_id": run["trigger_id"]}
        if carried:
            # Provenance for the target's run record: workspace IDs only, never their content.
            delegation["transfer_from"] = carried
        return self.caps.invoke(step.operation, payload, user_id=run["user_id"], workspace_id=step.workspace_id,
                                via="flow", idempotency_key=key, correlation_id=run["correlation_id"],
                                parent_run_id=run["id"], depth=run["depth"] + 1, delegation=delegation)

    def _prior_op(self, run, step, saved):
        if not saved["key"]:
            return None
        return self.store.one("SELECT * FROM op_runs WHERE user_id=? AND op=? AND COALESCE(workspace_id,'')=? AND idempotency_key=?",
                              (run["user_id"], step.operation, step.workspace_id or "", saved["key"]))

    def _call(self, t, run, spec, step, saved):
        op = self._operation(step, run["user_id"])
        # A dispatch marker survives only non-transactional calls, async receipts, or faults.
        if saved["status"] in ("executing", "operation_wait", "unknown"):
            prior = self._prior_op(run, step, saved)
            if prior and prior["status"] in COMPLETE:
                self._complete_call(t, run, spec, step, {"run_id": prior["id"], "output": decode(prior["output"]), "status": prior["status"]})
            elif saved["status"] == "operation_wait" and prior and prior["status"] in DEFINITE:
                t.execute("UPDATE flow_steps SET op_run_id=? WHERE run_id=? AND step_id=?", (prior["id"], run["id"], step.id))
                raise error(DEFINITE[prior["status"]], 409)
            elif saved["status"] == "operation_wait" and prior and prior["status"] in ("queued", "running", "waiting_input", "waiting_approval"):
                self._release(t, run, "waiting_approval" if prior["status"] == "waiting_approval" else "waiting_input",
                              due=min(self.clock() + 30, run["deadline"]))
            else:
                raise error("flow_outcome_unknown")
            return None
        if saved["attempt"] >= step.max_attempts:
            raise error("flow_attempts_exhausted")
        carried = self._carried(run, step, op)
        payload = self._resolve(run, step.input)
        attempt = saved["attempt"] + 1
        key = f"flow:{run['flow_id']}:{run['id']}:{step.id}:{attempt}"
        self._charge(run)
        t.execute("UPDATE flow_steps SET status='executing',attempt=?,key=?,error_code=NULL,updated_at=? WHERE run_id=? AND step_id=?",
                  (attempt, key, self.clock(), run["id"], step.id))
        self._history(t, run, "step_started", "executing", step=self._step_row(run))
        if not op.transactional:
            return step, payload, key
        try:
            result = self._invoke(run, step, payload, key, carried)
            self._complete_call(t, run, spec, step, result, payload=payload)
        except ServiceError as exc:
            raise AttemptFailed(exc, attempt) from exc
        return None

    def _complete_call(self, t, run, spec, step, result, *, payload=None):
        state = result["status"]
        t.execute("UPDATE flow_steps SET op_run_id=?,receipt_json=?,updated_at=? WHERE run_id=? AND step_id=?",
                  (result["run_id"], canonical(result["output"]), self.clock(), run["id"], step.id))
        if state in ("queued", "running", "waiting_input", "waiting_approval"):
            t.execute("UPDATE flow_steps SET status='operation_wait' WHERE run_id=? AND step_id=?", (run["id"], step.id))
            self._release(t, run, "waiting_approval" if state == "waiting_approval" else "waiting_input",
                          due=min(self.clock() + 30, run["deadline"]))
            return
        if state in DEFINITE:
            raise error(DEFINITE[state], 409)
        if state not in COMPLETE:
            raise error("flow_outcome_unknown" if state == "unknown" else "flow_operation_incomplete")
        reader, value = None, result["output"]
        if step.result_read:
            read = step.result_read
            # Its own receipt back into the same workspace is no transfer; anything else is.
            if read.workspace_id != step.workspace_id or self._takes(run, read.input, own=("receipt",)):
                self._transfer(run, read.workspace_id, also={step.workspace_id})
            reader = read.model_copy(update={"input": self._resolve(run, read.input, receipt=value)})
        elif self.caps.registry.get(step.operation).side_effect == "none":
            if payload is None:
                prior = self.store.one("SELECT input FROM op_runs WHERE id=?", (result["run_id"],))
                payload = decode(prior["input"])
            reader = Read(operation=step.operation, operation_version=step.operation_version,
                          workspace_id=step.workspace_id, input=payload)
        if reader:
            try:
                value = self._inspect(reader, run["user_id"], run=run)
            except ServiceError as exc:
                raise (ResultReadFailed(exc, reader) if step.result_read else exc) from exc
        t.execute("UPDATE flow_steps SET status=?,value_json=?,reader_json=?,error_code=NULL,updated_at=? "
                  "WHERE run_id=? AND step_id=?",
                  (state, canonical(value), canonical(reader.model_dump(mode="json")) if reader else None,
                   self.clock(), run["id"], step.id))
        self._history(t, run, "step_completed", state, step=self._step_row(run))
        self._advance(t, run, spec, step)

    def _external(self, claim, step, payload, key):
        # Check cancellation and ask the transfer again immediately before dispatch, without
        # holding SQLite over I/O.
        with self.store.tx():
            run = self._fenced(claim)
            self._policy(run)
            carried = self._carried(run, step, self._operation(step, run["user_id"]))
        try:
            result = self._invoke(run, step, payload, key, carried)
        except Exception:  # A timeout or even an application error may follow an external write.
            self._failure(claim, error("flow_outcome_unknown"))
            return
        try:
            with self.store.tx() as t:
                run = self._fenced(claim)
                self._complete_call(t, run, self._spec(run["flow_id"], run["version"]), step, result, payload=payload)
        except ServiceError as exc:
            self._failure(claim, exc, charges=run.get("_charges", 0))

    def _failure(self, claim, exc, *, charges=0):
        with self.store.tx() as t:
            try:
                run = self._fenced(claim)
            except LostLease:
                return
            try:
                spec, step = self._stored_step(run)
            except ServiceError as broken:
                # Nothing of the run can be trusted to say which step failed: it ends here, with
                # the code that says why, and every other run goes on.
                self._release(t, run, "failed", code=broken.code)
                self._history(t, run, "step_failed", "failed", code=broken.code)
                return
            saved = self._step_row(run)
            ts = self.clock()
            if not saved:
                t.execute("INSERT INTO flow_steps (run_id,step_id,status,started_at,updated_at) VALUES (?,?,'queued',?,?)",
                          (run["id"], step.id, ts, ts))
                saved = self._step_row(run)
            attempts = saved["attempt"]
            # The failed transactional attempt was rolled back with its business data. Its
            # bounded attempt accounting and error history are committed separately here.
            op = self.caps.registry.get(step.operation) if isinstance(step, CallStep) else None
            if isinstance(exc, AttemptFailed):
                attempts = exc.attempt
            if charges:
                self.store.execute("UPDATE flow_runs SET calls_used=calls_used+? WHERE id=?", (charges, run["id"]))
            state, due = "failed", ts
            # Refused while waiting for a dispatched operation's receipt (a policy closed, a role
            # withdrawn): the operation's own run still records its outcome, so nothing is
            # uncertain. The step keeps waiting for it, and the run resumes from there once the
            # refusal is lifted (CR-048 DF-B3) instead of asking for a reconciliation for ever.
            waiting = saved["status"] == "operation_wait" and exc.status in (401, 403)
            uncertain = (not waiting and saved["status"] in ("executing", "operation_wait", "unknown")
                         and exc.code not in DEFINITE.values())
            if exc.code == "flow_outcome_unknown" or uncertain:
                state = "unknown"
            if exc.status in (401, 403):
                state = "blocked"
            elif not uncertain and op and op.transactional and exc.status >= 500 and exc.code in step.retry_codes and attempts < step.max_attempts:
                state, due = "retrying", ts + min(3600, step.backoff_s * 2 ** max(0, attempts - 1))
            t.execute("UPDATE flow_steps SET status=?,attempt=?,error_code=?,updated_at=? WHERE run_id=? AND step_id=?",
                      ("operation_wait" if waiting else "unknown" if state == "unknown" else "failed", attempts,
                       exc.code, ts, run["id"], step.id))
            # A dispatched external call remains recoverable by its original key, never a retry.
            if uncertain:
                t.execute("UPDATE flow_steps SET status='unknown' WHERE run_id=? AND step_id=?", (run["id"], step.id))
            reader = getattr(exc, "reader", None)
            if reader is not None:
                # The result read was reached and answered with this failure (its attempt was
                # rolled back): where it read is a source of the run like a read that succeeded.
                t.execute("UPDATE flow_steps SET reader_json=? WHERE run_id=? AND step_id=?",
                          (canonical(reader.model_dump(mode="json")), run["id"], step.id))
            self._history(t, run, "step_failed", state, step=self._step_row(run), code=exc.code)
            if state == "failed" and step.on_error and exc.code not in ("flow_budget_exhausted", "flow_deadline_exceeded"):
                self._advance(t, run, spec, step, target=step.on_error)
            else:
                self._release(t, run, state, due=due, code=exc.code)

    def pause(self, run_id, *, user_id):
        return self._control(run_id, user_id, "paused")

    def cancel(self, run_id, *, user_id):
        return self._control(run_id, user_id, "cancelled")

    def _control(self, run_id, user_id, status):
        with self.store.tx() as t:
            run = self._owned_run(run_id, user_id)
            if run["status"] in TERMINAL:
                return self._summary(run)
            self._release(t, run, status)
            if status == "cancelled":
                t.execute("UPDATE flow_steps SET status='unknown' WHERE run_id=? AND status IN ('executing','operation_wait')", (run_id,))
            t.execute("UPDATE flow_runs SET fence=fence+1 WHERE id=?", (run_id,))
            self._history(t, run, status, status)
            return self._summary(self._owned_run(run_id, user_id))

    def resume(self, run_id, *, user_id):
        with self.store.tx() as t:
            run = self._owned_run(run_id, user_id)
            if run["status"] not in ("paused", "blocked", "unknown", "failed"):
                raise error("flow_not_resumable")
            self._policy(run)
            if self.clock() >= run["deadline"]:
                raise error("flow_deadline_exceeded")
            spec, step = self._stored_step(run)
            saved = self._step_row(run)
            if isinstance(step, FailStep):
                raise error("flow_not_resumable")
            if isinstance(step, CallStep):
                self._operation(step, user_id)
                if saved and saved["status"] in ("executing", "unknown"):
                    prior = self._prior_op(run, step, saved)
                    if not prior or prior["status"] not in COMPLETE:
                        raise error("flow_reconciliation_required")
                    t.execute("UPDATE flow_steps SET status='unknown' WHERE run_id=? AND step_id=?", (run_id, step.id))
                elif saved and saved["status"] != "operation_wait" and saved["attempt"] >= step.max_attempts:
                    raise error("flow_attempts_exhausted")
            elif isinstance(step, WaitStep):
                self._operation(step.read, user_id, read=True)
                if saved and self.clock() >= saved["started_at"] + step.timeout_s:
                    raise error("flow_wait_timeout")
            self._release(t, run, "queued")
            t.execute("UPDATE flow_runs SET fence=fence+1,finished_at=NULL WHERE id=?", (run_id,))
            self._history(t, run, "resumed", "queued")
            return self._summary(self._owned_run(run_id, user_id))

    def tick(self, *, limit=20):
        """Bounded work; waits store a next check time and hold no thread/model between ticks."""
        if not 1 <= limit <= 100:
            raise ValueError("tick limit must be between 1 and 100")
        self.trigger_tick(limit=limit)
        count = 0
        for _ in range(limit):
            claim = self.claim()
            if claim is None:
                break
            self.process(claim)
            count += 1
        return count
