"""A small, versioned, acyclic JSON language. No evaluated code or interpolated SQL.

References are exact objects: {"$ref": "params.name"}, {"$ref": "steps.collect.id"}.
Workspace targets are literal IDs, frozen into the version. A result_read converts a write
receipt to a currently authorized business read; its input may reference ``receipt``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

ID = r"^[A-Za-z][A-Za-z0-9_-]{0,79}$"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def bounded(value: Any, limit: int = 262144) -> Any:
    # Also rejects non-JSON values and unbounded reference nesting before interpretation.
    raw = json.dumps(value, allow_nan=False, ensure_ascii=False)
    if len(raw.encode()) > limit:
        raise ValueError("JSON exceeds the flow size budget")
    def walk(v, depth=0):
        if depth > 24:
            raise ValueError("JSON is nested too deeply")
        if isinstance(v, dict):
            for k, child in v.items():
                if not isinstance(k, str):
                    raise ValueError("JSON keys must be strings")
                walk(child, depth + 1)
        elif isinstance(v, list):
            for child in v:
                walk(child, depth + 1)
    walk(value)
    return value


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise ValueError("A reference must contain only a string $ref")
            path = value["$ref"].split(".")
            if len(path) < 2 or len(path) > 24 or any(not p or len(p) > 100 for p in path):
                raise ValueError("Invalid reference path")
            yield path
        else:
            for v in value.values():
                yield from references(v)
    elif isinstance(value, list):
        for v in value:
            yield from references(v)


class Parameter(Model):
    type: Literal["string", "integer", "number", "boolean", "object", "array"]
    required: bool = True


class Predicate(Model):
    left: Any
    op: Literal["eq", "ne", "in", "exists"] = "eq"
    right: Any = None


class Read(Model):
    operation: str = Field(min_length=3, max_length=120)
    operation_version: int = Field(default=1, ge=1)
    workspace_id: str | None = Field(default=None, max_length=100)
    input: dict = Field(default_factory=dict)


class BaseStep(Model):
    id: str = Field(pattern=ID)
    next: str | None = Field(default=None, max_length=80)
    on_error: str | None = Field(default=None, max_length=80)


class CallStep(BaseStep):
    kind: Literal["call"]
    operation: str = Field(min_length=3, max_length=120)
    operation_version: int = Field(default=1, ge=1)
    workspace_id: str | None = Field(default=None, max_length=100)
    input: dict = Field(default_factory=dict)
    result_read: Read | None = None
    max_attempts: int = Field(default=1, ge=1, le=5)
    retry_codes: list[str] = Field(default_factory=list, max_length=10)
    backoff_s: int = Field(default=5, ge=1, le=3600)


class WaitStep(BaseStep):
    kind: Literal["wait"]
    read: Read
    until: Predicate
    reject: Predicate | None = None
    state: Literal["waiting_input", "waiting_approval"] = "waiting_input"
    event_types: list[str] = Field(default_factory=list, max_length=10)
    poll_s: int = Field(default=60, ge=1, le=3600)
    timeout_s: int = Field(default=604800, ge=1, le=2592000)


class BranchStep(BaseStep):
    kind: Literal["branch"]
    condition: Predicate
    then: str = Field(max_length=80)
    otherwise: str = Field(max_length=80)


class FailStep(BaseStep):
    kind: Literal["fail"]
    code: str = Field(pattern=ID)


Step = Annotated[CallStep | WaitStep | BranchStep | FailStep, Field(discriminator="kind")]


class Definition(Model):
    parameters: dict[str, Parameter] = Field(default_factory=dict, max_length=50)
    steps: list[Step] = Field(min_length=1, max_length=64)
    max_calls: int = Field(default=1000, ge=1, le=10000)
    timeout_s: int = Field(default=604800, ge=1, le=2592000)

    @model_validator(mode="after")
    def graph(self):
        bounded(self.model_dump(mode="json"))
        positions = {s.id: n for n, s in enumerate(self.steps)}
        if len(positions) != len(self.steps) or "end" in positions:
            raise ValueError("Step IDs must be unique; end is reserved")
        for n, step in enumerate(self.steps):
            targets = [step.next, step.on_error]
            if isinstance(step, BranchStep):
                targets += [step.then, step.otherwise]
            for target in targets:
                if target is not None and target != "end" and positions.get(target, -1) <= n:
                    raise ValueError("Only forward branches to existing steps are supported")
            data = step.model_dump(mode="json")
            for path in references(data):
                if path[0] == "steps":
                    if len(path) < 3 or positions.get(path[1], n) >= n:
                        raise ValueError("Step references must name an earlier step")
                elif path[0] == "params":
                    if path[1] not in self.parameters:
                        raise ValueError("Unknown parameter")
                elif path[0] == "outcome":
                    # "succeeded" or "partial" of an earlier completed step; no business content.
                    if len(path) != 2 or positions.get(path[1], n) >= n:
                        raise ValueError("Outcome references must name an earlier step")
                elif path[0] not in ("record", "receipt"):
                    raise ValueError("Unsupported reference namespace")
            # Context-specific namespaces cannot sneak into operation input or branch rules.
            ordinary = data.copy()
            ordinary.pop("result_read", None)
            ordinary.pop("until", None)
            ordinary.pop("reject", None)
            if any(p[0] in ("record", "receipt") for p in references(ordinary)):
                raise ValueError("record/receipt only exist in wait conditions/result reads")
            if isinstance(step, CallStep) and step.result_read:
                if any(p[0] == "record" for p in references(step.result_read.input)):
                    raise ValueError("No record is available in a result read")
            if isinstance(step, WaitStep):
                for cond in (step.until, step.reject):
                    if cond and any(p[0] == "receipt" for p in references(cond.model_dump())):
                        raise ValueError("No receipt is available in a wait condition")
        return self

    def parameters_for(self, values: dict) -> dict:
        bounded(values)
        if set(values) - set(self.parameters):
            raise ValueError("Unknown flow parameters")
        types = {"string": (str,), "integer": (int,), "number": (int, float),
                 "boolean": (bool,), "object": (dict,), "array": (list,)}
        for name, spec in self.parameters.items():
            if name not in values:
                if spec.required:
                    raise ValueError("Required flow parameter is missing")
            elif type(values[name]) not in types[spec.type]:
                raise ValueError("Flow parameter has the wrong type")
        return values


class Policy(Model):
    tools: list[str] = Field(min_length=1, max_length=128)
    workspaces: list[str] = Field(default_factory=list, max_length=50)
    max_runs: int = Field(ge=1, le=10000)
    expires_at: float | None = None
    until_revoked: bool = False

    @model_validator(mode="after")
    def lifecycle(self):
        if (self.expires_at is None) == (not self.until_revoked):
            raise ValueError("Choose an expiry or explicitly until_revoked")
        return self


class Trigger(Model):
    kind: Literal["event", "schedule"]
    input: dict = Field(default_factory=dict)
    # Event values can only supply routing identities, never unauthenticated business content.
    event_type: str | None = Field(default=None, max_length=120)
    workspace_id: str | None = Field(default=None, max_length=100)
    object_kind: str | None = Field(default=None, max_length=100)
    object_id: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=100)
    at: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    misfire: Literal["skip", "once"] = "skip"
    grace_s: int = Field(default=300, ge=1, le=86400)

    @model_validator(mode="after")
    def contract(self):
        bounded(self.model_dump(mode="json"))
        if self.kind == "event":
            if not self.event_type or not self.workspace_id or self.timezone or self.at:
                raise ValueError("Event triggers require an explicit workspace and event type")
            allowed = {"event.id", "event.object_id", "event.workspace_id", "event.type"}
            if any(".".join(p) not in allowed for p in references(self.input)):
                raise ValueError("Event input may reference only routing identities")
        else:
            if not self.timezone or not self.at or self.event_type or self.workspace_id or self.object_id or self.object_kind:
                raise ValueError("Schedules require an IANA timezone and local HH:MM")
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Unknown IANA timezone") from None
            if list(references(self.input)):
                raise ValueError("Schedule inputs are fixed parameters")
        if any(type(d) is not int or d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("Weekdays are Monday=0 through Sunday=6")
        return self


def next_occurrence(spec: Trigger, after: float) -> float:
    """Daily/weekly wall time. Spring gaps skip; autumn folds run once (first occurrence)."""
    zone = ZoneInfo(spec.timezone)
    day = datetime.fromtimestamp(after, timezone.utc).astimezone(zone).date()
    hour, minute = map(int, spec.at.split(":"))
    for offset in range(15):
        date = day + timedelta(days=offset)
        if date.weekday() not in spec.weekdays:
            continue
        local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone, fold=0)
        stamp = local.timestamp()
        if datetime.fromtimestamp(stamp, zone).replace(tzinfo=None) != local.replace(tzinfo=None):
            continue
        if stamp > after:
            return stamp
    raise ValueError("No schedule occurrence within two weeks")
