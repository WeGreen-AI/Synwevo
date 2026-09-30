# DataPrep integration and declared behavior

DataPrep uses the shared operation registry, Store, upload acceptance path and suite shell.
It is installed through app/schema auto-discovery and explicitly enabled by `SYNWEVO_SUITE_APPS`.

## Integrated services

- `dataprepRoutes` registers `/dataprep/w/:wid` and its import, recipe, dataset and run deep links.
- `blob_refs.DIRECT` registers `dataprep_files.blob` and `dataprep_datasets.rows_blob` (gzip table
  JSON); writer checks cover `DataPrep.upload` and `DataPrep.save_table`. Content/definition/output
  fingerprints are classified as non-blob hashes.
- Every write declares `Operation.result_guard`; shared replay/run history enforces current
  access without rerunning a mutation. `Capabilities.inspect` performs authorized source reads
  without creating operation runs. Forms lineage requires live source access and an unchanged
  answers fingerprint. Extract lineage requires the current confirmed revision and an unchanged
  complete result fingerprint. Historical guard denial retains the original idempotency key.
- Worker terminal state and events commit transactionally; observers wake after commit. Events:
  `dataprep.dataset.created`, `dataprep.recipe.published`, `dataprep.run.queued`, and
  `dataprep.run.succeeded|partial|failed|cancelled`. Consumers query live results by run ID.
- UI execution reports obtain step labels from the pinned published version, and input dataset
  names from an authorized read. They do not use an editable draft to describe old runs.
- Current browser and integration evidence lives in canonical `docs/agent-work/EVIDENCE.md` in
  the main checkout. The historical isolated observations below do not imply production release.

## Confirmed Extract import (operation version 1; B1 integration contract)

`dataprep.dataset.from_extract` is a transactional, synchronous write in the capability registry,
available to API/flow/Agent callers with DataPrep write and Extract read access in the **same**
explicit workspace. Its Agent policy is `confirm`; human Extract confirmation remains a separate
operation. Titles are supplied in Simplified Chinese, Traditional Chinese and English. This patch
adds no UI, adapter, flow, migration, parser, model provider or fallback.

POST `/api/suite/ops/dataprep.dataset.from_extract` with the usual workspace and idempotency envelope:

```json
{
  "workspace_id": "<workspace>",
  "idempotency_key": "<stable-key-for-this-import>",
  "input": {
    "name": "Confirmed invoices",
    "tasks": [{"task_id": "<extask_id>", "revision": 2}],
    "field_ids": ["code", "amount"],
    "detail_policy": "zip_strict"
  }
}
```

`tasks` and `field_ids` are required, ordered and unique; revision is an explicit integer, never
“latest”. Field IDs become column names in the supplied order. Every selected ID must exist in
each task's confirmed result; labels, renamed labels and arbitrary aliases do not resolve IDs.
Unconfirmed, superseded or withdrawn revisions fail through `extract.task.result`. Import resolves
that current operation through `Capabilities.inspect`, not sibling imports, Extract tables or
historical review snapshots. No child read runs or source writes are created by the import/guards.

Repeat policy is **positional strict zip** within each task: selected list fields must all have
equal length, scalar fields repeat on each detail row, and task results append in request order.
Lengths never pad, truncate, take a Cartesian product or silently choose one field. Unequal lengths
fail the whole import with `extract_detail_length`, including zero versus nonzero lengths. Selected
empty lists yield zero rows for that task; with no selected lists there is one row per task. This
does not infer a relational join between independently extracted lists; callers choose fields whose
ordered elements correspond. Select only scalar IDs when a task-level summary row is intended.

Values stay exact strings: decimal scale, leading zeros, dates and boolean text are unchanged;
JSON numbers/booleans and nested objects are rejected rather than coerced. A field with Extract
`status="missing"` is omitted from the row. An explicitly present, non-missing null remains null;
`""` remains empty text. The current Extract service produces null only for missing scalar values,
so those become missing keys, not invented explicit nulls. An absent field ID is a mapping error,
not a missing value. Human-corrected `unverified` values are accepted only after the upstream
confirmation contract has required its explicit acknowledgement. Machine/correction evidence
remains available at the pinned Extract task/review; it is not fabricated or flattened into cells.

The exact successful response shape is below (angle-bracket values are illustrative references).
It contains persisted metadata, **not preview rows**; read rows with `dataprep.dataset.get`.
The registry also publishes the typed output schema.

```json
{
  "run_id": "<run_operation_receipt>",
  "operation": "dataprep.dataset.from_extract",
  "status": "succeeded",
  "replayed": false,
  "workspace_id": "<workspace>",
  "output": {
    "id": "<dset_id>",
    "workspace_id": "<workspace>",
    "name": "Confirmed invoices",
    "origin": "records",
    "source": {
      "kind": "extract",
      "workspace_id": "<workspace>",
      "operation": "extract.task.result",
      "operation_version": 1,
      "field_ids": ["code", "amount"],
      "detail_policy": "zip_strict",
      "tasks": [{
        "task_id": "<extask_id>",
        "revision": 2,
        "template_id": "<extpl_id>",
        "template_version": 1,
        "definition_digest": "<sha256>",
        "file_id": "<exfile_id>",
        "content_digest": "<sha256>",
        "result_fingerprint": "<sha256-of-complete-current-result>"
      }]
    },
    "columns": ["code", "amount"],
    "row_count": 2,
    "column_count": 2,
    "content_hash": "<sha256-of-table-values-and-origins>"
  }
}
```

