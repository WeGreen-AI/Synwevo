"""Synthetic integration fixture and redacted HTTP evidence around the real ASGI application.

Only identity/client provisioning uses core fixture APIs. Token issuance and every Docs
business operation run through the unchanged production HTTP routes, over loopback.
"""
import argparse
import json
import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs


def bootstrap(root):
    from synwevo_core.apps import Apps, bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.oidc import Clients
    from synwevo_core.store import Store

    store = Store(root / "data")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    user = ident.create_user("python-fixture@example.invalid", secrets.token_urlsafe(40), status="active")
    workspace = ident.workspaces_for(user["id"])[0]["id"]
    Apps(store, ident).enable_for_workspace(workspace, "docweave")
    client, secret = Clients(store).register(
        client_id="synthetic-python-sdk", app_id="docweave", name="SYNTHETIC SDK acceptance",
        redirect_uris=[], scopes="", confidential=True, kind="service", workspace_id=workspace,
        api_scopes="docweave.read docweave.write", audiences=["docweave-api"])
    secret_file = root / "client-secret"
    with open(secret_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
        f.write(secret)
    # A person with an organization of two workspaces, and a public client they sign in through:
    # the delegated leg of the acceptance (organization-shared templates need a person).
    from synwevo_core.organizations import Organizations
    password = secrets.token_urlsafe(24)
    person = ident.create_user("python-person@example.invalid", password, status="active", display_name="SYNTHETIC person",
                               with_personal_workspace=True)
    personal_workspace = next(w["id"] for w in ident.workspaces_for(person["id"]) if w["kind"] == "personal")
    Apps(store, ident).enable_for_workspace(personal_workspace, "docweave")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (1.0, person["id"]))
    orgs = Organizations(store, ident)
    created = orgs.create(person["id"], "SYNTHETIC org", "SYNTHETIC team A", "fixture-idem-python-person-org")
    ws_a = created["workspace"]["id"]
    ws_b = orgs.create_workspace(created["organization"]["id"], person["id"], "SYNTHETIC team B")["id"]
    for wid in (ws_a, ws_b):
        store.execute("INSERT OR IGNORE INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
                      "VALUES (?,?,'owner',1,1,1,?)", (wid, person["id"], person["id"]))
        Apps(store, ident).enable_for_workspace(wid, "docweave")
    redirect = "http://127.0.0.1:1/synthetic-callback"        # never fetched: the code is read from the Location header
    Clients(store).register(client_id="synthetic-python-person", app_id="docweave", name="SYNTHETIC person client",
                            redirect_uris=[redirect], scopes="openid docweave.read docweave.write", audiences=["docweave-api"])
    with open(root / "person-password", "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
        f.write(password)
    (root / "identity.json").write_text(json.dumps({
        "client_id": client["client_id"], "kind": client["kind"], "workspace": workspace,
        "scopes": client["api_scopes"].split(), "audiences": client["audiences"],
        "person": {"email": "python-person@example.invalid", "client_id": "synthetic-python-person", "redirect_uri": redirect,
                   "organization": created["organization"]["id"], "workspace_a": ws_a, "workspace_b": ws_b,
                   "personal_workspace": personal_workspace},
        "bootstrap": "Store + Identity + Apps + Clients + Organizations fixture APIs; no business-table writes"}))


class Evidence:
    """Observe real requests without changing handlers or persisting credentials/bodies."""

    def __init__(self, app, path):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        auth = headers.get(b"authorization", b"")
        token_request = scope["path"] == "/oidc/token"
        request_body, response_body = bytearray(), bytearray()
        row = {"method": scope["method"], "path": scope["path"],
               "cookie": b"cookie" in headers, "set_cookie": False,
               "authorization": "legacy_api_key" if auth.startswith(b"Bearer dw_") else
                                "bearer" if auth.startswith(b"Bearer ") else "none"}

        async def receive_tap():
            message = await receive()
            if token_request:
                request_body.extend(message.get("body", b""))
            return message

        async def send_tap(message):
            if message["type"] == "http.response.start":
                row["status"] = message["status"]
                row["set_cookie"] = any(k.lower() == b"set-cookie" for k, _ in message["headers"])
            if token_request and message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, receive_tap, send_tap)
        finally:
            principal = scope.get("state", {}).get("principal", {})
            row["principal"] = {k: principal.get(k) for k in ("kind", "client_id", "scopes", "tenant")}
            route = scope.get("route")
            row["route"] = getattr(route, "path", None)
            if token_request:
                form = parse_qs(request_body.decode())
                row["grant"] = {k: form.get(k) for k in ("grant_type", "client_id", "scope", "resource")}
                body = json.loads(response_body or b"{}")
                row["token_response"] = {k: body.get(k) for k in ("token_type", "scope", "expires_in", "error")}
            with self.path.open("a") as f:
                f.write(json.dumps(row) + "\n")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--bootstrap", action="store_true")
    p.add_argument("--audit", type=Path)
    args = p.parse_args()
    assert os.environ.get("SYNWEVO_TEST_INSTANCE") == "1"
    assert Path(os.environ["SYNWEVO_DATA_DIR"]).resolve() == (args.root / "data").resolve()
    if args.bootstrap:
        bootstrap(args.root)
        return
    import uvicorn
    from synwevo_server.app import create_app
    app = create_app(str(args.root / "data"))
    if os.environ["SYNWEVO_ROLE"] == "app":
        from synwevo_docs.api.v1 import openapi_subset
        (args.audit.parent / "openapi.json").write_text(json.dumps(openapi_subset(app), ensure_ascii=False, indent=2))
    uvicorn.run(Evidence(app, args.audit), host="127.0.0.1", port=int(os.environ["SYNWEVO_PORT"]),
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
