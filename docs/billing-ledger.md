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
* A quote cannot choose a run shorter than one send: `RunQuotes.issue` refuses
  `run_seconds < send_window` (`run_time_too_short`) and the offer publishes
  `run_seconds_min`, which the budget dialog uses as the input's minimum.

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
