> Current integration status (2026-10-06, Hong Kong): Store migrations, governed provider routes, wallet, organization payer, payment service and gateway are integrated on the Agent billing release branch. Seven real-model calls settled once each in an isolated test wallet. Production enablement and real payment acceptance remain gated. The September handoff below is historical; see `docs/agent-work/EVIDENCE.md` for current evidence.

> Coordinator integration update: this foundation is now registered with the sole Store migration owner in the business-apps integration worktree. Fresh/repeated migrations are tested without funding, prices or enablement. The handoff below records the original isolated delivery; gateway, payments and charging acceptance remain separate.

# Billing ledger foundation — coordinator handoff

Status (2026-09-30): isolated implementation on `codex/billing-ledger`, base `8e8fcec`.
Charging is **not enabled**. No migration is registered, no routes/provider/UI are changed,
and no model, payment, network or production calls were made. No commit or push was made.

Owned files only:

- `packages/core/synwevo_core/billing/__init__.py`
- `packages/core/synwevo_core/billing/models.py`
- `packages/core/synwevo_core/billing/ledger.py`
- `packages/core/synwevo_core/suite_schema/billing.py`
- `tests/integration/test_billing_ledger.py`
- `docs/billing-ledger.md`

## Baseline review and decisions

Read root/packages AGENTS, CLAUDE, architecture/monorepo, Store initialization/connection/
transaction/migration methods, suite schema registration, Identity user lifecycle,
Capabilities authorization and deferred `authority` checks. Read the updated unified master
§28 (especially §28.3–9) and §37. Searches of core, identity, suite and repository Python/SQL/
TypeScript found **no existing wallet, monetary ledger or payment service**. Existing Agent
token counters are attribution, not money facts. This is a bounded review of this work package,
not a claim to have reviewed the entire platform or live deployment.

| ID | Evidence / risk | Decision and verification |
|---|---|---|
| BILL-01 | Store is the sole opener and migration owner | Service receives Store; no SQLite opener/migrator. Only fixture applies proposed SCHEMA through Store.conn. |
| BILL-02 | Store.tx nests without savepoints | Reject calls inside an existing transaction; a returned dispatch permission always follows an actual commit. Tested. |
| BILL-03 | Store connections default to WAL/NORMAL | Each ledger transaction sets the existing Store connection to synchronous=FULL before BEGIN IMMEDIATE. No network inside the transaction. Tested; affects subsequent users of that same connection. |
| BILL-04 | Generic authority has a legacy operator fallback; workspace rights do not confer payment authority | No reuse of that fallback. Required injected payment authorizer plus current active payer/actor checks. Coordinator must bind current delegation/data/tool rules explicitly. Tested refusal/revocation. |
| BILL-05 | No payments or approved commercial prices exist | No seeds, default exchange, provider adapter, fake-paid API or enablement. Required durable payment verifier, immutable server-created orders. |
| BILL-06 | SQLite REPLACE may bypass DELETE triggers | Immutable insert guards cover primary and alternate unique keys, in addition to UPDATE/DELETE guards. Tested. |
| BILL-07 | Unknown dispatch cannot prove upstream did not execute | One-shot durable send fence; no takeover/resend after dispatch. Bounded final release, cost-only late usage. Tested with process death. |

## Coordinator integration contract

The coordinator alone reviews and serially installs `suite_schema.billing.SCHEMA` through
the existing Store migration owner. This module declares SQL only; it is deliberately absent
from `suite_schema.MODULES`. Apply additive tables/views/triggers before starting a consumer.
The fixture test asserting “proposal not installed” documents this sidecar boundary and must
be adapted when the coordinator intentionally registers migration. There is no production
fallback if tables are missing: new charging operations fail closed.

Import from `synwevo_core.billing`. Construct:

```python
ledger = Ledger(store, environment="test", namespace="agent",
                authorize=check_current_payment_authority,
                verify_payment=check_durable_verified_payment,
                max_hold_seconds=86400)
```

`environment` is exactly `test` or `live`, selected by trusted deployment configuration;
namespace is a fixed server-owned business scope. A namespace partitions run/call/voucher
identities, **not** wallets. Wallets are global per personal user/environment/unit, so changing
workspace or namespace cannot duplicate funds. All service methods are internal Python APIs.
Do not expose them directly as HTTP endpoints, Agent tools or arbitrary administration SQL.

Both callbacks receive the same Store transaction connection, must make local database checks,
must return exactly `True` to authorize, and must not perform network I/O. Missing callbacks
deny their corresponding operation. Test callbacks are explicitly fixture-only.

- `authorize(connection, ChargeContext, action)` is called at `open_run`, `reserve`, `prepare`,
  `claim`, and `dispatch`. Resolve `authorization_ref` and `delegation_id` from trusted current
  identity/grant rows; enforce payment permission, current workspace/tool/data-export policy,
  revoked delegations, and the coordinator's period/platform budgets. Actor and payer must
  currently be active users. A descriptor alone is not authorization. Different actor/payer
  requires a delegation ID; organization payers are rejected until a real policy exists.
- `verify_payment(connection, PaymentReceipt)` must resolve durable signature-verified inbox
  evidence and independently verify raw signature provenance, merchant, environment, success,
  amount/currency/order binding and current reversal/dispute status. It must reject stale
  success after a reversal. `PaymentReceipt` is a typed input, **not a proof** of payment.
  The proposed `billing_payment_events` table is the atomic accepted-credit event record;
  it is not a network webhook inbox or signature verifier.