`origin="records"` is the existing schema's record storage category; `source.kind="extract"`
is the authoritative source discriminator. No migration is needed. The complete result fingerprint
includes file metadata, review, values and machine evidence, including additive `file.source`
provenance supplied by upstream Forms integration. That provenance is not copied as an alternative
authority: every source check calls the live `extract.task.result` again, so its current upstream
authorization and quarantine refusals propagate. Source changes never cause implicit re-import.

Rows have `{"values":{"code":"000127","amount":"12345678901234567890.12"},
"origins":[["<extask_id>",1,2]]}`. Origins mean task ID, **1-based output detail position**, confirmed
revision; the position is not a fabricated physical CSV/PDF location. The engine preserves origins
through processing, including issue `params.origins` and JSON export. For B1, bind the import's
`output.id` to `dataprep.run.start.input.inputs.main`, with an explicit published recipe/version.
That start operation initially returns `output.id=<dpu_id>` and status `queued`; poll
`dataprep.run.get` with `run_id=<dpu_id>`. A terminal result exposes `output_dataset_id`, `counts`,
`issue_total` and `reports`; inspect all issues via `dataprep.run.issues`. Its shared operation receipt
eventually has `output={run_id,status,output_dataset_id,issue_total}`. Keep the outer `run_...`
operation receipt distinct from the `dpu_...` processing run. Fetch the resulting dataset by its
ID before downstream Tables mapping; do not treat queue acceptance or a `partial` result as a
business approval. Derived dataset `source.inputs` retains the import dataset/hash dependency.

Source checks apply to dataset reads/issues/listings, run reads/issues/listings/cancel responses,
preview, enqueue, rerun, worker checkpoints and final commit, exports, generic history and
idempotent replay. A source failure in a requested listing page rejects the page rather than
returning old names, columns or execution reports. History returns the shared redacted receipt;
replay returns `run_result_unavailable` without another dataset/event. Re-confirming a correction
does not reactivate the old revision. A new import explicitly pins the new confirmed revision.

Limits: 100 distinct tasks per import (also bounds live registry checks), 64 selected columns,
100 details per task, 50,000 rows / 500,000 cells, 4,096 characters per cell, 24 MiB cumulative
canonical source results, and the existing 24 MiB table and workspace storage budgets. Current
Extract templates have at most 40 fields and lists at most 100 values, so the current producer
further limits an import to 10,000 rows and at most 400,000 cells. Large evidence can reach the
source-byte limit earlier (`extract_source_limit`). All limits reject atomically without a dataset
or creation event; no successful result is silently truncated. These bounds fit the existing
synchronous record-import model without introducing an unbounded batch or another worker type.

## Processing contract

- Uploaded files: CSV (UTF-8 with optional BOM, UTF-16, GB18030; comma, semicolon, tab, pipe) and
  XLSX. Inclusive 1-based row/column range, first selected row is a unique nonempty header.
  Import creates an immutable dataset. The upload key is accepted as `Idempotency-Key` header
  or multipart `upload_key`, so the shared authenticated upload client can retry safely.
- Limits: 8 MiB file, 32 MiB XLSX expanded content, 500 ZIP members, 50 sheets, 8 MiB per non-sheet
  XML part; 50,000 data rows, 64 columns, 500,000 cells, 4,096 characters per cell, 24 MiB canonical
  table plus import issues. File row limits apply to the file even when a smaller range is chosen.
  All bound sources combined obey the row/cell/byte limits. Joins and every step are bounded.
  Exceeding limits fails explicitly, never silently truncates a full run.
- Workspace quotas: 500 files / 256 MiB uploads; 1,000 datasets / 256 MiB canonical data;
  1,000 recipes, 500 versions per recipe, 10,000 runs, 20 queued/running jobs. The app has no
  destructive cleanup operation; reaching a quota requires coordinator-owned retention work.
- Raw values are text. CSV leading zeros survive. XLSX numeric cells preserve the XML decimal
  lexical value, not display formatting (a `0000` display mask does not manufacture zeros).
  Decimal/integer conversion uses Decimal, a 38-digit input bound, explicit scale, and either
  rejection of precision loss or half-even rounding. Aggregation uses an 80-digit context.
- Missing keys, explicit null and empty strings are distinct in rows and join/deduplication keys.
  Default CSV null marker is literal `\N`, configurable. Whitespace cleaning is explicit.
  Split uses literal separators, limited by target count; the last field retains the remainder.
  Merge has an explicit null policy. Filter/check comparisons use operators, never code/eval/SQL.
  Parameter references are exact `$name` values declared in the recipe.
