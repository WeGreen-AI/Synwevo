"""Disposable Accounts service for SDK directory acceptance; only synthetic identity setup."""
import argparse
import json
import os
from pathlib import Path
import secrets


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--bootstrap", action="store_true")
    p.add_argument("--audit", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    assert os.environ.get("SYNWEVO_TEST_INSTANCE") == "1"
    assert Path(os.environ["SYNWEVO_DATA_DIR"]).resolve() == root / "data"
    if args.bootstrap:
        from synwevo_core.identity import Identity
        from synwevo_core.store import Store
        ident = Identity(Store(root / "data"))
        password = secrets.token_urlsafe(32)
        admin = ident.create_user("directory-admin@example.invalid", password, platform_role="admin", status="active")
        owner = ident.create_user("directory-owner@example.invalid", password, status="active")
        workspace = ident.workspaces_for(owner["id"])[0]["id"]
        members = {}
        for language in ("python", "typescript"):
            user = ident.create_user(f"directory-{language}@example.invalid", password, status="active")
            ident.add_member(workspace, user["id"], "viewer")
            members[language] = user["id"]
        with open(root / "identity.json", "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
            json.dump({"password": password, "admin": admin["email"], "owner": owner["email"], "workspace": workspace, "members": members}, f)
        return
    import uvicorn
    from synwevo_server.app import create_app
    from python_service import Evidence
    app = create_app(str(root / "data"), role="accounts", start_jobs=False)
    uvicorn.run(Evidence(app, args.audit), host="127.0.0.1", port=int(os.environ["SYNWEVO_PORT"]), log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
