"""The machine API's contract (OpenAPI 3.1), built from the registry the server runs on.

One path per operation, with that operation's own input and output models as its schemas — the
same Pydantic models `Capabilities.invoke` validates with, so the document cannot describe a
request the server would parse differently. `docs/integration/suite-openapi.json` is this
function's output for the composed deployment (`python -m synwevo_server.suite_contract`: the
applications plus the operations only the composition root registers); a cross-module test rebuilds
it and fails when the two differ.

What the document deliberately does not contain: the page-only routes under `/api/suite/`, the
public fill-in links, and any operation a credential may not ask for (the personal Agent among
them) — an integration reading it should find nothing it cannot call.
"""
from __future__ import annotations

from synwevo_core.capabilities import RUN_STATES, TERMINAL_STATES, Operation, canonical
from synwevo_core.http import SUITE_V1
from synwevo_core.oidc import SUITE_API_SCOPES, suite_resource

from . import machine_events, machine_webhooks
from .machine import MAX_CALLER_KEY, idempotency_rule, machine_operations

VERSION = "1.0.0"
SERVER = "https://workplace.synwevo.com"
ISSUER = "https://accounts.synwevo.com"

#: What a person is told each scope allows (the consent page says the same, in three languages).
SCOPE_SUMMARY = {
    "forms.read": "Read forms, fill-in requests and submissions",
    "forms.write": "Create, change and publish forms, set public links, send and close fill-in requests, review submissions",
    "tables.read": "Read tables, records and change history",
    "tables.write": "Add, change and archive records, import and export, save views; change table structure where the person's role allows",
    "dataprep.read": "Read datasets, recipes and run results",
    "dataprep.write": "Import data, save and publish recipes, start and cancel runs",
    "extract.read": "Read extraction templates, tasks and results",
    "extract.write": "Create and publish extraction templates, create and cancel extraction tasks",
    "extract.decide": "Correct and confirm extraction results as the person (human review)",
    "approvals.read": "Read approval templates, requests, timelines and eligible approvers",
    "approvals.write": "Start, revise and withdraw approval requests, add comments; manage templates where the person's role allows",
    "approvals.decide": "Decide approvals as the person, hand over their tasks, repair a stalled stage where their role allows",
}

_ERRORS = (("400", "Bad Request"), ("401", "Unauthorized"), ("403", "Forbidden"), ("404", "Not Found"),
           ("409", "Conflict"), ("422", "Unprocessable Content"), ("429", "Too Many Requests"),
           ("500", "Internal Server Error"))
#: Not one of the refusals any operation may give: only the routes that list it answer it.
_UNAVAILABLE = ("503", "Service Unavailable")
_UNAVAILABLE_MEANS = ("Service Unavailable: journal_unavailable. A change that takes something back (a pause, another "
                      "address, another secret, a removal) is recorded durably before it is made; the record could not "
                      "be written, so nothing was changed. Repeat the call.")


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


class _Schemas:
    """Component schemas, named per application; one name never means two different things."""

    def __init__(self):
        self.out: dict[str, dict] = {}

    def add(self, model, *, app: str, name: str, op_id: str) -> dict:
        """Register a model under `name` (and what it refers to under `<app>.<Model>`)."""
        schema = self._hoist(model, f"{app}.")
        if schema is None:                                  # an inner name already means something else
            schema = self._hoist(model, f"{op_id}.", force=True)
        self.out[name] = schema
        return _ref(name)

    def _hoist(self, model, prefix: str, *, force: bool = False) -> dict | None:
        schema = model.model_json_schema(ref_template=f"#/components/schemas/{prefix}{{model}}")
        defs = schema.pop("$defs", {})
        named = {prefix + k: v for k, v in defs.items()}
        if not force and any(k in self.out and canonical(self.out[k]) != canonical(v) for k, v in named.items()):
            return None
        self.out.update(named)
        return schema


_ERROR_NAMES = {code: "".join(word.capitalize() for word in description.split())
                for code, description in (*_ERRORS, _UNAVAILABLE)}


def _headers(code: str) -> dict:
    h = {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}}
    if code.startswith("2") or code == "429":
        h["RateLimit"] = {"$ref": "#/components/headers/RateLimit"}
        h["RateLimit-Policy"] = {"$ref": "#/components/headers/RateLimitPolicy"}
    if code == "429":
        h["Retry-After"] = {"$ref": "#/components/headers/RetryAfter"}
    return h


def _errors(codes=None) -> dict:
    """Every refusal has the same two bodies; said once, under components."""
    return {code: {"$ref": f"#/components/responses/{_ERROR_NAMES[code]}"}
            for code, _ in (_ERRORS if codes is None else (*_ERRORS, _UNAVAILABLE)) if codes is None or code in codes}