### Gateway sequence and exact APIs

1. Publish approved `PriceSnapshot` with `publish_price(snapshot) -> price_id`. Fields:
   `price_id, purpose ('sale'|'cost'), Unit(code, scale), provider, model,
   Rate input_uncached/input_cached/output, terms_ref, effective_from` (integer UTC seconds),
   `per_tokens` (default 1,000,000). Version IDs are immutable and global per environment.
   Alias-to-effective-model confirmation and selection of a cost tariff/time window belong
   to the provider/gateway. There is no imported price, cost estimate fallback or FX conversion.
2. Build `ChargeContext(payer_user_id, actor_user_id, run_id, authorization_ref,
   workspace_id=None, delegation_id=None, payer_kind='personal')` from authenticated context.
   `open_run(context, unit=..., budget=..., max_calls=..., max_attempts=..., deadline=...)
   -> run_key`. The run fixes payer, actor, delegation, authorization and budget.
3. `reserve_call(context, logical_call_id=..., amount=..., price_id=..., deadline=...)
   -> call_key`. Per-call workspace may differ, fixed payer may not. Gateway must calculate a
   conservative authorized upper bound over all input/output/reasoning limits and enforce
   those limits upstream. This ledger does not tokenize or invent that bound. Only child
   calls freeze funds: spent plus active child holds may not exceed the parent run budget.
   An idempotent replay with changed arguments conflicts, including a changed workspace.
4. `prepare_attempt(call_key, attempt_id=..., owner=..., cost_price_id=...,
   requested_model=..., lease_seconds=60, retry_reason=None) -> AttemptTicket`.
   Ticket fields: `attempt_key, owner, fence`. A prepared attempt has not crossed the send
   boundary. `claim_prepared(attempt_key, owner=..., lease_seconds=60)` rotates its fence only
   after lease expiry, while the call remains active and uncancelled.
5. `dispatch(ticket, send_window=...) -> None` commits `dispatch_unknown` and a timestamp.
   **Only the caller whose invocation returns successfully may now perform one network send.**
   Replaying this method always refuses after that boundary, even with the same ticket. A crash
   between commit and network send is conservatively unknown. Do not repeat the send based on
   lease expiry, lost response, worker restart or idempotent prepare replay. There is no claim
   of upstream exactly-once execution. Never wrap these methods in another Store transaction.
   `send_window` (whole seconds) is the dispatcher's bound on how long the send may take until
   its usage is settled; a network dispatcher must pass it. When the call's hold has less time
   left than that, the dispatch is refused under the writer lock with `run_time_too_short` and
   nothing is sent (see "Hold deadlines and the send window" below).
6. `mark_usage_pending(ticket, request_id=None, reason='awaiting_final_usage')` records a
   verified upstream request ID or stream interruption without charging or releasing.
7. Adapter verifies final usage provenance, then constructs `UsageEvidence(provider,
   request_id, evidence_ref, usage=Usage(...), effective_model, capability_version, metered_at)`.
   `settle_trusted_usage(attempt_key, evidence, outcome='completed') -> Settlement` atomically
   writes normalized usage, exact provider cost, the at-most-once user sale, fractional carry
   and hold release. This is an **adapter-only trusted entry point**; passing user-supplied
   usage/evidence to it is forbidden. No browser-supplied 'trusted' flag exists. Evidence IDs
   reference restricted provider records; the ledger stores no prompt, response or secret.
   The gateway must durably retain the necessary original usage counters, reliability and
   dispatch observations. `effective_model` means the gateway-confirmed effective route,
   never an inference from the response's model-name string. Both cost and sale versions
   must be explicitly bound; neither has a default published version.
8. Known metered platform failure uses `outcome='platform_fault'`: record cost only, retain
   the original bounded hold, then create another attempt with a nonempty `retry_reason`.
   Unknown attempts cannot be retried. A completed logical call is terminal; explicit user
   reruns require a new logical call/run and authorization. Every attempt contributes its
   own cost; only one completed attempt can produce the logical call's sale.

`Settlement` fields: `call_key, attempt_key, charged, absorbed, cost, cost_unit, late, outcome`.
`charged` is integer sale units; `absorbed` is the exact uncharged sale-unit amount, **not**
provider expense or accounting profit. `cost` is a Fraction in the separately declared cost
unit. It is calculated from metered usage and the supplied immutable cost tariff, not an
external invoice or proof of actual upstream payment. Never add these different units.

`cancel_call(call_key)` returns `reserved`, `released` or `settled`. Undispatched cancellation
releases immediately. Dispatched unknown/pending consumption stays reserved; cancellation
stops future dispatch but cannot imply zero upstream cost. `expire_due()` terminally releases
all overdue holds in this namespace. Call it from a bounded recurring recovery worker; reads,
reserves and settlement also reconcile expiry. Deadlines cannot be extended, are no later
than the run deadline, and are at most 24 hours from reservation (configurable downward).
At the exact deadline settlement is late, even if the sweeper has not run. Late evidence
records cost and zero charge forever. No private debt or future secret charge is retained.

