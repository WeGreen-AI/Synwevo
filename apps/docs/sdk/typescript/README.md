# Synwevo Docs Node / TypeScript client

`synwevo-docs.mjs` is the executable second-language client: a dependency-free ES module for
Node 18+. It can be imported from JavaScript or TypeScript and needs no build step; its type
declarations are `synwevo-docs.d.ts` (see [Types](#types)).

Run commands from the repository root (or use the script's absolute path from another directory):

```sh
export SYNWEVO_BASE_URL=http://127.0.0.1:PORT
export SYNWEVO_CLIENT_ID=your-isolated-service-client
export SYNWEVO_CLIENT_SECRET_FILE=/path/to/private-client-secret
export SYNWEVO_TOKEN_URL=http://127.0.0.1:ACCOUNT_PORT/oidc/token
export SYNWEVO_SCOPE="docweave.read docweave.write"
export SYNWEVO_RESOURCE=urn:synwevo:app:docweave
node apps/docs/sdk/typescript/synwevo-docs.mjs whoami
node apps/docs/sdk/typescript/synwevo-docs.mjs --help
node apps/docs/sdk/typescript/synwevo-docs.mjs generate \
  --source /path/to/kit/customers.xlsx --template /path/to/kit/template.docx \
  --fields /path/to/kit/fields.json --definition /path/to/kit/project.json \
  --out /path/to/output --key-prefix unique-logical-run --expected-tenant YOUR_SYNTHETIC_WORKSPACE
```

The chain creates a project, uploads sources and a template, creates fields through the API,
binds the template, saves the rule and mappings as a project definition, preflights, performs
simulated rendering, downloads an actual native preview through `POST /projects/{id}/preview`,
submits a job, polls it and downloads every artifact. The preview is pinned to the preflight
signature, requires a SHA-256 header, and saves project signature, template version and encoded
record key metadata. It creates no job. Use `downloadPreview(pid, path, {index, signature})` or
`preview PROJECT --out preview.docx --index 0`; `key` can replace `index`. Full jobs render all samples.

Keep `--key-prefix` stable when recovering the same logical run. Reuse requires identical inputs
and remains subject to the server's idempotency retention period; it is not indefinite deduplication.
Use a new prefix for a new run. The client retains created resources for inspection; deletion is
explicit via `projects delete` / `templates delete` and requires admin scope.

```js
import { Client, credentialsFromEnv } from './synwevo-docs.mjs';
const client = new Client(process.env.SYNWEVO_BASE_URL, await credentialsFromEnv());
const projects = await client.collect(client.listProjects({ limit: 20 }));
for await (const artifact of client.artifacts(jobId)) {
  await client.downloadArtifact(artifact, { outDir: './output' });
}
```

Credentials are an `ApiKey` or `ClientCredentials` object. Environment loading accepts
`SYNWEVO_API_KEY_FILE` / `SYNWEVO_API_KEY`, or `SYNWEVO_CLIENT_ID` with
`SYNWEVO_CLIENT_SECRET_FILE` / `SYNWEVO_CLIENT_SECRET` and `SYNWEVO_TOKEN_URL`.
Optional service-account settings are `SYNWEVO_SCOPE` and `SYNWEVO_RESOURCE`.
Token values are hidden from object enumeration and JSON serialization. Credentials are never
accepted as command-line options. Service-account refresh is implemented; the isolated acceptance
runner below exercises an actual Account HTTP token endpoint with read/write service credentials.
It uses no legacy API key or admin scope. `retryJob(id, {failedOnly: true})` and CLI
`jobs retry ID --failed-only` request only failed items; do not combine with `includeSkipped`.

The exported `Client` covers projects, files, template fields, rules, mappings, definitions,
preflight/issues, jobs/items/events/runs, manifests/downloads/reports and webhook subscriptions.
List helpers are async iterators; `get`, `requestJson` and `paginate` expose lower-level API access.
Typed errors include status, code, request ID, field, details and retry delay. Retries are bounded,
use jitter, honor Retry-After, and only repeat safe/idempotent operations. A rejected concurrency
precondition is never retried automatically. Resumable uploads stream chunks from disk and query
the server's received offset after a lost acknowledgment.

Builtin document and sheet templates use these v1 methods. `editor` is the product editor model;
the server validates it and creates native DOCX/XLSX bytes and fields.

| Operation ID | Client method | Input |
|---|---|---|
| `create_builtin_template` | `createBuiltinTemplate({kind, editor, name?}, {idempotencyKey?})` | `kind`: `builtin_doc` or `builtin_sheet` |
| `save_builtin_template` | `saveBuiltinTemplate(id, {editor, baseVersion, name?}, {idempotencyKey?})` | Required positive `baseVersion` is sent as `base_version` |
| `get_builtin_template` | `getBuiltinTemplate(id, {version?})` | Returns the head or requested version's editor model and fields |

Create/save generate one idempotency key per logical call. Supply a stable key to recover across
process restarts. Save conflicts leave the caller's editor model intact and are not retried as new
versions. CLI `--file` contains only the editor model:

```sh
node apps/docs/sdk/typescript/synwevo-docs.mjs templates create-builtin \
  --kind builtin_doc --file editor.json --name 'Example' --idempotency-key create-example
node apps/docs/sdk/typescript/synwevo-docs.mjs templates get-builtin TEMPLATE_ID --version 1
node apps/docs/sdk/typescript/synwevo-docs.mjs templates save-builtin TEMPLATE_ID \
  --file editor-updated.json --base-version 1 --idempotency-key save-example-v2
```

Bind the returned template ID and version with `bindTemplate`, save mappings with `putDefinition`
or `putMappings`, then use preflight, `downloadPreview`, and the job/download methods. The
`generate` convenience chain and n8n acceptance continue to use uploaded templates.

Downloads use a temporary file and atomic rename after hashing. Artifacts are checked against both
manifest and HTTP SHA-256. Existing matching files are reused. ZIP and CSV endpoints currently
do not provide a checksum header: their result truthfully reports `verified: false`. Acceptance
also compares each ZIP entry with the checked artifact bytes. Standard and legacy webhook
verification helpers require the original raw body. The optional receiver's duplicate cache is
process-local and acknowledges only after its handler succeeds. The n8n companion has persistent
receipts; see [the receiver workflow](../n8n/README.md).

CLI stdout is JSON; progress/errors go to stderr. Exit codes are 0 success, 2 usage, 3 API 4xx or
preflight failure, 4 transport/server/integrity failure, 5 failed/cancelled job, 6 wait timeout,
130 interruption. `--cancel-on-interrupt` cancels a known job when interrupting generation/wait.

## Types

`synwevo-docs.d.ts` declares every export: the constants, the error classes and their fields,
`ApiKey` / `ClientCredentials`, every `Client` method with its option object, the webhook helpers
and `main`. Responses are typed after what `/api/v1` returns (`Project`, `SourceFile`, `Template`,
`Job`, `JobItem`, `Artifact`, `WebhookEvent`, `Page<T>`, …); objects the server describes only
loosely are `Record<string, unknown>` plus their known fields. Statuses are unions (`JobStatus`,
`SourceFileStatus`, `ItemStatus`) and a webhook or feed event narrows on `type`. JSON objects the
client returns carry a non-enumerable `$meta` (`WithMeta<T>`: HTTP status, request ID, idempotent
replay). The declarations reference Node's types, so a TypeScript project needs `@types/node`.
Argument checks that the client does before sending (for example `updateField` without
`baseHead`) throw a `ValidationError` synchronously; the declarations catch most of them at
compile time.

```ts
import { Client, credentialsFromEnv, type Job } from '@synwevo/docs-sdk';  // from a packed tarball
// vendored copy: import { Client, credentialsFromEnv, type Job } from './synwevo-docs.mjs';
const client = new Client(process.env.SYNWEVO_BASE_URL ?? '', await credentialsFromEnv());
const job: Job = await client.waitForJob(jobId);
```

When vendoring, copy `synwevo-docs.mjs`, `synwevo-docs.d.ts` and `synwevo-docs.d.mts` together:
for a relative `.mjs` import TypeScript reads the `.d.mts`, which only re-exports the `.d.ts`.
`test/types-check.ts` exercises the main flow and some misuse the types must reject; from the
repository root:

```sh
node_modules/.bin/tsc --noEmit --strict --module nodenext --moduleResolution nodenext \
  --target es2022 apps/docs/sdk/typescript/test/types-check.ts
```

`package.json` names the package `@synwevo/docs-sdk` 0.1.0. It is **private: not published to npm
or any other registry**. `npm pack` in this directory builds a local tarball (the module, the
declarations and this README) that another project can install by path, and `npm test` runs the
contract tests. It has no dependencies and is not one of the repository's npm workspaces.

## Reproducible synthetic acceptance

The runner imports the shared `acceptance/make_kit.py` unchanged and starts the real server using
`apps/docs/scripts/_testenv.py`. It takes no existing service URL. A controlled synthetic fixture
registers a workspace and service client; a separate real Account process issues the OAuth tokens. The existing workspace Python environment must have the repository's locked
dependencies installed; the Node client itself has no npm dependencies.

```sh
node --test apps/docs/sdk/typescript/test/client.test.mjs apps/docs/sdk/n8n/test-workflow.mjs
.venv/bin/python apps/docs/sdk/acceptance/ts_run.py \
  --evidence /private/tmp/synwevo-ts-new-run --node /path/to/node
```

The evidence directory must not exist. This validates library and CLI generation, multipart and
resumable uploads, retries after discarding real successful responses, idempotent replay, cursor
pagination, checksum rejection and all 12 downloaded DOCX contents. It writes commands/results
and removes plaintext credentials on exit. See [the n8n README](../n8n/README.md) for its optional
real local execution in the same acceptance run. The same runner also executes `ts_builtin.mjs`:
both builtin kinds use all 12 shared source records, create/save/export through the library and CLI,
check version history, replay and stale-save rejection, and render/download a preview plus 12
artifacts each. Native DOCX paragraphs and XLSX cells are read back against the shared kit, including
the updated editor's revision marker. A real read-only OAuth token can export models but receives
403 on create/save; the normal token has only Docs read/write scopes.
Add `--builtin-only` to select just this smoke test; it cannot be combined with `--n8n`.

The compatibility entry point above delegates service orchestration to
`tests/integration/sdk_acceptance/ts_run.py`; identity setup is in `ts_fixture.py`, and n8n
orchestration in `n8n_run.py` / `n8n_receiver.py` in that same integration directory. The external
client and workflow files import no backend packages. The shared synthetic kit stays in
`apps/docs/sdk/acceptance/make_kit.py` unchanged.