def _error_responses() -> dict:
    return {_ERROR_NAMES[code]: {"description": description, "headers": _headers(code), "content": {
        "application/json": {"schema": _ref("SuiteError")},
        "application/problem+json": {"schema": _ref("SuiteProblem")}}}
        for code, description in (*_ERRORS, (_UNAVAILABLE[0], _UNAVAILABLE_MEANS))}


def _common(operation: dict, *, scopes: list[str]) -> dict:
    operation["security"] = [{"suiteToken": scopes}]
    operation["x-synwevo-resource"] = suite_resource()
    operation["x-synwevo-identity"] = "delegated"
    operation.setdefault("parameters", []).append({"$ref": "#/components/parameters/RequestId"})
    return operation


def _operation(op: Operation, scope: str, schemas: _Schemas) -> dict:
    name = op.id
    body = {"type": "object", "additionalProperties": False, "properties": {
        "input": schemas.add(op.input, app=op.app, name=f"{name}.input", op_id=op.id),
        "workspace_id": {"type": ["string", "null"], "maxLength": 100,
                         "description": "Optional. The credential is bound to one workspace; any other value is refused."},
        "idempotency_key": {"type": ["string", "null"], "minLength": 1, "maxLength": MAX_CALLER_KEY,
                            "pattern": "^[!-~]+$", "description": "Same as the Idempotency-Key header."},
        "correlation_id": {"type": ["string", "null"], "maxLength": 100}}}
    output = schemas.add(op.output, app=op.app, name=f"{name}.output", op_id=op.id) if op.output else {}
    def result(states, shape):
        return {"type": "object", "required": ["run_id", "operation", "status", "output", "replayed", "workspace_id"],
                "properties": {"run_id": {"type": "string"}, "operation": {"const": op.id},
                               "status": {"enum": sorted(states)}, "output": shape,
                               "replayed": {"type": "boolean"}, "workspace_id": {"type": "string"}}}

    def answer(code, description, schema):
        return {"description": description, "headers": _headers(code),
                "content": {"application/json": {"schema": schema}}}

    rule = idempotency_rule(op)
    if op.mode == "sync":
        # A failure is an error response, never a 200 that says "failed".
        responses = {"200": answer("200", "Done", result({"succeeded", "partial"}, output))}
    else:
        # The handler queues or waits. It may also settle at once; the output's shape then depends
        # on how it settled, so it is not constrained here — read the object the run names.
        responses = {"200": answer("200", "Settled", result(TERMINAL_STATES - {"failed"}, {})),
                     "202": answer("202", "Accepted, not finished: read the run or the object it names",
                                   result(set(RUN_STATES) - TERMINAL_STATES, {}))}
    responses.update(_errors())
    out = {"operationId": op.id, "summary": op.summary, "tags": [op.app],
           "x-synwevo-need": op.need, "x-synwevo-side-effect": op.side_effect, "x-synwevo-mode": op.mode,
           "x-synwevo-idempotency": rule, "x-synwevo-events": list(op.events),
           "x-synwevo-operation-version": op.version,
           "requestBody": {"required": True, "content": {"application/json": {"schema": body}}},
           "responses": responses}
    if op.object_kind:
        out["x-synwevo-object-kind"] = op.object_kind
    if rule != "none":
        out["parameters"] = [{"$ref": "#/components/parameters/"
                                      + ("IdempotencyKeyRequired" if rule == "required" else "IdempotencyKey")}]
    out["x-synwevo-scope"] = scope
    return _common(out, scopes=[scope, *op.reaches])


#: The routes that move bytes: the applications' own upload, download and export handlers, served a
#: second time under the machine prefix (`machine.acting`). (app, method, path, id in the contract,
#: scope, what it is, kind) — kind: "upload" (multipart `file`, JSON answer), "upload-keyed" (the
#: same, and the handler will not run without an Idempotency-Key), "file" (bytes), "json-file" (a
#: JSON attachment).
FILE_ROUTES = (
    ("forms", "get", "/forms/files/{file_id}", "forms.file.download", "forms.read",
     "Download an attachment of a submission (the person's export right applies)", "file"),
    ("tables", "post", "/tables/{table_id}/imports", "tables.import.upload", "tables.write",
     "Upload a CSV or XLSX to stage an import; returns the import ticket for tables.import.get / apply", "upload"),
    ("tables", "get", "/tables/exports/{export_id}", "tables.export.download", "tables.read",
     "Download an export created with tables.export.create: the records the person may read now", "file"),
    ("dataprep", "post", "/dataprep/files", "dataprep.file.upload", "dataprep.write",
     "Upload a CSV or XLSX through the platform's upload check; returns the file for dataprep.dataset.import",
     "upload-keyed"),
    ("dataprep", "get", "/dataprep/datasets/{dataset_id}/export", "dataprep.dataset.export", "dataprep.read",
     "Export a dataset as csv, xlsx or json; its sources are re-checked as this credential", "file"),
    ("dataprep", "get", "/dataprep/runs/{run_id}/issues", "dataprep.run.issues.export", "dataprep.read",
     "Every issue of a run, as a JSON attachment", "json-file"),
    ("extract", "post", "/extract/files", "extract.file.upload", "extract.write",
     "Upload a PDF, image or CSV/TSV through the platform's upload check; returns the file for extract.task.create",
     "upload-keyed"),
    ("extract", "get", "/extract/files/{file_id}", "extract.file.download", "extract.read",
     "Download an uploaded original (the person's export right applies)", "file"),
    ("extract", "get", "/extract/tasks/{task_id}/export", "extract.task.export", "extract.read",
     "A confirmed result revision, as a JSON attachment", "json-file"),
)
_FILE_QUERY = {"/dataprep/datasets/{dataset_id}/export": [
                   {"name": "format", "in": "query", "required": False, "schema": {"enum": ["csv", "xlsx", "json"]}}],
               "/extract/tasks/{task_id}/export": [
                   {"name": "revision", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1}}]}