#### Hold deadlines and the send window (review finding money-1, 2026-10-05)

A late settlement is free for the payer, so a send must never be able to outlive its hold.
Before this fix a call could be dispatched one second before its deadline (the attempt lease
only needed to be unexpired) while the transport streamed for up to `Limits.timeout_s`
(120 s by default, at most 300 s); the usage then settled late and the platform paid the
provider for output the person received free, on every short-deadline turn.

Two designs were possible: charge trusted usage of an attempt dispatched before the deadline
even when it settles after it (capped at the hold), or never send what cannot finish inside the
hold. The second is chosen because it is the only one that keeps the promise the hold makes to
the payer: the deadline is final, the hold is the most they can be charged, and once it is
released at the deadline that money is theirs to spend again. Charging after release could
take money already reserved or spent elsewhere, or need an unbounded "debt" the ledger refuses
to keep. So:

* `MeteredDeepSeek.send_window = ceil(Limits.timeout_s) + SETTLE_MARGIN_S` (30 s: one SQLite
  busy timeout for writing the observation and settling, which also covers the ledger's
  whole-second clock). The transport's total timeout starts before the dispatch fence, so a
  stream never ends later than `send_window − SETTLE_MARGIN_S` after `dispatch`.
* The gateway refuses a step, before recording, reserving or sending anything, when
  `min(run deadline, now + max_hold_seconds) − now < send_window` (checked before and again
  after any upstream/traffic wait); the error carries `not_dispatched` and reaches the person
  as `billing_run_time_too_short` ("not sent, not charged").
* `Ledger.dispatch(..., send_window=gateway.send_window)` repeats the check under the writer
  lock, so time spent between reserving and the send boundary cannot slip a send past its hold.
* A run cannot be shorter than one send: `RunQuotes.issue` (legacy quote) refuses
  `run_seconds < send_window` (`run_time_too_short`) and the offer publishes `run_seconds_min`;
  the automatic authorization (`issue_automatic`, send-and-go) uses `min(3600, route.valid_until − now)`
  and refuses `route_confirmation_expired` when less than one send is left.
* rr-money-1 (legacy path): a quote's deadline is fixed when it is issued, so `quotes.bind` refuses
  `run_time_too_short` when `deadline − now < send_window`, and the quote's `expires_at` is
  `max(now+1, min(now+300, deadline − send_window))` — a quote never stays acceptable for a run that
  could no longer make one call. The automatic path has no issue-to-bind gap: the deadline is fixed
  at admission.

Remaining (accepted) late cases are platform faults, never a payer charge: a crash after the
stream and before settlement whose recovery runs after the deadline, or a settlement transaction
that waits longer than the margin. They are recorded as cost with `late=1`.

### Payment credit boundary

`create_payment_order(order_id, payer_user_id, provider, merchant_id, amount_minor, currency,
unit, credit_units, exchange_version)` uses keyword arguments and records an immutable
server-priced order. No funds are credited. `amount_minor` is payment currency minor units;
`credit_units` is the fixed number of wallet units from the approved exchange policy.
Neither may be taken from an unvalidated checkout/browser request.

`credit_verified_payment(receipt) -> journal_id` verifies the durable evidence callback and
matches the immutable order. Transaction uniqueness is `(environment, provider, payment_id)`;
changing merchant, namespace, order or payer cannot reuse that transaction. Event uniqueness
is `(environment, provider, merchant_id, event_id)`. Event dedupe, payment dedupe, unique funded order,
journal posting and balance update are in one transaction. Different events for the same
payment return the same journal; changed transaction/event facts conflict. Funding kinds
are `paid` in live and `test_payment` in test, stored separately from `sale`. There is no
promotional or compensation grant API, refund API, arbitrary balance setter, payment adapter,
checkout, callback endpoint, signature implementation, simulated production payment or
fallback success path in this foundation.

### Projections and reconstruction

`balance_for_user(requester_user_id=..., payer_user_id=..., unit=...) -> Balance` requires
identical authenticated requester/payer IDs; admin/workspace roles do not bypass this.
`Balance` contains `unit, booked, held` and `available = booked - held`.
`statement_for_user(requester_user_id=..., payer_user_id=..., after_seq=0, limit=100)` returns
only `seq,id,kind,unit,scale,created_at,delta`; paginate using the last `seq` (maximum 500).
It deliberately does not disclose provider costs, evidence, actor/delegation or workspace
details. The coordinator must derive requester from the authenticated session, never a
request field. Internal call/run records retain full stable attribution for separately
authorized projections. `call_status(call_key)` is an internal recovery view, not public auth.

`audit_balances(rebuild=False)` compares all environment balances and fractional caches
against immutable facts. `rebuild=True` repairs derived caches under the write lock and
returns pre-repair differences. It cannot accept an arbitrary desired balance. Run in a
restricted reconciliation/maintenance path. The journal is structurally double-sided: each
row transfers one nonnegative signed-64-bit integer amount between distinct same-unit,
same-environment accounts; `billing_entries` presents the two opposite legs. Insert triggers
update both cached balances atomically, reject negative wallet balance and integer overflow.
Journal, price, order, payment, usage, settlement and audit event facts reject mutation.
Corrections need future explicitly modeled reversal records, never editing existing facts.

### Cost rollup (derived from the cost facts; D17 §6)

