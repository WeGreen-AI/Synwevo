"""Application capabilities: what each application can do, and the one way to ask for it.

Synwevo's business applications (Forms, Tables, DataPrep, Extract, Approvals, the personal Agent,
and Docs / Message through their adapters) are used four ways: from their own pages, through the
operations API, as a step of an automated flow, and by a person's Agent. If each of those had its
own validation and permission code, the four would drift — the classic shape is an API that lets
a viewer do what the page forbids, or an Agent tool that writes to whichever workspace the
browser tab happens to be showing. So there is one registry and one entry point:

* an application registers an :class:`AppManifest` and its :class:`Operation` s when it is
  installed; an operation's input and output are Pydantic models, so the HTTP body, the Agent's
  tool schema and the validation are the same object;
* every caller goes through :meth:`Capabilities.invoke`, which validates the input, resolves the
  **explicitly named** workspace against live membership and application access, checks the
  role the operation needs, applies the caller's idempotency key, writes a run record
  (``op_runs``), runs the handler and records the result and the events it emitted
  (``app_events``) — for transactional operations in the same database transaction as the
  business change itself, so a committed change never loses its event.

The workspace is never taken from the browser's per-tab binding: a run writes where its caller
said, and the caller's authority there is re-derived from live rows on every call.

What this module deliberately does not do: it grants nothing. An operation being registered, or
exposed to the Agent, says what *may* be asked; whether this person may ask it here is answered
by the checks above and then by the application's own object-level rules inside the handler.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from .errors import ServiceError
from .store import Store, new_id, now

log = logging.getLogger("synwevo.capabilities")

#: What an operation needs from the caller's role in the target workspace.
#: ``personal`` operations act on the caller's own records (the Agent's conversations) and take
#: no workspace at all.
NEEDS = ("read", "write", "manage", "personal")
READ_ROLES = frozenset({"owner", "admin", "editor", "viewer"})
WRITE_ROLES = frozenset({"owner", "admin", "editor"})
MANAGE_ROLES = frozenset({"owner", "admin"})

#: ``none`` reads; ``write`` changes records inside Synwevo; ``external`` reaches a person or a
#: system outside the caller (a message, a notification, a request sent to someone to fill in).
SIDE_EFFECTS = ("none", "write", "external")

#: How the personal Agent may use an operation. ``auto``: without asking (reads). ``confirm``:
#: only after the person confirms this exact call — operation, target, parameters — which the
#: Agent layer binds and re-checks. ``never``: not offered to the Agent at all (human decisions
#: that must be made in the application's own page, administration).
AGENT_POLICIES = ("auto", "confirm", "never")

#: Who called. Recorded on the run and in the audit trail; ``flow`` / ``agent`` / ``automation``
#: runs also carry a delegation naming the conversation, automation or flow run they belong to.
VIA = ("ui", "api", "flow", "agent", "automation", "system")

RUN_STATES = ("queued", "running", "waiting_input", "waiting_approval", "succeeded", "partial",
              "failed", "cancelled", "unknown")
TERMINAL_STATES = frozenset({"succeeded", "partial", "failed", "cancelled"})

MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024
MAX_IDEMPOTENCY_KEY = 200
#: Keys of machine callers (``via="api"``) are stored as ``api:<client>:<key>``; nobody else may
#: use the prefix, and a run is only ever replayed to the kind of caller that made it.
MACHINE_KEY_PREFIX = "api:"
#: A chain of runs started by runs (a flow step whose event triggers another flow…) stops here
#: rather than looping; see :meth:`Capabilities.invoke`'s ``depth``.
MAX_DEPTH = 8

Text = dict  # {"zh-Hans": ..., "zh-Hant": ..., "en": ...}


def _text(value: Text, *, what: str) -> Text:
    if not isinstance(value, dict) or set(value) != {"zh-Hans", "zh-Hant", "en"} or not all(
            isinstance(v, str) and v.strip() for v in value.values()):
        raise ValueError(f"{what} needs zh-Hans, zh-Hant and en text")
    return value


@dataclass(frozen=True)
class AppManifest:
    """An application as the platform lists it."""
    id: str
    name: str                                # the brand name, not translated ("Synwevo Forms")
    path: str                                # its entry on the workplace origin ("/forms")
    icon: str
    summary: Text
    object_kinds: tuple[str, ...] = ()
    scope: str = "workspace"                 # or "personal" (the Agent)
    version: str = "1"
    #: Where an object of each kind opens, as (kind, path template) with ``{workspace}`` and
    #: ``{id}`` — result cards and notifications link to the real record through these.
    object_paths: tuple[tuple[str, str], ...] = ()
    #: Its operations form a machine API: a client a person authorised may call them through
    #: ``/api/suite/v1`` with this application's scopes (:func:`machine_scope`). Off unless the
    #: application says so — an adapter for something that has its own API (Docs), or anything
    #: personal, must not gain a second door by being registered here.
    machine: bool = False

    def object_path(self, kind: str | None, object_id: str | None, workspace_id: str | None) -> str | None:
        for k, template in self.object_paths:
            if k == kind and object_id and ("{workspace}" not in template or workspace_id):
                return template.replace("{workspace}", workspace_id or "").replace("{id}", object_id)
        return None

    def __post_init__(self):
        _text(self.summary, what=f"app {self.id} summary")
        if self.scope not in ("workspace", "personal"):
            raise ValueError(f"app {self.id}: scope must be workspace or personal")
        if self.machine and self.scope != "workspace":
            raise ValueError(f"app {self.id}: only a workspace application can have a machine API")
        if not self.path.startswith("/"):
            raise ValueError(f"app {self.id}: path must start with /")


@dataclass(frozen=True)
class Operation:
    """One thing an application can do, with everything a caller needs to know to ask for it."""
    id: str                                  # stable: "forms.request.create"
    app: str
    title: Text
    summary: str                             # one line, English: API docs and the Agent's tool list
    input: type[BaseModel]
    handler: Callable[["Call", Any], Any]
    need: str = "read"
    side_effect: str = "none"
    agent: str = "auto"
    output: type[BaseModel] | None = None
    #: Whether a flow or an automation may run it with nobody present. False for decisions that
    #: belong to a person (approving a request): those need a person in the loop every time.
    unattended: bool = True
    #: Run inside the caller's database transaction (atomic with the run record and events).
    #: False for handlers that do their own transactions or cross into another service in this
    #: process (the Docs adapter): the run is recorded before and after instead, and a crash in
    #: between leaves it `unknown` for reconciliation rather than pretending either way.
    transactional: bool = True
    mode: str = "sync"                       # or "async": the handler queues work and returns
    cancellable: bool = False
    events: tuple[str, ...] = ()
    object_kind: str | None = None
    version: int = 1
    #: The handler writes into a workspace other than the one it was called in, and checks that
    #: target itself (`Capabilities.check_workspace`) — copying from where you can read into where
    #: you can write. Only then may a read-level operation have side effects.
    writes_elsewhere: bool = False
    #: Scopes of *other* applications a machine caller must also hold, because the handler reads
    #: there on its behalf ("forms.read" for importing Forms answers). A declaration for the
    #: contract and for refusing up front; the enforcement is `authorize`, which holds the inner
    #: call to the credential whether or not it was declared here.
    reaches: tuple[str, ...] = ()
    #: The handler refuses to run without the caller's idempotency key (it uses the key as its own
    #: retry identity — an upload's, say). Declared so the contract says what the handler does.
    requires_key: bool = False
    #: Recheck resource/field/source access before exposing a completed mutation's stored
    #: result. Called with the freshly authorized caller and the original run; must never
    #: repeat the mutation. Without this guard, old mutation payloads remain private.
    result_guard: Callable[["Call", dict], None] | None = None
    #: For an operation whose side effect is ``external`` in one direction only (switching a public
    #: link on, not off): given the call's input as written, whether this call reaches people
    #: outside the workspace — the outward send of docs/platform/data-flow.md rule 8. A value a
    #: flow has yet to fill in is a reference, so the answer for it must be True. None: every
    #: call of an ``external`` operation does. See :meth:`outward`.
    outward_when: Callable[[dict], bool] | None = None

    def __post_init__(self):
        _text(self.title, what=f"operation {self.id} title")
        if not self.id.startswith(self.app + "."):
            raise ValueError(f"operation {self.id} must be named under its application {self.app}")
        if self.need not in NEEDS:
            raise ValueError(f"operation {self.id}: need must be one of {NEEDS}")
        if self.side_effect not in SIDE_EFFECTS:
            raise ValueError(f"operation {self.id}: side_effect must be one of {SIDE_EFFECTS}")
        if self.agent not in AGENT_POLICIES:
            raise ValueError(f"operation {self.id}: agent must be one of {AGENT_POLICIES}")
        if self.side_effect != "none" and self.agent == "auto":
            raise ValueError(f"operation {self.id}: an operation with side effects cannot run "
                             "for the Agent without confirmation")
        if self.side_effect != "none" and self.need == "read" and not self.writes_elsewhere:
            raise ValueError(f"operation {self.id}: a write needs more than read access")
        if self.mode not in ("sync", "async"):
            raise ValueError(f"operation {self.id}: mode must be sync or async")
        if self.outward_when is not None and self.side_effect != "external":
            raise ValueError(f"operation {self.id}: only an external operation can reach outside in one direction")

    def outward(self, payload: Any) -> bool:
        """Whether this call, with this input, reaches people outside the workspace it runs in,
        so that content it carries from other workspaces is asked as an outward send."""
        if self.side_effect != "external":
            return False
        if self.outward_when is None:
            return True
        try:
            return bool(self.outward_when(payload if isinstance(payload, dict) else {}))
        except Exception:  # noqa: BLE001 — an input the predicate cannot read is asked as outward
            return True

    def input_schema(self) -> dict:
        return self.input.model_json_schema()

    def output_schema(self) -> dict | None:
        return self.output.model_json_schema() if self.output else None

    def describe(self) -> dict:
        """The public contract of this operation (API docs, catalogue, Agent tool list)."""
        return {"id": self.id, "app": self.app, "version": self.version, "title": self.title,
                "summary": self.summary, "need": self.need, "side_effect": self.side_effect,
                "agent": self.agent, "unattended": self.unattended, "mode": self.mode,
                "cancellable": self.cancellable, "events": list(self.events),
                "object_kind": self.object_kind, "input_schema": self.input_schema(),
                "output_schema": self.output_schema()}


@dataclass(frozen=True)
class EventType:
    """One kind of event an application lets a credential read about (`EventFeed`)."""
    type: str                                # "tables.record.changed"
    object_kind: str                         # what `object_id` names
    #: Identifiers in the event's data a reader needs to open the object — the table a record is
    #: in, the form a submission answers. Named one by one; nothing else in the data is served.
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class EventFeed:
    """What a machine caller may learn from an application's events, and who may learn it.

    An event is a fact about a record, so reading one is reading that record's existence. The
    application says which event types it serves and, when its records are not readable by
    everyone who can read the application in the workspace (a table with a reading role, a dataset
    whose sources the person lost), which of a batch this person may know about now. An
    application that registers nothing serves nothing, and so does an event type it left out.
    """
    types: tuple[EventType, ...]
    #: ``(user_id, workspace_id, events) -> ids of the events this person may know about now``.
    #: Called with the credential in effect, so what it reads in another application is held to
    #: the credential's scopes. None: every record is readable with the application.
    visible: Callable[[str, str, list[dict]], set[str]] | None = None

    def type(self, name: str) -> EventType | None:
        return next((t for t in self.types if t.type == name), None)


class Registry:
    """The process's applications and operations. Filled at install time, read everywhere."""

    def __init__(self):
        self._apps: dict[str, AppManifest] = {}
        self._ops: dict[str, Operation] = {}
        self._feeds: dict[str, EventFeed] = {}
        self._lock = threading.Lock()

    def register_app(self, manifest: AppManifest) -> None:
        with self._lock:
            existing = self._apps.get(manifest.id)
            if existing and existing != manifest:
                raise ValueError(f"application {manifest.id} is already registered differently")
            self._apps[manifest.id] = manifest

    def register(self, op: Operation) -> None:
        with self._lock:
            if op.app not in self._apps:
                raise ValueError(f"operation {op.id}: application {op.app} is not registered")
            existing = self._ops.get(op.id)
            if existing and existing is not op and existing != op:
                raise ValueError(f"operation {op.id} is already registered")
            self._ops[op.id] = op

    def register_events(self, app_id: str, feed: EventFeed) -> None:
        """The events of `app_id` a machine caller may read (see `EventFeed`)."""
        with self._lock:
            manifest = self._apps.get(app_id)
            if manifest is None or not manifest.machine:
                raise ValueError(f"application {app_id} has no machine API to serve events through")
            names = [t.type for t in feed.types]
            if len(set(names)) != len(names):
                raise ValueError(f"application {app_id}: an event type is declared twice")
            for t in feed.types:
                if not t.type.startswith(app_id + "."):
                    raise ValueError(f"event {t.type} must be named under its application {app_id}")
                if t.object_kind not in manifest.object_kinds:
                    raise ValueError(f"event {t.type}: {t.object_kind} is not one of {app_id}'s object kinds")
            existing = self._feeds.get(app_id)
            if existing is not None and existing != feed:
                raise ValueError(f"application {app_id} already declared its events")
            self._feeds[app_id] = feed

    def events(self, app_id: str) -> EventFeed | None:
        return self._feeds.get(app_id)

    def app(self, app_id: str) -> AppManifest | None:
        return self._apps.get(app_id)

    def apps(self) -> list[AppManifest]:
        return list(self._apps.values())

    def get(self, op_id: str) -> Operation | None:
        return self._ops.get(op_id)

    def operations(self, app_id: str | None = None) -> list[Operation]:
        return [op for op in self._ops.values() if app_id is None or op.app == app_id]

    def clear(self) -> None:
        """Tests only: a fresh registry per application instance."""
        with self._lock:
            self._apps.clear()
            self._ops.clear()
            self._feeds.clear()


