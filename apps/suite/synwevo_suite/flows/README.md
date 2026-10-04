# WP7 deterministic flows: integration contract

This package registers user actions under **the personal Agent app**, not an administrator
API or a new platform application. The Suite installer calls `flows.install(ctx)` after the
explicitly released Agent and all business-operation adapters have been installed. It refuses
an absent/non-personal Agent manifest. It never enables an app, mounts routes, or migrates a
database. The existing Store schema loader already lists `flows`; the new additive DDL module
is picked up through that existing entry. No Store/schema-entry change is required on this baseline.

With `ctx.start_jobs=True`, installation starts a lifecycle-managed `Ticker` every five seconds.
Event listeners only wake it **after commit**. Durable polling still works if a listener is lost.
Every replica can run the ticker: database leases, monotonically increasing fences, unique
operation keys and atomic cursor/delivery writes arbitrate concurrent execution.

## User operations

All operation IDs have the prefix `agent.flow.` and `need=personal`. Their top-level workspace
must be null. They are available through the existing operations router; a definition names
each business workspace explicitly. Flow ownership is always the stable user ID.

| Suffix | Purpose |
| --- | --- |
| `create`, `publish`, `get`, `list` | Create immutable v1, publish another immutable version with expected-version conflict check, inspect, paginate |
| `start` | Pin a version and typed parameters; explicit idempotency key binds flow + user + version + parameters |
| `run`, `runs` | Owner-only metadata, paginated history/run list, currently authorized output snapshots |
| `pause`, `resume`, `cancel` | Fence workers; keep original run/version/step keys when recovering |
| `trigger.create`, `trigger.get`, `trigger.list` | Fixed version, input, trigger and scoped recurring policy |
| `trigger.enable`, `trigger.deliveries` | Disable/re-enable explicitly; inspect paginated started/skipped/refused delivery receipts |

Writes have Agent confirmation policy. Creating/re-enabling recurring authority is person-only
(`agent=never`, `unattended=False`); the coordinator's user interface must collect the concrete
policy approval. All flow operations refuse unattended invocation, preventing recursive flow
creation through the tool registry. Registry result guards are supplied when supported by the
shared Operation contract.

## Definition v1

`parameters` maps names to `{type, required}` (JSON string/integer/number/boolean/object/array).
Input has no unknown keys and no implicit defaults. `steps` is a bounded, forward-only graph.
`next` defaults to the following step; `end` completes the flow. `on_error` names a forward
failure exit. An explicit `fail` step terminates with its code; a handled failure followed by a
successful exit reports `partial`. Permission failures and unknown outcomes cannot take a
side-effecting failure path.

```json
{
  "parameters": {"form_id": {"type": "string"}},
  "max_calls": 1000,
  "timeout_s": 604800,
  "steps": [
    {
      "id": "request", "kind": "call",
      "operation": "forms.request.create", "operation_version": 1,
      "workspace_id": "EXPLICIT_WORKSPACE_ID",
      "input": {"form_id": {"$ref": "params.form_id"}, "version": 1},
      "result_read": {
        "operation": "forms.request.get", "workspace_id": "EXPLICIT_WORKSPACE_ID",
        "input": {"request_id": {"$ref": "receipt.request_id"}}
      }
    },
    {
      "id": "collect", "kind": "wait", "state": "waiting_input",
      "read": {
        "operation": "forms.request.get", "workspace_id": "EXPLICIT_WORKSPACE_ID",
        "input": {"request_id": {"$ref": "steps.request.id"}}
      },
      "until": {"left": {"$ref": "record.status"}, "op": "eq", "right": "submitted"},
      "reject": {"left": {"$ref": "record.status"}, "op": "in", "right": ["closed", "revoked", "expired"]},
      "event_types": ["forms.submission.created"], "poll_s": 60, "timeout_s": 604800
    }
  ]
}
```

The coordinator extends this with registered production Approvals create/get operations, a
`waiting_approval` record read, then a `branch` with `condition`, `then`, and `otherwise`. The
condition reads the actual decision, not event data. The approval decision itself remains a
human/business-policy operation. Subsequent Docs/Message calls use their registered adapters.
The isolated regression uses real Forms and an **explicit `fixtureflow` approval implementation**;
it is not evidence that production Approvals, Extract, DataPrep, Tables, Docs, or Message have
been integrated here. The sample above stops after form submission and is not full B1.
`test_flows_approvals.py` is a separate production-operation composition gate: it explicitly
skips when Agent/Approvals are absent, and runs real form submission, independent approval or
rejection, Store restart, result mapping and a subsequent Forms business operation when present.
The coordinator must obtain passing results for both cases after integration, not count the
baseline skips as acceptance.

