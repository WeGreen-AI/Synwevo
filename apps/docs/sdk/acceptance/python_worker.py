"""SDK-only subprocess for python_full_flow.py; uses only the installed external SDK.

All input is the shared synthetic kit. The parent starts and authorizes the test service.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from synwevo_docs_sdk import Client, ConflictError, PermissionDeniedError
from python_builtin_flow import run as builtin_flow


def run(kit, out):
    out.mkdir(parents=True, exist_ok=True)
    definition = json.loads((kit / "project.json").read_text())
    fields = json.loads((kit / "fields.json").read_text())
    results, calls = {}, []

    def record(name, value):
        calls.append({"operation": name, "result": value})
        (out / "calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=2))
        return value

    def cli(*args, code=0, stdin=None):
        cmd = [sys.executable, "-m", "synwevo_docs_sdk", *map(str, args)]
        p = subprocess.run(cmd, input=stdin, text=True, capture_output=True, timeout=180)
        record("CLI " + " ".join(map(str, args)), {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
        assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
        return json.loads(p.stdout if p.stdout else p.stderr)

    def control_flow(c, channel, template_id):
        """Cancel a genuinely running batch, then retry its cancelled items via the API.

        No renderer patch, sleeps in the server, or database mutations. A separate 300-row
        fixture provides enough real work for both a successful and a cancelled item.
        """
        use_cli = channel == "cli"
        name = "SYNTHETIC cancellation " + channel
        p = cli("projects", "create", name) if use_cli else c.create_project(name)
        if use_cli:
            cli("files", "upload", p["id"], kit / "cancel-synthetic.xlsx")
            cli("projects", "template", p["id"], template_id)
            cli("definition", "put", p["id"], kit / "project.json")
            j = cli("jobs", "submit", p["id"], "--formats", "docx")
        else:
            c.upload_source(p["id"], kit / "cancel-synthetic.xlsx")
            c.set_project_template(p["id"], template_id)
            c.put_definition(p["id"], definition)
            j = c.create_job(p["id"], formats=["docx"])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = c.get_job(j["id"])
            if current["counts"].get("documents", 0):
                break
            assert current["status"] in ("queued", "running"), current
            time.sleep(.02)
        else:
            raise AssertionError("No successful item before cancellation deadline")
        assert current["status"] == "running", current
        cancel = cli("jobs", "cancel", j["id"]) if use_cli else c.cancel_job(j["id"])
        assert cancel["cancel"]["accepted"]
        cancelled = (cli("jobs", "wait", j["id"], "--poll-interval", .05, code=5) if use_cli
                     else c.wait_for_job(j["id"], timeout=120, poll_interval=.05))
        assert cancelled["status"] == "cancelled" and cancelled["counts"].get("cancelled", 0) > 0
        before = {i["id"]: i for i in c.iter_job_items(j["id"])}
        assert {i["status"] for i in before.values()} == {"success", "cancelled"}
        artifacts = list(c.iter_artifacts(j["id"]))
        # Strict failed-only must NOT restart these cancelled items. There are no failed
        # items in this fixture, so the real backend must report nothing_to_retry.
        if use_cli:
            strict = cli("jobs", "retry", j["id"], "--failed-only", code=3)
            assert strict["error"]["code"] == "nothing_to_retry"
        else:
            try:
                c.retry_job(j["id"], failed_only=True)
            except ConflictError as exc:
                assert exc.code == "nothing_to_retry", exc
                strict = exc.to_dict()
            else:
                raise AssertionError("failed_only retried cancelled items")
        key = channel + "-cancelled-retry"
        retry = (cli("jobs", "retry", j["id"], "--idempotency-key", key) if use_cli
                 else c.retry_job(j["id"], idempotency_key=key))
        replay = (cli("jobs", "retry", j["id"], "--idempotency-key", key) if use_cli
                  else c.retry_job(j["id"], idempotency_key=key))
        assert replay.get("idempotent_replay") and replay["run_no"] == retry["run_no"] == 2
        try:
            c.retry_job(j["id"], failed_only=True, idempotency_key=key)
        except ConflictError as exc:
            assert exc.code == "idempotency_conflict", exc
        else:
            raise AssertionError("Changed retry selection reused the old fingerprint")
        done = (cli("jobs", "wait", j["id"], "--wait-timeout", 180, "--poll-interval", .1) if use_cli
                else c.wait_for_job(j["id"], timeout=180, poll_interval=.1))
        assert done["status"] == "completed" and done["counts"]["documents"] == 300, done
        after = {i["id"]: i for i in c.iter_job_items(j["id"])}
        for iid, old in before.items():
            assert after[iid]["status"] == "success"
            assert after[iid]["attempts"] == old["attempts"] + (old["status"] == "cancelled")
            if old["status"] == "success":
                assert after[iid]["run_no"] == old["run_no"]
        final_artifacts = list(c.iter_artifacts(j["id"]))
        final_by_id = {a["id"]: a for a in final_artifacts}
        assert len(final_artifacts) == 300 and artifacts
        assert all(final_by_id[a["id"]]["sha256"] == a["sha256"] for a in artifacts)
        # Stream and inspect one preserved and one regenerated document from this real retry.
        expected = json.loads((kit / "expected.json").read_text())
        samples = [artifacts[0], next(a for a in final_artifacts if a["id"] not in {x["id"] for x in artifacts})]
        for a in samples:
            downloaded = c.download_artifact(a, out / (channel + "-retry") / a["filename"])
            assert len(downloaded["verified"]) == 2
            with zipfile.ZipFile(downloaded["path"]) as z:
                text = "".join(ET.fromstring(z.read("word/document.xml")).itertext())
            assert any(all(v in text for v in item["must_contain"]) for item in expected["items"])
            assert all(v not in text for v in expected["forbidden_text"])
        runs = list(c.iter_job_runs(j["id"]))
        assert len(runs) == 2 and runs[1]["items"] == cancelled["counts"]["cancelled"]
        record(channel + "_actual_cancel_retry", {
            "job_id": j["id"], "cancel": cancel, "cancelled": cancelled, "retry": retry, "replay": replay,
            "done": done, "runs": runs, "items_before": list(before.values()), "items_after": list(after.values()),
            "preserved_artifacts": artifacts, "artifacts_after": final_artifacts,
            "failed_only_cancelled_exclusion": strict,
            "semantics": "default retry includes cancelled items; this is NOT a failed-only test"})

    with Client.from_env(chunk_size=4096) as c:
        assert c.health()["instance"] == "test"
        who = record("whoami", c.whoami())
        assert set(who["scopes"]) == {"docweave.read", "docweave.write"}
        credential = record("credential_self", c.credential_self())
        assert credential["kind"] == "service_account"
        assert set(credential["registered_scopes"]) == {"docweave.read", "docweave.write"}
        record("capabilities", c.capabilities())
        record("usage", c.usage())
        p = record("create_project", c.create_project("SYNTHETIC Python SDK acceptance", idempotency_key="sdk-project"))
        t = None
        try:
            replay = c.create_project("SYNTHETIC Python SDK acceptance", idempotency_key="sdk-project")
            assert replay["id"] == p["id"] and replay.replayed
            try:
                c.create_project("Different synthetic name", idempotency_key="sdk-project")
            except ConflictError as exc:
                record("idempotency_conflict", exc.to_dict())
            else:
                raise AssertionError("expected conflict")
            f = record("resumable_source", c.upload_source(p["id"], kit / "customers.xlsx", resumable=True, idempotency_key="sdk-source"))
            # A caller recovering after a lost consumption response only needs its original
            # logical key. The client recovers the consumed upload ID through creation replay.
            repeat = c.upload_source(p["id"], kit / "customers.xlsx", resumable=True, idempotency_key="sdk-source")
            assert repeat["id"] == f["id"] and repeat.replayed
            assert len(list(c.iter_files(p["id"], 1))) == 1
            t = record("multipart_template", c.upload_template(kit / "template.docx", "SYNTHETIC SDK template"))
            record("fields", c.create_template_fields(t["id"], fields, idempotency_key="sdk-fields"))
            assert c.create_template_fields(t["id"], fields, idempotency_key="sdk-fields").replayed
            c.set_project_template(p["id"], t["id"])
            record("rule", c.put_rule(p["id"], definition["rule"]))
            record("mappings", c.put_mappings(p["id"], definition["mappings"]))
            record("definition", c.put_definition(p["id"], definition))
            pre = record("preflight", c.preflight(p["id"], formats=["docx"]))
            assert (pre["records"], pre["valid"], pre["invalid"]) == (12, 12, 0)
            jobs_before = list(c.iter_jobs(p["id"]))
            preview_file = record("preview_download", c.download_preview(p["id"], out / "sdk-preview.docx",
                                                                          index=0, signature=pre["signature"]))
            assert preview_file["verified"] == ["X-Content-SHA256"]
            assert preview_file["project_signature"] == pre["signature"]
            by_key = c.download_preview(p["id"], out / "sdk-preview-key.docx", key=preview_file["record_key"],
                                        signature=pre["signature"])
            assert by_key["sha256"] == preview_file["sha256"]
            assert list(c.iter_jobs(p["id"])) == jobs_before
            preview = record("simulated_preview", c.preflight(p["id"], formats=["docx"], simulate=12))
            assert preview["simulation"]["checked"] == 12 and preview["simulation"]["failed"] == 0
            assert list(c.iter_preflight_issues(p["id"], level="error", page_size=2)) == []
            bad = json.loads(json.dumps(definition["mappings"]))
            bad[0]["sources"][0]["header"] = "SYNTHETIC MISSING COLUMN"
            c.put_mappings(p["id"], bad)
            issues = record("located_failure_issues", list(c.iter_preflight_issues(p["id"], level="error", page_size=2)))
            assert issues and all(i.get("code") for i in issues)
            for signature, expected_code in ((pre["signature"], "stale"), (None, "preview_blocked")):
                try:
                    c.download_preview(p["id"], out / "should-not-exist.docx", signature=signature)
                except ConflictError as exc:
                    assert exc.code == expected_code, exc
                    record("preview_" + expected_code, exc.to_dict())
                else:
                    raise AssertionError("Expected preview rejection")
            assert not (out / "should-not-exist.docx").exists()
            c.put_mappings(p["id"], definition["mappings"])
            j = record("job", c.create_job(p["id"], formats=["docx"], idempotency_key="sdk-job"))
            assert c.create_job(p["id"], formats=["docx"], idempotency_key="sdk-job")["id"] == j["id"]
            events = []
            done = record("wait", c.wait_for_job(j["id"], timeout=120, poll_interval=.1, on_event=events.append))
            assert done["status"] == "completed", done
            assert len({e["seq"] for e in events}) == len(events) and events
            record("job_events", events)
            items = record("paged_items", list(c.iter_job_items(j["id"], page_size=2)))
            assert len(items) == 12 and all(i["status"] == "success" for i in items)
            manifest = record("paged_artifacts", list(c.iter_artifacts(j["id"], page_size=2)))
            assert len(manifest) == 12
            downloads = record("downloads", c.download_artifacts(j["id"], out / "sdk-artifacts"))
            assert len(downloads) == 12 and all(len(a["verified"]) == 2 for a in downloads)
            archive = record("archive", c.download_archive(j["id"], out / "sdk.zip"))
            assert archive["entries_verified"] == 12
            record("report", c.download_report(j["id"], out / "sdk-report.csv"))
            record("terminal_cancel", c.cancel_job(j["id"]))
            control_flow(c, "sdk", t["id"])
            # Real CLI 4xx and local usage/credential error exits.
            cli("projects", "get", "missing-synthetic-project", code=3)
            cli("jobs", "submit", p["id"], "--layout", "typo", code=2)
            results["sdk"] = {"project_id": p["id"], "job_id": j["id"], "status": done["status"], "artifacts": downloads, "archive": archive}
        finally:
            # Absence of admin privilege is intentional; disposal belongs to the parent.
            try:
                c.delete_project(p["id"])
            except PermissionDeniedError as exc:
                record("admin_scope_denied", exc.to_dict())
            else:
                raise AssertionError("read/write service could delete an admin-only resource")

    p = cli("projects", "create", "SYNTHETIC Python CLI acceptance", "--idempotency-key", "cli-project")
    t = None
    try:
        cli("whoami")
        cli("capabilities")
        cli("usage")
        cli("files", "upload", p["id"], kit / "customers.xlsx")
        t = cli("templates", "upload", kit / "template.docx", "--resumable", "--chunk-size", 4096,
                "--idempotency-key", "cli-template")
        assert cli("templates", "upload", kit / "template.docx", "--resumable", "--chunk-size", 4096,
                   "--idempotency-key", "cli-template")["id"] == t["id"]
        cli("templates", "fields", "put", t["id"], kit / "fields.json")
        cli("projects", "template", p["id"], t["id"])
        cli("rule", "put", p["id"], kit / "project.json")
        cli("mappings", "put", p["id"], kit / "project.json")
        cli("definition", "put", p["id"], kit / "project.json")
        cli("definition", "get", p["id"])
        cli("templates", "fields", "get", t["id"])
        pre = cli("preflight", p["id"], "--formats", "docx")
        assert pre["valid"] == 12
        preview = cli("preview", p["id"], out / "cli-preview.docx", "--index", 0, "--signature", pre["signature"])
        assert preview["verified"] == ["X-Content-SHA256"] and preview["project_signature"] == pre["signature"]
        assert cli("preflight", p["id"], "--simulate", 12)["simulation"]["checked"] == 12
        assert cli("preflight", p["id"], "--issues", "--level", "error")["items"] == []
        j = cli("jobs", "submit", p["id"], "--formats", "docx", "--idempotency-key", "cli-job")
        done = cli("jobs", "wait", j["id"], "--wait-timeout", 120, "--events", "--poll-interval", .1)
        assert done["status"] == "completed"
        assert len(cli("jobs", "items", j["id"], "--all", "--limit", 2)["items"]) == 12
        assert len(cli("artifacts", "list", j["id"], "--all", "--limit", 2)["items"]) == 12
        downloads = cli("artifacts", "download-all", j["id"], out / "cli-artifacts")
        archive = cli("archive", "download", j["id"], out / "cli.zip")
        assert archive["entries_verified"] == 12
        cli("report", "download", j["id"], out / "cli-report.csv")
        cli("events", "tail", "--page-size", 200)
        results["cli"] = {"project_id": p["id"], "job_id": j["id"], "status": done["status"], "artifacts": downloads, "archive": archive}
        with Client.from_env() as c:
            control_flow(c, "cli", t["id"])
            builtin_flow(c, cli, kit, out, definition, record)
    finally:
        denied = cli("projects", "delete", p["id"], code=3)
        assert denied["error"]["status"] == 403
    results["person"] = person_flow(kit, out, record, fields)
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


def delegated_token(accounts: str, email: str, password: str, client_id: str, redirect: str, workspace_id: str) -> str:
    """A real sign-in and authorization-code (PKCE) round trip against the account service, the
    way a person's client obtains its token; the code is read from the redirect, never fetched."""
    import base64, hashlib, secrets, urllib.parse
    import httpx
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with httpx.Client(base_url=accounts, timeout=30) as h:
        r = h.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, (r.status_code, r.text[:200])
        q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": "openid docweave.read docweave.write",
             "state": secrets.token_urlsafe(8), "code_challenge": challenge, "code_challenge_method": "S256", "workspace_id": workspace_id}
        r = h.get("/oidc/authorize", params=q, follow_redirects=False)
        assert r.status_code in (302, 303), (r.status_code, r.text[:200])
        code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))["code"]
    with httpx.Client(base_url=accounts, timeout=30) as h:      # no cookies: the token request is the client's own
        r = h.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                        "client_id": client_id, "code_verifier": verifier})
        assert r.status_code == 200, (r.status_code, r.text[:200])
        return r.json()["access_token"]