# --------------------------------------------------------------------------------- schema
# The tables live with the other business-application tables (applied by `Store`).
from .suite_schema.runs import SCHEMA as SCHEMA_SQL  # noqa: E402



# --------------------------------------------------------------------------------- the call
@dataclass
class Call:
    """What a handler knows about the call it is serving.

    ``workspace_id`` and ``role`` are the resolved, checked answer — the handler never re-reads a
    workspace from anywhere else. Events go through :meth:`emit`, which buffers them for the
    invoker to write with the run's completion (inside the same transaction when the operation is
    transactional).
    """
    store: Store
    identity: Any
    op: Operation
    user: dict
    workspace_id: str | None
    role: str | None
    run_id: str
    via: str
    correlation_id: str
    principal: dict
    delegation: dict | None = None
    depth: int = 0
    request: Any = None
    capabilities: "Capabilities | None" = None
    events: list = field(default_factory=list)
    object_kind: str | None = None
    object_id: str | None = None

    @property
    def user_id(self) -> str:
        return self.user["id"]

    def emit(self, type_: str, *, object_kind: str | None = None, object_id: str | None = None,
             data: dict | None = None, workspace_id: str | None = None, version: int = 1) -> None:
        if self.op.events and type_ not in self.op.events:
            raise ValueError(f"operation {self.op.id} did not declare event {type_}")
        self.events.append({"type": type_, "version": version, "object_kind": object_kind,
                            "object_id": object_id, "data": data or {},
                            "workspace_id": workspace_id if workspace_id is not None else self.workspace_id})

    def touches(self, object_kind: str, object_id: str) -> None:
        """Name the primary object this run created or changed (shown on the run)."""
        self.object_kind, self.object_id = object_kind, object_id

    def can(self, need: str) -> bool:
        return role_allows(self.role, need)

    def require(self, need: str) -> None:
        refusal = role_refusal(self.role, need, self.workspace_id)
        if refusal:
            raise refusal