def _file_route(app: str, path: str, name: str, scope: str, summary: str, kind: str) -> dict:
    import re
    parameters = [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                  for name in re.findall(r"{(\w+)}", path)]
    parameters.append({"name": "workspace_id", "in": "query", "required": True, "schema": {"type": "string"},
                       "description": "The credential's workspace; any other value is refused."})
    parameters += _FILE_QUERY.get(path, [])
    out = {"operationId": name, "summary": summary, "tags": [app], "x-synwevo-scope": scope,
           "parameters": parameters}
    if kind.startswith("upload"):
        if kind == "upload-keyed":
            parameters.append({"$ref": "#/components/parameters/IdempotencyKeyRequired"})
        out["requestBody"] = {"required": True, "content": {"multipart/form-data": {"schema": {
            "type": "object", "required": ["file"], "properties": {"file": {"type": "string", "format": "binary"}}}}}}
        ok = {"application/json": {"schema": {"type": "object"}}}
    elif kind == "json-file":
        ok = {"application/json": {"schema": {}}}
    else:
        ok = {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
    out["responses"] = {"200": {"description": "OK", "headers": _headers("200"), "content": ok},
                        **_errors({"400", "401", "403", "404", "409", "422", "429", "500"})}
    return _common(out, scopes=[scope])


#: Signed webhooks: (method, path under /webhooks/subscriptions, operationId, summary, request body
#: schema, response schema, success status, extra parameters).
_SUB = [{"name": "sid", "in": "path", "required": True, "schema": {"type": "string"}}]
_DEL = _SUB + [{"name": "did", "in": "path", "required": True, "schema": {"type": "string"}}]
WEBHOOK_ROUTES = (
    ("post", "", "webhooks.subscription.create",
     "Subscribe an https endpoint to the events this credential may read. The signing secret is in this answer "
     "and never again. The grant must be renewable (offline_access); one subscription per address, so a second "
     "create for the same address is refused with 409 subscription_exists naming the first",
     "WebhookCreate", "WebhookSubscriptionWithSecret", "201", []),
    ("get", "", "webhooks.subscription.list", "This grant's subscriptions", None, "WebhookSubscriptionList", "200", []),
    ("get", "/{sid}", "webhooks.subscription.get", "One subscription of this grant", None, "WebhookSubscription", "200", _SUB),
    ("patch", "/{sid}", "webhooks.subscription.update",
     "Change the address, the events or the description; If-Match (or `version`) guards against a lost update. "
     "Changing the address or the events makes the calling grant the one that answers for the subscription",
     "WebhookUpdate", "WebhookSubscription", "200",
     _SUB + [{"name": "If-Match", "in": "header", "required": False, "schema": {"type": "string"}}]),
    ("delete", "/{sid}", "webhooks.subscription.delete",
     "Delete a subscription: its queue is dropped and its secret destroyed", None, "WebhookDeleted", "200", _SUB),
    ("post", "/{sid}/pause", "webhooks.subscription.pause",
     "Stop delivering, including what is already queued (those deliveries end as dropped). Nothing changes if "
     "it is already paused", None, "WebhookSubscription", "200", _SUB),
    ("post", "/{sid}/resume", "webhooks.subscription.resume",
     "Make the subscription active under the calling grant. Paused or disabled: delivers again from the present "
     "(what happened meanwhile is in the event feed, not sent). Already active: its place and queue are kept; "
     "only the answering grant changes, and nothing at all if that is already the caller's",
     None, "WebhookSubscription", "200", _SUB),
    ("post", "/{sid}/rotate-secret", "webhooks.subscription.rotate_secret",
     "Issue a new signing secret; the one it replaces keeps signing for the overlap. Rotating again inside an "
     "overlap replaces only the newest secret and keeps the earlier one and its end; overlap_s 0 ends every "
     "earlier secret at once", "WebhookRotate", "WebhookSubscriptionWithSecret", "200", _SUB),
    ("post", "/{sid}/ping", "webhooks.subscription.ping",
     "Send one signed webhook.ping now, through the same checks and connection as a delivery. Not retried, "
     "apart from the subscription's health; at most one per "
     f"{machine_webhooks.PING_MIN_INTERVAL_S} s and {machine_webhooks.PING_PER_HOUR} per hour for a subscription, "
     f"and {machine_webhooks.MAX_PER_GRANT} per {machine_webhooks.PING_MIN_INTERVAL_S} s and "
     f"{machine_webhooks.MAX_PER_GRANT * machine_webhooks.PING_PER_HOUR} per hour for a grant, the subscriptions it "
     "has deleted included (429 ping_rate_limited with Retry-After; details.reason is interval, hourly or busy — "
     "one ping at a time for a workspace)", None, "WebhookDelivery", "200", _SUB),
    ("get", "/{sid}/deliveries", "webhooks.delivery.list", "The delivery log, newest first", None,
     "WebhookDeliveryPage", "200",
     _SUB + [{"name": "status", "in": "query", "required": False,
              "schema": {"enum": list(machine_webhooks.DELIVERY_STATUSES)}},
             {"name": "cursor", "in": "query", "required": False, "schema": {"type": "string"}},
             {"name": "limit", "in": "query", "required": False,
              "schema": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}}]),
    ("get", "/{sid}/deliveries/{did}", "webhooks.delivery.get", "One delivery", None, "WebhookDelivery", "200", _DEL),
    ("post", "/{sid}/deliveries/{did}/redeliver", "webhooks.delivery.redeliver",
     "Queue the event of an earlier delivery once more; it passes the same checks before it is sent. At most "
     f"{machine_webhooks.MAX_REDELIVERIES} times for one event (409 redelivery_limit_reached), within "
     f"{int(machine_webhooks.REDELIVERY_WINDOW_S // 86400)} days of the event (409 redelivery_window_expired), and "
     f"not while {machine_webhooks.MAX_PENDING} deliveries already wait for the subscription (409 "
     "delivery_queue_full: ask again once some have settled)", None, "WebhookDelivery", "202", _DEL),
)
#: The changes that take something back, journalled before they are made (`Webhooks._journal`).
_JOURNALLED = ("webhooks.subscription.update", "webhooks.subscription.delete", "webhooks.subscription.pause",
               "webhooks.subscription.rotate_secret")