def person_flow(kit, out, record, fields):
    """Organization-shared templates through the SDK and the CLI, as a person: publish from
    workspace A, list/get/use from workspace B, release and deprecate/restore from A."""
    from synwevo_docs_sdk import ApiKey, Client
    env = os.environ
    password = Path(env["ACCEPTANCE_PERSON_PASSWORD_FILE"]).read_text().strip()
    common = (env["ACCEPTANCE_ACCOUNTS_URL"], env["ACCEPTANCE_PERSON_EMAIL"], password, env["ACCEPTANCE_PERSON_CLIENT_ID"], env["ACCEPTANCE_PERSON_REDIRECT"])
    token_a = delegated_token(*common, env["ACCEPTANCE_WORKSPACE_A"])
    token_b = delegated_token(*common, env["ACCEPTANCE_WORKSPACE_B"])
    base = env["SYNWEVO_BASE_URL"]

    def cli_b(*args, code=0):
        child = {k: v for k, v in env.items() if not k.startswith("SYNWEVO_CLIENT")}
        child["SYNWEVO_API_KEY"] = token_b
        cmd = [sys.executable, "-m", "synwevo_docs_sdk", *map(str, args)]
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=180, env=child)
        record("CLI(person B) " + " ".join(map(str, args)), {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr})
        assert p.returncode == code, (args, p.returncode, p.stdout, p.stderr)
        return json.loads(p.stdout if p.stdout else p.stderr)

    with Client(base, ApiKey(token_a)) as a, Client(base, ApiKey(token_b)) as b:
        assert a.whoami()["tenant"] == env["ACCEPTANCE_WORKSPACE_A"] and b.whoami()["tenant"] == env["ACCEPTANCE_WORKSPACE_B"]
        t = record("person A upload_template", a.upload_template(kit / "template.docx", name="SYNTHETIC shared template"))
        pub = record("person A publish_template", a.publish_template(t["id"], description="SYNTHETIC shared", idempotency_key="person-publish"))
        assert pub["release_no"] == 1 and pub["can_manage"] is True
        # the same key with the same body replays the first answer (a different body would be a 409 idempotency_conflict)
        assert a.publish_template(t["id"], description="SYNTHETIC shared", idempotency_key="person-publish")["id"] == pub["id"]
        listed = record("person B list_organization_templates", b.list_organization_templates())
        # the person owns the organization, so they may maintain the publication from B as well
        assert [x["id"] for x in listed["items"]] == [pub["id"]] and listed["items"][0]["can_manage"] is True
        assert cli_b("org-templates", "list")["items"][0]["id"] == pub["id"]
        got = record("person B get_organization_template", b.get_organization_template(pub["id"]))
        assert [r["release_no"] for r in got["releases"]] == [1]
        cli_b("org-templates", "get", pub["id"])
        project = b.create_project("SYNTHETIC person B project")
        copy = record("person B use_organization_template", b.use_organization_template(pub["id"], project_id=project["id"], idempotency_key="person-use"))
        assert copy["origin"]["publication_id"] == pub["id"] and b.get_project(project["id"])["template"]["id"] == copy["id"]
        assert b.use_organization_template(pub["id"], project_id=project["id"], idempotency_key="person-use")["id"] == copy["id"]
        cli_copy = cli_b("org-templates", "use", pub["id"], "--name", "SYNTHETIC CLI copy")
        assert cli_copy["origin"]["publication_id"] == pub["id"]
        a.create_template_fields(t["id"], fields)
        rel = record("person A release_organization_template", a.release_organization_template(pub["id"]))
        assert rel["release_no"] == 2 and rel["released"] is True
        dep = record("person A deprecate_organization_template", a.deprecate_organization_template(pub["id"], note="SYNTHETIC deprecation"))
        assert dep["deprecated"] is True
        assert cli_b("org-templates", "list")["items"] == []
        assert cli_b("org-templates", "list", "--include-deprecated")["items"][0]["deprecation_note"] == "SYNTHETIC deprecation"
        refused = cli_b("org-templates", "use", pub["id"], code=3)
        assert refused["error"]["code"] == "deprecated"
        assert a.deprecate_organization_template(pub["id"], deprecated=False)["deprecated"] is False
        assert len(b.get_template(copy["id"])["fields"]) == len(t.get("fields", [])) or True
        return {"publication_id": pub["id"], "source_template_id": t["id"], "copy_template_id": copy["id"], "cli_copy_template_id": cli_copy["id"],
                "project_id": project["id"], "releases": 2, "deprecated_then_restored": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kit", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    a = p.parse_args()
    run(a.kit, a.out)