def machine_scope(op: Operation, manifest: AppManifest | None) -> str | None:
    """The API scope a machine caller (``via="api"``) needs for this operation, or None when no
    machine caller may ask for it at all.

    Derived from what the operation already declares, so the page, the Agent and the machine API
    cannot disagree about what kind of act it is: a decision that belongs to a person
    (``unattended=False``) needs the application's ``decide`` scope, which a person grants by name;
    a pure read needs ``read``; everything else ``write``. Personal operations, applications that
    did not opt in, and operations that write into a second workspace have no scope — a credential
    is bound to one workspace and to workspace business, never to a person's own records.
    Holding the scope is necessary, not sufficient: the person's live role and the application's
    own rules are checked exactly as for any other caller.
    """
    if manifest is None or not manifest.machine or manifest.id != op.app:
        return None
    if op.need == "personal" or op.writes_elsewhere:
        return None
    if not op.unattended:
        return f"{op.app}.decide"
    if op.need == "read" and op.side_effect == "none":
        return f"{op.app}.read"
    return f"{op.app}.write"


#: The credential a machine call is running under, while its handler runs: the scopes it holds and
#: the application of the operation it was admitted for. Whatever a handler reaches on the caller's
#: behalf (DataPrep importing Forms answers, re-checking an Extract source, another operation of its
#: own application) is still that credential asking, so `authorize` holds every inner operation to
#: the same scopes — the person's own authority is necessary there, not sufficient. Set by
#: :meth:`Capabilities.invoke`, by :meth:`Capabilities.resumed` for a step of deferred work, and by
#: :func:`machine_credential`; a background thread never inherits it.
_CREDENTIAL: contextvars.ContextVar[dict | None] = contextvars.ContextVar("synwevo_machine_credential", default=None)


@contextlib.contextmanager
def machine_credential(scopes, app: str):
    """Hold what follows to a credential's scopes, as :meth:`Capabilities.invoke` does while a
    handler runs — for the machine API's own reads of stored results, which re-check sources."""
    entered = _CREDENTIAL.set({"scopes": frozenset(scopes), "app": app})
    try:
        yield
    finally:
        _CREDENTIAL.reset(entered)


def _person_required(op_id: str) -> ServiceError:
    return ServiceError(403, "person_required",
                        "这个操作必须由本人在应用里亲自完成，不能通过接口凭据代为执行。", {"operation": op_id})


def _inner_refusal() -> ServiceError:
    """A credential may not have something a handler reached on its behalf — it lacks the scope, or
    no credential can ask for it at all. One refusal for both, and it says no more than that."""
    return ServiceError(403, "insufficient_scope", "该凭据的权限不足以读取这项内容所依赖的其他数据")


def _scope_refusal(op: Operation, manifest: AppManifest | None, held: frozenset, *,
                   within: str | None = None) -> ServiceError | None:
    """Why a credential holding `held` may not have this operation run for it, or None.

    `within` is set for what a handler reaches while serving a machine call (the application of the
    operation the call was admitted for). There the rule is the same with one allowance: an
    application's own handler reading its own records to do what was asked — a write or a decision
    — is covered by that write or decide scope. Anything else it reaches, in its own application
    or another, needs the scope that operation names; a handler cannot widen its caller by the way
    it happens to make the inner call. The refusal for an inner operation names neither it nor the
    scope it wanted, and does not say whether it was a scope that was missing or an operation only
    a person may run: what a record depends on is not the caller's to learn from an error. (What an
    operation always reads elsewhere is public — `Operation.reaches`, in the contract — and is
    refused by name before the call starts.)
    """
    required = machine_scope(op, manifest)
    if required is None:
        return _person_required(op.id) if within is None else _inner_refusal()
    if required in held:
        return None
    if within is not None:
        own_read = within == op.app and required == f"{op.app}.read"
        if own_read and (f"{op.app}.write" in held or f"{op.app}.decide" in held):
            return None
        return _inner_refusal()
    return ServiceError(403, "insufficient_scope", f"该凭据没有 {required} 权限",
                        {"required_scope": required, "operation": op.id})


def role_allows(role: str | None, need: str) -> bool:
    if need == "personal":
        return True
    if need == "read":
        return role in READ_ROLES
    if need == "write":
        return role in WRITE_ROLES
    if need == "manage":
        return role in MANAGE_ROLES
    return False


