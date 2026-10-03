"""The machine API's published contract is the one the server runs.

`docs/integration/suite-openapi.json` is generated (`python -m synwevo_suite.cli openapi`); these
tests rebuild it from the fixture application's registry and hold the document, the routes and the
scope catalogue to one another. A deliberate change regenerates the file in the same commit.
"""
from __future__ import annotations

import json
from pathlib import Path

from synwevo_suite.common.machine_contract import SCOPE_SUMMARY, contract

PUBLISHED = Path(__file__).resolve().parents[3] / "docs" / "integration" / "suite-openapi.json"


def test_the_published_contract_is_the_running_one(suite):
    built = contract(suite.caps.registry)
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(built)) == published, \
        "regenerate with: python -m synwevo_suite.cli openapi --out docs/integration/suite-openapi.json"
    assert PUBLISHED.read_text(encoding="utf-8") == json.dumps(built, ensure_ascii=False, indent=2) + "\n"


def test_every_route_is_documented_and_every_documented_path_is_served(suite):
    from synwevo_core.oidc import SUITE_API_SCOPES
    doc = contract(suite.caps.registry)
    served = set()
    pending = list(suite.api.routes)
    while pending:
        route = pending.pop()
        inner = getattr(route, "original_router", None)
        if inner is not None:
            pending.extend(inner.routes)
        path = getattr(route, "path", "")
        if path.startswith("/api/suite/v1/"):
            served.update((path, method.lower()) for method in route.methods or () if method != "HEAD")
    documented = {(path, method) for path, item in doc["paths"].items() for method in item}
    # the one route with no entry: the catch-all that refuses what is not a credential's to ask for
    assert served - documented == {("/api/suite/v1/ops/{operation}", "post")}
    assert documented - served == set()

    ids = [op["operationId"] for item in doc["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))
    granted = doc["components"]["securitySchemes"]["suiteToken"]["flows"]["authorizationCode"]["scopes"]
    assert list(granted) == list(SUITE_API_SCOPES) == list(SCOPE_SUMMARY)
    asked = {s for item in doc["paths"].values() for op in item.values() for need in op["security"]
             for s in need["suiteToken"]}
    assert asked == set(SUITE_API_SCOPES)
    # nothing of the personal Agent is in a document an integration reads
    assert not any("/ops/agent." in path for path in doc["paths"])
    assert not any(name.startswith("agent.") for name in doc["components"]["schemas"])
    assert {tag for item in doc["paths"].values() for op in item.values() for tag in op["tags"]} == {
        "forms", "tables", "dataprep", "extract", "approvals", "credential"}


def test_every_reference_resolves_and_requests_are_closed_objects(suite):
    doc = contract(suite.caps.registry)

    def resolve(ref):
        node = doc
        for part in ref[2:].split("/"):
            node = node[part]
        return node

    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get("$ref"), str):
                assert value["$ref"].startswith("#/components/") and resolve(value["$ref"]) is not None
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(doc)
    for path, item in doc["paths"].items():
        post = item.get("post")
        if not post:
            continue
        body = post["requestBody"]["content"]["application/json"]["schema"]
        assert body["additionalProperties"] is False and set(body["properties"]) == {
            "input", "workspace_id", "idempotency_key", "correlation_id"}, path
        rule = post["x-synwevo-idempotency"]
        names = [p["$ref"].rsplit("/", 1)[1] for p in post["parameters"]]
        assert ("IdempotencyKeyRequired" in names) == (rule == "required"), path
        assert ("IdempotencyKey" in names) == (rule == "supported"), path
        assert ("202" in post["responses"]) == (post["x-synwevo-mode"] == "async"), path
        assert (post["x-synwevo-side-effect"] == "none") == (rule == "none"), path
