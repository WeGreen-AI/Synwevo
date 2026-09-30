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
  answers fingerprint. Historical guard denial retains the original frontend idempotency key.
- Worker terminal state and events commit transactionally; observers wake after commit. Events:
  `dataprep.dataset.created`, `dataprep.recipe.published`, `dataprep.run.queued`, and
  `dataprep.run.succeeded|partial|failed|cancelled`. Consumers query live results by run ID.
- UI execution reports obtain step labels from the pinned published version, and input dataset
  names from an authorized read. They do not use an editable draft to describe old runs.
- Current browser and integration evidence lives in canonical `docs/agent-work/EVIDENCE.md` in
  the main checkout. The historical isolated observations below do not imply production release.

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
  Cancellation, current account/app/workspace access and retained Forms dependencies are checked
  at checkpoints and again in the result-commit transaction. Results, issues, op_run status and
  terminal event commit atomically. Manual rerun creates a separate run and requires the version
  still to be available for new work. Existing jobs survive version retirement/recipe archival.
- CSV prefixes potential formulas and quotes all fields; XLSX exports explicit text cells.
  Both flatten null/missing to empty display cells. JSON preserves all three states and complete
  provenance. Downloads enforce current workspace export permission. Issue exports do too.

## Validation scope

Focused tests use the root venv with an explicit PYTHONPATH containing only this worktree's
core, auth-client/python, Docs, Messages, Portal, identity, server and Suite paths. The test
`test_isolated_import_roots` asserts module locations. Tests create data only in pytest tmp_path.
`test_shared_history_and_replay_gate_after_revocation` now runs against the integrated shared
contract. Its compatibility skip only applies to earlier standalone worktree baselines.

The 20,000-row/two-column CSV test exercises real parsing, a published decimal conversion,
full processing, 200 invalid rows, paged errors, and a complete downloadable error list. It is
an isolated local measurement, not a claim about production capacity. No external systems,
commits, pushes, deployments or real-user datasets are involved.

Observed local verification (2026-09-30, macOS, Python 3.14.5):

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