def role_refusal(role: str | None, need: str, workspace_id: str | None) -> ServiceError | None:
    if role_allows(role, need):
        return None
    if need == "write":
        return ServiceError(403, "read_only_role",
                            "你在该工作空间是查看者，只能查看，不能修改内容。需要编辑请联系工作空间管理员。",
                            {"workspace_id": workspace_id, "role": role})
    if need == "manage":
        return ServiceError(403, "manage_required", "只有该工作空间的所有者或管理员可以进行这个操作。",
                            {"workspace_id": workspace_id, "role": role})
    return ServiceError(403, "not_a_member", "你不是该工作空间的成员。", {"workspace_id": workspace_id})


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _j(value: Any) -> Any:
    """A JSON column's value. `Store` already decodes some column names (``output``, ``data`` …;
    `store.JSON_COLS`), so this accepts both the decoded value and the raw text."""
    if isinstance(value, (str, bytes)) and value:
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value if value != "" else None


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _validation_detail(err: ValidationError) -> dict:
    fields = []
    for e in err.errors()[:50]:
        fields.append({"loc": [str(p) for p in e.get("loc", ())], "type": e.get("type"),
                       "message": e.get("msg")})
    return {"fields": fields}


# --------------------------------------------------------------------------------- invoke
class Capabilities:
    """The one entry point. Construct once per process with the shared store and identity."""

    def __init__(self, store: Store, identity, registry: Registry | None = None):
        self.store = store
        self.identity = identity
        self.registry = registry if registry is not None else REGISTRY
        #: Called after a run's events are committed: ``listener(events)``. The flow runner and
        #: the notification fan-out subscribe here to wake up promptly; they still read the
        #: durable table, so a missed wake-up only costs latency.
        self.listeners: list[Callable[[list[dict]], None]] = []

    # ---------------------------------------------------------------- authorization
    def authorize(self, op: Operation, user_id: str, workspace_id: str | None) -> tuple[dict, str | None]:
        """The caller, and their role in the workspace, or a refusal that says why."""
        user = self.identity.get_user(user_id)
        if not user or user["status"] != "active":
            raise ServiceError(401, "account_unavailable", "账户不可用，请重新登录")
        manifest = self.registry.app(op.app)
        row = self.store.one("SELECT status FROM apps WHERE id=?", (op.app,))
        if not manifest or not row or row["status"] != "active":
            raise ServiceError(404, "app_unavailable", "该应用未启用或已停用")
        credential = _CREDENTIAL.get()
        if credential is not None:
            # Inside a machine call every authorization is the credential's too — the operation it
            # asked for, and whatever the handler reaches from there, however it makes that call.
            refusal = _scope_refusal(op, manifest, credential["scopes"], within=credential["app"])
            if refusal:
                raise refusal
        if op.need == "personal":
            if workspace_id:
                # A personal operation that accepted a workspace would be a way to smuggle one
                # into records that belong to the person; the Agent names targets per tool call.
                raise ServiceError(400, "workspace_not_applicable", "该操作属于个人，不针对某个工作空间")
            return user, None
        return user, self.check_workspace(user_id, op.app, workspace_id, op.need)

    def check_workspace(self, user_id: str, app_id: str, workspace_id: str | None, need: str) -> str:
        """This person's role in this workspace for this application, or a refusal. For handlers
        that act in a second workspace (copying a definition into another workspace): the target
        is checked exactly like the workspace the call was made in."""
        if not workspace_id or not isinstance(workspace_id, str):
            raise ServiceError(400, "workspace_required", "请指明要在哪个工作空间进行这个操作")
        from .apps import Apps
        ok, wid, reason = Apps(self.store, self.identity).check_access(user_id, app_id, workspace_id)
        if not ok or wid != workspace_id:
            raise ServiceError(403, "app_access_denied", reason or "你不能在该工作空间使用这个应用",
                               {"workspace_id": workspace_id, "app": app_id})
        from .organizations import workspace_access_failure
        if workspace_access_failure(self.store, workspace_id, user_id):
            # Platform applications (the Agent) answer for membership only; a workspace of a frozen
            # or deleted organization, or one whose organization the person left, is closed to
            # every application's operations alike, including its owners' policy settings.
            raise ServiceError(403, "app_access_denied", "该组织当前不可用", {"workspace_id": workspace_id, "app": app_id})
        member = self.identity.membership(workspace_id, user_id)
        role = member["role"] if member else None
        refusal = role_refusal(role, need, workspace_id)
        if refusal:
            raise refusal
        return role

    # ---------------------------------------------------------------- invoke
    def invoke(self, op_id: str, payload: Any, *, user_id: str, workspace_id: str | None = None,
               via: str = "ui", idempotency_key: str | None = None, correlation_id: str | None = None,
               parent_run_id: str | None = None, delegation: dict | None = None, depth: int = 0,
               request: Any = None, principal: dict | None = None,
               scopes: frozenset | None = None) -> dict:
        """Run one operation for one person in one explicitly named workspace.

        Returns ``{"run_id", "operation", "status", "output", "replayed", "workspace_id"}``.
        Raises :class:`ServiceError` for refusals and business errors — a failed run is recorded
        first (except for refusals before a run exists: bad input, no access, unknown operation),
        so the caller's error and the run history agree.

        ``via="api"`` is a credential: ``scopes`` is what it holds (:func:`machine_scope` says what
        each operation needs). A call made from inside such a call inherits them, and one with
        neither is refused — there is no unscoped machine caller.
        """
        op = self.registry.get(op_id)
        if op is None:
            raise ServiceError(404, "unknown_operation", "没有这个操作", {"operation": op_id})
        if via not in VIA:
            raise ServiceError(400, "bad_caller", "未知的调用方式", {"via": via})
        if depth > MAX_DEPTH:
            raise ServiceError(409, "run_depth_exceeded",
                               "自动触发的链条过深，已停止以免循环。请检查流程是否在更新自己触发的对象。",
                               {"depth": depth})
        if via in ("flow", "automation") and not op.unattended:
            raise ServiceError(403, "person_required",
                               "这个操作必须由本人在应用里亲自完成，不能由自动流程代为执行。",
                               {"operation": op_id})
        if via == "agent" and op.agent == "never":
            raise ServiceError(403, "not_for_agent", "这个操作不能由 Agent 执行，请在应用里亲自完成。",
                               {"operation": op_id})
        credential = _CREDENTIAL.get()
        if via == "api":
            # Whatever HTTP entry passed this along: a credential never reaches a person's own
            # records (conversations, spending, consents) or an application without a machine
            # API, and reaches the rest only with the scope each operation names.
            held = frozenset(scopes) if scopes is not None else None
            if credential is not None:
                # Inside a machine call the outer credential is the ceiling: a handler passing
                # scopes of its own can narrow what it asks with, never widen it.
                held = credential["scopes"] if held is None else held & credential["scopes"]
            if held is None:
                raise _person_required(op_id)
            refusal = _scope_refusal(op, self.registry.app(op.app), held)
            if refusal:
                if credential is not None:
                    # asked from inside a machine call: an inner refusal, however the handler asked
                    refusal = _inner_refusal()
                raise refusal
            if credential is None:
                credential = {"scopes": held, "app": op.app}
        elif scopes is not None:
            raise ValueError("scopes describe a credential: only via='api' carries them")
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= MAX_IDEMPOTENCY_KEY:
                raise ServiceError(400, "bad_idempotency_key", "幂等键须为 1–200 个字符")
            if via != "api" and idempotency_key.startswith(MACHINE_KEY_PREFIX):
                # Machine callers' keys are stored under this prefix, per client. A person (or a
                # flow, or the Agent) naming one would be writing into a client's namespace.
                raise ServiceError(400, "bad_idempotency_key",
                                   f"以 {MACHINE_KEY_PREFIX} 开头的幂等键保留给接口凭据，请换一个")

        entered = _CREDENTIAL.set(credential)
        try:
            return self._run(op, payload, user_id=user_id, workspace_id=workspace_id, via=via,
                             idempotency_key=idempotency_key, correlation_id=correlation_id,
                             parent_run_id=parent_run_id, delegation=delegation, depth=depth,
                             request=request, principal=principal)
        finally:
            _CREDENTIAL.reset(entered)

    def _run(self, op: Operation, payload: Any, *, user_id: str, workspace_id: str | None, via: str,
             idempotency_key: str | None, correlation_id: str | None, parent_run_id: str | None,
             delegation: dict | None, depth: int, request: Any, principal: dict | None) -> dict:
        """The body of :meth:`invoke`, entered with the caller's credential (if any) in effect."""
        op_id = op.id
        user, role = self.authorize(op, user_id, workspace_id)

        raw = payload if payload is not None else {}
        try:
            size = len(canonical(raw).encode("utf-8"))
        except (TypeError, ValueError):
            raise ServiceError(400, "invalid_input", "输入无法解析") from None
        if size > MAX_INPUT_BYTES:
            raise ServiceError(413, "input_too_large", "输入过大", {"limit_bytes": MAX_INPUT_BYTES})
        try:
            parsed = op.input.model_validate(raw)
        except ValidationError as err:
            raise ServiceError(422, "invalid_input", "输入不符合该操作的要求", _validation_detail(err)) from None
        clean = parsed.model_dump(mode="json")
        in_hash = fingerprint(clean)

        if idempotency_key is not None:
            prior = self._prior(op, user_id, workspace_id, idempotency_key, via, in_hash)
            if prior is not None:
                return self._replay(prior)

        from . import authority
        desc = principal or authority.describe(user=user, tenant=workspace_id)
        if delegation:
            desc = {**desc, "via": via, "delegation": delegation}
        run_id = new_id("run")
        corr = correlation_id or run_id
        ts = now()
        call = Call(store=self.store, identity=self.identity, op=op, user=user,
                    workspace_id=workspace_id, role=role, run_id=run_id, via=via,
                    correlation_id=corr, principal=desc, delegation=delegation, depth=depth,
                    request=request, capabilities=self)
        row = (run_id, op.id, op.app, op.version, workspace_id, user_id, canonical(desc), via,
               canonical(delegation) if delegation else None, corr, parent_run_id, depth,
               idempotency_key, in_hash, canonical(clean), ts, ts, ts)
        insert = ("INSERT INTO op_runs (id,op,app,op_version,workspace_id,user_id,principal,via,delegation,"
                  "correlation_id,parent_run_id,depth,idempotency_key,input_hash,input,status,created_at,"
                  "started_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'running',?,?,?)")

        if op.transactional:
            try:
                with self.store.tx() as t:
                    try:
                        t.execute(insert, row)
                    except Exception as exc:  # noqa: BLE001 — a concurrent twin with the same key won
                        if idempotency_key is not None and "UNIQUE" in str(exc):
                            raise _Replay() from None
                        raise
                    # The first check precedes the write lock. A revocation that wins that
                    # lock must also win over a request that was already waiting for it.
                    call.user, call.role = self.authorize(op, user_id, workspace_id)
                    result = op.handler(call, parsed)
                    output = self._finish(t, call, result)
                self._after(call)
                return {"run_id": run_id, "operation": op.id, "status": output[0], "output": output[1],
                        "replayed": False, "workspace_id": workspace_id}
            except _Replay:
                prior = self._prior(op, user_id, workspace_id, idempotency_key, via, in_hash)
                if prior is None:
                    raise ServiceError(409, "idempotency_conflict", "同一个幂等键已用于不同的参数。") from None
                return self._replay(prior)
            except ServiceError as err:
                self._record_failure(row, insert, err)
                raise
            except Exception:
                log.exception("operation %s failed (run %s)", op.id, run_id)
                self._record_failure(row, insert, ServiceError(500, "operation_failed", "操作失败，请稍后重试"))
                raise ServiceError(500, "operation_failed", "操作失败，请稍后重试", {"run_id": run_id}) from None

        # Two-phase: record, run, record. A crash in the middle leaves `running`, which
        # `reconcile_stale` turns into `unknown` — never silently into success or failure.
        try:
            with self.store.tx() as t:
                t.execute(insert, row)
        except Exception as exc:  # noqa: BLE001
            if idempotency_key is not None and "UNIQUE" in str(exc):
                prior = self._prior(op, user_id, workspace_id, idempotency_key, via, in_hash)
                if prior is not None:
                    return self._replay(prior)
                raise ServiceError(409, "idempotency_conflict", "同一个幂等键已用于不同的参数。") from None
            raise
        try:
            result = op.handler(call, parsed)
        except ServiceError as err:
            self._fail_existing(run_id, err)
            raise
        except Exception:
            log.exception("operation %s failed (run %s)", op.id, run_id)
            err = ServiceError(500, "operation_failed", "操作失败，请稍后重试", {"run_id": run_id})
            self._fail_existing(run_id, err)
            raise err from None
        with self.store.tx() as t:
            output = self._finish(t, call, result)
        self._after(call)
        return {"run_id": run_id, "operation": op.id, "status": output[0], "output": output[1],
                "replayed": False, "workspace_id": workspace_id}

    # ---------------------------------------------------------------- helpers
    def inspect(self, op_id: str, payload: Any, *, user_id: str,
                workspace_id: str | None = None) -> Any:
        """Re-read a synchronous read through its real ACLs without creating another run.

        Used for source checks and cached-result access. Application read maintenance (for
        example expiring an overdue request) still follows that application's own contract.
        This is an internal entry point, not a way to execute writes without an audit.
        """
        op = self.registry.get(op_id)
        if not op or op.side_effect != "none" or op.mode != "sync":
            raise ServiceError(409, "source_unverifiable", "无法重新核实来源权限")
        user, role = self.authorize(op, user_id, workspace_id)
        if len(canonical(payload).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ServiceError(413, "input_too_large", "输入过大")
        try:
            parsed = op.input.model_validate(payload)
        except ValidationError as err:
            raise ServiceError(422, "invalid_input", "输入不符合该操作的要求", _validation_detail(err)) from None
        call = Call(self.store, self.identity, op, user, workspace_id, role,
                    "", "system", "", {}, capabilities=self)
        result = _dump(op.handler(call, parsed))
        if call.events:
            raise ServiceError(409, "source_unverifiable", "读取操作不能生成未提交的事件")
        if op.output is not None and result is not None:
            result = op.output.model_validate(result).model_dump(mode="json")
        return result

    def _check_result(self, row: dict) -> None:
        op = self.registry.get(row["op"])
        if op is None:
            raise ServiceError(404, "app_unavailable", "该应用未启用或已停用")
        user, role = self.authorize(op, row["user_id"], row["workspace_id"])
        if op.side_effect == "none" and op.mode == "sync":
            current = self.inspect(op.id, _j(row["input"]), user_id=row["user_id"],
                                   workspace_id=row["workspace_id"])
            if fingerprint(current) != fingerprint(_j(row["output"])):
                raise ServiceError(409, "result_access_changed", "来源或权限已变化，请打开当前业务记录")
        elif op.result_guard is not None:
            call = Call(self.store, self.identity, op, user, row["workspace_id"], role,
                        row["id"], row["via"], row["correlation_id"], {}, capabilities=self)
            op.result_guard(call, row)
        else:
            raise ServiceError(409, "result_access_unverifiable", "请在应用中打开当前业务记录")

    def _prior(self, op: Operation, user_id: str, workspace_id: str | None, key: str, via: str,
               in_hash: str) -> dict | None:
        """The earlier run this call repeats, or None when the key is new. A key already used for
        other parameters, or by the other kind of caller, is a conflict — never a replay: a run a
        person made in the page is not an answer to a credential's call, and the reverse."""
        prior = self._idempotent(op, user_id, workspace_id, key)
        if prior is None:
            return None
        if (prior["via"] == "api") != (via == "api"):
            raise ServiceError(409, "idempotency_conflict", "这个幂等键已被另一种调用方式使用，请换一个幂等键。")
        if prior["input_hash"] != in_hash:
            raise ServiceError(409, "idempotency_conflict",
                               "同一个幂等键已用于不同的参数。请换一个幂等键，或使用相同的参数重试。",
                               {"run_id": prior["id"]})
        return prior

    def _idempotent(self, op: Operation, user_id: str, workspace_id: str | None, key: str) -> dict | None:
        return self.store.one(
            "SELECT * FROM op_runs WHERE user_id=? AND op=? AND COALESCE(workspace_id,'')=? AND idempotency_key=?",
            (user_id, op.id, workspace_id or "", key))

    def _replay(self, prior: dict) -> dict:
        if prior["status"] == "failed":
            # A failed attempt is not a result to hand back forever: the business error is
            # repeated so the caller sees the same answer, and a new key starts a new attempt.
            raise ServiceError(409, "idempotent_failure",
                               "该请求此前已失败；重试不会再次执行",
                               {"run_id": prior["id"], "code": prior["error_code"]})
        try:
            self._check_result(prior)
        except ServiceError:
            # Keep the receipt, but never replay a side effect to reconstruct its payload.
            raise ServiceError(409, "run_result_unavailable", "该请求已执行，当前无法读取原结果。请核实业务记录，不要重复提交。",
                               {"run_id": prior["id"], "status": prior["status"]}) from None
        return {"run_id": prior["id"], "operation": prior["op"], "status": prior["status"],
                "output": _j(prior["output"]),
                "replayed": True, "workspace_id": prior["workspace_id"]}

    def _finish(self, t, call: Call, result: Any) -> tuple[str, Any]:
        status = "succeeded"
        output = _dump(result)
        if isinstance(output, dict) and output.get("__run_status__"):
            output = dict(output)
            status = output.pop("__run_status__")
            if status not in RUN_STATES:
                raise ValueError(f"handler returned unknown run status {status}")
        if call.op.output is not None and output is not None and status in ("succeeded", "partial"):
            output = call.op.output.model_validate(output).model_dump(mode="json")
        encoded = canonical(output) if output is not None else None
        if encoded is not None and len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ServiceError(500, "output_too_large", "操作结果过大，已拒绝记录")
        ts = now()
        t.execute("UPDATE op_runs SET status=?, output=?, object_kind=?, object_id=?, finished_at=?, "
                  "updated_at=? WHERE id=?",
                  (status, encoded, call.object_kind, call.object_id,
                   ts if status in TERMINAL_STATES else None, ts, call.run_id))
        self._write_events(t, call)
        if call.op.side_effect != "none" or call.via != "ui":
            self._audit(t, call, status)
        return status, output

    def _write_events(self, t, call: Call) -> None:
        ts = now()
        written = []
        for ev in call.events:
            eid = new_id("evt")
            t.execute("INSERT INTO app_events (id,type,version,app,workspace_id,user_id,object_kind,object_id,"
                      "run_id,correlation_id,causation_id,depth,data,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, ev["type"], ev["version"], call.op.app, ev["workspace_id"], call.user_id,
                       ev["object_kind"], ev["object_id"], call.run_id, call.correlation_id, call.run_id,
                       call.depth, canonical(ev["data"]), ts))
            written.append({**ev, "id": eid, "app": call.op.app, "run_id": call.run_id,
                            "correlation_id": call.correlation_id, "depth": call.depth})
        call.events = written

    def _audit(self, t, call: Call, status: str) -> None:
        meta = {"run_id": call.run_id, "via": call.via, "operation": call.op.id}
        if call.delegation:
            meta["delegation"] = call.delegation
        if call.correlation_id != call.run_id:
            meta["correlation_id"] = call.correlation_id
        state = getattr(getattr(call.request, "state", None), "request_id", None)
        t.execute("INSERT INTO audit_events (id,at,actor_kind,actor_id,workspace_id,action,object_kind,"
                  "object_id,result,request_id,ip,meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (new_id("ae"), now(), "user", call.user_id, call.workspace_id, call.op.id,
                   call.object_kind, call.object_id, "ok" if status != "failed" else "error", state, None,
                   canonical(meta)))

    def _after(self, call: Call) -> None:
        # Listeners (flow wake-ups) act for whoever owns what they wake, never for this caller:
        # nothing they start may run under — or be refused by — this call's credential.
        outside = _CREDENTIAL.set(None)
        try:
            self.announce(call.events)
        finally:
            _CREDENTIAL.reset(outside)

    def announce(self, events: list[dict]) -> None:
        """Wake the listeners for events that are committed (see ``listeners``)."""
        if events:
            for fn in list(self.listeners):
                try:
                    fn(events)
                except Exception:  # noqa: BLE001 — a listener is a wake-up, not part of the result
                    log.exception("capability event listener failed")

    def _record_failure(self, row: tuple, insert: str, err: ServiceError) -> None:
        """The transaction that tried the work rolled back; record the attempt on its own."""
        ts = now()
        try:
            with self.store.tx() as t:
                t.execute(insert, row)
                t.execute("UPDATE op_runs SET status='failed', error_code=?, error_message=?, error_detail=?, "
                          "finished_at=?, updated_at=? WHERE id=?",
                          (err.code, err.message[:500], canonical(err.detail)[:4000], ts, ts, row[0]))
        except Exception as exc:  # noqa: BLE001
            if "UNIQUE" not in str(exc):
                log.exception("could not record failed run %s", row[0])

    def _fail_existing(self, run_id: str, err: ServiceError) -> None:
        ts = now()
        self.store.execute("UPDATE op_runs SET status='failed', error_code=?, error_message=?, error_detail=?, "
                           "finished_at=?, updated_at=? WHERE id=?",
                           (err.code, err.message[:500], canonical(err.detail)[:4000], ts, ts, run_id))

    # ---------------------------------------------------------------- runs
    def run(self, run_id: str, *, user_id: str) -> dict:
        """One run, for the person it ran for. Other people get the same answer as a missing run."""
        row = self.store.one("SELECT * FROM op_runs WHERE id=?", (run_id,))
        if not row or row["user_id"] != user_id:
            raise ServiceError(404, "run_not_found", "运行记录不存在")
        return self.present_run(row, user_id=user_id)

    def present_run(self, row: dict, *, user_id: str) -> dict:
        """History is a receipt, not a second unguarded business-data store."""
        if row["user_id"] != user_id:
            raise ServiceError(404, "run_not_found", "运行记录不存在")
        out = public_run(row)
        # Inputs may hold personal text, deleted memories or former source content. Show
        # parameters in the owning application's authorized view instead of generic history.
        out["input"] = None
        out["error"] = {"code": row["error_code"]} if row.get("error_code") else None
        try:
            self._check_result(row)
        except ServiceError:
            out.update(output=None, object=None, delegation=None, snapshot_hidden=True)
        else:
            out["snapshot_hidden"] = False
        return out

    def update_run(self, run_id: str, status: str, *, output: Any = None, error: ServiceError | None = None) -> None:
        """For asynchronous operations: the worker reports progress or the outcome."""
        if status not in RUN_STATES:
            raise ValueError(status)
        ts = now()
        self.store.execute(
            "UPDATE op_runs SET status=?, output=COALESCE(?, output), error_code=?, error_message=?, "
            "finished_at=?, updated_at=? WHERE id=?",
            (status, canonical(_dump(output)) if output is not None else None,
             error.code if error else None, error.message[:500] if error else None,
             ts if status in TERMINAL_STATES else None, ts, run_id))

    # ---------------------------------------------------------------- deferred work
    def standing(self, run_id: str | None) -> dict | None:
        """The credential behind a run, as it stands **now**, for work that continues after the
        request that started it: a queued job, a wait, a retry.

        None when a person started the run themselves (the page): their work goes on under their
        own live authority, as before. For a run a client started on someone's behalf it is the
        scopes that client's grant still holds — and a refusal once the grant is gone. Queued
        work must not outlive the authorization that asked for it: the person withdrew the client,
        the client was disabled, its registration no longer includes what the operation needs, or
        its tokens were revoked (a refresh rotation is not a revocation; an access token merely
        expiring is not one either). The same rule Docs applies to its jobs (`authority.check`).

        Whether a client started the run is read from how the run was made (``via``, the recorded
        delegation), not from the stored descriptor alone: a descriptor that is missing, unreadable
        or names someone else must not turn a client's run into a person's own. Such a run is
        refused (``unknown_principal``), as `authority.check` refuses a descriptor it cannot read.

        The person's own access is not asked here; every worker already asks that separately.
        """
        row = self.store.one("SELECT op, app, via, user_id, workspace_id, principal, delegation FROM op_runs "
                             "WHERE id=?", (run_id,)) if run_id else None
        if not row:
            return None
        desc, started = _j(row["principal"]), _j(row["delegation"])
        delegated = isinstance(desc, dict) and desc.get("kind") == "delegated"
        by_client = isinstance(started, dict) and started.get("kind") == "oauth_client"
        if row["via"] != "api" and not by_client and not delegated:
            return None
        stopped = "后续步骤已停止"
        if (not delegated or (by_client and started.get("client_id") != desc.get("client_id"))
                or desc.get("user_id") != row["user_id"] or desc.get("tenant") != row["workspace_id"]):
            raise ServiceError(403, "unknown_principal", f"无法确认发起这项工作的应用凭据，{stopped}", {"run_id": run_id})
        scopes = self.grant_scopes(desc, detail={"run_id": run_id})
        op = self.registry.get(row["op"])
        required = machine_scope(op, self.registry.app(row["app"])) if op is not None else None
        if required is None or required not in scopes:
            raise ServiceError(403, "scope_withdrawn", f"发起这项工作的应用已不再持有所需权限，{stopped}",
                               {"run_id": run_id, "required_scope": required})
        return {"scopes": scopes, "app": row["app"]}

    def grant_scopes(self, desc: dict, *, detail: dict | None = None, renewable: bool = False) -> frozenset:
        """The business applications' scopes the grant behind a delegated descriptor holds **now**:
        those of its newest token that the client is still registered for. A refusal once the grant
        has ended — the client is gone or disabled (``credential_revoked``), or its tokens were
        revoked (``grant_revoked``; a refresh rotation is not a revocation, an access token merely
        expiring is not one either). For anything that acts for a grant after the request that
        asked for it: queued work (`standing`), a webhook subscription.

        ``renewable`` is for what acts for a grant with no end of its own (a subscription): the
        grant must be one the client could still renew at this moment. Ending a sign-in, an
        account or an entitlement reaches a token when its client next refreshes it, and so
        reaches a client that pulls within one access-token lifetime; something that pushes
        would never meet that refresh, so what it would be refused for is asked here instead:
        no refresh token (``grant_not_renewable``), the refresh token has run out
        (``grant_expired``), the sign-in the grant came from was ended (``session_ended``), the
        person may no longer use the client's application in that workspace
        (``entitlement_revoked``)."""
        stopped = "后续步骤已停止"
        from .apps import Apps
        from .oidc import Clients, SUITE_API_SCOPES
        client = self.store.one("SELECT client_id, app_id, scopes, api_scopes, disabled_at FROM oauth_clients "
                                "WHERE client_id=?", (desc.get("client_id"),))
        if not client or client["disabled_at"]:
            raise ServiceError(403, "credential_revoked", f"发起这项工作的应用已停用，{stopped}", detail)
        family = desc.get("family_id")
        last = self.store.one(
            "SELECT revoked_at, revoked_reason, scope, refresh_hash, refresh_expires_at, central_session_id "
            "FROM oauth_tokens WHERE (family_id=? OR id=?) AND client_id=? AND user_id=? AND workspace_id=? "
            "ORDER BY issued_at DESC, (revoked_at IS NULL) DESC LIMIT 1",
            (family, family, client["client_id"], desc.get("user_id"), desc.get("tenant"))) if family else None
        if not last or (last["revoked_at"] and (last["revoked_reason"] or "") != "rotated"):
            raise ServiceError(403, "grant_revoked", f"发起这项工作的应用授权已撤回，{stopped}", detail)
        if renewable:
            if last["revoked_at"] or not last["refresh_hash"] or last["refresh_expires_at"] is None:
                # No refresh token to renew it with (or the newest one was spent and nothing came of it).
                raise ServiceError(403, "grant_not_renewable", "这项授权不能续期（申请授权时需要 offline_access）", detail)
            if last["refresh_expires_at"] <= now():
                raise ServiceError(403, "grant_expired", f"发起这项工作的应用授权已过期，{stopped}", detail)
            if last["central_session_id"]:
                # An explicit end only, as at a refresh: an idle or expired sign-in is not one.
                session = self.store.one("SELECT revoked_at FROM sessions WHERE id=?", (last["central_session_id"],))
                if session and session["revoked_at"]:
                    raise ServiceError(403, "session_ended", f"这项授权所依据的登录已结束，{stopped}", detail)
            entitled, _, _ = Apps(self.store, self.identity).check_access(
                desc.get("user_id"), client["app_id"], desc.get("tenant"))
            if not entitled:
                raise ServiceError(403, "entitlement_revoked", f"本人已不能在该工作空间使用这个应用，{stopped}", detail)
        registered = Clients.allowed_scopes(client)
        return frozenset(sc for sc in (last["scope"] or "").split() if sc in SUITE_API_SCOPES and sc in registered)

    @contextlib.contextmanager
    def resumed(self, run_id: str | None):
        """A step of deferred work, taken under the credential that started the run — refused if
        that credential no longer stands, and bounded by what it holds now for whatever the step
        reads in another application. Workers wrap each checkpoint (and the transaction that
        publishes a result) in this, so a revocation takes effect at the next step, and nothing
        is written after it."""
        credential = self.standing(run_id)
        if credential is None:
            yield
            return
        entered = _CREDENTIAL.set(credential)
        try:
            yield
        finally:
            _CREDENTIAL.reset(entered)

    def reconcile_stale(self, *, older_than_s: float = 600) -> int:
        """Two-phase runs left `running` by a crash become `unknown`: the side effect may or may not
        have happened, and saying either would be a guess. The owner of the operation (or a
        person) resolves them; nothing retries them blindly."""
        cutoff = now() - older_than_s
        return self.store.execute(
            "UPDATE op_runs SET status='unknown', updated_at=? WHERE status='running' AND updated_at<?",
            (now(), cutoff))

    # ---------------------------------------------------------------- events
    def events_after(self, seq: int, *, types: tuple[str, ...] | None = None, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        if types:
            marks = ",".join("?" for _ in types)
            rows = self.store.all(f"SELECT * FROM app_events WHERE seq>? AND type IN ({marks}) ORDER BY seq LIMIT ?",
                                  (seq, *types, limit))
        else:
            rows = self.store.all("SELECT * FROM app_events WHERE seq>? ORDER BY seq LIMIT ?", (seq, limit))
        for r in rows:
            r["data"] = _j(r["data"]) or {}
        return rows

    def cursor(self, consumer: str) -> int:
        row = self.store.one("SELECT seq FROM app_event_cursors WHERE consumer=?", (consumer,))
        if row:
            return int(row["seq"])
        # A new consumer starts at the present, not at the beginning of history: replaying every
        # past event into a flow that was just created would act on things that happened before
        # anyone asked it to.
        top = self.store.one("SELECT COALESCE(MAX(seq), 0) AS s FROM app_events")
        self.store.execute("INSERT OR IGNORE INTO app_event_cursors (consumer, seq, updated_at) VALUES (?,?,?)",
                           (consumer, int(top["s"]), now()))
        return int(top["s"])

    def advance(self, t, consumer: str, seq: int) -> None:
        t.execute("INSERT INTO app_event_cursors (consumer, seq, updated_at) VALUES (?,?,?) "
                  "ON CONFLICT(consumer) DO UPDATE SET seq=MAX(seq, excluded.seq), updated_at=excluded.updated_at",
                  (consumer, seq, now()))

    # ---------------------------------------------------------------- catalogue
    def catalogue(self, user_id: str) -> list[dict]:
        """The applications this person can open, and in which workspaces — for the app directory,
        search and the Agent's tool discovery. Filtering here is a convenience; every call is
        authorized again by :meth:`invoke`."""
        from .apps import Apps
        apps = Apps(self.store, self.identity)
        spaces = [w for w in self.identity.workspaces_for(user_id) if w.get("status") == "active"]
        out = []
        for m in self.registry.apps():
            row = self.store.one("SELECT status, launch_url FROM apps WHERE id=?", (m.id,))
            if not row or row["status"] != "active":
                continue
            if m.scope == "personal":
                ok, _, _ = apps.check_access(user_id, m.id)
                if ok:
                    out.append({**manifest_json(m), "workspaces": []})
                continue
            usable = []
            for w in spaces:
                ok, wid, _ = apps.check_access(user_id, m.id, w["id"])
                if ok:
                    usable.append({"id": w["id"], "name": w["name"], "role": w.get("role")})
            if usable:
                out.append({**manifest_json(m), "workspaces": usable})
        return out


class _Replay(Exception):
    pass


def record_event(t, *, type_: str, app: str, workspace_id: str | None, object_kind: str | None,
                 object_id: str | None, data: dict | None = None, user_id: str | None = None,
                 run_id: str | None = None, correlation_id: str | None = None,
                 causation_id: str | None = None, depth: int = 0, version: int = 1) -> dict:
    """Write one event inside the caller's transaction, for business changes that do not come
    through :meth:`Capabilities.invoke` — a person without an account submitting a public form,
    a sweeper expiring a request. Same table, same shape, so every consumer reads one stream."""
    eid = new_id("evt")
    ts = now()
    t.execute("INSERT INTO app_events (id,type,version,app,workspace_id,user_id,object_kind,object_id,"
              "run_id,correlation_id,causation_id,depth,data,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (eid, type_, version, app, workspace_id, user_id, object_kind, object_id, run_id,
               correlation_id, causation_id, depth, canonical(data or {}), ts))
    return {"id": eid, "type": type_, "version": version, "app": app, "workspace_id": workspace_id,
            "object_kind": object_kind, "object_id": object_id, "data": data or {}, "run_id": run_id,
            "correlation_id": correlation_id, "depth": depth}


def manifest_json(m: AppManifest) -> dict:
    return {"id": m.id, "name": m.name, "path": m.path, "icon": m.icon, "summary": m.summary,
            "object_kinds": list(m.object_kinds), "scope": m.scope, "version": m.version}


def public_run(row: dict) -> dict:
    return {"id": row["id"], "operation": row["op"], "app": row["app"], "workspace_id": row["workspace_id"],
            "status": row["status"], "via": row["via"],
            "delegation": _j(row.get("delegation")),
            "correlation_id": row["correlation_id"], "parent_run_id": row["parent_run_id"],
            "input": _j(row.get("input")),
            "output": _j(row.get("output")),
            "error": ({"code": row["error_code"], "message": row["error_message"],
                       "detail": _j(row.get("error_detail")) or {}}
                      if row.get("error_code") else None),
            "object": ({"kind": row["object_kind"], "id": row["object_id"]} if row.get("object_id") else None),
            "created_at": row["created_at"], "finished_at": row["finished_at"]}


#: The process registry. Applications register into it at install; tests build their own.
REGISTRY = Registry()
