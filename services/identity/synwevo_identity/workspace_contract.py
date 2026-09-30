"""Directory contract from its actual routes, without importing the Docs application.

The admin catalogue composes this document with the injected application's contract. A
standalone account service still documents its own machine API when Docs is unavailable.
"""
from __future__ import annotations

import copy

from fastapi.openapi.utils import get_openapi

from synwevo_core.oidc import WORKSPACE_API_SCOPES, workspace_resource

PREFIX = "/api/workspace/v1"
METHODS = frozenset({"get", "post", "patch", "put", "delete"})
VERSION = "1.0.0"


def contract(api, *, issuer: str) -> dict:
    # FastAPI may retain included routers lazily. Let its exporter resolve them, then select
    # paths and their reachable schemas so account/admin bodies cannot leak into this contract.
    spec = get_openapi(title="Synwevo Workspace API", version=VERSION, openapi_version="3.1.0", routes=api.routes)
    spec["paths"] = {p: v for p, v in spec["paths"].items() if p.startswith(PREFIX + "/")}
    components = spec.setdefault("components", {})
    components["securitySchemes"] = {"workspaceToken": {
        "type": "oauth2", "flows": {"clientCredentials": {
            "tokenUrl": issuer.rstrip("/") + "/oidc/token",
            "scopes": {scope: scope for scope in WORKSPACE_API_SCOPES}}}}}
    components.setdefault("schemas", {})["DirectoryError"] = {
        "type": "object", "required": ["error"], "properties": {"error": {
            "type": "object", "required": ["code", "message", "request_id"],
            "properties": {"code": {"type": "string"}, "message": {"type": "string"},
                           "request_id": {"type": "string"}, "field": {"type": ["string", "null"]},
                           "details": {"type": "object", "additionalProperties": True}}}}}
    components["schemas"]["DirectoryProblem"] = {"type": "object",
        "required": ["type", "title", "status", "detail", "code", "request_id"], "properties": {
            "type": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "integer"},
            "detail": {"type": "string"}, "code": {"type": "string"}, "request_id": {"type": "string"},
            "instance": {"type": "string"}}}
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in METHODS:
                continue
            scope = "workspace.read" if method == "get" else "workspace.members.write"
            operation["security"] = [{"workspaceToken": [scope]}]
            operation["x-synwevo-resource"] = workspace_resource()
            operation["x-synwevo-identity"] = "service_account"
            operation["servers"] = [{"url": issuer.rstrip("/")}]
            operation.setdefault("parameters", []).append({"name": "X-Request-ID", "in": "header",
                "required": False, "schema": {"type": "string", "maxLength": 64}})
            idempotent = method == "post" and (path == PREFIX + "/invitations" or path.endswith("/resend"))
            if idempotent:
                operation["parameters"].append({"name": "Idempotency-Key", "in": "header", "required": False,
                    "schema": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": "^[ -~]+$"}})
            responses = operation.setdefault("responses", {})
            responses.pop("422", None)  # The shared machine gateway returns the 400 envelope.
            for code, description in (("400", "Bad Request"), ("401", "Unauthorized"),
                                      ("403", "Forbidden"), ("404", "Not Found"),
                                      ("409", "Conflict"), ("429", "Too Many Requests"),
                                      ("500", "Internal Server Error"), ("503", "Service Unavailable")):
                responses.setdefault(code, {"description": description, "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/DirectoryError"}},
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/DirectoryProblem"}}}})
            for code, response in responses.items():
                headers = response.setdefault("headers", {})
                headers["X-Request-ID"] = {"schema": {"type": "string"}}
                if code.startswith("2") or code == "429":
                    headers["RateLimit"] = {"schema": {"type": "string"}}
                    headers["RateLimit-Policy"] = {"schema": {"type": "string"}}
                if idempotent and code.startswith("2"):
                    headers["Idempotent-Replayed"] = {"schema": {"type": "boolean"}}
                if code == "429":
                    headers["Retry-After"] = {"schema": {"type": "integer", "minimum": 0}}
    schemas = components.get("schemas", {})
    reachable = set()

    def collect(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/components/schemas/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in reachable:
                    reachable.add(name)
                    collect(schemas[name])
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(spec["paths"])
    components["schemas"] = {name: value for name, value in schemas.items() if name in reachable}
    return spec


def catalogue(api, application: dict | None, *, issuer: str) -> dict:
    directory = contract(api, issuer=issuer)
    if application is None:
        return directory
    out = copy.deepcopy(application)
    out["info"] = {"title": "Synwevo API", "version": VERSION}
    out["paths"].update(directory["paths"])
    for category, definitions in directory.get("components", {}).items():
        target = out.setdefault("components", {}).setdefault(category, {})
        for name, definition in definitions.items():
            if name in target and target[name] != definition:
                raise ValueError(f"Conflicting API component: {category}/{name}")
            target[name] = definition
    return out