- Dates use ISO strings. Timestamps with an offset normalize to UTC; timestamps without one
  require a recipe-provided fixed offset. There is no guess at local timezone or daylight saving.
  XLSX dates use the workbook epoch and millisecond resolution, returning local ISO values;
  Excel's fictitious 1900-02-29 is an error. Formula policy defaults to reject; opt-in cached
  values are never recalculated, missing caches are errors. Macros/external workbook links are
  refused. XML entities/DTDs and UTF-16 XML parts are refused; there is no execution engine.
- Stable sort; deduplication explicitly keeps first/last; append aligns strict columns or unions
  them; joins choose unique/first/all matches with explicit left/inner behavior. Null/missing
  join keys do not match by default. All-match joins deliberately permit bounded many-to-many
  expansion. Duplicate right keys under unique matching are a structural failure.
- Cast, numeric sort and numeric aggregate errors exclude the affected row and record its
  source; validation explicitly keeps or drops an invalid row. Numeric aggregations ignore
  null/missing values, use exact arithmetic; count counts rows. Complete row errors are paged
  and downloadable. Structural failure commits no output dataset.
- A preview applies the draft to actual selected samples (offset/size for each source), declares
  those ranges and is read-only. It displays up to 200 result rows and refuses oversized previews.
  Full jobs pin an immutable published definition, all input dataset hashes and parameters.
- Progress comes from actual processing. One persisted job per database runs at a time. A
  120-second heartbeat lease with a fencing token protects recovery; deterministic computations
  restart from pinned inputs, maximum three attempts. The processing budget is 90 seconds.
  Cancellation, current account/app/workspace access and retained Forms/Extract dependencies are checked
  at checkpoints and again in the result-commit transaction. Results, issues, op_run status and
  terminal event commit atomically. Manual rerun creates a separate run and requires the version
  still to be available for new work. Existing jobs survive version retirement/recipe archival.
- CSV prefixes potential formulas and quotes all fields; XLSX exports explicit text cells.
  Both flatten null/missing to empty display cells. JSON preserves all three states and complete
  provenance. Downloads enforce current workspace export permission. Issue exports do too.

## Validation scope

Confirmed Extract import patch verification (2026-09-30, base `8e8fcec`, uncommitted scoped patch):

- Main checkout's Python venv, all package paths resolved to this worktree, including Docs SDK:
  `pytest apps/suite/tests -o addopts=--import-mode=importlib -q --tb=short --durations=3`:
  **209 passed, 0 failed, 0 skipped**, 69.01 s. The new `test_dataprep_extract.py` contributes
  **17 passed**; its focused run took 10.68 s. One existing Starlette/AnyIO deprecation warning.
- Boundary, brand, design-token, component-name and original-logo checks passed; `git diff --check`
  passed. No core/schema, Extract, UI, adapter, flow or progress files changed.
- Tests use real Extract services and declared isolated CSV fixtures, actual human confirmation,
  processing, precision/provenance checks, repeat mismatch/empty policies, source revocation and
  changed revisions, history/replay, cross-workspace refusals and worker commit reauthorization.
  Resource-overflow tests lower budgets on real inputs; they are not maximum-capacity benchmarks.
- Frontend/browser tests, coordinator-owned Forms-to-Extract provenance/quarantine changes,
  production providers and deployment were not exercised by this patch. No services were started,
  no real user data was accessed, and no commits or pushes were made.

Focused tests use the root venv with an explicit PYTHONPATH containing only this worktree's
core, auth-client/python, Docs, Docs SDK, Messages, Portal, identity, server and Suite paths. The test
`test_isolated_import_roots` asserts module locations. Tests create data only in pytest tmp_path.
`test_shared_history_and_replay_gate_after_revocation` now runs against the integrated shared
contract. Its compatibility skip only applies to earlier standalone worktree baselines.

The 20,000-row/two-column CSV test exercises real parsing, a published decimal conversion,
full processing, 200 invalid rows, paged errors, and a complete downloadable error list. It is
an isolated local measurement, not a claim about production capacity. No external systems,
commits, pushes, deployments or real-user datasets are involved.

Earlier standalone DataPrep verification (2026-09-30, macOS, Python 3.14.5; retained history):

- Entire isolated `apps/suite/tests`: **57 passed, 1 skipped** (12.26 s). DataPrep contributes
  21 passes and the shared-history integration skip; Forms and capability regressions passed.
- The 20,000-row processing/error-export test call took **0.43 s** in that run (single test client,
  local temporary database, no network service). This includes import/run/error checks, not a
  production latency guarantee.
- Suite frontend typecheck passed; frontend tests **13 passed**, including all catalogue keys
  and matching placeholders in English and Traditional Chinese.
- Isolated Vite production build of `dataprep/index.tsx` passed (49 modules). It writes to a
  temporary directory and deliberately leaves the coordinator-owned route index untouched.
- Boundaries, brand, design-token and component-name checks passed.
- Browser, integrated shared-history returns, production rollout and external integrations were
  not verified. One pre-existing Starlette/AnyIO deprecation warning was reported by pytest.

Cancellation retains current DataPrep write authorization but does not require continued source access. It returns only run ID, status and cancellation flag; historical cancellation receipts contain no source values or parameters. Quarantined or revoked sources still block reads, previews, exports, retries and worker publication.
