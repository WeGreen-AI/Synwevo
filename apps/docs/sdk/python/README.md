# Synwevo Docs Python SDK and CLI

Standalone external client for Python 3.10+. Its only runtime dependency is HTTPX;
it imports no server or repository packages. Nothing here publishes or deploys a service.

From the repository root:

```sh
python -m venv /tmp/synwevo-sdk-env
/tmp/synwevo-sdk-env/bin/python -m pip install -r apps/docs/sdk/python/requirements.lock
/tmp/synwevo-sdk-env/bin/python -m pip install --no-build-isolation --no-deps apps/docs/sdk/python
```

From this directory, use `requirements.lock` and `.` for those two paths. The lock
pins the build tools and runtime dependency closure used for acceptance; server
acceptance separately needs the repository's environment and its pinned requirements.

Set `SYNWEVO_BASE_URL` to the Docs origin, without `/api/v1`. For a service account,
set `SYNWEVO_CLIENT_ID`, `SYNWEVO_CLIENT_SECRET_FILE` (or `SYNWEVO_CLIENT_SECRET`),
and `SYNWEVO_TOKEN_URL` (the Account service's `/oidc/token`). The default token scope
is `docweave.read docweave.write`; `SYNWEVO_TOKEN_SCOPE` and `SYNWEVO_TOKEN_RESOURCE`
can override the scope and select a registered audience. Provision the service client
through Account's operator CLI/configuration. No browser cookie is involved.
Legacy `SYNWEVO_API_KEY_FILE` (plaintext key, first line) or `SYNWEVO_API_KEY` is also
supported and takes precedence; clear those variables when using a service account.
Keep credential files private (mode 0600); credentials are never command arguments.
OAuth tokens are cached and refreshed ahead of expiry or once after a 401.

```python
from synwevo_docs_sdk import Client

with Client.from_env(timeout=30, max_retries=4) as client:
    project = client.create_project("My batch", idempotency_key="my-import-2026-09")
    source = client.upload_source(project["id"], "customers.xlsx")
    template = client.upload_template("template.docx")
    # Load declarative definitions from your own JSON files.
    client.create_template_fields(template["id"], fields_definition)
    client.set_project_template(project["id"], template["id"])
    client.put_definition(project["id"], project_definition)
    preflight = client.preflight(project["id"], formats=["docx"], simulate=1)
    client.download_preview(project["id"], "preview.docx", index=0, signature=preflight["signature"])
    job = client.create_job(project["id"], formats=["docx"], idempotency_key="batch-2026-09")
    result = client.wait_for_job(job["id"], timeout=300)
    if result["status"] != "completed":
        raise RuntimeError(result)
    client.download_archive(job["id"], "results.zip")
```

`list_*` returns one page; `iter_*` follows cursors or offsets. Responses are ordinary
dicts with additional `request_id`, `status_code`, `headers`, and `replayed` attributes.
Errors derive from `SynwevoError`, with status, code, field, details, request_id,
and retry_after. Status-specific subclasses and `to_dict()` support automation.

Docs resource-list cursors are opaque, signed, valid for 24 hours, and bound to the
workspace, list and filters. Pass them unchanged. On `400 invalid_cursor`, explicitly
restart from the first page and deduplicate by resource ID; the SDK does not silently
restart an iterator. Upgrading from unsigned cursors or rotating the server root key
also requires restarting a list. This rule does not apply to the `/events` checkpoint,
Webhook delivery pagination or WorkspaceClient's offset pages.

Job lifecycle methods have public `TypedDict` return types: `Job`, `CancelledJob`,
`JobListItem`, `JobItem`, `JobRun`, `JobEvent`, `Artifact` and their page types, imported
directly from `synwevo_docs_sdk`. These are static descriptions; response dictionaries,
unknown JSON fields and response metadata remain unchanged. Lists keep sparse stored
counts; job details always include ten counters (missing keys become zero, explicit
historical nulls remain null). Check nullable historical fields before using them.
`idempotent_replay` is present only on replayed requests, and `cancel` only on a cancellation
response. Cursor pages have `offset=None`; planning runs may have `items=None`.
Issues, verification and event data retain their open JSON shapes.

The compile-only consumer example is `tests/types_check.py`; use a separately installed
Mypy with `--strict --follow-imports=silent --python-executable <installed-sdk-python>`.
The SDK does not depend on Mypy or any server model package.

For the nine job lifecycle routes and a real failed-only recovery, run from the repository root:

```sh
.venv/bin/python tests/integration/sdk_acceptance/job_types_run.py \
  --sdk-python /tmp/synwevo-sdk-env/bin/python --evidence /tmp/synwevo-jobs-sdk-evidence
```

This creates a temporary service and synthetic documents, temporarily removes one template
blob after submission, then restores the identical bytes. It verifies eleven failed samples
recover through `failed_only=True`, one invalid skipped sample stays unchanged, the frozen
snapshot stays identical, and every generated document matches its content and checksum.
It never accepts an existing service or data directory; it restores the fixture blob on exit.

Retries use bounded exponential backoff with full jitter and respect Retry-After
(delta or HTTP date) and the server's RateLimit reset hint. Creating calls use one
UUID per logical call. Supply your own key to safely repeat a create across process
restarts (within the server's retention window). GET/HEAD, keyed creates and built-in saves, and
explicitly repeatable operations (preflight, cancel, upload completion) retry;
mapping/definition revision writes, webhook creation/rotation and other unsafe
writes do not. A token rejected with 401 is refreshed once before replay.

Uploads hash in blocks, stream multipart below 8 MiB and use resumable chunks above
it. `resumable=True`, `chunk_size=…`, and `upload_id=…` allow explicit control and
cross-process resume. After a chunk failure the client queries the received offset.
Preserve the logical consumption idempotency key across processes. Within the server's
idempotency retention window, automatic resumable upload recovers the original session
even after consumption, reads its current state and replays the keyed source/template
creation. It creates no second upload or business object. Explicit `upload_id` plus the
same consumption key remains supported (CLI: `--upload-id`, `--idempotency-key`).
Replays still require current authorization and a live consumer object: deleting its
source, parent project or template makes upload metadata and cached creation unavailable.
Aborted/expired unused sessions remain terminal; a new key cannot consume an upload twice.
Downloads stream into a sibling temporary file, check provided header/manifest
SHA-256 values, then atomically replace the destination. ZIP entries are checked
against the complete artifact manifest. Reports/templates without a server checksum
return an empty `verified` list; their local hash alone is not a server attestation.

HTTP `timeout` bounds each network operation. `wait_for_job(timeout=…)` bounds polling
between requests; an in-flight request, token refresh, or transport retry can extend
that wall time. A wait timeout leaves the remote job running.

By default, `retry_job()` retries failed **and cancelled** items (plus skipped items
when `include_skipped=True`), preserving successful artifacts. On a server with the
new retry contract, `failed_only=True` excludes cancelled, skipped and unplanned work;
it cannot be combined with `include_skipped=True`. Changing that selection with the
same idempotency key is a conflict. Older servers may ignore unknown JSON fields:
only use `failed_only` with a server whose published retry schema includes it.

`download_preview()` uses the new `POST /api/v1/projects/{pid}/preview` endpoint and
requires write plus export permission (the tested service read/write grant permits
both). It streams one actual native-format attachment, verifies `X-Content-SHA256`,
and returns `project_signature`, `template_version` (a header string) and decoded
`record_key`. Select `index=0` or `key="…"`, never both. Supplying the preflight
signature pins the inputs; stale inputs and blocked samples raise typed 409 errors.
Preview creates no batch job or artifact. `preflight(simulate=…)` still runs trial
renders without downloading their output. Neither operation overrides native preview format.

## CLI

Data commands emit JSON to stdout by default (`--json` is accepted). Errors are JSON
on stderr. `events tail --follow` emits one JSON page per line; retain its cursor
after processing. `jobs wait --events` emits observed events on stderr.

```sh
synwevo-docs whoami
synwevo-docs projects create 'My batch' --idempotency-key project-001
synwevo-docs files upload PROJECT customers.xlsx --resumable
synwevo-docs templates upload template.docx --name Confirmation
synwevo-docs templates fields put TEMPLATE fields.json --idempotency-key fields-001
synwevo-docs projects template PROJECT TEMPLATE
synwevo-docs rule put PROJECT rule.json
synwevo-docs mappings put PROJECT mappings.json --base-rev 0
synwevo-docs definition put PROJECT project.json
synwevo-docs preflight PROJECT --formats docx --simulate 1
synwevo-docs preview PROJECT preview.docx --index 0 --signature PREFLIGHT_SIGNATURE
synwevo-docs preflight PROJECT --issues
synwevo-docs jobs submit PROJECT --formats docx --idempotency-key job-001
synwevo-docs jobs wait JOB --wait-timeout 300 --cancel-on-interrupt
synwevo-docs jobs retry JOB --failed-only --idempotency-key retry-001
synwevo-docs jobs items JOB --all --limit 2
synwevo-docs artifacts download-all JOB ./artifacts
synwevo-docs archive download JOB results.zip
synwevo-docs report download JOB report.csv
```

JSON inputs can be `-` for stdin. `--timeout`, `--base-url`, `--max-retries`, and
`--json` work before or after the command. `--all` lists every page, `--limit` sets
page size; omit `--all` to use `--cursor`/`--offset`. Use `--help` on each resource
for commands including deletion, job cancellation/retry and webhook administration.
Webhook add/rotate responses contain a new secret once: save stdout privately.
`verify_webhook` and `verify_legacy_signature` verify raw bytes before JSON parsing.

| Exit | Meaning |
|---|---|
| 0 | Success |
| 2 | Usage, input, local file or credential configuration error |
| 3 | API 4xx |
| 4 | API 5xx, transport or integrity error |
| 5 | Wait ended failed, cancelled or completed_with_errors |
| 6 | Wait timeout |
| 130 | Interrupted; `--cancel-on-interrupt` requests cancellation before exiting |

## Workspace directory

The same package exports an independent `WorkspaceClient` for Account's
`/api/workspace/v1` API. It exposes directory methods only. Its environment factory
uses `SYNWEVO_WORKSPACE_BASE_URL` (default `https://accounts.synwevo.com`),
`SYNWEVO_WORKSPACE_CLIENT_ID`, `SYNWEVO_WORKSPACE_CLIENT_SECRET_FILE` (or
`SYNWEVO_WORKSPACE_CLIENT_SECRET`), and optional `SYNWEVO_WORKSPACE_TOKEN_URL`
(default: the selected origin plus `/oidc/token`). Docs environment variables are
ignored. It always requests resource `urn:synwevo:platform:workspace` and defaults
to scope `workspace.read`. To write, explicitly set `SYNWEVO_WORKSPACE_SCOPE` to
`workspace.read workspace.members.write` after the workspace owner has approved it.

```python
from synwevo_docs_sdk import WorkspaceClient

with WorkspaceClient.from_env() as directory:
    workspace = directory.whoami()["workspace"]
    members = directory.list_members(role="viewer", limit=50)
    invitation = directory.create_invitation("colleague@example.com", "viewer",
                                             idempotency_key="onboard-2026-09")
    resent = directory.resend_invitation(invitation["id"], idempotency_key="remind-2026-09")
    directory.revoke_invitation(resent["id"])
    # Use the role just read; a concurrent edit returns 409 role_changed.
    directory.change_member(member_id, "editor", expected_role="viewer")
    directory.remove_member(member_id, expected_role="editor", reason="Membership ended")
```

`iter_members` and `iter_invitations` follow bounded offset pages. Only invitation
create/resend are keyed writes with automatic retries; member changes/removal and
invitation revocation must be read back after uncertain outcomes. Manageable roles
are `viewer` and `editor`; neither a directory token nor this client can grant
owner/admin authority. Output annotations live in `synwevo_docs_sdk.workspace`.

## Copy requests

The project owner's delegated OAuth token can call `copy_targets()`,
`copy_manifest(project_id)`, `request_copy(project_id, target_workspace_id,
idempotency_key=...)`, `list_copy_requests(project_id, status="pending", limit=50)`,
`get_copy_request(copy_id)`, and `withdraw_copy_request(copy_id)`. Creation retries
keep one idempotency key. Withdrawal has no automatic retry after an uncertain
server outcome; read the request status first. Lists are newest first, capped at
100, and have no cursor. API keys and service accounts cannot act for the owner.
Approval remains an authenticated browser action by a target workspace approver.
Subscribe to `copy.*` webhook events or filter the event feed by explicit event
types such as `copy.requested` and `copy.withdrawn`.

## Built-in template models

`create_builtin_template(kind, editor, name=None, *, idempotency_key=None)`,
`save_builtin_template(template_id, editor, *, base_version, name=None, idempotency_key=None)`,
and `get_builtin_template(template_id, *, version=None)` use the shared server editor
model. `kind` is `builtin_doc` (native DOCX) or `builtin_sheet` (native XLSX). Model
validation, field extraction and rendering happen on the server. Built-in fields
come from the model; bind the resulting template and map those field names through
the normal rule/mapping APIs. See the [shared editor model contract](../../../../docs/integration/editor-models.md)
for the complete authoring shapes and constraints.

A minimal document editor JSON (`editor.json`) with one required field:

```json
{"body":{"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Customer: "},{"type":"field","attrs":{"id":"customer","name":"Customer","required":true}}]}]}}
```

The equivalent spreadsheet editor JSON:

```json
{"sheets":[{"name":"Result","cells":{"A1":{"v":"Customer"}},"fields":[{"id":"customer","kind":"cell","name":"Customer","ref":"B1","required":true}]}]}
```

```sh
synwevo-docs templates builtin create builtin_doc editor.json --name Letter --idempotency-key letter-001
synwevo-docs templates builtin export TEMPLATE > model.json
# Edit model.json's editor object, then save against the version you exported.
synwevo-docs templates builtin save TEMPLATE model.json --base-version 1 --idempotency-key letter-edit-001
synwevo-docs templates builtin export TEMPLATE --version 1 > original.json
# Import an exported model as a separate template (use builtin_sheet for a sheet model).
synwevo-docs templates builtin create builtin_doc model.json --name Copy --idempotency-key letter-copy-001
```

Create/save accept raw editor JSON or an export envelope containing `editor`; `-`
reads stdin. Only the editor is imported from an envelope: kind/name and expected
base version come from the command arguments. Export returns the model, fields and
version metadata as JSON. Saving requires the current head as `base_version` and
returns `409 stale_template` on a concurrent edit (CLI exit 3). A save replay with
Restoring a version while the template's editing room holds live edits nobody saved yet
returns `409 live_edits` (who is online, who changed it last); pass `discard_live=True` /
`--discard-live` only when throwing those edits away is intended.
the same idempotency key/body returns its original result without adding a revision.
Use a new key for each intentional edit. `note=` / `--note` names the version the save creates
(a checkpoint people can find in the history; at most 200 characters). Omitting `version` exports the current head;
an explicit positive version exports that historical editor model.

## Verification

Run from this directory after installing the package:

```sh
python -m unittest discover -s tests -v
```

The unit tests use no network. `tests/test_http.py` uses a labelled loopback HTTP
fault fixture for retry and transfer semantics; it is not a substitute for the real
service acceptance. From the repository root, with the SDK installed in the repo
interpreter (or `--sdk-python` pointing to a separately installed SDK environment):

```sh
.venv/bin/python apps/docs/sdk/acceptance/python_full_flow.py --sdk-python /tmp/synwevo-sdk-env/bin/python --evidence /tmp/synwevo-python-acceptance
```

The runner also needs Node.js 18+ (`ACCEPTANCE_NODE` can select its executable) to
exercise the TypeScript copy-request methods with the same delegated test identity.
That script builds the unchanged shared synthetic kit and starts separate real
Account and Docs roles on temporary loopback ports with a fresh disposable store.
The cross-module service launcher lives in `tests/integration/sdk_acceptance/python_service.py`;
the acceptance parent invokes it as a repository-interpreter subprocess. The installed SDK
and its worker need no server packages.
Core `Identity`, `Apps` and `Clients` fixture APIs provision only the synthetic identity
and service registration. Access tokens come from Account's real HTTP client_credentials
endpoint. All project, source, template-field, rule and mapping setup uses business APIs.
The service is registered for exactly Docs read/write, with no admin/manage scope.
Cleanup destroys the temporary instance; the client intentionally cannot delete projects.

The recorded scoped run verified 24 generated DOCX files (12 SDK, 12 CLI), three
downloaded previews, preview stale/blocked errors, resumable and multipart uploads,
idempotency replay/conflicts, paginated items/artifacts, ZIP contents and CSV downloads.
The same run also verified 48 generated built-in outputs and four native previews:
12 DOCX and 12 XLSX files per channel. Both channels created models, replayed creates
and saves, rejected stale saves, exported current/historical models, imported an
export as a separate template, and mapped the shared kit through APIs. Independent
DOCX/XLSX read-back checked all expected customer values and the saved revision marker.
CLI authoring exercised stdin and imported export-envelope files. The installed SDK
environment contained no core, Docs, Account or server implementation packages.
Each channel also cancelled a running 300-item batch made by repeating the same
synthetic kit rows. Default retry completed its cancelled items and preserved prior
successful artifact IDs/hashes and item attempts. `failed_only` returned
`nothing_to_retry` for these success/cancelled-only batches, and changing the selection
under the same key conflicted. This does **not** test a positive failed-item retry or
claim that default retry is failed-only. Redacted HTTP evidence checks every machine
request's principal/scopes and rejects any Cookie, Set-Cookie or legacy API key.
The script accepts no live server URL and never uses external user data.
The person authorizes each workspace through the actual consent endpoint and CSRF
check. Python and TypeScript each exercise copy target/manifest discovery, request
creation and idempotent replay, filtered listing, status, withdrawal, and the two
corresponding feed events. Approval and execution are covered by the separate
scenario C integration tests; this SDK smoke does not claim to exercise them.

The directory acceptance runs both clients against fresh Accounts services and
file-only mail delivery. It creates synthetic identities, obtains owner approval
and credentials through real HTTP, checks invitation replay/resend/revocation and
member role conflicts/removal, and independently verifies mail and machine audit
records. Python uses the installed SDK environment; TypeScript is packed and
installed from the local package without publishing it:

```sh
.venv/bin/python tests/integration/sdk_acceptance/workspace_run.py \
  --sdk-python /tmp/synwevo-sdk-env/bin/python --evidence /tmp/synwevo-directory-new-run
.venv/bin/python apps/docs/sdk/python/tools/audit_workspace_openapi.py \
  docs/integration/workspace-openapi.json \
  --http-evidence /tmp/synwevo-directory-new-run/http.jsonl --out /tmp/workspace-coverage.json
```

The directory audit checks every operation against both clients' method inventories
and labels actual successful HTTP observations separately from implementation.

## API coverage and limits

[`operation-coverage.json`](operation-coverage.json) inventories every operationId from
the actual exported v1 OpenAPI, including probes. The reviewed snapshot (2026-09-30) contains
100 operations: 98 have SDK methods and 64 have CLI commands (some upload
operations are reached indirectly). The SDK operations without a method are the
source page image and the source locator probe.
The real acceptance run observed successful responses for 56 operations over two legs: a
service account (client_credentials) for the document flows, and a person signing in at the
account service and authorizing a public client (authorization_code with PKCE) for the
organization-shared template and copy-request operations, which need a delegated token. Implementation
coverage is not test coverage. Credential administration, webhook administration and several
peripheral methods remain outside the acceptance chain. CLI coverage is a subset.

Re-audit an actual JSON export; new/stale routes or nonexistent mapped methods fail:

```sh
# Repository environment, from the root; export uses a throwaway store.
.venv/bin/python -m synwevo_docs.cli openapi --out /tmp/synwevo-openapi.json
# SDK environment, from the root; add --http-evidence PATH/app-http.jsonl for run evidence.
python apps/docs/sdk/python/tools/audit_openapi.py /tmp/synwevo-openapi.json --out /tmp/operation-coverage.json
```

The manifest records source/spec/evidence hashes and observed HTTP statuses. A successful
response does not establish every parameter or behavior. Transport fault tests are labelled
fixtures; the separate Account/Docs acceptance is the evidence for the business chain.
