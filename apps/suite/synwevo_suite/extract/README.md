# Synwevo Extract

The independent `/extract` application uses the same versioned operations as the API and
Agent. Enable it with `SYNWEVO_SUITE_APPS=extract` (or the existing comma-separated selection).
The server composition injects the existing Docs `DocumentIngest`; the application never
imports Docs or creates another document engine. The worker runs in the deployed portal/all
service when `start_jobs=True`, not in the development agent.

## Contract

`POST /api/suite/ops/extract.<operation>` accepts the normal suite envelope with explicit
`workspace_id`, `input`, and an `idempotency_key` for writes. The authenticated operation
catalogue supplies input/output schemas, permissions and event names. A user needs current
workspace membership to read, and an owner/admin/editor role to change business records.
Downloads additionally check workspace export permission. Agent writes use the shared
confirmation contract; human corrections and confirmations are not unattended tools.

| Capability | Operations |
|---|---|
| Templates | `template.list/get/create/save/publish/retire/archive` |
| Tasks | `task.create/list/get/cancel` |
| Evidence and review | `task.segments/correct/confirm/review_version/result` |

File upload is multipart `POST /api/suite/extract/files?workspace_id=…`, with `file`, CSRF
and `Idempotency-Key`. A reused key with different bytes/name conflicts. Uploads preserve
original bytes and a SHA-256 digest; `GET /api/suite/extract/files/{id}?workspace_id=…` checks
current access on every download. Malware screening follows the platform's actual policy;
an absent scanner is reported as not configured.

Published templates are immutable and tasks pin their version and original file. Retiring a
version prevents new tasks but does not change existing tasks. Each file in a browser batch
gets an independent task. A create receipt remains `queued`/`running`/`waiting_input` until
the actual task changes state. Parser output alone never produces a successful receipt.

All results currently require manual confirmation, even when a field's `review_required`
flag is false. This is a conservative application policy. Machine results, located source
segments, human corrections and immutable review revisions are separate. A correction
invalidates the current confirmation; it never overwrites machine evidence. Human-only
values retain `unverified` status and need explicit acknowledgement. `task.result` and JSON
export require the explicit, currently confirmed revision. Historical review revisions stay
readable through `task.review_version`, but are not implicitly valid downstream results.
The original create receipt projects current task state, including a later return to review.

Events include queued, started, ready, failed, cancelled, correction and confirmation;
they are persisted in the transaction with the business change and retain correlation and
causality depth. Consumers must de-duplicate event IDs and bind confirmed revisions.

## Supported data and limits

- PDF, PNG/JPEG/WebP/TIFF, CSV/TSV uploads, at most 12 MiB per file; 500 files/250 MiB per
  workspace. The import page accepts ten files per batch. CSV encoding/delimiter parsing
  reuses the existing document reader; no spreadsheet macros or user code execute here.
- Deterministic literal line prefixes, exact table cells or header columns; text, fixed
  decimal, ISO calendar date and boolean fields; at most 40 fields, 100 matches per field,
  100 published versions. Decimal strings preserve precision and leading zeroes.
- Page/line, table/cell and actual text offsets are the source locations. No invented
  bounding boxes or model confidence probabilities. Missing, ambiguous, conflicting,
  invalid and unverified values are distinct. All parser/file content stays untrusted data.
- Every match is accounted for: each field carries `occurrences` (`labelled` matches,
  `located` values). A line that starts with the prefix but has nothing after it (text
  recognition often reads a column of stacked labels apart from their values) is an
  `unresolved` occurrence with its location, and the field is `incomplete`. Its value is not
  taken from the next line, since the reading order does not prove which value belongs to
  which label. A field with more than 100 matches is `over_limit`: it is left unread, with its
  counts and a field-level message; the other fields are still read. `incomplete` and
  `over_limit` block confirmation until a person corrects the field.
- Text PDF ceiling: 200 pages; OCR ceiling: 20 pages. Known truncated reader results fail
  explicitly. A structurally damaged PDF fails with `damaged_file` and yields no values: the
  reader reports a page it cannot load, a page content entry that is not a stream or, after
  rebuilding a broken cross-reference table, a referenced object that is gone or the engine's
  own lost-content diagnostics. A rebuild that loses nothing (wrong offsets, no table) is read. Further caps: 10,000 segments, 150,000 source characters, 2 MiB child result,
  200,000 field-result bytes, 2,000 rows and 100 columns per table. Long lines at the legacy
  reader's 500-character truncation ceiling are rejected conservatively.
- The worker admits 200 pending tasks globally/20 per workspace, with at most two active
  leases. A spawn-isolated parser has a 90-second deadline; cancellation terminates its
  process group including OCR descendants. Linux also applies an address-space limit.
  Expired leases and graceful deployment interruptions resume fixed input within two
  attempts. Exhaustion fails explicitly. Current authorization is rechecked before parsing
  and before storing results. Confirmed records cannot be replaced by delayed workers.
- At most 200 correction/confirmation revisions. Errors do not echo provider secrets or
  file text. Missing parser/OCR configuration is a real error, with no fake fallback.

## Verification and remaining acceptance

`test_extract.py` covers isolated CSV contracts, permissions, versions, idempotency, source
spans, review races, cancellation, bounded parser failure and deployment recovery.
`tests/integration/test_extract_reader.py` uses actual PDF bytes and the deployed portal/all
composition with the real parser child. Browser evidence and commit-bound results live in
the canonical `docs/agent-work/EVIDENCE.md` in the coordinating checkout.

This slice does not yet establish the complete Forms → Extract → DataPrep → Tables →
Approvals → Docs → Message workflow. Forms attachment adoption and reviewed-result mapping
still need their explicit authorization/transfer adapters. Production and full acceptance
remain subject to the existing repository CI/CD gates; local passing tests are not a
deployment claim.