def _webhook_route(method, name, summary, request, response, status, parameters, readers) -> dict:
    out = {"operationId": name, "summary": summary, "tags": ["credential"],
           "parameters": [dict(x) for x in parameters],
           "responses": {status: {"description": "OK", "headers": _headers(status),
                                  "content": {"application/json": {"schema": _ref(response)}}},
                         **_errors({"400", "401", "403", "404", "409", "429", "500"}
                                   | ({_UNAVAILABLE[0]} if name in _JOURNALLED else set()))}}
    if request:
        out["requestBody"] = {"required": request != "WebhookRotate",
                              "content": {"application/json": {"schema": _ref(request)}}}
    out = _common(out, scopes=[])
    if name == "webhooks.subscription.create":
        out["security"] = [{"suiteToken": [scope]} for scope in readers]
    return out


def _webhook_schemas(registry, event: dict) -> dict:
    subscription = {
        "id": {"type": "string"}, "workspace_id": {"type": "string"}, "url": {"type": "string"},
        "description": {"type": "string"},
        "events": {"type": "array", "items": {"enum": machine_webhooks.subscribable(registry)}},
        "status": {"enum": ["active", "paused", "disabled"]},
        "disabled_reason": {"type": ["string", "null"],
                            "description": "Why delivery stopped: gone (the receiver answered 410), failing (no "
                                           f"success for {int(machine_webhooks.DISABLE_AFTER_S // 86400)} days and "
                                           f"{machine_webhooks.MAX_ATTEMPTS} attempts in a row), backlog_expired (it "
                                           "fell further behind than the feed keeps events), journal_replay (the "
                                           "database was restored from before a later pause, move, rotation or "
                                           "disable: check the address, rotate the secret, resume), or "
                                           "authorization_revoked:<code> — the grant "
                                           "that answered for it ended; <code> is one of "
                                           + ", ".join(machine_webhooks.ENDED) + ". `resume` re-enables any of "
                                           "them under the calling grant."},
        "status_changed_at": {"type": "number", "description": "When it became active, paused or disabled."},
        "missed_events_cursor": {
            "type": ["string", "null"],
            "description": "Where pushing last stopped (a pause, a disable), as a `cursor` for GET /events of this "
                           "same grant: read from it to get what was not pushed, de-duplicating on the event id. "
                           "Kept after a resume; null if it never stopped or the place is older than the feed keeps."},
        "stalled_since": {
            "type": ["number", "null"],
            "description": "Set while an event cannot be decided because of a fault on the platform's side: "
                           "nothing after it is sent until it can be, and nothing is passed over."},
        "authorized_at": {"type": "number",
                          "description": "When the grant that answers for it was bound: on create, resume, a change "
                                         "of address or events, or a secret rotation."},
        "authorized_by_caller": {"type": "boolean",
                                 "description": "Whether the grant making this call is the one deliveries are "
                                                "decided for. If false, that other grant ending disables the "
                                                "subscription; `resume` binds it to the caller."},
        "created_at": {"type": "number"}, "updated_at": {"type": "number"}, "version": {"type": "integer"},
        "consecutive_failures": {"type": "integer"}, "last_success_at": {"type": ["number", "null"]},
        "last_failure_at": {"type": ["number", "null"]}, "previous_secret_expires_at": {"type": ["number", "null"]}}
    writable = {"url": {"type": "string", "maxLength": 2048,
                        "description": "https, to a publicly routable address, without credentials in it. Where it "
                                       "leads is checked when it is set and on resume, one address at a time for a "
                                       "workspace: 429 destination_check_busy (Retry-After) says another is being "
                                       "checked or no resolver is free — not a verdict on the address; nothing "
                                       "changed, repeat the call."},
                "events": {"type": "array", "minItems": 1, "maxItems": machine_webhooks.MAX_EVENTS,
                           "items": {"enum": machine_webhooks.subscribable(registry)},
                           "description": "Event types, \"<app>.*\" or \"*\" (everything the credential may read)."},
                "description": {"type": "string", "maxLength": machine_webhooks.MAX_DESCRIPTION_LEN}}
    delivery = {
        "id": {"type": "string"}, "webhook_id": {"type": "string", "description": "The event's id: the dedupe key."},
        "event_type": {"type": "string"}, "status": {"enum": list(machine_webhooks.DELIVERY_STATUSES)},
        "attempts": {"type": "integer"}, "last_code": {"type": ["integer", "null"]},
        "last_error": {"type": ["string", "null"],
                       "description": "What ended the last attempt: one of "
                                      + ", ".join(machine_webhooks.ATTEMPT_ERRORS) + "; why a pending delivery waits "
                                      "through no fault of the receiver (not counted as an attempt): "
                                      + ", ".join(machine_webhooks.LOCAL_ERRORS) + "; or why it was dropped unsent: "
                                      "subscription_paused, subscription_deleted, subscription_disabled:<reason>, "
                                      "no_longer_readable, event_out_of_window, event_missing. Never a receiver's "
                                      "answer and never the platform's view of the network."},
        "duration_ms": {"type": ["integer", "null"]},
        "created_at": {"type": "number"}, "delivered_at": {"type": ["number", "null"]},
        "next_attempt_at": {"type": ["number", "null"]}, "redelivery": {"type": "boolean"},
        "redelivery_of": {"type": ["string", "null"]}}
    closed = lambda props, required: {"type": "object", "additionalProperties": False,  # noqa: E731
                                      "required": required, "properties": props}
    pushed = {**event["properties"], "type": {"enum": [*event["properties"]["type"]["enum"], machine_webhooks.PING_EVENT]},
              "app": {"enum": [*event["properties"]["app"]["enum"], None]},
              "object": {"anyOf": [event["properties"]["object"], {"type": "null"}]},
              "workspace_id": {"type": "string"},
              "subscription_id": {"type": "string",
                                  "description": "The subscription this copy was decided for — under the signature, "
                                                 "so it is that person's grant and no other's."}}
    return {
        "WebhookSubscription": closed(subscription, list(subscription)),
        "WebhookSubscriptionWithSecret": closed({**subscription, "secret": {
            "type": "string", "description": "whsec_<base64>. Shown here once; store it now."}},
            [*subscription, "secret"]),
        "WebhookSubscriptionList": closed({
            "items": {"type": "array", "items": _ref("WebhookSubscription")},
            "limit": {"type": "integer", "description": "Subscriptions one grant may have, disabled ones included."},
            "workspace_limit": {"type": "integer",
                                "description": "Active and paused subscriptions the workspace may have, everyone's "
                                               f"together; one client may hold {machine_webhooks.MAX_PER_CLIENT}."}},
            ["items", "limit", "workspace_limit"]),
        "WebhookCreate": closed(writable, ["url", "events"]),
        "WebhookUpdate": closed({**writable, "version": {"type": "integer"}}, []),
        "WebhookRotate": closed({"overlap_s": {"type": "integer", "minimum": 0, "maximum": machine_webhooks.MAX_OVERLAP_S,
                                               "default": machine_webhooks.DEFAULT_OVERLAP_S}}, []),
        "WebhookDeleted": closed({"deleted": {"type": "string"}}, ["deleted"]),
        "WebhookDelivery": closed(delivery, list(delivery)),
        "WebhookDeliveryPage": closed({"items": {"type": "array", "items": _ref("WebhookDelivery")},
                                       "next_cursor": {"type": ["string", "null"]}, "has_more": {"type": "boolean"}},
                                      ["items", "next_cursor", "has_more"]),
        "WebhookEvent": closed(pushed, [*event["required"], "workspace_id", "subscription_id"]),
    }