The platform cost admission and the velocity brake read an exact rollup of the provider-cost
facts instead of the facts themselves (`suite_schema/model_cost_rollup.py`,
`billing/cost_rollup.py`). The facts are unchanged and stay the authority:
`model_cost_admissions` and `billing_usage` (insert-only), `model_cost_endings` (insert-only) and
the one-way `billing_calls.status`. Everything below is derived and can always be rebuilt from them.

* **Tables.** `model_cost_buckets` (per scope = environment, provider, account_ref; dim `account`
  per UTC day, dims `payer` (wallet owner) and `route` (requested model) per 1, 60 and 3,600 s;
  counts, admitted and resolved bounds and exact resolved cost; `den` 0 for counts, bounds and
  on-grid cost, `den` d for other canonical rationals over d), `model_cost_inflight` and
  `model_cost_hold` (the open set), `model_cost_exceptions` (costs that fit neither form, read back
  and parsed as before). Integers are two int64 limbs (base 10^15); an on-grid cost keeps its
  fraction as an integer count of 10^-15 plus a carry. SQL never parses big text and never
  overflows; Python rebuilds exact integers and Fractions from the limbs.
* **Triggers.** An INSERT of an admission or a usage fact (from the Ledger, a fixture, an older
  release or plain SQL), an ending, and a call leaving `reserved` each insert a row into the
  transient `model_cost_rollup_deltas`; apply triggers on that table upsert the buckets and the
  open set; the producing trigger empties it again. One producer template serves the triggers
  and the replay, so a backfill and a live write use the same arithmetic. Everything runs in the
  writer's transaction: the rollup commits or rolls back with the fact. No Python runs on a fact
  write path, and there is no backlog to drain.
* **Totality.** Derived tables have no CHECK and no NOT NULL and are written only with COALESCE
  and upserts (their WITHOUT ROWID key columns, which SQLite keeps non-NULL, are always COALESCEd;
  an upsert keeps its meaning even under an outer INSERT OR REPLACE/IGNORE), so no derived row can
  make a fact INSERT raise. A malformed admission (time not an
  integer, negative, or expiry more than a day after admission) or an orphan one (no attempt,
  call or wallet) becomes a fault of its scope instead of a bucket. Its id names the attempt key
  and each scope part by SQLite type and bytes (`fact:text:<hex>@text:<hex>/…`), since SQLite's
  `||` turns a BLOB into the TEXT of the same bytes: a BLOB and a TEXT key with the same bytes, or
  the same key in two scopes, each latch their own scope. An expiry equal to (or before) the
  admission time is not a fault: such an admission is never in flight, for the rollup and for the
  oracle alike.
* **Cost classes.** A cost is canonical only as text of ASCII digits with no NUL byte: SQLite's
  GLOB, `length()` and `CAST` stop at an embedded NUL (`'\x0099'` would glob, measure and cast as
  `''`/0), so the SQL test rejects a NUL first (`instr(x,char(0))=0`) and agrees with the verifier's
  full-string match; such a cost is class X and refuses `invalid_cost_fact` as before.
* **Gate.** BEFORE INSERT/UPDATE/DELETE triggers on the four derived tables refuse with
  `cost_rollup_derived` unless a delta row exists, i.e. unless the write comes from an apply
  trigger. Hand-writing deltas or dropping triggers bypasses the gate; a scheduled verify finds
  what it left, but not necessarily the next hourly one. Every round reports a delta row at rest
  or a missing sentinel even when no scope exists, and compares a scope's whole open set, its
  velocity rows from the horizon (all of them while the scope has no admission yet) and its day
  rows from a lower bound: the hourly round (`cost_rollup_verify_recent`) from the start of the
  UTC day two days back, without comparing the read path's counters with the uncapped oracle; the
  daily round (`cost_rollup_verify`, the first round of each UTC day) from the previous month's
  first day, with that comparison. So a value written into an older day row with its structure
  intact, one the weekly or monthly window still sums, is found only by the next daily verify, up
  to about 24 hours later, and until then admission reads the altered total
  (docs/platform/model-risk.md §13). The velocity read also checks each bucket row it sums.
* **Sentinels and marker.** Each derived table holds one sentinel row in the empty scope; meta
  `model_cost_rollup_v1` marks the backfill and `model_cost_rollup_triggers` the version of the
  versioned triggers' SQL (the producers and the fault mapping; 4 since the fault id names the key
  and the scope by type and bytes). A start that finds the
  marker, the current trigger version and all four sentinels issues no fact query. A dropped or
  emptied derived table (sentinel gone), a missing marker or another trigger version (whose
  triggers it drops and recreates in the same transaction) replays all facts once, inside the
  migration's BEGIN IMMEDIATE (recorded as a global rebuild and a `backfill` audit row).
