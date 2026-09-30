"""Audit the separate directory OpenAPI against both SDKs and optional real HTTP evidence.

This is a reviewed method inventory, not proof of every parameter or failure behavior. Node is
used only to inspect the local exported WorkspaceClient prototype; no request is sent.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

ROUTES = {
    "GET /api/workspace/v1/whoami": ("whoami", "whoami"),
    "GET /api/workspace/v1/members": ("list_members", "listMembers"),
    "PATCH /api/workspace/v1/members/{user_id}": ("change_member", "changeMember"),
    "POST /api/workspace/v1/members/{user_id}/remove": ("remove_member", "removeMember"),
    "GET /api/workspace/v1/invitations": ("list_invitations", "listInvitations"),
    "POST /api/workspace/v1/invitations": ("create_invitation", "createInvitation"),
    "POST /api/workspace/v1/invitations/{iid}/resend": ("resend_invitation", "resendInvitation"),
    "POST /api/workspace/v1/invitations/{iid}/revoke": ("revoke_invitation", "revokeInvitation"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("openapi", type=Path)
    parser.add_argument("--http-evidence", action="append", type=Path, default=[])
    parser.add_argument("--node", default="node")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    py = root / "synwevo_docs_sdk/workspace.py"
    ts = root.parent / "typescript/synwevo-docs.mjs"
    tree = ast.parse(py.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WorkspaceClient")
    py_methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    js = "import(" + json.dumps(ts.as_uri()) + ").then(m => console.log(JSON.stringify(Object.getOwnPropertyNames(m.WorkspaceClient.prototype))))"
    ts_methods = set(json.loads(subprocess.check_output([args.node, "--input-type=module", "--eval", js], text=True)))
    actual, ids, operations = set(), set(), []
    observed = {}
    for path in args.http_evidence:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            route = row["method"] + " " + (row.get("route") or row["path"])
            if route in ROUTES:
                observed.setdefault(route, Counter())[row["status"]] += 1
    spec = json.loads(args.openapi.read_text())
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method not in {"get", "post", "patch", "put", "delete", "head", "options", "trace"}:
                continue
            route = method.upper() + " " + path
            actual.add(route)
            assert route in ROUTES, f"unreviewed operation: {route}"
            assert operation["operationId"] not in ids
            ids.add(operation["operationId"])
            pm, tm = ROUTES[route]
            assert pm in py_methods and tm in ts_methods, route
            counts = observed.get(route, {})
            operations.append({"route": route, "operationId": operation["operationId"], "python": pm, "typescript": tm,
                               "observed_status_counts": {str(k): v for k, v in sorted(counts.items())},
                               "real_service_success_observed": any(200 <= k < 300 for k in counts)})
    assert actual == set(ROUTES), {"stale_routes": sorted(set(ROUTES) - actual)}
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"openapi_sha256": sha(args.openapi), "python_sha256": sha(py), "typescript_sha256": sha(ts),
              "counts": {"operations": len(operations), "python_implemented": len(operations), "typescript_implemented": len(operations),
                         "real_service_success_observed": sum(o["real_service_success_observed"] for o in operations)},
              "interpretation": "Exact reviewed operation inventory and local SDK method existence; HTTP observations cover these runs only.",
              "evidence": [{"file": p.name, "sha256": sha(p)} for p in args.http_evidence], "operations": operations}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
