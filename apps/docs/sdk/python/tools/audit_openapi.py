"""Audit every operation in a supplied actual v1 OpenAPI JSON export against this SDK.

Run with the SDK environment; no server imports or network. The route map is a reviewed
implementation inventory, not a claim that every operation is integration-tested. Optional
redacted HTTP evidence adds observed statuses from the real acceptance service.
"""
import argparse
import ast
import hashlib
import json
from collections import Counter
from pathlib import Path


# METHOD PATH | SDK methods (including pagination helpers) | CLI commands (semicolon separated)
ROUTES = """
GET /healthz | health | health
GET /readyz | readiness |
GET /api/v1/capabilities | capabilities | capabilities
GET /api/v1/whoami | whoami | whoami
POST /api/v1/projects | create_project | projects create
GET /api/v1/projects | list_projects,iter_projects | projects list
GET /api/v1/projects/{pid} | get_project | projects get
PATCH /api/v1/projects/{pid} | update_project |
GET /api/v1/copy-targets | copy_targets |
GET /api/v1/projects/{pid}/copy-manifest | copy_manifest |
GET /api/v1/projects/{pid}/copy-requests | list_copy_requests |
POST /api/v1/projects/{pid}/copy-requests | request_copy |
GET /api/v1/copy-requests/{cid} | get_copy_request |
POST /api/v1/copy-requests/{cid}/withdraw | withdraw_copy_request |
DELETE /api/v1/projects/{pid} | delete_project | projects delete
PUT /api/v1/projects/{pid}/rule | put_rule | rule put
PUT /api/v1/projects/{pid}/template | set_project_template | projects template
GET /api/v1/projects/{pid}/mappings | get_mappings | mappings get
PUT /api/v1/projects/{pid}/mappings | put_mappings | mappings put
GET /api/v1/projects/{pid}/definition | get_definition | definition get
PUT /api/v1/projects/{pid}/definition | put_definition | definition put
POST /api/v1/projects/{pid}/preflight | preflight | preflight
GET /api/v1/projects/{pid}/preflight/issues | list_preflight_issues,iter_preflight_issues | preflight --issues
POST /api/v1/projects/{pid}/preview | download_preview | preview
POST /api/v1/projects/{pid}/files | upload_source | files upload
GET /api/v1/projects/{pid}/files | list_files,iter_files | files list
GET /api/v1/files/{fid} | get_file,wait_for_file | files get
DELETE /api/v1/files/{fid} | delete_file | files delete
POST /api/v1/files/{fid}/scan/retry | retry_source_scan |
POST /api/v1/templates | upload_template | templates upload
POST /api/v1/templates/builtin | create_builtin_template | templates builtin create
GET /api/v1/templates/{tid}/builtin | get_builtin_template | templates builtin export
PUT /api/v1/templates/{tid}/builtin | save_builtin_template | templates builtin save
GET /api/v1/templates | list_templates,iter_templates | templates list
GET /api/v1/templates/{tid} | get_template | templates get
DELETE /api/v1/templates/{tid} | delete_template | templates delete
GET /api/v1/templates/{tid}/usage | get_template_usage | templates usage
POST /api/v1/templates/{tid}/restore | restore_template_version | templates restore
GET /api/v1/files/{fid}/structure | get_source_structure |
GET /api/v1/files/{fid}/view | get_source_view |
GET /api/v1/files/{fid}/blocks | list_source_blocks,iter_source_blocks |
GET /api/v1/files/{fid}/search | search_source,iter_source_search_pages |
GET /api/v1/files/{fid}/words/{page} | list_source_page_words,iter_source_page_words |
GET /api/v1/files/{fid}/content | download_source_content |
GET /api/v1/files/{fid}/page/{page}.png | |
PUT /api/v1/files/{fid}/options | update_source_options |
PUT /api/v1/files/{fid}/corrections/{mid} | update_source_correction |
POST /api/v1/files/{fid}/ocr | run_source_ocr |
POST /api/v1/files/{fid}/representative | select_representative_source |
POST /api/v1/files/{fid}/locator | |
PUT /api/v1/projects/{pid}/files/order | reorder_project_sources |
GET /api/v1/projects/{pid}/locate | locate_source_mappings |
GET /api/v1/projects/{pid}/records | list_source_records,iter_source_records |
GET /api/v1/organization-templates | list_organization_templates,iter_organization_templates | org-templates list
GET /api/v1/organization-templates/{pid} | get_organization_template | org-templates get
POST /api/v1/templates/{tid}/publish | publish_template | org-templates publish
POST /api/v1/organization-templates/{pid}/releases | release_organization_template | org-templates release
POST /api/v1/organization-templates/{pid}/deprecate | deprecate_organization_template | org-templates deprecate; org-templates restore
POST /api/v1/organization-templates/{pid}/use | use_organization_template | org-templates use
GET /api/v1/templates/{tid}/fields | get_template_fields | templates fields get
POST /api/v1/templates/{tid}/fields | create_template_fields | templates fields put
PATCH /api/v1/templates/{tid}/fields/{field_id} | update_template_field |
DELETE /api/v1/templates/{tid}/fields/{field_id} | delete_template_field |
GET /api/v1/templates/{tid}/content | download_template |
POST /api/v1/projects/{pid}/jobs | create_job | jobs submit
GET /api/v1/projects/{pid}/jobs | list_jobs,iter_jobs | jobs list
GET /api/v1/jobs/{jid} | get_job,wait_for_job | jobs get; jobs wait
GET /api/v1/jobs/{jid}/items | list_job_items,iter_job_items | jobs items
GET /api/v1/jobs/{jid}/runs | iter_job_runs |
GET /api/v1/jobs/{jid}/events | job_events,iter_job_events | jobs wait --events
POST /api/v1/jobs/{jid}/cancel | cancel_job | jobs cancel; jobs wait --cancel-on-interrupt
POST /api/v1/jobs/{jid}/retry | retry_job | jobs retry
GET /api/v1/jobs/{jid}/artifacts | list_artifacts,iter_artifacts | artifacts list
GET /api/v1/artifacts/{aid}/content | download_artifact,download_artifacts | artifacts download; artifacts download-all
POST /api/v1/artifacts/{aid}/link | artifact_link |
GET /api/v1/jobs/{jid}/archive | download_archive | archive download
POST /api/v1/jobs/{jid}/archive-link | archive_link |
GET /api/v1/jobs/{jid}/report.csv | download_report | report download
GET /api/v1/download/{token} | download_signed |
POST /api/v1/uploads | create_upload,resumable_upload | files upload --resumable; templates upload --resumable
GET /api/v1/uploads/{uid} | get_upload | files upload --resumable; templates upload --resumable
PUT /api/v1/uploads/{uid} | put_upload_chunk | files upload --resumable; templates upload --resumable
POST /api/v1/uploads/{uid}/complete | complete_upload | files upload --resumable; templates upload --resumable
POST /api/v1/uploads/{uid}/abort | abort_upload |
GET /api/v1/usage | usage | usage
GET /api/v1/credentials/self | credential_self |
POST /api/v1/credentials/self/rotate | rotate_own_key |
POST /api/v1/credentials/self/revoke | revoke_own_credential |
GET /api/v1/webhooks/subscriptions | list_webhooks,iter_webhooks | webhooks list
POST /api/v1/webhooks/subscriptions | create_webhook | webhooks add
GET /api/v1/webhooks/subscriptions/{sid} | get_webhook | webhooks get
PATCH /api/v1/webhooks/subscriptions/{sid} | update_webhook |
DELETE /api/v1/webhooks/subscriptions/{sid} | delete_webhook | webhooks delete
POST /api/v1/webhooks/subscriptions/{sid}/pause | pause_webhook | webhooks pause
POST /api/v1/webhooks/subscriptions/{sid}/resume | resume_webhook | webhooks resume
POST /api/v1/webhooks/subscriptions/{sid}/rotate-secret | rotate_webhook_secret | webhooks rotate
POST /api/v1/webhooks/subscriptions/{sid}/ping | ping_webhook | webhooks ping
GET /api/v1/webhooks/subscriptions/{sid}/deliveries | list_webhook_deliveries,iter_webhook_deliveries | webhooks deliveries
GET /api/v1/webhooks/subscriptions/{sid}/deliveries/{did} | get_webhook_delivery |
POST /api/v1/webhooks/subscriptions/{sid}/deliveries/{did}/redeliver | redeliver_webhook | webhooks redeliver
GET /api/v1/events | events_page,iter_event_pages,iter_events | events tail
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(spec_path, evidence):
    root = Path(__file__).resolve().parents[1]
    source = root / "synwevo_docs_sdk" / "client.py"
    tree = ast.parse(source.read_text())
    client = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Client")
    methods = {n.name for n in client.body if isinstance(n, ast.FunctionDef)}
    routes = {}
    for line in ROUTES.strip().splitlines():
        route, sdk, cli = (part.strip() for part in line.split("|"))
        assert route not in routes, route
        names = [s.strip() for s in sdk.split(",") if s.strip()]
        assert set(names) <= methods, (route, set(names) - methods)
        routes[route] = (names, [s.strip() for s in cli.split(";") if s.strip()])
    observed = {}
    for path in evidence:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            path = row.get("route") or row["path"]
            # A nested router can retain its unprefixed path on scope['route'].
            # Resolve only an exact known v1 route; never guess by a lossy ID regex.
            if row["path"].startswith("/api/v1/") and not path.startswith("/api/v1/"):
                path = "/api/v1" + path
            route = row["method"] + " " + path
            observed.setdefault(route, Counter())[row["status"]] += 1
    spec = json.loads(spec_path.read_text())
    operations, actual, ids = [], set(), set()
    for path, values in spec["paths"].items():
        for method, op in values.items():
            if method.lower() not in {"get", "put", "post", "delete", "patch", "head", "options", "trace"}:
                continue
            route = method.upper() + " " + path
            actual.add(route)
            assert "operationId" in op and op["operationId"] not in ids, route
            ids.add(op["operationId"])
            sdk, cli = routes.get(route, ([], []))
            statuses = observed.get(route, {})
            operations.append({"operationId": op["operationId"], "method": method.upper(), "path": path,
                               "sdk_methods": sdk, "cli_commands": cli,
                               "implementation": "implemented" if sdk else "missing",
                               "observed_http_status_counts": {str(k): v for k, v in sorted(statuses.items())},
                               "real_service_success_observed": any(200 <= s < 300 for s in statuses)})
    assert actual == set(routes), {"unreviewed_routes": sorted(actual - set(routes)),
                                  "stale_routes": sorted(set(routes) - actual)}
    operations.sort(key=lambda x: (x["path"], x["method"]))
    return {"schema_version": 1, "api_scope": "all operations in actual v1 OpenAPI export, including health/readiness",
            "openapi_sha256": digest(spec_path), "openapi_version": spec["openapi"],
            "sdk_source_sha256": digest(source), "cli_source_sha256": digest(root / "synwevo_docs_sdk" / "cli.py"),
            "evidence": [{"file": p.name, "sha256": digest(p)} for p in evidence],
            "unmatched_http_observations": {route: dict(counts) for route, counts in observed.items() if route not in actual},
            "interpretation": "Implementation inventory reviewed against source; method existence and exact route/operation inventory checked. Observed statuses combine SDK/CLI and harness probes; they do not prove every parameter or behavior. Empty counts mean not exercised by this run.",
            "limitations": ["No source page image or source locator probe SDK method",
                            "CLI is a subset; resumable session routes are exercised indirectly by uploads",
                            "Credential administration and webhooks are implemented but not part of the read/write service acceptance",
                            "failed_only verifies exclusion of cancelled items and selection fingerprint conflict, not a positive failed-item retry"],
            "counts": {"operations": len(operations), "sdk_implemented": sum(bool(o["sdk_methods"]) for o in operations),
                       "cli_reachable": sum(bool(o["cli_commands"]) for o in operations),
                       "real_service_success_observed": sum(o["real_service_success_observed"] for o in operations)},
            "operations": operations}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("openapi", type=Path)
    p.add_argument("--http-evidence", action="append", type=Path, default=[])
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    report = audit(args.openapi, args.http_evidence)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["counts"]))


if __name__ == "__main__":
    main()