* **Backfill and the writer lock.** That replay holds the writer lock for its whole length:
  about 48 s per million admissions on the local benchmark machine, with the WAL growing by about
  320 MB per million (reused afterwards, not returned). It happens on the first start of a release
  that brings it (the first rollup release, a new trigger version) or after a derived table was
  lost. A second process that opens the Store meanwhile does not fail at its 30 s busy_timeout:
  opening retries its idempotent migration for up to 15 minutes (`Store`, `MIGRATION_WAIT_S`). A
  process already running that writes meanwhile still gets `database is locked` after 30 s, which
  is why a backfill or a large rebuild runs with the services stopped.
  **At deploy the budget is the health check, not that wait.** The receiver
  (`infra/production/receive.py`) stops every role and starts them with `up --wait --wait-timeout
  180`; `docs`, `portal` and `messages` wait for `accounts` to be healthy, so `accounts` replays
  alone. The Store opens at import, so its `/readyz` answers only after the replay, and the image's
  HEALTHCHECK (40 s start period, then 3 failed checks 30 s apart) marks it unhealthy about two
  minutes in: a replay longer than that, past roughly 2 million admissions at the local rate (fewer
  on the 1-CPU `accounts` container), fails the deploy and the receiver restores the pre-deploy
  backup (nothing is lost: the replay is one transaction). Before a release that brings a replay,
  compare `SELECT COUNT(*) FROM model_cost_admissions` with that ceiling. Above it, or after such a
  failed deploy (its image `synwevo:<sha>` stays on the host), migrate with the services stopped
  in a one-off container of the new image, then submit the same deploy again; its start finds the
  marker, version and sentinels and replays nothing. The command opens (and so migrates) the
  Store, then verifies read-only; its exit status reports the verification, not the migration.
  Derived from receive.py, compose.yaml and the Dockerfile; not yet rehearsed on the host.

  ```bash
  docker compose -f /opt/synwevo/config/compose.yaml stop gateway accounts docs portal messages
  IMAGE=synwevo:<sha> docker compose -f /opt/synwevo/config/compose.yaml run --rm --no-deps -T portal python -m synwevo_core.billing.cost_rollup verify --all --no-record </dev/null
  ```
* **Faults, verify, rebuild, prune.** `model_cost_rollup_faults` latches one scope (resolved once,
  never deleted; its id names the scope as a JSON array, so scopes whose parts contain `:` never
  share one); the risk monitor verifies hourly (two days) and daily (from the previous month);
  `python -m synwevo_core.billing.cost_rollup verify|rebuild` in a role container (locally
  `platform.py cost-rollup`) is the operator's tool (docs/platform/model-risk.md §13). The verifier
  streams the facts in admission-time order and keeps only the buckets still open, writing complete
  expected buckets to a temporary table it joins against the live ones, so its memory does not grow
  with the facts it reads (it runs inside the portal process). It reads attempt keys as SQLite type
  and bytes, and checks each scope on its own: a value of one scope that Python cannot read (a
  non-UTF-8 TEXT a broken import wrote) is that scope's difference (`undecodable_key`, or
  `verify_error` naming the column), latched, audited and alerted, and every other scope is still
  checked; a scope whose environment, provider or account name is not UTF-8 text (a BLOB, a NULL or
  a non-UTF-8 TEXT, in a fact or in a derived row: no read can name it, every read binds UTF-8 text)
  is left out and reported as the store-wide issue `undecodable_scope`, audited and alerted on its
  own (object `*`) even when other scopes differ, while the scan goes on past it. The scan seeks once
  per distinct environment, provider and account in each table, so even the hourly two-day verify
  does work that does not grow with the history behind them. The read path refuses such a value with
  `cost_rollup_inconsistent` (check `undecodable`, latched after the refusal) instead of raising; a
  class-X row whose time is not an integer refuses as check `exceptions`, like a malformed day row.
  **Broken facts fail closed.** The product never writes a malformed or orphan admission or a
  non-UTF-8 key; only a write outside it can (an import with foreign keys off, hand SQL). Facts are
  immutable, so a rebuild repairs only derived rows: while any fact of the scope is broken it
  refuses (`RebuildRefused.attempt_keys`, each key printed as itself when it is printable UTF-8 text,
  else as its type and hex bytes such as `text:x'66FF'`, and a key over 128 bytes as its first 128
  bytes, its length and the SHA-256 of all of them, so two keys never share a line) and changes
  nothing. The scope stays
  refused through restarts, rebuilds and verifies until the database is restored from a backup taken
  before that write (docs/platform/model-risk.md §13 step 3); everything written after that backup
  is lost and is reconciled from the provider's and the payment service's records, which is why an
  import or hand SQL is preceded by a consistent backup and followed by `verify --all --no-record`.
  Rebuilds and verifier results are insert-only (`model_cost_rollup_rebuilds`,
  `model_cost_rollup_audits`). The daily prune deletes, under the gate, velocity buckets older than
  the newest admission minus two days and open-set rows its time has passed; day buckets stay. The
  newest admission never moves back (facts are insert-only), so a prune that read it earlier can only
  delete less.
  A rebuild recreates every row a read can use exactly; it may differ from the live table only
  in rows the prune removes (so prune, rebuild, prune leaves the same rows as prune).

#### Per-wallet spend (the personal limits; cost-capacity phase 2, D18)

`PayerBudgets._exposure` reads exact per-wallet day rows instead of up to 10,000 calls
(`suite_schema/payer_spend_rollup.py`, `billing/spend_rollup.py`; docs/platform/agent-run-budgets.md,
"Personal spending limits"). Its semantics are unchanged: held is the hold of every reserved call of
the wallet (any creation time, namespace or deadline), the same in every period; spent in a period is
the sum of the journal amounts reached through `billing_settlements.journal_id` from the wallet's
settled calls created at or after the period's start (no upper bound, any journal kind).

