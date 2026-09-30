# Synwevo Tables — contract and integration

Tables owns typed business records within one workspace. It never reads platform identity tables
as business data and never imports a sibling application. UI, API, workflows and Agent use the
same `tables.*` version-1 operations. `ops.py` is the executable input/permission/event contract;
`models.py` supplies bounded, extra-forbidden schemas.

## Installation and the shared integration boundary

`app.APP_MODULES` and `suite_schema.MODULES` already contain `tables`. Installing this package
supplies `MANIFEST` and `install(ctx)`; Store automatically discovers the additive Tables DDL and
five permission columns. No Store, schema index or backend route registration edit is needed.
The deployment must still explicitly release `tables` through `SYNWEVO_SUITE_APPS`.

`tablesRoutes` is registered in the suite `web/src/routes.tsx` `APP_ROUTES`.
The independent routes are `/tables`,
`/tables/w/:wid`, and `/tables/w/:wid/t/:tid/*` (grid, fields, history, import, settings,
new, `r/:rid`). The registry record deep link `/tables/records/:rid?workspace_id=…` resolves
the current authorized table before redirecting; it is not an access grant.

The coordinator's `Operation.result_guard` is required before release. `ops.register` passes
`guard.result_guard(service)` on all write operations when that field exists. The guard receives
a full historical run row and never invokes a write handler. It checks the caller, workspace,
table, current readable fields, input/output records and linked input targets. Content-bearing
old previews are refused; references may be returned only after these checks. Read operation
snapshots require the shared `Capabilities.inspect` and fingerprint comparison. The operation
registry must apply these rules to both idempotent replay and run-history reads, including any
Agent presentation of cached tool results. The compatibility branch for older local worktrees
does **not** provide that shared protection; it exists to enable isolated service tests only.
The integrated shared core now supplies this contract.
`test_registry_history_and_idempotency_with_new_guard_contract` tests revocation without duplicate
writes; older worktree compatibility paths are not release substitutes.

No notification kinds, stored blobs or digest-named columns were added. No blob-reference or
notification catalogue changes are required. Events carry only identifiers, action and revision;
consumers must resolve referenced objects with current authority.

## Permissions and lifecycle

Workspace owners/admins create tables, configure fields, set role thresholds, and archive/restore
tables. Editors write records and save views within their table/record/field rights; viewers read.
Saved-view updates/deletion require the creator or a workspace manager. Export creation currently
requires a workspace editor or manager; a viewer's separate `can_export` flag does not grant this
operation. Downloads always check current workspace export rights as well as object rights.

Table, record and field permissions are minimum role thresholds (`viewer`, `editor`, `admin` for
reading; `editor`, `admin` for writing). Every read is scoped by workspace. Missing or unreadable
tables/records return 404. Field projection happens before search, filters, sorting, aggregation,
history and export. Explicitly requesting a hidden field is refused. Formula visibility inherits
all transitive field dependencies. Relation projection removes unreadable/archived targets and
redacts inaccessible primary labels; a target's public sequence is the fallback label.

Archive retains cells and relation indexes. Incoming relations to archived records or tables
render unavailable without exposing their IDs, and reappear only if restored and readable.
Archived records release unique keys; restoration checks constraints and may conflict. Records
are never physically deleted by these operations. Field deletion retains original values and is
reversible; formula/view dependencies block deletion. Type changes cannot silently break formulas
or views, and require a preview bound to schema revision and all record revisions. Failed/lossy
conversions preserve originals in `tb_leftovers` and record history. Currency changes require an
explicit conversion outside this operation, not relabelling amounts. History restoration uses the
current type, constraints, rights and revision; it may legitimately refuse old incompatible values.

## Writes, queries and events

Each update/archive/restore supplies its expected `rev`; schema changes supply `schema_rev`.
Store transactions serialize validation, unique reservations, writes, history, run completion and
events. Stale writes return `revision_conflict`, never merge silently. Effective mutations emit
`tables.table.changed`, `tables.schema.changed`, `tables.record.changed` or `tables.view.changed`.
No-op record updates emit no event and do not advance revision. Registry idempotency keys identify
caller intents; imports also persist a single application outcome. Partial batches use SQL
savepoints (nested Store transactions alone are not independent rollback boundaries).

`record.query` supports search, up to 20 AND filters, up to 5 ordered sorts, selected fields,
archived records, and saved views. Filter operators are eq/ne/contains/gt/gte/lt/lte/empty/not_empty.
Numeric values are exact decimals; timestamps compare by instant. Ties retain record sequence.
Pages contain 1–200 records and at most 700 KB of record payload; follow `next_offset`, not an
assumed page size. Offset pagination reflects current state, not a frozen multi-page snapshot;
concurrent mutations may shift later pages. Aggregates use the same authorized projection.

A batch has at most 100 items. `atomic=true` rolls back every item on failure. With `atomic=false`,
successful rows commit and each failure has its input index, code and safe details. Schema and
record operations are synchronous and bounded: 100 fields and 10,000 retained records per table,
64 KB encoded cells per record and 16 MB of cell data per table. Queries currently scan the
bounded authorized table in memory; they are not an unbounded database query interface.

## Import/export and values

Field types: text/long_text, number, money, date/datetime, boolean, single/multiple select,
email/URL/phone, related records, formula, creation/update time/person. Number/money use decimal
strings, money also carries currency, select options have stable IDs. Constraints include required,
unique, decimals, number/money/date minimum/maximum and text length. Datetimes require an offset
and second precision; nonzero subsecond inputs are refused. Formulas use the closed parser in
`formula.py` (no arbitrary code, network, SQL or platform tools). Evaluation errors are explicit
cell errors. Field IDs remain stable when labels change.

`POST /api/suite/tables/{table_id}/imports?workspace_id=…` accepts multipart `file`, at most 2 MB,
500 data rows and 100 columns. UTF-8 CSV and one-sheet XLSX are supported. XLSX formulas are
refused, archive expansion is bounded, headers must be unique and nonempty. Staging is private to
the uploader and expires after one hour (up to 10 active tickets/person/workspace). Mapping and
preview/apply use `tables.import.apply`; preview executes the real validators/writers inside a
rolled-back savepoint. Partial mode reports failed rows without retrying successful rows. Upload
a corrected file for failed rows. No import overwrites existing records.

`tables.export.create` returns a private download URL, valid for ten minutes (up to 50 tickets).
The saved query is frozen, but its records and permissions are evaluated **at download time**.
CSV neutralizes spreadsheet formula prefixes; XLSX cells are explicitly strings. Downloads are
no-store and bound to caller/workspace. No export artifact is persisted as a blob. Expired staging
is swept by the existing Ticker when jobs run, and when another transfer is created.

## Validation scope

Tests must use the worktree's absolute package paths with the specified shared Python runtime;
`test_auto_discovery_and_worktree_imports` verifies core and suite module origins. The focused
suite covers role/field/record/relation restrictions, concurrent writes, no-op events, idempotency,
partial rollback, migration previews, restore conflicts, exact values, parser limits, import
mapping/replay, CSV/XLSX output, ticket revocation, schema reopening and a representative 1,000-row
query budget. The full independent Tables frontend entry can be compiled as a Vite library without
editing the shared routes. Browser acceptance and final shared-registry integration belong to the
coordinator; static checks are not browser evidence. Current cross-application evidence is
maintained in the canonical `docs/agent-work/EVIDENCE.md` in the main checkout.
