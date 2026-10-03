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


_ERROR_NAMES = {code: "".join(word.capitalize() for word in description.split()) for code, description in _ERRORS}


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
    return {code: {"$ref": f"#/components/responses/{_ERROR_NAMES[code]}"} for code, _ in _ERRORS
            if codes is None or code in codes}


def _error_responses() -> dict:
    return {_ERROR_NAMES[code]: {"description": description, "headers": _headers(code), "content": {
        "application/json": {"schema": _ref("SuiteError")},
        "application/problem+json": {"schema": _ref("SuiteProblem")}}} for code, description in _ERRORS}


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
    return {
        "openapi": "3.1.0",
        "info": {"title": "Synwevo Suite API", "version": VERSION,
                 "description": "Operations of Synwevo Forms, Tables, DataPrep, Extract and Approvals for a client a "
                                "person authorised. Every call acts as that person, in the one workspace they "
                                "chose, with their current role; the scope is necessary, not sufficient."},
        "servers": [{"url": server.rstrip("/")}],
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": components, "responses": _error_responses(), "headers": {
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