* **Facts.** `billing_calls` (`account_id`, `hold_units`, `created_at`, `deadline` immutable;
  `status` leaves `reserved` once; no delete or replace; `created_at` has no type CHECK),
  `billing_settlements` and `billing_journal` (insert-only; `call_key` the settlement's primary key,
  `journal_id` UNIQUE, `amount` an integer >= 0). The product writes journal, settlement, then
  `status='settled'` in one transaction; a recovery fixture or an import may insert a call already
  settled, or (foreign keys off) a settlement before its journal entry.
* **Table.** `payer_spend_days(account_id, start, n_set, n_paid, amt_hi, amt_lo)`, WITHOUT ROWID,
  primary key `(account_id, start)`: per wallet and UTC day of the call's `created_at`, the settled
  calls, how many have their settlement and journal entry, and the exact sum of those amounts in two
  int64 limbs (base 10^15). Derived: no CHECK, no NOT NULL, the cost rollup's gate refuses any other
  write (`cost_rollup_derived`).
* **Producers.** Whichever arrival completes a call books it, so each call counts once and
  `n_paid <= n_set`: a call becoming settled (AFTER UPDATE OF status, `OLD.status IS NOT 'settled'`)
  or inserted settled books `n_set` 1, plus `n_paid` 1 and the amount when its settlement and journal
  exist; a settlement of a settled call whose journal exists books `n_paid` 1 and the amount; a journal
  entry that a settlement of a settled call names does the same (one UNIQUE probe per journal insert).
  One template (`produce_spend`) serves the four triggers and the replay.
* **Mapping onto the cost rollup's deltas** (no column added: a trigger naming a missing NEW column
  would make every delta INSERT, and so every fact write, raise): `kind='spend'`,
  `attempt_key`/`call_key` = call id, `environment` = the wallet account's (or ''), `provider` = '',
  `account_ref` = wallet id, `admitted_at` = `created_at`, `reserved` = n_set, `xunit` = n_paid,
  `c_hi`/`c_lo` = amount in limbs. A settled call whose `created_at` is not a non-negative integer is a
  `fault` delta (`malformed_settled_call`) that the cost rollup's fault mapping latches unchanged, with
  id `fact_fault_id(call id, (environment, '', wallet id))`. The wallet scope `(environment, '',
  wallet id)` never meets a provider scope: `identifier()` refuses '' as a provider.
* **Read.** One fault probe, the table's sentinel (lost: `payer_budget_inconsistent`, not latched;
  it goes only with a write past the gate or with the table, and the next start replays every wallet's
  rows, so nothing is left to rebuild), the day rows from the earliest period start (at most 31, plus
  any future days; a broken structure refuses as `SpendRollupInconsistent`, code
  `payer_budget_inconsistent`, which the gateway hands to `after_refusal` whether the reservation or the
  dispatch read it, and which latches the wallet), the reserved holds through
  `billing_calls_holds` (more than 10,000 refuse `payer_budget_capacity`), then an unpaid settled call
  in the periods (`payer_budget_inconsistent`, not latched: it clears when the fact arrives). It
  never reads the settlements, the journal or `billing_calls_payer_period` (kept for the verifier and
  the uncapped `reference_exposure`).