References are exact JSON `{"$ref":"params.name"}` / `{"$ref":"steps.previous.field"}`;
list indexes are decimal path segments. `receipt` only exists in result-read inputs; `record`
only exists in wait predicates. Predicates support `eq`, `ne`, `in`, `exists`. There is no code,
expression evaluation, shell, SQL, or arbitrary HTTP target. Operation versions are pinned and
rechecked. Application configuration/template versions belong explicitly in operation input.

## Safety and recovery semantics

* Transactional calls, successful step history, next-step transition and outbox commit together.
  A failed attempt rolls back business writes; its attempt/error and consumed read budget are
  recorded separately. Explicit retry codes apply only to transactional server failures, with
  exponential bounded backoff, at most five attempts, per-run call budget and deadline.
* Non-transactional calls persist a dispatch marker before I/O. An expired marker is never
  dispatched again. A successful `op_runs` receipt permits finishing the step; a missing,
  running, failed, partial or unknown receipt requires reconciliation. `resume` cannot supply
  an invented result or reset the attempt budget. The owning business service must reconcile
  and update the **original** operation receipt. This baseline has no proof-of-non-dispatch
  contract; such ambiguous cases deliberately stay blocked for reconciliation.
  In-flight I/O timeout/cancellation remains the operation adapter's responsibility; the engine
  cannot safely kill an arbitrary Python handler or retract a remote write. Lease expiry fences
  it and prevents another worker from dispatching the same step.
* Queued/running async receipts are polled without reinvoking the operation. Business waits
  persist their deadline and next check and hold no thread or model session between ticks.
* Cancellation prevents unstarted steps and fences completion. It cannot recall an external
  effect already dispatched. History includes its operation ID/status even if the response
  arrives after cancellation. Completed earlier business records are not deleted or replayed.
* Every step revalidates the user, Agent availability, delegation lifecycle, operation version,
  target app/role and the handler's resource ACL. Sources are re-read before mapping; a changed
  referenced field blocks stale consumption. Historical output is hidden when it cannot be
  reauthorized or no longer matches. User inputs, receipts and source bodies are absent from
  generic run metadata and the flow event outbox.
* **Shared integration**: `Capabilities.inspect` avoids recording an op run on every read poll.
  On the old baseline, reads use the real audited `invoke` instead. Write outputs need an
  explicit `result_read` or the upgraded `Operation.result_guard`; workspace membership alone
  is not treated as a resource guard. The coordinator should retain upgraded capability result
  presentation for its generic operation-history endpoint too.
* Independent operations can target several explicitly authorized workspaces. Carrying business
  content across spaces follows the shared data-flow contract (`docs/platform/data-flow.md` §3,
  Flows): once a run has read a workspace, every later write elsewhere, and every read whose input
  takes a value from the run, is asked for that source; runs started by the run's events carry
  its sources on. Personal operations and `writes_elsewhere` operations are not flow steps. This
  package does not infer data-transfer authority from dual membership. No browser workspace
  binding is read.

## Trigger contract

Event triggers bind a type (v1), explicit workspace and optional object kind/ID. They start at
the committed stream tail when created/re-enabled, and never replay historical events by default.
Event input can reference routing identities (`id`, `object_id`, `workspace_id`, `type`) only;
business content must be fetched with an authorized operation. Cursor advance, deduplication
receipt, run creation and budget increment commit in one transaction. Correlation, causation
and depth propagate; excessive depth records a refusal. Duplicate wait events only cause reads.

Schedules use IANA `timezone`, local `at: HH:MM`, and `weekdays` (Monday=0). A spring-forward
gap skips that local time; a fall-back fold runs only the first occurrence. `misfire=skip`
records a skipped delivery beyond `grace_s` (default 300); `once` coalesces downtime into one
run. Neither replays an unbounded backlog. The next occurrence is persisted in UTC.

Both require an immutable policy with exact `tools`, `workspaces`, `max_runs` and either an
`expires_at` or explicit `until_revoked=true` (not both). Inputs/version fix resources and
audiences for that trigger. Step budget/deadline also apply. Disabling fences active runs;
re-enabling does not restore exhausted budget, reset cursors backwards, or resume runs silently.
Expired/exhausted triggers stop generating refusal rows on every subsequent tick.

Production-app registration is integrated. The combined 2026-09-30 suite and adapter gate passed
270 tests without skips, including both actual Approvals restart cases. The personal automation
UI and full seven-application B1–B6 acceptance remain in progress.