def contract(registry, *, server: str = SERVER, issuer: str = ISSUER) -> dict:
    """The document for every machine operation registered in `registry`."""
    schemas = _Schemas()
    paths: dict[str, dict] = {}
    for op, scope in machine_operations(registry):
        paths[f"{SUITE_V1}/ops/{op.id}"] = {"post": _operation(op, scope, schemas)}

    for app, method, path, name, scope, summary, kind in FILE_ROUTES:
        manifest = registry.app(app)
        if manifest is not None and manifest.machine:
            paths[SUITE_V1 + path] = {method: _file_route(app, path, name, scope, summary, kind)}

    def json_ok(schema):
        return {"200": {"description": "OK", "headers": _headers("200"),
                        "content": {"application/json": {"schema": schema}}}}

    paths[f"{SUITE_V1}/whoami"] = {"get": _common({
        "operationId": "whoami", "summary": "Who this credential is, for whom, where, and what it may ask for",
        "tags": ["credential"], "responses": {**json_ok(_ref("WhoAmI")), **_errors({"401", "403", "429", "500"})}},
        scopes=[])}
    paths[f"{SUITE_V1}/operations"] = {"get": _common({
        "operationId": "operations", "tags": ["credential"],
        "summary": "The operations the person can use in this workspace, and whether the credential holds each scope",
        "parameters": [{"name": "app", "in": "query", "required": False, "schema": {"type": "string"}}],
        "responses": {**json_ok(_ref("OperationList")), **_errors({"401", "403", "404", "429", "500"})}},
        scopes=[])}
    paths[f"{SUITE_V1}/runs/{{run_id}}"] = {"get": _common({
        "operationId": "run", "tags": ["credential"],
        "summary": "A run this client started for this person here; its result is shown only while its sources are still readable",
        "parameters": [{"name": "run_id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "responses": {**json_ok(_ref("Run")), **_errors({"401", "403", "404", "429", "500"})}},
        scopes=[])}

    served = machine_events.event_types(registry)
    if served:
        # Any one application's read scope opens the feed; each event needs its own application's.
        paths[f"{SUITE_V1}/events"] = {"get": {**_common({
            "operationId": "events", "tags": ["credential"],
            "summary": "What changed in this workspace since the cursor, for the applications the credential may "
                       "read: where to look, never what is there",
            "description": "Items are in the order the events were written. An item names the object and the "
                           "identifiers needed to open it; the event's data is not served. Only events about "
                           "records the person may read now are returned. Keep `next_cursor` and send it back. "
                           f"A read without a cursor starts {machine_events.RETENTION_S // 86400} days back; a "
                           "cursor is refused with 410 once what follows it is older than that by more than "
                           f"{machine_events.CURSOR_GRACE_S // 3600} hours, or when it no longer agrees with the "
                           "log (a restore). One request reads a bounded part of the log: `has_more` says to ask "
                           "again, even after an empty page.",
            "parameters": [
                {"name": "cursor", "in": "query", "required": False,
                 "description": "The `next_cursor` of the previous page. Opaque; belongs to this client, person "
                                "and workspace.",
                 "schema": {"type": "string", "maxLength": machine_events.MAX_CURSOR}},
                {"name": "types", "in": "query", "required": False,
                 "description": "Comma-separated event types to return (see `x-synwevo-event-types`).",
                 "schema": {"type": "string"}},
                {"name": "limit", "in": "query", "required": False,
                 "schema": {"type": "integer", "minimum": 1, "maximum": machine_events.MAX_LIMIT,
                            "default": machine_events.DEFAULT_LIMIT}},
                {"name": "start", "in": "query", "required": False,
                 "description": "Without a cursor: `oldest` (the default) reads the retained window from its "
                                "beginning; `now` returns no items and a cursor at the present.",
                 "schema": {"enum": list(machine_events.STARTS)}}],
            "responses": {**json_ok(_ref("EventPage")), **_errors({"400", "401", "403", "429", "500"}),
                          "410": {"$ref": "#/components/responses/Gone"}}}, scopes=[]),
            "security": [{"suiteToken": [scope]} for scope in sorted({e["scope"] for e in served})],
            "x-synwevo-event-types": served,
            "x-synwevo-retention-seconds": machine_events.RETENTION_S,
            "x-synwevo-cursor-grace-seconds": machine_events.CURSOR_GRACE_S}}
        readers = sorted({e["scope"] for e in served})
        for method, sub, name, summary, request, response, status, parameters in WEBHOOK_ROUTES:
            paths.setdefault(f"{SUITE_V1}/webhooks/subscriptions{sub}", {})[method] = _webhook_route(
                method, name, summary, request, response, status, parameters, readers)

    text = {"type": "object", "required": ["zh-Hans", "zh-Hant", "en"],
            "properties": {k: {"type": "string"} for k in ("zh-Hans", "zh-Hant", "en")}}
    components = dict(sorted(schemas.out.items()))
    components.update({
        "SuiteError": {"type": "object", "required": ["error"], "properties": {"error": {
            "type": "object", "required": ["code", "message", "request_id"],
            "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                           "request_id": {"type": "string"}, "field": {"type": ["string", "null"]},
                           "details": {"type": "object", "additionalProperties": True}}}}},
        "SuiteProblem": {"type": "object",
                         "required": ["type", "title", "status", "detail", "code", "request_id"],
                         "properties": {"type": {"type": "string"}, "title": {"type": "string"},
                                        "status": {"type": "integer"}, "detail": {"type": "string"},
                                        "code": {"type": "string"}, "request_id": {"type": "string"},
                                        "instance": {"type": "string"}}},
        "WhoAmI": {"type": "object",
                   "required": ["kind", "client_id", "user_id", "workspace_id", "role", "scopes", "resource", "apps", "expires_at"],
                   "properties": {"kind": {"const": "delegated"}, "client_id": {"type": "string"},
                                  "user_id": {"type": "string"}, "workspace_id": {"type": "string"},
                                  "role": {"enum": ["owner", "admin", "editor", "viewer"]},
                                  "scopes": {"type": "array", "items": {"enum": list(SUITE_API_SCOPES)}},
                                  "resource": {"type": "string"},
                                  "apps": {"type": "array", "items": {"type": "string"}},
                                  "expires_at": {"type": ["number", "null"]}}},
        "OperationContract": {"type": "object",
                              "required": ["id", "app", "version", "title", "summary", "path", "scope",
                                           "also_requires", "need",
                                           "side_effect", "mode", "cancellable", "idempotency", "events",
                                           "object_kind", "input_schema", "output_schema", "granted"],
                              "properties": {"id": {"type": "string"}, "app": {"type": "string"},
                                             "version": {"type": "integer"}, "title": text,
                                             "summary": {"type": "string"}, "path": {"type": "string"},
                                             "scope": {"enum": list(SUITE_API_SCOPES)},
                                             "also_requires": {"type": "array",
                                                               "items": {"enum": list(SUITE_API_SCOPES)}},
                                             "need": {"enum": ["read", "write", "manage"]},
                                             "side_effect": {"enum": ["none", "write", "external"]},
                                             "mode": {"enum": ["sync", "async"]},
                                             "cancellable": {"type": "boolean"},
                                             "idempotency": {"enum": ["required", "supported", "none"]},
                                             "events": {"type": "array", "items": {"type": "string"}},
                                             "object_kind": {"type": ["string", "null"]},
                                             "input_schema": {"type": "object"},
                                             "output_schema": {"type": ["object", "null"]},
                                             "granted": {"type": "boolean"}}},
        "OperationList": {"type": "object", "required": ["workspace_id", "operations", "apps"],
                          "properties": {"workspace_id": {"type": "string"},
                                         "operations": {"type": "array", "items": _ref("OperationContract")},
                                         "apps": {"type": "array", "items": {"type": "object"}}}},
        "Event": {"type": "object", "additionalProperties": False,
                  "required": ["id", "type", "version", "app", "object", "refs", "occurred_at", "run_id",
                               "correlation_id"],
                  "properties": {
                      "id": {"type": "string"}, "type": {"enum": [e["type"] for e in served]},
                      "version": {"type": "integer"}, "app": {"enum": sorted({e["app"] for e in served})},
                      "object": {"type": "object", "additionalProperties": False, "required": ["kind", "id"],
                                 "properties": {"kind": {"type": "string"}, "id": {"type": "string"}}},
                      "refs": {"type": "object", "additionalProperties": {"type": "string"},
                               "description": "Identifiers needed to open the object, by name."},
                      "occurred_at": {"type": "number"},
                      "run_id": {"type": ["string", "null"],
                                 "description": "The run that caused the event, when this client started it."},
                      "correlation_id": {"type": ["string", "null"],
                                         "description": "The correlation id of the run that caused the event, "
                                                        "when this client started that run."}}},
        "EventPage": {"type": "object", "additionalProperties": False,
                      "required": ["workspace_id", "items", "next_cursor", "has_more"],
                      "properties": {"workspace_id": {"type": "string"},
                                     "items": {"type": "array", "items": _ref("Event"),
                                               "maxItems": machine_events.MAX_LIMIT},
                                     "next_cursor": {"type": "string"},
                                     "has_more": {"type": "boolean",
                                                  "description": "True when the request stopped before the end "
                                                                 "of the feed — even with no items. Ask again."}}},
        "Run": {"type": "object",
                "required": ["id", "operation", "app", "workspace_id", "status", "via", "input", "output", "error",
                             "object", "created_at", "finished_at", "snapshot_hidden"],
                "properties": {"id": {"type": "string"}, "operation": {"type": "string"}, "app": {"type": "string"},
                               "workspace_id": {"type": "string"}, "status": {"enum": list(RUN_STATES)},
                               "via": {"const": "api"}, "input": {"type": "null"}, "output": {},
                               "error": {"type": ["object", "null"]}, "object": {"type": ["object", "null"]},
                               "delegation": {"type": ["object", "null"]},
                               "correlation_id": {"type": "string"}, "parent_run_id": {"type": ["string", "null"]},
                               "created_at": {"type": "number"}, "finished_at": {"type": ["number", "null"]},
                               "snapshot_hidden": {"type": "boolean"}}},
    })
    pushes = {}
    if served:
        components.update(_webhook_schemas(registry, components["Event"]))
        pushes = {"event": {"post": {
            "operationId": "webhooks.event", "tags": ["credential"],
            "summary": "What a subscribed endpoint receives: the feed's item, signed",
            "description": "Standard Webhooks. `X-Synwevo-Subscription` names the subscription, hence the secret. "
                           "Verify `webhook-signature` (v1,<base64 HMAC-SHA256(secret, "
                           "\"<webhook-id>.<webhook-timestamp>.<body>\")>; during a rotation there are two, either may "
                           "match) over the raw body as received, and reject a `webhook-timestamp` more than a few "
                           "minutes from now — it is the time of this attempt, not of the event (`occurred_at`). "
                           "`webhook-id` is the event's id, the same value the feed gives: de-duplicate on it within "
                           "one grant, and on (subscription, webhook-id) when one endpoint serves several people's "
                           "grants — each gets its own copy, decided for that person. Answer 2xx within "
                           f"{int(machine_webhooks.TIMEOUT_S)} s to accept; 410 to be unsubscribed; anything else, a "
                           "redirect included, is retried, and later events of the subscription wait behind it: a "
                           "subscription's deliveries are sent one at a time in the order they were queued. At most "
                           f"{machine_webhooks.MAX_PENDING} wait for one subscription (the rest stay in the log until "
                           f"there is room) and one host is sent at most {machine_webhooks.HOST_PER_MINUTE} requests a "
                           "minute by one worker, whoever subscribed.",
            "parameters": [{"name": name, "in": "header", "required": True, "schema": {"type": "string"}}
                           for name in ("webhook-id", "webhook-timestamp", "webhook-signature", "X-Synwevo-Subscription",
                                        "X-Synwevo-Delivery", "X-Synwevo-Event")],
            "requestBody": {"required": True, "content": {"application/json": {"schema": _ref("WebhookEvent")}}},
            "responses": {"2XX": {"description": "Accepted. The response body is never read."}}}}}
    return {
        "openapi": "3.1.0",
        "info": {"title": "Synwevo Suite API", "version": VERSION,
                 "description": "Operations of Synwevo Forms, Tables, DataPrep, Extract and Approvals for a client a "
                                "person authorised. Every call acts as that person, in the one workspace they "
                                "chose, with their current role; the scope is necessary, not sufficient."},
        "servers": [{"url": server.rstrip("/")}],
        "paths": dict(sorted(paths.items())),
        **({"webhooks": pushes} if pushes else {}),
        "components": {"schemas": components, "responses": {**_error_responses(), "Gone": {
            "description": "Gone", "headers": _headers("410"), "content": {
                "application/json": {"schema": _ref("SuiteError")},
                "application/problem+json": {"schema": _ref("SuiteProblem")}}}}, "headers": {
            "RequestId": {"schema": {"type": "string"}},
            "RateLimit": {"schema": {"type": "string"}},
            "RateLimitPolicy": {"schema": {"type": "string"}},
            "RetryAfter": {"schema": {"type": "integer", "minimum": 0}}}, "parameters": {
            "RequestId": {"name": "X-Request-ID", "in": "header", "required": False,
                          "schema": {"type": "string", "maxLength": 64}},
            **{name: {"name": "Idempotency-Key", "in": "header", "required": required,
                      "description": "Makes the call repeatable for this client: the same key and input "
                                     "answer with the first result; the same key with another input is a 409.",
                      "schema": {"type": "string", "minLength": 1, "maxLength": MAX_CALLER_KEY,
                                 "pattern": "^[!-~]+$"}}
               for name, required in (("IdempotencyKey", False), ("IdempotencyKeyRequired", True))}},
            "securitySchemes": {"suiteToken": {
            "type": "oauth2", "description": f"Request tokens with resource={suite_resource()}",
            "flows": {"authorizationCode": {
                "authorizationUrl": issuer.rstrip("/") + "/oidc/authorize",
                "tokenUrl": issuer.rstrip("/") + "/oidc/token", "refreshUrl": issuer.rstrip("/") + "/oidc/token",
                "scopes": {scope: SCOPE_SUMMARY[scope] for scope in SUITE_API_SCOPES}}}}}},
    }
