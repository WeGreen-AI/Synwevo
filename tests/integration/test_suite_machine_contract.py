"""The published machine API contract is the composed deployment's.

`docs/integration/suite-openapi.json` used to be exported from the applications alone, while the
server also serves what only the composition root registers (importing a form attachment into
Extract) — the document was one operation short of the routes. Here the composed application is
built as a deployment builds it, and the file, the served routes and that operation's declared
requirements are held to one another.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PUBLISHED = Path(__file__).resolve().parents[2] / "docs" / "integration" / "suite-openapi.json"
SUITE = "urn:synwevo:platform:suite"


@pytest.fixture()
def composed_suite(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "all")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    return app_mod.create_app(str(tmp_path / "d"), start_jobs=False)


def test_the_published_contract_is_the_running_one(composed_suite):
    from synwevo_suite.common.machine_contract import contract
    built = contract(composed_suite.state.capabilities.registry)
    assert PUBLISHED.read_text(encoding="utf-8") == json.dumps(built, ensure_ascii=False, indent=2) + "\n", \
        "regenerate with: python -m synwevo_server.suite_contract --out docs/integration/suite-openapi.json"


def test_the_exporter_writes_that_document(tmp_path):
    from synwevo_server import suite_contract
    assert json.dumps(suite_contract.build(), ensure_ascii=False, indent=2) + "\n" == PUBLISHED.read_text(encoding="utf-8")


def test_every_served_machine_route_is_documented(composed_suite):
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    served, pending = set(), list(composed_suite.routes)
    while pending:
        route = pending.pop()
        inner = getattr(route, "original_router", None)
        if inner is not None:
            pending.extend(inner.routes)
        path = getattr(route, "path", "")
        if path.startswith("/api/suite/v1/"):
            served.update((path, method.lower()) for method in route.methods or () if method != "HEAD")
    documented = {(path, method) for path, item in published["paths"].items() for method in item}
    assert served - documented == {("/api/suite/v1/ops/{operation}", "post")}
    assert documented - served == set()
    # nothing of an adapter that has its own API, and nothing personal
    assert {tag for item in published["paths"].values() for op in item.values() for tag in op["tags"]} == {
        "forms", "tables", "dataprep", "extract", "approvals", "credential", "mcp"}


def test_the_composition_operation_is_declared_as_it_behaves(composed_suite):
    """`extract.file.from_forms`: needs Extract write and Forms read, and its handler will not run
    without an idempotency key. The contract, `/operations` and the route say the same."""
    from synwevo_core import oidc
    from synwevo_core.identity import Identity
    from synwevo_core.sessions import Sessions
    from synwevo_core.store import Store
    store = Store(composed_suite.state.capabilities.store.root)
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", "a-sufficiently-long-passphrase", status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    oidc.Clients(store).register(client_id="crm-sync", app_id="platform", name="Fixture CRM sync",
                                 redirect_uris=["https://crm.example.test/callback"],
                                 scopes="openid extract.read extract.write forms.read")

    def bearer(scopes):
        _, session = Sessions(store).create(alice["id"], workspace_id=None, amr="password")
        out = oidc.Grants(store).issue_tokens(
            client=oidc.Clients(store).get("crm-sync"), with_refresh=False,
            code_row={"user_id": alice["id"], "scope": scopes, "central_session_id": session["id"], "workspace_id": ws})
        return {"Authorization": "Bearer " + out["access_token"]}

    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    doc = published["paths"]["/api/suite/v1/ops/extract.file.from_forms"]["post"]
    assert doc["security"] == [{"suiteToken": ["extract.write", "forms.read"]}]
    assert doc["x-synwevo-idempotency"] == "required"
    assert {"$ref": "#/components/parameters/IdempotencyKeyRequired"} in doc["parameters"]

    c = TestClient(composed_suite, base_url="http://testserver")
    listed = c.get("/api/suite/v1/operations?app=extract", headers=bearer("extract.read extract.write")).json()
    told = next(o for o in listed["operations"] if o["id"] == "extract.file.from_forms")
    assert (told["scope"], told["also_requires"], told["idempotency"], told["granted"]) == (
        "extract.write", ["forms.read"], "required", False)
    ask = {"input": {"submission_id": "fsub_" + "0" * 12, "file_id": "ffile_" + "0" * 12}}
    path = "/api/suite/v1/ops/extract.file.from_forms"
    short = c.post(path, json=ask, headers={**bearer("extract.read extract.write"), "Idempotency-Key": "k1"})
    assert short.status_code == 403 and short.json()["error"]["details"]["required_scope"] == "forms.read"
    both = bearer("extract.write forms.read")
    keyless = c.post(path, json=ask, headers=both)
    assert keyless.status_code == 400 and keyless.json()["error"]["code"] == "idempotency_key_required"
    # with both scopes and a key the call reaches the applications, which answer about the submission
    reached = c.post(path, json=ask, headers={**both, "Idempotency-Key": "k1"})
    assert reached.status_code == 404 and reached.json()["error"]["code"] == "submission_not_found", reached.text
    assert store.one("SELECT COUNT(*) AS n FROM extract_files")["n"] == 0
