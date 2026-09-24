"""Opt-in real Chrome acceptance against an already-running isolated local platform.

Run with .venv/bin/python tests/integration/api_platform_browser_acceptance.py
--fixture /private/tmp/<isolated-run>/api-console-fixture.json --output <evidence-dir>.
The 0600 fixture contains explicitly synthetic, verified admin/owner accounts and
accounts/docs/portal origins. No account or business object is inserted by this script.
Secrets remain in memory, are never printed, and are excluded from screenshots.
After UI issuance, the installed Python SDK uses real loopback HTTP to create all
business objects, preview and generate the shared synthetic 12-document kit. It
does not require or change TEST_INSTANCE: the approved fixture is explicitly synthetic.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import tempfile
import uuid
import zipfile
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from playwright.sync_api import expect, sync_playwright
from synwevo_docs_sdk import APIError, ApiKey, Client


# Explicit values from the module-local Chinese-source catalog. These assertions
# exercise the translated mutation dialogs, not only translated navigation.
DIALOG_COPY = {
    "en": {
        "reason": "Reason (8–500 characters, recorded in the audit log)",
        "confirm": "Confirm action", "password": "Your password",
        "secret_title": "Secret shown only once", "secret": "Secret", "close": "Saved securely — close",
        "audit_reason": "Synthetic local browser acceptance approval",
    },
    "zh-Hans": {
        "reason": "操作理由（8 至 500 个字，将写入审计）",
        "confirm": "确认操作", "password": "你的口令",
        "secret_title": "密钥仅显示一次", "secret": "密钥", "close": "已安全保存，关闭",
        "audit_reason": "合成测试：简体中文轮换密钥验收",
    },
    "zh-Hant": {
        "reason": "操作理由（8 至 500 個字，將寫入稽核）",
        "confirm": "確認操作", "password": "你的密碼",
        "secret_title": "金鑰僅顯示一次", "secret": "金鑰", "close": "已安全儲存，關閉",
        "audit_reason": "合成測試：繁體中文撤回授權驗收",
    },
}


def private_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)


def installed_sdk_and_kit():
    """Load only the shared input/checker; all server operations use the installed SDK."""
    root = Path(__file__).resolve().parents[2]
    sdk_source = root / "apps/docs/sdk/python/synwevo_docs_sdk"
    installed = Path(importlib.util.find_spec("synwevo_docs_sdk").origin).parent
    hashes = {}
    for source in sorted(sdk_source.glob("*.py")):
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert hashlib.sha256((installed / source.name).read_bytes()).hexdigest() == digest, "Installed SDK differs from this checkout"
        hashes[source.name] = digest
    kit_path = root / "apps/docs/sdk/acceptance/make_kit.py"
    spec = importlib.util.spec_from_file_location("synthetic_api_console_kit", kit_path)
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    return kit, {"distribution": "synwevo-docs-sdk", "version": importlib.metadata.version("synwevo-docs-sdk"),
                 "source_sha256": hashes, "kit_sha256": hashlib.sha256(kit_path.read_bytes()).hexdigest()}


class BrowserSdkPipeline:
    """Actual socket transport; no browser cookies, server imports, fixture DB or mock engine."""

    def __init__(self, origin, token, workspace, output, stack, kit, provenance):
        self.origin, self.workspace, self.kit = origin, workspace, kit
        self.output = output / ("sdk-flow-" + uuid.uuid4().hex[:12])
        self.output.mkdir(mode=0o700)
        self.calls = []
        self.report = {"status": "running", "synthetic": True, "sdk": provenance,
                       "origin": origin, "transport": "httpx.Client real loopback HTTP; trust_env=False; no redirects or cookies",
                       "credential_source": "key captured from owner-approved administrator UI issuance",
                       "workspace_id": workspace, "authority_checks": {}}
        self.http = stack.enter_context(httpx.Client(timeout=30, trust_env=False, follow_redirects=False,
            event_hooks={"request": [self.before_request], "response": [self.after_response]}))
        self.original = self.active = Client(origin, ApiKey(token), http=self.http, max_retries=2,
                                             max_retry_wait=5, backoff_max=1)
        self.save()

    def before_request(self, request):
        assert str(request.url).split("?", 1)[0].startswith(self.origin + "/"), "SDK target left the approved local origin"
        assert not request.headers.get("cookie"), "SDK must never reuse the administrator session"
        if request.url.path.startswith("/api/v1/"):
            assert request.headers.get("authorization", "").startswith("Bearer ")

    def after_response(self, response):
        request = response.request
        # Headers/bodies/query strings are intentionally excluded, including all credentials.
        self.calls.append({"method": request.method, "path": request.url.path, "status": response.status_code,
                           "request_id": response.headers.get("x-request-id"),
                           "bearer": "authorization" in request.headers, "cookie": "cookie" in request.headers})
        private_json(self.output / "http-calls.json", self.calls)

    def save(self):
        self.report["http_calls"] = len(self.calls)
        private_json(self.output / "evidence.json", self.report)

    def generate(self):
        c = self.original
        health = c.health()
        self.report["service_instance_marker"] = health.get("instance")
        self.report["isolation"] = "Explicit synthetic fixture and temporary data; service instance marker is observed, never overridden"
        who = c.whoami()
        assert who["tenant"] == self.workspace
        assert set(who["scopes"]) == {"read", "write"}, "UI-issued key must remain restricted to read/write"
        expected = self.kit.make(self.output / "kit")
        definition = json.loads((self.output / "kit/project.json").read_text())
        fields = json.loads((self.output / "kit/fields.json").read_text())
        assert expected["samples"] == 12
        project = c.create_project("SYNTHETIC browser-approved Python SDK acceptance")
        self.project_id = project["id"]
        self.report["project_id"] = self.project_id
        self.save()
        source = c.upload_source(self.project_id, self.output / "kit/customers.xlsx", wait_timeout=120)
        assert source["status"] == "ready"
        template = c.upload_template(self.output / "kit/template.docx", "SYNTHETIC browser-approved SDK template")
        tid = template["id"]
        created = c.create_template_fields(tid, fields)
        assert len(created["created"]) == len(fields["fields"])
        c.set_project_template(self.project_id, tid)
        rule = c.put_rule(self.project_id, definition["rule"])
        assert rule["records"] == 12
        slots = {field["name"]: field for field in c.get_template(tid)["fields"]}
        mappings = [{"type": mapping["type"], "sources": mapping["sources"],
                     "target": {"binding": slots[mapping["field"]]["binding"]}} for mapping in definition["mappings"]]
        saved = c.put_mappings(self.project_id, mappings)
        assert len(saved["mappings"]) == len(fields["fields"])
        c.put_definition(self.project_id, definition)
        preflight = c.preflight(self.project_id, formats=["docx"], naming=expected["naming"])
        assert (preflight["records"], preflight["valid"], preflight["invalid"]) == (12, 12, 0)
        assert list(c.iter_jobs(self.project_id)) == []
        preview = c.download_preview(self.project_id, self.output / "preview.docx", index=0, signature=preflight["signature"])
        assert preview["verified"] == ["X-Content-SHA256"]
        assert preview["project_signature"] == preflight["signature"]
        preview_bytes = Path(preview["path"]).read_bytes()
        assert hashlib.sha256(preview_bytes).hexdigest() == preview["sha256"]
        assert not self.kit.check_docx(preview_bytes, expected["items"][0], expected["forbidden_text"])
        assert list(c.iter_jobs(self.project_id)) == [], "Native preview must not create a job"
        job = c.create_job(self.project_id, formats=["docx"], naming=expected["naming"])
        done = c.wait_for_job(job["id"], timeout=180, poll_interval=.2, max_poll_interval=1)
        assert done["status"] == "completed" and done["counts"]["documents"] == 12
        artifacts = list(c.iter_artifacts(job["id"], page_size=5))
        assert len(artifacts) == 12
        self.first_artifact = artifacts[0]
        downloads = c.download_artifacts(job["id"], self.output / "artifacts")
        archive = c.download_archive(job["id"], self.output / "generated.zip", verify=True)
        assert archive["entries_verified"] == 12
        assert hashlib.sha256(Path(archive["path"]).read_bytes()).hexdigest() == archive["sha256"]
        by_name = {a["filename"]: a for a in artifacts}
        downloaded = {a["filename"]: a for a in downloads}
        assert len(artifacts) == len(by_name) == len(downloads) == len(downloaded) == 12
        checks = []
        readback = self.output / "zip-readback"
        readback.mkdir(mode=0o700)
        with zipfile.ZipFile(archive["path"]) as bundle:
            entries = [entry for entry in bundle.infolist() if entry.filename.endswith(".docx")]
            zip_names = {Path(entry.filename).name: entry for entry in entries}
            assert len(entries) == len(zip_names) == 12
            for item in expected["items"]:
                filename = item["file_base"] + ".docx"
                metadata, download = by_name[filename], downloaded[filename]
                assert set(download["verified"]) == {"X-Content-SHA256", "产物清单"}
                data = Path(download["path"]).read_bytes()
                zipped = bundle.read(zip_names[filename])
                digest = hashlib.sha256(data).hexdigest()
                assert digest == download["sha256"] == metadata["sha256"] == hashlib.sha256(zipped).hexdigest()
                assert len(data) == len(zipped) == metadata["size"]
                assert not self.kit.check_docx(data, item, expected["forbidden_text"])
                assert not self.kit.check_docx(zipped, item, expected["forbidden_text"])
                # Write only a filename from the trusted synthetic kit; never extract archive paths.
                (readback / filename).write_bytes(zipped)
                checks.append({"filename": filename, "artifact_id": metadata["id"], "sha256": digest,
                               "native_content": "passed", "zip_readback": "passed", "bytes": len(data)})
        self.report.update(source_id=source["id"], template_id=tid, job_id=job["id"],
                           programmatic_fields=len(created["created"]), mappings=len(mappings),
                           preflight={"records": 12, "valid": 12, "invalid": 0},
                           preview={"sha256": preview["sha256"], "native_content": "passed", "created_job": False},
                           archive={"sha256": archive["sha256"], "entries_verified": 12},
                           documents=checks, status="generated; awaiting rotation and revocation")
        self.save()

    def expect_denied(self, client, label):
        target = self.output / (label + "-must-not-exist.docx")
        assert not target.exists()
        checks = []
        for operation, call in (("project", lambda: client.get_project(self.project_id)),
                                ("artifact", lambda: client.download_artifact(self.first_artifact, target))):
            try:
                call()
            except APIError as error:
                assert error.status in (401, 403), "Expected credential authority denial"
                checks.append({"operation": operation, "status": error.status, "code": error.code,
                               "request_id": error.request_id})
            else:
                raise AssertionError("SDK retained data access after " + label)
        assert not target.exists(), "Denied SDK download must not publish a file"
        self.report["authority_checks"][label] = checks
        self.save()

    def rotated(self, token):
        self.expect_denied(self.original, "old-key-after-rotation")
        self.active = Client(self.origin, ApiKey(token), http=self.http, max_retries=0)
        assert self.active.get_project(self.project_id)["id"] == self.project_id
        download = self.active.download_artifact(self.first_artifact, self.output / "rotated-key.docx")
        assert download["sha256"] == self.first_artifact["sha256"]
        self.report["rotated_key_same_artifact_sha256"] = download["sha256"]
        self.save()

    def revoked(self):
        self.expect_denied(self.active, "new-key-after-owner-revocation")
        self.report["status"] = "authority verified; awaiting server call correlation"
        self.save()

    def corroborate_calls(self, admin_page, accounts):
        wanted = {call["request_id"] for call in self.calls if call["path"].startswith("/api/v1/") and call["status"] < 400}
        assert wanted and None not in wanted
        matched, offset = {}, 0
        while True:
            response = admin_page.request.get(accounts + "/api/admin/api-platform/calls",
                params={"workspace_id": self.workspace, "limit": 100, "offset": offset})
            assert response.status == 200
            page = response.json()
            matched.update({row["request_id"]: row for row in page["items"] if row["request_id"] in wanted})
            if wanted <= matched.keys() or page["next_offset"] is None:
                break
            offset = page["next_offset"]
        assert wanted <= matched.keys(), "SDK successful calls missing from genuine server call records"
        assert all(row["workspace_id"] == self.workspace and row["credential_kind"] == "api_key" for row in matched.values())
        private_json(self.output / "server-call-records.json", list(matched.values()))
        self.report["server_calls_correlated_by_request_id"] = len(matched)
        self.report["status"] = "passed"
        self.save()


def run(fixture_file: Path, output: Path):
    os.umask(0o077)  # Dedicated acceptance process: all kits, screenshots and evidence are private.
    assert fixture_file.stat().st_mode & 0o777 == 0o600, "Fixture secrets require mode 0600"
    fixture = json.loads(fixture_file.read_text())
    origins = {fixture[key] for key in ("accounts", "docs", "portal")}
    for origin in origins:
        parts = urlsplit(origin)
        assert parts.scheme == "http" and parts.hostname in {
            "accounts.localhost", "docweave.localhost", "portal.localhost"}, "Only isolated local origins are accepted"
        assert parts.port and 1024 <= parts.port <= 65535
        assert not parts.username and not parts.password and not parts.query and not parts.fragment and not parts.path
    assert fixture["docs"] == "http://docweave.localhost:28765", "Use the explicitly approved live synthetic Docs service"
    assert all(identity["email"].endswith("@example.test") for identity in fixture["identities"].values())
    data = Path(fixture["data"]).resolve()
    assert any(data.is_relative_to(root) for root in (Path("/tmp").resolve(), Path(tempfile.gettempdir()).resolve()))
    kit, sdk_provenance = installed_sdk_and_kit()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    evidence, errors, blocked = {}, [], []
    secrets = []

    with ExitStack() as stack, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)

        def context_for(identity, destination):
            context = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 1050})

            def restrict(route):
                parsed = urlsplit(route.request.url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
                if origin in origins:
                    route.continue_()
                else:
                    blocked.append(origin)  # Origin only; never query strings or tokens.
                    route.abort()

            context.route("**/*", restrict)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(fixture["accounts"] + "/login?next=" + quote(destination, safe=""))
            language(page, "en")
            page.get_by_role("textbox", name="Email", exact=True).fill(identity["email"])
            page.get_by_label("Password", exact=True).fill(identity["password"])
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.wait_for_function("location.pathname !== '/login'")
            assert "/verify-email" not in page.url, "Verify the synthetic fixture mailbox before running acceptance"
            return context, page

        def language(page, locale):
            page.locator(".language-menu select").select_option(locale)
            expect(page.locator("html")).to_have_attribute("lang", locale)

        def navigate(page, path):
            page.goto(fixture["accounts"] + path)
            language(page, "en")

        def confirm(page, identity, locale="en"):
            copy = DIALOG_COPY[locale]
            expect(page.locator("html")).to_have_attribute("lang", locale)
            dialog = page.get_by_role("alertdialog")
            dialog.get_by_role("textbox", name=copy["reason"], exact=True).fill(copy["audit_reason"])
            dialog.screenshot(path=str(output / f"confirmation-{locale}.png"), mask=[dialog.locator("input")], animations="disabled")
            dialog.get_by_role("button", name=copy["confirm"], exact=True).click()
            page.wait_for_function("!document.querySelector('[role=alertdialog]') || document.querySelector('[role=alertdialog] input[type=password]')")
            if dialog.is_visible():
                dialog.get_by_label(copy["password"], exact=True).fill(identity["password"])
                dialog.get_by_role("button", name=copy["confirm"], exact=True).click()
            expect(dialog).not_to_be_visible()

        def capture_secret(page, locale="en"):
            copy = DIALOG_COPY[locale]
            dialog = page.get_by_role("dialog", name=copy["secret_title"], exact=True)
            field = dialog.get_by_label(copy["secret"], exact=True)
            expect(field).to_have_attribute("type", "password")
            token = field.input_value()
            assert len(token) > 20
            secrets.append(token)
            dialog.get_by_role("button", name=copy["close"], exact=True).click()
            expect(dialog).not_to_be_visible()
            stored = page.evaluate("JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})")
            assert token not in stored, "One-time secret must not enter browser storage"
            return token

        admin, owner = fixture["identities"]["admin"], fixture["identities"]["owner"]
        admin_context, page = context_for(admin, "/admin/api-platform")
        owner_context, owner_page = context_for(owner, "/account/api-approvals")
        try:
            navigate(page, "/admin/api-platform")
            page.get_by_role("navigation", name="API administration navigation").wait_for()
            evidence["account_assets"] = [urlsplit(src).path for src in
                                          page.locator("script[src]").evaluate_all("nodes => nodes.map(node => node.src)")]
            print(json.dumps({"account_assets": evidence["account_assets"]}), flush=True)
            name = "Synthetic browser key " + uuid.uuid4().hex[:8]
            page.get_by_label("Credential name", exact=True).fill(name)
            page.get_by_label("Workspace ID", exact=True).fill(owner["workspace"])
            page.get_by_role("combobox", name=re.compile("^Credential type")).select_option("api_key")
            for scope in ("docweave.read", "docweave.write"):
                page.get_by_role("checkbox", name=scope, exact=True).check()
            page.get_by_role("button", name="Request approval", exact=True).click()
            confirm(page, admin)
            row = page.locator("article.api-platform-record").filter(has_text=name)
            expect(row).to_contain_text("Pending")

            navigate(owner_page, "/account/api-approvals")
            owner_row = owner_page.locator("article.api-platform-record").filter(has_text=name)
            owner_row.get_by_role("button", name="Approve access", exact=True).click()
            confirm(owner_page, owner)
            expect(owner_row).to_contain_text("Approved")
            page.get_by_role("button", name="Refresh", exact=True).click()
            row.get_by_role("button", name="Issue credential", exact=True).click()
            confirm(page, admin)
            token = capture_secret(page)
            evidence["approval_owner_consent_issue"] = True

            # Exercise the genuine machine API with the explicit secret, on the Docs origin.
            machine = playwright.request.new_context(base_url=fixture["docs"])
            assert machine.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status == 200
            assert machine.get("/api/v1/whoami").status == 401
            sdk = BrowserSdkPipeline(fixture["docs"], token, owner["workspace"], output, stack, kit, sdk_provenance)
            sdk.generate()
            evidence["sdk_pipeline_directory"] = sdk.output.name

            tabs = {
                "en": ["Approvals", "Service credentials", "Webhooks", "API contract", "Call records", "Request debugger"],
                "zh-Hans": ["授权审批", "服务凭据", "Webhook", "接口契约", "调用记录", "请求调试"],
                "zh-Hant": ["授權審批", "服務憑證", "Webhook", "介面契約", "呼叫記錄", "請求偵錯"],
            }
            for locale, labels in tabs.items():
                language(page, locale)
                header = page.locator("header.topbar")
                header_labels = {
                    "en": ["Account & security", "Organizations", "Platform management", "API management", "Sign out"],
                    "zh-Hans": ["账户与安全", "组织", "平台管理", "API 管理", "退出登录"],
                    "zh-Hant": ["帳戶與安全", "組織", "平台管理", "API 管理", "登出"],
                }[locale]
                for label in header_labels[:-1]:
                    expect(header.get_by_role("link", name=label, exact=True)).to_be_visible()
                expect(header.get_by_role("button", name=header_labels[-1], exact=True)).to_be_visible()
                for link in header.get_by_role("link").all():
                    assert f"lang={locale}" in link.get_attribute("href")
                nav = page.locator("nav[aria-label]").filter(has=page.get_by_role("button", name=labels[0], exact=True))
                for index, label in enumerate(labels):
                    nav.get_by_role("button", name=label, exact=True).click()
                    if index == 3:
                        search = page.locator("input[type=search]")
                        search.fill({"en": "Download an artifact", "zh-Hans": "下载一个产物", "zh-Hant": "下載一個產物"}[locale])
                        result = page.locator("article.api-platform-record")
                        expect(result).to_have_count(1)
                        expect(result).to_contain_text("GET /api/v1/artifacts/{aid}/content")
                        expect(result).to_contain_text("docweave.read")
                        result.locator("summary").click()
                        if locale == "en":
                            assert not re.search(r"[\u4e00-\u9fff]", result.inner_text()), "Contract prose must use English"
                        page.screenshot(path=str(output / f"contract-{locale}.png"), full_page=True, animations="disabled")
                        search.fill("")
                        expect(result).to_have_count(25)
                        page.get_by_role("button", name={"en": "Next", "zh-Hans": "下一页", "zh-Hant": "下一頁"}[locale], exact=True).click()
                        expect(result).to_have_count(25)
                page.screenshot(path=str(output / f"console-{locale}.png"), full_page=True, animations="disabled")
            evidence["languages"] = list(tabs)
            evidence["translated_contract_search_pagination"] = True
            evidence["shared_topbar_translations_navigation_logout_present"] = True

            language(page, "en")
            page.get_by_role("button", name="Request debugger", exact=True).click()
            page.get_by_label("Service access token or API key", exact=True).fill(token)
            page.get_by_role("button", name="Run read-only request", exact=True).click()
            confirm(page, admin)
            expect(page.locator("pre.api-platform-json")).to_contain_text(owner["workspace"])
            # Reject arbitrary URL without dispatching it, using the existing restriction.
            page.get_by_label("Endpoint path", exact=True).fill("http://127.0.0.1:1/private")
            page.get_by_role("button", name="Run read-only request", exact=True).click()
            dialog = page.get_by_role("alertdialog")
            dialog.get_by_role("textbox").fill("Synthetic forbidden URL rejection check")
            dialog.get_by_role("button", name="Confirm action", exact=True).click()
            expect(dialog.get_by_role("alert")).to_contain_text("request_not_allowed")
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            evidence["debugger_explicit_credential_and_restricted_target"] = True

            language(page, "zh-Hans")
            page.get_by_role("button", name="服务凭据", exact=True).click()
            key_row = page.locator("article.api-platform-record").filter(has_text=name)
            key_row.get_by_role("button", name="轮换密钥", exact=True).click()
            confirm(page, admin, "zh-Hans")
            new_token = capture_secret(page, "zh-Hans")
            assert machine.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).status in (401, 403)
            assert machine.get("/api/v1/whoami", headers={"Authorization": f"Bearer {new_token}"}).status == 200
            sdk.rotated(new_token)
            language(owner_page, "zh-Hant")
            owner_row.get_by_role("button", name="撤回授權", exact=True).click()
            confirm(owner_page, owner, "zh-Hant")
            assert machine.get("/api/v1/whoami", headers={"Authorization": f"Bearer {new_token}"}).status in (401, 403)
            sdk.revoked()
            sdk.corroborate_calls(page, fixture["accounts"])
            evidence["sdk_pipeline"] = sdk.report
            evidence["rotation_and_owner_revocation"] = True
            evidence["sensitive_actions"] = {"en": ["request", "approve", "issue", "debug"],
                                             "zh-Hans": ["rotate", "one-time secret"], "zh-Hant": ["revoke"]}
            machine.dispose()

            denial = owner_page.goto(fixture["accounts"] + "/admin/api-platform")
            # The server rejects the ordinary owner before serving the console shell.
            assert denial is not None and denial.status == 404
            assert json.loads(owner_page.locator("body").inner_text())["error"] == "not_found"
            expect(owner_page.locator("nav[aria-label]")).to_have_count(0)
            assert owner_page.request.get(fixture["accounts"] + "/api/admin/api-platform/overview").status == 404
            evidence["ordinary_owner_denied_ui_and_direct_api"] = True
            with page.expect_response(lambda response: urlsplit(response.url).path == "/api/auth/logout"
                                      and response.request.method == "POST") as logout_response:
                page.locator("header.topbar").get_by_role("button", name="退出登录", exact=True).click()
            assert logout_response.value.status == 200
            expect(page).to_have_url(re.compile(r"/(?:logged-out|login)(?:\?|$)"))
            assert page.request.get(fixture["accounts"] + "/api/auth/me").status == 401
            evidence["shared_topbar_logout_revokes_session"] = True
            evidence["logout_landing_path"] = urlsplit(page.url).path
            evidence["origins"] = sorted(origins)
            evidence["coverage"] = "Account approval UI -> installed Python SDK over real Docs HTTP -> source/template/fields/rule/mappings/preflight/native preview/job/ZIP/12 DOCX readback -> SDK rotation/revocation denial; portal SSO is not exercised"
            evidence["browser"] = "Installed Chrome; normal local DNS; no resolver overrides"
            assert not errors, "Unexpected browser page errors"
            assert not blocked, "Unexpected external browser request"
            evidence["page_errors"] = errors
            evidence["blocked_external_requests"] = blocked
            serialized = json.dumps(evidence, ensure_ascii=False, indent=2)
            assert all(secret not in serialized for secret in secrets)
            private_json(output / "evidence.json", evidence)
            print(serialized)
        except Exception:
            # Only structural diagnostics and masked views; never form values, cookies or response bodies.
            for label, current in (("admin", page), ("owner", owner_page)):
                current.screenshot(path=str(output / f"failure-{label}.png"), full_page=True, animations="disabled",
                                   mask=[current.locator("input,textarea,pre")])
                print(json.dumps({"surface": label, "path": urlsplit(current.url).path,
                                  "buttons": current.get_by_role("button").all_text_contents(),
                                  "alerts": current.get_by_role("alert").all_text_contents()}, ensure_ascii=False))
            raise
        finally:
            admin_context.close()
            owner_context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.fixture, args.output)
