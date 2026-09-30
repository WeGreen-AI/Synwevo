"""The admin catalogue exposes real directory contracts without exporting account internals."""
import json
from pathlib import Path

from fastapi import FastAPI

from synwevo_identity.workspace_api import build_router
from synwevo_identity.workspace_contract import contract
from tests.integration.test_api_platform import BASE, plane  # noqa: F401


def test_directory_export_matches_mounted_routes_and_committed_contract(plane):
    spec = contract(plane["api"], issuer="https://accounts.synwevo.com")
    saved = Path(__file__).resolve().parents[2] / "docs/integration/workspace-openapi.json"
    assert spec == json.loads(saved.read_text())
    expected = {
        ("get", "/whoami"), ("get", "/members"), ("get", "/invitations"),
        ("post", "/invitations"), ("post", "/invitations/{iid}/resend"),
        ("post", "/invitations/{iid}/revoke"), ("patch", "/members/{user_id}"),
        ("post", "/members/{user_id}/remove"),
    }
    assert {(m, p.removeprefix("/api/workspace/v1")) for p, methods in spec["paths"].items()
            for m in methods} == expected
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            scope = "workspace.read" if method == "get" else "workspace.members.write"
            assert operation["security"] == [{"workspaceToken": [scope]}]
            assert "422" not in operation["responses"]
            assert "400" in operation["responses"]
    assert all(n.startswith("Directory") for n in spec["components"]["schemas"])


def test_directory_catalogue_is_governed_and_does_not_export_account_routes(plane):
    e = plane
    result = e["admin"].get(BASE + "/openapi")
    assert result.status_code == 200, result.text
    spec = result.json()
    assert "/api/workspace/v1/invitations" in spec["paths"]
    assert "/api/v1/projects" in spec["paths"]
    assert not any(p.startswith(("/api/auth/", "/api/admin/", "/oidc/")) for p in spec["paths"])
    listing = e["admin"].get(BASE + "/operations", params={"q": "/api/workspace/v1", "limit": 100}).json()
    assert listing["total"] == 8
    assert e["owner"].get(BASE + "/openapi").status_code == 404


def test_schema_export_does_not_need_a_store_or_docs():
    api = FastAPI()
    api.include_router(build_router(None, None, None, None, None))
    spec = contract(api, issuer="https://accounts.synwevo.com")
    assert len(spec["paths"]) == 7
    assert all(p.startswith("/api/workspace/v1/") for p in spec["paths"])
