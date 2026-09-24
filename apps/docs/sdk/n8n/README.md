# Local n8n standard HTTP workflows

These importable n8n 2.40.5 workflows use standard nodes and Node 24. They require an isolated
loopback Docs service and synthetic data. No custom n8n node, cloud, tunnel or product UI is used.

`generate-documents.json` reads the shared 12-record `acceptance/make_kit.py` kit, creates the
project/source/template/fields/mapping through HTTP, runs preflight and simulated rendering,
downloads a real signature-pinned native preview, submits/polls a job, follows manifest pages,
and downloads artifacts through n8n binary storage. Code nodes verify manifest and response
SHA-256 before standard file nodes save bytes. The acceptance runner checks DOCX contents too.
Configure `baseUrl`, `kitDir`, `outDir` in Config; create the output directory first. Health must
report `instance=test`. Its bounded polling remains available when webhook delivery is unavailable.

Bind authenticated Docs HTTP nodes to one n8n **OAuth2 API** credential: grant type
`clientCredentials`, Account `accessTokenUrl`, client ID/secret, scope `docweave.read docweave.write`,
authentication `body`, and additional body properties `{"resource":"urn:synwevo:app:docweave"}`.
Register the service account for the synthetic workspace using the Account CLI or controlled
fixture. It needs no admin scope. Secrets belong in n8n's encrypted credential store; exported
workflow JSON contains only credential references, never secret values or environment expressions.

`receive-documents.json` is a runnable standard Webhook → HTTP Request → binary/file workflow.
The Webhook node retains **raw body** bytes in binary `data`. An HTTP node forwards those bytes
and Standard Webhooks headers to `verify-receiver.mjs`, bound only to `127.0.0.1`. This small local
companion verifies HMAC and a 300-second timestamp window before returning a job ID. It keeps
atomic, fsynced receipts with a 60-second lease and persistent event fingerprints. Completed
replays receive 204 without another fetch; in-flight attempts receive 503. Failed auth/download/
write paths release their lease; a crash leaves it retryable after expiry. Run one companion per
state directory and retain receipts for as long as the sender may redeliver.

The receiver then calls Docs whoami, job, paginated manifest and content using the OAuth credential.
It checks the configured workspace, ignores all event URLs, verifies checksums, writes binary
files and a result, marks the receipt complete, then returns 204. It accepts `job.completed`
notifications; subscribe only to that type. Other events, invalid signatures and wrong workspaces
receive 401. Its workflow timeout is 50 seconds; the sender may timeout sooner and retry safely.
The receipt protects completed fetches, not an arbitrary external business transaction.

Configure `baseUrl`, `verifierUrl`, `tenant`, `outDir` in Receiver config. In a second **Custom Auth**
credential store JSON with headers `Authorization` (a random local verifier bearer token) and
`X-Synwevo-Signing-Secret` (the subscription's one-time signing secret). Bind that credential to
Verify raw body, Complete receipt and Release failed receipt. These headers are sent only to the
local verifier. Bind the other HTTP nodes to the OAuth credential. Start the companion with the
same bearer token in a private file (arguments are paths/IDs, not secrets):

```sh
node apps/docs/sdk/n8n/verify-receiver.mjs AUTH_TOKEN_FILE STATE_DIR WORKSPACE_ID FREE_PORT
```

Create the synthetic subscription using `python -m synwevo_docs.cli --data ISOLATED_DATA webhooks
add --tenant WORKSPACE_ID --url http://127.0.0.1:N8N_PORT/webhook/synwevo-events --events job.completed
--json`. Enable HTTP/private-address allowances only in the isolated Docs process. Import both
workflows and credentials using n8n CLI. Activate the receiver locally using `publish:workflow
--id synwevo-synthetic-receiver` before starting that local n8n process. This n8n command activates
the stored workflow on this instance; it does not publish it to a remote service.

The acceptance runner automates all of this, using an existing local installation:

```sh
.venv/bin/python apps/docs/sdk/acceptance/ts_run.py \
  --evidence /private/tmp/synwevo-n8n-new-run \
  --node /path/to/node24/bin/node --n8n /path/to/n8n/node_modules/n8n/bin/n8n
```

The evidence directory must be new. The runner binds services and the n8n task broker to loopback,
sets explicit proxy bypass, disables telemetry/version checks/templates/community packages, enables
filesystem binary storage, and restricts file nodes to the evidence directory. It imports mode-0600
credential files into a fresh encrypted n8n store, removes plaintext files, and shuts down all
processes. Account and Docs data are removed at shutdown. The evidence retains n8n's encrypted
store, execution data, binary artifacts, commands/results and per-document content checks; the
n8n-managed encryption key remains in its mode-0600 config inside the private mode-0700 test
home. Treat that home as credential material; exported workflow JSON contains no secrets.

`receiver-verification.json` records actual dispatcher delivery, tampered/wrong/stale/future
signatures, wrong workspace, replay across companion restart, service disable denial and the
same event succeeding after reenable. `verification.json` checks generated DOCX content. Previous
failed runs remain failures in their own evidence directories. The installation was cached for
this test; the repository does not claim to supply a reproducible n8n install lockfile.

The generation workflow uses an execution-specific idempotency prefix. Reuse its exact `runKey`
with identical inputs to recover a logical run. Polling is bounded to three minutes, workflow to
four. The 12-record assertions belong to the fixture. Regenerate JSON after changing builders:

```sh
node apps/docs/sdk/n8n/build-workflow.mjs
node apps/docs/sdk/n8n/build-receiver.mjs
node --test apps/docs/sdk/typescript/test/client.test.mjs apps/docs/sdk/n8n/test-workflow.mjs
```

The compatibility entry point above delegates service orchestration to
`tests/integration/sdk_acceptance/ts_run.py`; identity setup is in `ts_fixture.py`, and n8n
orchestration in `n8n_run.py` / `n8n_receiver.py` in that same integration directory. The external
client and workflow files import no backend packages. The shared synthetic kit stays in
`apps/docs/sdk/acceptance/make_kit.py` unchanged.