* **Sentinel, marker, backfill.** The sentinel `('', 0, 0, 0, 0, 0)`, meta `payer_spend_rollup_v1` and
  `payer_spend_rollup_triggers` (version "1"). A start that finds all three issues no fact query; a
  dropped table, a lost sentinel, a missing marker or another trigger version recreates the versioned
  triggers and replays every settled call once in rowid order inside the migration's BEGIN IMMEDIATE,
  recorded as a rebuild and a `backfill` audit in scope `('', '@wallets', '')`. Wallet scopes read
  their newest global rebuild there (provider scopes at `('', '', '')`), so neither family's backfill
  supersedes the other's verify results. Store open never refuses: a malformed settled call becomes a
  fault of its wallet. Until that start, every wallet's read refuses on the lost sentinel (above) and
  the verifier reports it store-wide (`spend_sentinel_missing`: read once per run, audited each run and
  alerted, deduplicated per day, as object `*`) and compares no wallet's day rows or read while it is
  lost, whether the rows are still there or went with it (the table emptied past the gate, one wallet's
  rows deleted with the sentinel): the replay rewrites every wallet's rows, so no wallet is latched and
  none needs a rebuild after the restart; a malformed settled call is still named (its fault is the
  triggers'). A dropped table is different: its triggers still name it, so every reservation
  and settlement raises until the services restart (docs/platform/model-risk.md §13). The replay
  takes about 1 s per 200,000 settled calls on the local benchmark machine (about 7 s per million,
  `docs/agent-work/evidence/2026-10-26-payer-spend-rollup/`).
* **Verify, rebuild.** `CostRollup.check` adds wallet scopes (a loose index scan of the day rows' key
  and of `billing_calls_payer_period`, one seek per wallet) to its report (`VerifyReport.wallets`):
  each wallet's settled calls are streamed from the lower bound, its day rows recomputed and compared
  both ways, malformed calls named (`fact_fault`, `fact_fault_missing`; in a bounded run also those
  outside the streamed days, through the faults their writes latched and each re-read from its call: a
  negative or REAL creation time sorts below every window), and outside the hourly run the read
  compared with the uncapped `reference_exposure` (neither the rows nor the read while the table's
  sentinel is lost, above: only the malformed calls are named then). Mismatches latch the wallet (audit
  `billing.payer_spend.rollup_inconsistent`, alert with the wallet id only); `CostRollup.rebuild_wallet`
  (`cost_rollup rebuild --wallet <id> --reason …`) has `rebuild`'s shape and refuses while a settled
  call of the wallet is malformed (docs/platform/model-risk.md §13, "钱包"). One wallet's
  daily verify streams its settled calls from the previous month's first day: about 1.7 s for 200,000
  and 12 s for a million (the hourly two-day run 0.1-0.8 s), in a read snapshot with a flat ~107 MB process peak; the check
  itself stays at 0.06 ms p95 at either size, and the triggers add about 12 µs to a settlement.

**Backup, restore, rollback.** The SQLite backup API copies the facts and the rollup in one
consistent snapshot, so a restored copy verifies as it was; the restore rehearsal
(`platform.py verify-restore`) recomputes every day from the facts and fails on a difference, so
a backup taken after a broken fact fails it and one taken before passes (the recovery above). A
backup from before the rollup replays once when the restored store first opens. A code rollback
leaves the tables and triggers in place: older code ignores the tables, the triggers keep them
current under it, and a later roll-forward finds the marker and sentinels and replays nothing.

## Exact arithmetic and rounding

`Unit.scale` defines smallest units: an integer `n` represents `n / 10**scale` displayed
units. The same unit code cannot acquire a different scale. No floats are accepted in money,
rates, counters, budgets or deadlines; bools and numeric strings are rejected too.

Each Rate is integer numerator/positive denominator in smallest units per `per_tokens`:

```text
exact = (uncached * uncached_rate + cached * cached_rate + output * output_rate) / per_tokens
```

Use Python Fraction throughout. Reasoning is part of output, not added. Cache hit aliases must
agree; hits plus misses equal prompt input; supplied total tokens must equal input plus
output. Missing cache classification is rejected as unknown rather than invented.

Retail rounding is floor of accumulated exact smallest units **per wallet and price version**.
Persist the fractional remainder in the same transaction and carry it to the next metered
call at that version; do not round each call up. A new version starts a new carry, with old
carry retained, never silently repriced. Remainders below one smallest unit are unposted
fractions, not independently spendable funds. If accumulated usage exceeds this call's hold,
charge at most its hold and absorb **all** excess, including its fractional part. Do not carry
that overage to the next call. Platform-fault and terminally released calls do not change
retail carry. Exact provider costs are separately stored as numerator/denominator strings,
without forcing a per-attempt integer monetary rounding or representing them as cash paid.

## Limits and production blockers

This is a reusable local ledger foundation, not a complete payment provider or released
charging product. The coordinator still owns migration installation, actual authorization
and trusted usage wiring, durable verified payment inbox/provider integration, scheduler,
published prices/FX/policies, UI/API projections and live enablement.

Not implemented: refunds/chargebacks/disputes and reversal state machines, refund-vs-spend
locks, promotional lots/expiry/consumption priority, accounting/tax receipts, payment order
expiry/checkout reconciliation, organization billing, periodic budgets, global platform-cost
and unknown-exposure thresholds, admission/queue/rate controls, merchant signature checks,
or external reconciliation. These gaps **block enabling real-money charging**, rather than
being represented as completed or hidden behind success flags. A known platform fault must
already have trustworthy usage to use the retry path; unknown results wait for evidence or
final release. The provider adapter must determine effective routes and cost tariff windows.

Journal caches are reconstructible, not a tamper-proof database: arbitrary privileged SQL,
trigger removal or restoring stale files is outside the service's security boundary. Stop
new paid dispatch when Store is unavailable or reconciliation detects a discrepancy.
All accounts, including platform clearing accounts, use signed 64-bit bounds and refuse
overflow. Bounded synchronous transactions serialize writes; no throughput claim is made.

For §37 recovery: drain/fence old workers, pause new consumption and affected payment writes,
preserve verified events outside any restored snapshot or return retryable provider failures,
restore/reconcile authoritative payment IDs, settlements, usage, grants and business results
before reopening consumption. A cache rebuild cannot recover missing committed facts from
a stale backup. Do not rollback money facts with code, drop tables on code rollback, replay
old unknown dispatches, or acknowledge unpersisted payments. Actual power-loss/storage and
external-provider recovery drills remain coordinator acceptance work.

## Verification evidence

Final completed run: **47 passed in 5.05s**, no skipped/xfail. The earlier 43-test run also
passed. Boundary, brand, design, component-name and logo asset checks passed. The four final
regressions cover cross-merchant reuse of a payment ID, rollback after payment event collision,
dispatch/cancel contention, and rejection after spending the final unit.
Tests cover independent Store connections competing for the final unit; same-key reserve/
settlement/payment races; parent-budget accounting; strict integers/usage normalization;
immutable snapshots/journal including REPLACE; isolated projections; actor/payer and
workspace separation; authority revocation; dispatch lease fences; unknown cancellation,
exact deadline and late usage; platform retry costs; fractional accumulation/overage;
cache rebuild; nested transaction refusal; process death before/after settlement commit
and after dispatch, with retry/reopen verification. No real money or network is used.

The core package was built offline from a temporary copy with `pip --no-index --no-deps
--no-build-isolation --target <temporary-installed-directory>`, then pytest ran from that
installed directory using the existing Python 3.14.5 environment and its pinned installed
test dependencies. No shared editable installation was changed and no sys.path hack was
used. This is evidence of an offline package build and tests, **not** a fresh full dependency
rebuild from the lockfile. Initial build with the shared interpreter lacked setuptools;
using the already available isolated build interpreter succeeded without network access.

Canonical command after the coordinator installs this worktree's packages normally:

```bash
python -m pytest tests/integration/test_billing_ledger.py -q
python scripts/check_boundaries.py
python scripts/check_brand.py
```

The final installed package was byte-compared to all four owned production Python files;
each matched the worktree. Final JUnit output is in the local temporary verification directory
(`results.xml`); it is not a committed artifact. This worktree still contains only the six
authorized new files and no changes to Store, schema registration, identity or gateway.

Full platform suites/frontend/browser/provider/payment/live acceptance were not run for this
isolated six-file service change. Tool sessions for the completed first tests/checks exited 0;
OS-wide `ps` inspection was denied by the sandbox, so no system-wide process inventory is
claimed. Final tests and checks also exited 0. Crash-test child processes were reaped by
subprocess.run, no service was launched.

## Grants, chargebacks and organization wallets (org-payments, 2026-10-05)

Owner decisions (agent_deepseek_prompt §6.1): wallet currency CNY, a one-time CNY 1 welcome
allowance, organizations may pay for members, **no refunds** (top-ups are final), disputes are
still handled. The ledger gained exactly what those decisions need; there is no refund API.

| Addition | Rule |
|---|---|
| Account kind `org_wallet` | An organization's wallet (owner = organization id). Same never-negative trigger as a person's `wallet`. |
| Account kinds `grant`, `chargeback` | Platform accounts: the source of grants, the sink of recovered disputed units. |
| Journal kind `grant` | `Ledger.grant(grant_id, kind, payer_kind, payer_id, unit, amount, reason, actor_id, abuse_key)`. `kind` is `welcome` (a person only, once per account **and** once per verified-address key, enforced by unique indexes) or `admin` (a named administrator). Reason 8–500 characters is mandatory; the immutable `billing_grants` row is the audit record (actor, reason, time). Idempotent by `grant_id`; changed facts conflict. |
| Journal kind `chargeback` | `Ledger.record_chargeback(DisputeNotice)` through the mandatory `verify_dispute` callback (durable, signature-verified provider event). Takes back `ceil(credit_units × disputed / paid)` but never more than the wallet's available units (booked − active holds): the wallet never goes negative, the rest is `outstanding_units`. The wallet is frozen in the same transaction. Idempotent per dispute. Bounds (review money-2/money-4): a disputed amount above the paid amount is capped at the paid amount (never refused); all disputes of one payment together take at most its `credit_units`; an inquiry (`DisputeNotice.inquiry`, Stripe status `warning_*`, no funds withdrawn) takes nothing and only freezes. A dispute that takes nothing is still recorded (`disputed_units` 0, column `inquiry`) and still freezes, so the finance view lists it with its freeze. Refusals: `payment_not_credited` (no credited payment here) and `dispute_payment_mismatch` (merchant or currency differ). |
| `billing_account_freezes` | A frozen wallet cannot reserve (`wallet_frozen`) or cross the dispatch fence. `Ledger.lift_freeze(freeze_id, actor_id, reason)` is an administrator's audited decision; a lifted freeze cannot be edited again. |
| `billing_orders.payer_kind/payer_org_id` | An order funds either the paying person (`user`, the default and every pre-existing order) or an organization (`organization`). |
| `ChargeContext.payer_kind='organization'` + `organization_id` | The organization's wallet is reserved; the member stays payer and actor (no delegation). Admitted only through `Ledger.org_payers` (the organization's current grant and monthly limits, checked under the reservation and dispatch writer lock); without it the ledger refuses (`organization_billing_not_configured`). The shared contract's payer kind `user` is `personal` inside `ChargeContext` (pre-existing stored contexts). |
| Projections | `overview_for_payer` / `statement_for_payer` serve organization wallets; the caller decides (current organization permission) who may read them. Statements show `grant` and `chargeback` beside `paid/test_payment/sale`. |

Migration: SQLite cannot alter a CHECK, so `suite_schema.billing.rebuild` widens
`billing_accounts.kind` and `billing_journal.kind` with the documented table rebuild
(`synwevo_core.schema_widen`: foreign keys off, one `BEGIN IMMEDIATE`, copy rows unchanged,
recreate indexes/triggers/views from their stored text, `foreign_key_check`, commit). It also
replaces the journal trigger so organization wallets cannot go negative. It is idempotent and
runs on every open; a fresh database is created wide and does nothing. Rollback: the previous
release reads the rebuilt tables unchanged; it does not know the new kinds and its wallet page
refuses a statement containing them rather than mislabelling them.

Tests: `tests/integration/test_billing_grants_chargebacks.py` (grants, welcome uniqueness,
dispute after spend, freeze/lift, organization wallet, and the upgrade of the previous
release's exact table text with rows, balances, immutability and foreign keys kept).
