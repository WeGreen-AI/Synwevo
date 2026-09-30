"""Run installed Python and packed TypeScript directory SDKs against a fresh Accounts service.

No existing service URL is accepted. Identity fixtures are synthetic, credentials are issued
through real admin/owner HTTP approval, and invitation delivery uses a disposable file mailbox.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[3]
RESOURCE = "urn:synwevo:platform:workspace"
SCOPES = ["workspace.read", "workspace.members.write"]


def run(argv, evidence, name, *, env=None, cwd=None):
    done = subprocess.run([str(a) for a in argv], env=env, cwd=cwd, capture_output=True, text=True, timeout=180)
    (evidence / f"{name}.stdout").write_text(done.stdout)
    (evidence / f"{name}.stderr").write_text(done.stderr)
    assert done.returncode == 0, f"{name} failed; see {evidence / (name + '.stderr')}"
    return done.stdout


def provision(base, identity):
    def post(c, path, body):
        r = c.post(path, json=body, headers={"X-Synwevo-CSRF": c.cookies.get("dw_csrf") or ""})
        assert r.status_code in (200, 201), (path, r.status_code)
        return r.json()
    with httpx.Client(base_url=base, trust_env=False) as admin, httpx.Client(base_url=base, trust_env=False) as owner:
        for client, user in ((admin, "admin"), (owner, "owner")):
            post(client, "/api/auth/login", {"email": identity[user], "password": identity["password"]})
            post(client, "/api/auth/reauth", {"password": identity["password"], "scope": "admin"})
        body = {"workspace_id": identity["workspace"], "name": "SYNTHETIC directory SDK", "credential_kind": "service_account",
                "scopes": SCOPES, "resources": [RESOURCE], "expires_in_days": 1, "reason": "Synthetic SDK acceptance approval"}
        approval = post(admin, "/api/admin/api-platform/approvals", body)
        post(owner, f"/api/auth/api-approvals/{approval['id']}/approve", {"scopes": SCOPES, "resources": [RESOURCE], "reason": body["reason"]})
        return post(admin, f"/api/admin/api-platform/approvals/{approval['id']}/issue", {"reason": body["reason"]})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", required=True, type=Path)
    p.add_argument("--sdk-python", required=True, type=Path)
    p.add_argument("--node", default="node")
    p.add_argument("--npm", default="npm")
    args = p.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("directory_testenv", ROOT / "apps/docs/scripts/_testenv.py")
    testenv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(testenv)
    with tempfile.TemporaryDirectory(prefix="synwevo-directory-sdk-") as tmp:
        root = Path(tmp)
        port = testenv._free_port()
        base = f"http://127.0.0.1:{port}"
        env = testenv._child_env(root, port)
        env.update(SYNWEVO_ROLE="accounts", SYNWEVO_UI_AUTH="session", SYNWEVO_ISSUER=base, SYNWEVO_COOKIE_SECURE="0",
                   SYNWEVO_MAIL_MODE="file", SYNWEVO_MAIL_DIR=str(root / "mail"), NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
        launcher = Path(__file__).with_name("workspace_service.py")
        run([sys.executable, launcher, "--root", root, "--bootstrap"], evidence, "bootstrap", env=env, cwd=root)
        identity = json.loads((root / "identity.json").read_text())
        log = evidence / "server.log"
        with log.open("wb") as handle:
            process = subprocess.Popen([sys.executable, str(launcher), "--root", str(root), "--audit", str(evidence / "http.jsonl")],
                                       env=env, cwd=root, stdout=handle, stderr=handle)
        try:
            # macOS's system proxy must also be bypassed for the harness health probe.
            os.environ["NO_PROXY"] = "127.0.0.1,localhost"
            testenv._wait(base, process, log)
            assert testenv.health(base)["instance"] == "test"
            credential = provision(base, identity)
            secret = root / "directory-secret"
            with open(secret, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
                f.write(credential["client_secret"])
            child = {k: v for k, v in env.items() if not k.startswith(("SYNWEVO_", "DOCWEAVE_"))}
            child.update(SYNWEVO_WORKSPACE_BASE_URL=base, SYNWEVO_WORKSPACE_CLIENT_ID=credential["client_id"],
                         SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE=str(secret), SYNWEVO_WORKSPACE_SCOPE=" ".join(SCOPES),
                         ACCEPTANCE_DIRECTORY_WORKSPACE=identity["workspace"])
            python = run([args.sdk_python.absolute(), ROOT / "apps/docs/sdk/acceptance/python_workspace.py"], evidence, "python", env={**child,
                         "ACCEPTANCE_DIRECTORY_MEMBER": identity["members"]["python"]}, cwd=root)
            packed = json.loads(run([args.npm, "--cache", root / "npm-cache", "pack", ROOT / "apps/docs/sdk/typescript", "--json", "--pack-destination", root],
                                    evidence, "npm-pack", cwd=root))
            run([args.npm, "--cache", root / "npm-cache", "install", "--prefix", root, "--ignore-scripts", "--no-audit", "--no-fund", root / packed[0]["filename"]],
                evidence, "npm-install", cwd=root)
            script = root / "ts_workspace.mjs"
            shutil.copyfile(ROOT / "apps/docs/sdk/acceptance/ts_workspace.mjs", script)
            typescript = run([args.node, script], evidence, "typescript", env={**child,
                             "ACCEPTANCE_DIRECTORY_MEMBER": identity["members"]["typescript"]}, cwd=root)
            results = {"python": json.loads(python), "typescript": json.loads(typescript)}
            from synwevo_core.store import Store
            store = Store(root / "data")
            invitation_ids = {iid for out in results.values() for iid in out["invitation_ids"]}
            mails = store.all("SELECT ref_id,status FROM mail_outbox WHERE kind='invite'")
            assert {row["ref_id"] for row in mails} == invitation_ids and len(mails) == 4
            audit = store.all("SELECT actor_kind,actor_id,action,meta FROM audit_events WHERE actor_kind='machine' "
                              "AND (action LIKE 'invite.%' OR action LIKE 'member.%')")
            assert audit and all(row["actor_id"] == credential["client_id"] for row in audit)
            assert all(json.loads(row["meta"])["actor_kind"] == "service_account" for row in audit)
            actions = {row["action"] for row in audit}
            assert {"invite.create", "invite.resend", "invite.revoke", "member.role", "member.remove"} <= actions
            assert len(audit) == 10, "replayed invitations and stale role writes must not duplicate mutation audits"
            summary = {"result": "passed", "scope": SCOPES, "resource": RESOURCE, "clients": results,
                       "outbox_messages": len(mails), "audit_actions": sorted(actions), "typescript_package": packed[0]["shasum"]}
            (evidence / "acceptance.json").write_text(json.dumps(summary, indent=2) + "\n")
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    observations = [json.loads(line) for line in (evidence / "http.jsonl").read_text().splitlines()]
    business = [r for r in observations if r["path"].startswith("/api/workspace/v1/")]
    assert business and all(r["authorization"] == "bearer" and not r["cookie"] and not r["set_cookie"] for r in business)
    assert all(r["principal"]["kind"] == "service_account" and set(r["principal"]["scopes"]) == set(SCOPES) for r in business)
    print(json.dumps({"result": "passed", "languages": ["python", "typescript"], "outbox_messages": 4, "http_requests": len(business), "evidence": str(evidence)}))


if __name__ == "__main__":
    main()
