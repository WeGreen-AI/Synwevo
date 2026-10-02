# Personal Agent run budgets

This is the consent and admission slice for the existing metered gateway. It does **not**
enable a paid provider at startup, publish a price, fund a wallet or select a payment channel.
Deployment composition still needs governed provider configuration, current data-processing
policy, platform cost admission and commercial configuration before production enablement.

## User contract

The authenticated person reads `agent.billing.offer`, then asks `agent.turn.quote` to fix a
message (or a regeneration), conversation branch and scope, route, published sales price,
personal payer, maximum amount, maximum calls and deadline. A quote expires after at most
five minutes. No model request, funds reservation or charge happens while quoting.

`agent.turn.start` with `quote_id` is the user's acceptance. The quote can authorize one turn
only. Binding it and inserting the turn use the **same Store instance and transaction**;
failed admission rolls both back. A replay of the operation's idempotency key returns the
same turn. A new key cannot spend the same quote again. Changed text, branch, conversation
revision/scope or route requires a new quote. Regeneration needs new consent and does not
replay successful business writes.

`agent.quote.get` and `agent.quote.revoke` require the owner and matching conversation.
Revocation prevents future dispatch, while already incurred usage remains eligible for the
original settlement rules. Account, app, source access, source processing policy, cancellation
and payment authority remain separate checks. A quote does not confer any business permission.

The maximum is 12 model calls and a one-hour run deadline; the user can choose lower bounds.
At most 100 unaccepted, unrevoked, unexpired quotes may coexist for a person. A run may stop
earlier because of available balance, provider/context limits or other policy. Waiting past
a deadline cannot silently extend payment authority. This slice does not yet offer a budget
extension or resume-with-new-quote workflow.

## Amounts and operations

The operation registry exposes typed input/output schemas. All amounts and rate numerators /
denominators are decimal integer **strings** to preserve values above JavaScript's safe
integer range. Unit precision comes from the published price. There is no implicit currency,
exchange rate or sale price. Input/cached-input/output rates are quoted separately per the
published `per_tokens` denominator. Output reasoning is not billed twice.

The per-call hold is a conservative bound using the full approved context ceiling and output
limit, assuming the greater published input rate and allowing at most one fractional-unit
carry. It is **not** an expected bill. A quote exposes this hold, the total authorized maximum,
price/version and terms. Existing Ledger reserves each call under its writer lock; the parent
budget does not freeze the same funds a second time. Actual balance is checked on reservation,
so a quote is not a promise that funds will remain available.

## Composition and persistence

`RunQuotes` receives an existing `MeteredDeepSeek`, a callable returning the current approved
`Route`, and a current run/data-policy authority callback. Composition installs `quotes.authorize`
as Ledger's payment callback and passes the same quotes plus a server-side credential resolver
to `MeteredTurnProvider`. The constructor rejects mismatched callbacks or gateway instances,
and a quoted provider requires the platform cost admission service described below.
The Agent, capabilities, quote service and gateway must share the same Store for admission.
Credentials are neither inferred from Anthropic settings nor included in quotes.

`agent_model_quotes` is added through the existing Store schema runner. Its immutable terms
contain prices, route, identifiers, limits and an intent digest, never prompt text, key material
or tool output. Only initial turn binding and irreversible revocation may change. Old code
ignores the additive table. Conversation deletion does not erase the budget record; the existing
personal wallet still reports committed financial facts. Financial retention policy and pruning
of expired, unused quote records remain a production operations requirement.

The default factory still uses the existing explicitly configured provider. New paid controls
appear only when its settings response says billing is required. There is no automatic route,
credential, price or payer fallback.

## Evidence boundary

Tests use actual Store, Agent, operation registry and Ledger with explicitly isolated payment
and model protocol fixtures. They cover no-charge quoting, acceptance/rollback/concurrency,
parameter changes, expiry, revocation at dispatch, private reads, environment isolation and
idempotent operation replay. These tests do not establish a live DeepSeek, checkout or production
billing acceptance. Browser and real-provider evidence is tracked in `docs/agent-work/EVIDENCE.md`.

## Local reconciliation worker

When a trusted metered provider is installed, the suite factory creates `MeteringRecovery`;
`start_jobs` controls whether its thread starts and the application's shutdown hook closes it.
The default provider starts no billing worker. Defaults are a 30-second interval, a five-second
cooperative pass budget, at most 100 calls and 100 terminal turns per pass. SQLite transactions
already in progress may finish after stop; the worker does not claim forced cancellation.

The worker settles only persisted trusted usage and expires holds at their fixed deadlines.
A late observation can record platform cost but cannot charge a finally released hold. It never
requests the model, resumes an Agent turn or replays a business operation. Missing/unreliable
usage remains pending after money is released; release is not evidence of zero consumption.
A released attempt with no committed dispatch timestamp is provably unsent and contributes
known zero tokens, including when the engine's projection must be recovered after a crash.
One corrupt record is isolated and cursor rotation allows later records to be processed.

For ended turns, complete trusted step evidence replaces public token counters in one transaction
and emits `usage.reconciled` with only `turn_id`. Repeated passes or two worker instances do not
add usage or duplicate the event. An event-write failure rolls back the projection. Active turns
are left to the engine to avoid racing its incremental counters. The browser's event handler only
reloads an authorized snapshot. Detailed failures in `last_round` are internal metadata, with no
exception prose or content; a complete Admin recovery surface and operational alerting remain due.

## Platform cost admission

`CostAdmission` is an internal service injected into the metered gateway, with an explicit
`CostPolicy` supplied by trusted current configuration. It has no default budget, account,
currency or enablement. Its provider account reference must identify the real account across
credential rotations; changing a key or gateway namespace cannot create fresh budget. Test and
live remain separate. This service does not infer provider identity from a key.

The gateway calculates a conservative cost bound from the fixed cost-price version and route
limits, separately from the customer's retail hold. `Ledger.dispatch` invokes admission in
the same FULL-durability writer transaction that commits its one-shot send fence. A denial or
commit failure rolls back both. Only after that transaction commits may the transport send.
Customer holds reserved before a denial are released without charging. No network call occurs
inside this transaction, and `admit()` alone is never send permission.

Policy sets integer daily, weekly, monthly and unknown-cost budgets plus separate maximum
inflight and unknown counts. Calendar boundaries are fixed UTC: midnight, Monday midnight,
and the first day of the month. Costs are attributed to the original admission period, not
the settlement date. There is no mutable timezone with which to reset a period. Changing the
cost unit while relevant exposure remains is refused rather than treated as a new budget.

Each immutable `model_cost_admissions` row records the physical attempt, provider account,
cost unit, bound, admission time, connection expiry and policy revision/limits. Trusted
`billing_usage` replaces the bound with its exact rational actual cost, including platform
failures, late results and amounts beyond the estimate. The service never caps known cost
to hide an overrun. Unknown exposure remains counted across all periods until reliable usage
arrives, including after final release of the customer's money. Connection expiry releases
only the inflight slot; it is not proof of zero cost or permission to resend.

The current implementation materializes at most 10,000 relevant admissions per provider
account (current calendar periods plus all unknowns), using an overflow probe and refusing
new work at capacity. Recent facts and historical unknowns use disjoint indexed ranges,
bounded before joining usage. `model_cost_pending` is a derived pending projection, inserted
with admission and removed only with trusted usage in the same transaction. Store backfills
it once under a migration marker; ordinary reopen does not rescan settled history. This is a
declared fail-closed accounting limit, not a throughput claim.
The internal snapshot exposes consistent counters only; it grants no dispatch authority and
is not a public Admin API. Disabling admission stops new calls while existing usage recording,
wallet release and settlement remain available.

Aggregate rollups for larger workloads, RPM/TPM, fair queues, upstream balance alerts and an audited unknown-cost investigation
surface remain separate requirements. The default deployed factory still does not enable this
paid route. Isolated admission tests are not evidence of live provider capacity or live billing.

## Governed cost policy

Account administration exposes `/admin?tab=model-costs` and the typed
`/api/admin/billing/cost-policy` endpoints: GET current state, GET `/history` with
`before_revision` and bounded `limit`, POST `/preview`, and POST `/publish`.
`platform.config.view` permits reads; `platform.config.manage` permits preparation and
publication, with fresh `admin` authentication and an 8–500 character reason for publication.
Workspace ownership alone grants neither permission. The host captures
`SYNWEVO_BILLING_ENVIRONMENT=test|live` at startup. Missing configuration returns an explicit
unconfigured read state and refuses writes. Reads never create a policy or a price.

The editor takes exact major-unit decimal amounts; the API carries int64-bounded smallest-unit
integer strings. Its available units come only from published DeepSeek cost prices in the same
environment. All budgets, call limits and enablement must be explicit. There is no seeded
currency, budget or default authorization. The installation's one platform DeepSeek account
has the fixed internal reference `platform-deepseek`; neither the API nor a key rotation may
choose a new scope. This reference is not a claim that upstream account ownership has been
verified. Credential/route governance must bind the actual account before enabling dispatch.

The signed preview binds the actor, origin, environment, fixed account, expected revision,
current state and exact proposed policy, and expires after five minutes. The publication's
policy version, successful audit and idempotent result commit in one Store transaction, with
current authorization rechecked inside that transaction. An identical retry returns its original
result even after a later publication; a changed payload with that key conflicts. Concurrent
publishers cannot overwrite each other. Historical versions are immutable: restoring old settings
means copying them into a new reviewed publication, never rewriting past costs.

`CostSettings(store, environment=...).policy` is the current-policy callback for `CostAdmission`.
The next dispatch reads the latest revision under its own writer transaction. Turning admission
off stops new dispatches but leaves incurred usage settlement and reconciliation working. Unit
changes cannot hide relevant existing exposure. Publication changes neither wallet balances,
retail prices nor model-route enablement. The explicit governed provider composition described below is required for a paid request;
the default deployment does not select it automatically.

The editor retains inputs on revision conflicts and locks an uncertain publication to its original
request/key for retry. Its three-language layout and exact amount helpers are implemented; current
visual/keyboard acceptance is tracked separately in the evidence log and is not implied by a build.

## Governed provider composition

Account administration now mounts `/admin?tab=model-provider` and four typed routes under
`/api/admin/billing/model-provider`: GET current state, GET `/history`, POST `/preview`,
and POST `/publish`. Reading requires `platform.config.view`; changing configuration requires
`platform.config.manage`, a bound preview and fresh admin authentication at publication.
A platform role never supplies workspace access, payment consent or wallet funds.

The host explicitly selects `SYNWEVO_AGENT_PROVIDER=deepseek` and
`SYNWEVO_BILLING_ENVIRONMENT=test|live` for the Suite/portal process. Without the provider
selection, the existing Anthropic configuration remains in use. An unsupported explicit
selection fails startup. Merely selecting DeepSeek creates no model configuration or funds:
Account governance must publish an enabled route, a current credential, confirmed account/
route/cost evidence references, applicable sale/cost prices and an enabled matching cost policy.
The references record an administrator's verification; they do not themselves verify upstream
account ownership or establish that a pricing announcement applies to that account.

`configuration.GovernedDeepSeek` assembles the same Store, ProviderSettings, Ledger,
CostAdmission, RunQuotes, MeteredDeepSeek and Agent authority checks. It does not accept
browser-supplied provider URLs or credentials. It never borrows an Anthropic personal/platform
key, guesses retail prices, creates a payment grant or assumes a balance. The ledger's payment
authorizer is the current quote binding; its dispatch transaction also rechecks cancellation,
current Agent/account access, sealed source access, processing policy, configuration revision
and cost admission. Every quoted turn still needs the existing explicit user budget consent.
This is single-run consent, not the unfinished recurring payment authorization feature.

The active provider key is sealed with the platform-model-key purpose and participates in the
existing root rekey mechanism. Rotation/revocation replaces the active ciphertext; immutable
configuration history contains metadata only. Preview/idempotency binding uses a commitment,
never the plaintext key. The existing root-storage security model is unchanged: database-local
root storage is not protection against an adversary who can read the entire database.

A turn pins an internal credential binding containing provider, kind, model, configuration
revision and key identity. Worker start, restart, confirmation continuation and each dispatch
compare the current binding. Pre-migration queued turns with no binding fail closed rather than
adopting today's key. Already incurred usage can still settle; cancellation or a configuration
change never promises that completed business side effects were undone. The binding is absent
from public conversation and billing responses.

The current evidence log distinguishes isolated protocol/ledger tests from actual inference,
retail payments, browser acceptance and production deployment. No real model or payment service
is enabled by these tests. Rate/fairness limits, continuing payment consent, actual checkout/refund
integration and full production acceptance remain in the unified backlog.

Workspace processing consent additionally names allowed providers (`anthropic`/`deepseek`).
The credential-source choice (`none`/`platform`/`any`) remains a separate restriction.
Existing organization policies without a provider list retain Anthropic-only scope; they never
implicitly authorize the new DeepSeek path. An unset personal workspace follows its owner's
choice. An explicit empty provider list refuses all processing. Legacy clients that omit the
new field preserve an already explicit list (or the legacy Anthropic-only scope), including
when changing `none` back to another mode. Every tool result and replayed result rechecks the
current provider consent. Policy changes commit their audit/event with the state.

The paid platform route does not accept a personal Anthropic key. Existing keys remain sealed
and can be removed; personal-key submission/preference enablement is refused on that route.
The settings response identifies the actual active provider and whether personal keys are supported.


## Personal spending limits

The governed DeepSeek factory now installs `PayerBudgets` on its Ledger. Missing or disabled
personal limits refuse new paid calls. This is an additional restriction, not payment consent:
each run still needs its bound quote, current business permissions, wallet funds, provider policy
and platform cost admission. Only the authenticated person can read or set these limits through
`agent.billing.budget.get` and `agent.billing.budget.set`; they are unavailable to model tools,
workflow steps and personal unattended automation. Ordinary business API access is unchanged.

The limits belong to the stable user, billing environment and published retail unit. They apply
across gateway namespaces and price revisions. Day, week and month begin at UTC midnight,
Monday and the first of the month. Current exposure includes settled retail charges attributed
to their original reservation timestamp and **every still-reserved hold**, including older periods.
Changing limits or versions does not reset usage. A separate unit does not consume another unit's
limit. Zero blocks new positive charges. Disabling stops new calls; it neither cancels settled
bills nor releases holds from already accepted work. Trusted settlement and final release retain
the existing ledger rules, including no late charge after final release.

Reservation checks the full additional hold inside the same writer transaction, using exactly
the timestamp that will be stored on the call, and saves an immutable admission-policy reference.
Dispatch rechecks current limits and exposure before committing the send fence. The held amount
is already included at dispatch and is not added again. All accounting uses integer smallest units;
no second balance or duplicate funds reservation is created.

The get operation takes `{}` and returns `environment`, `timezone: "UTC"`, and `budgets` (at most
100 published retail units). Each item contains `unit`, current `revision`, `configured`, `enabled`,
three decimal-integer-string budgets, `usage_state`, `periods`, and the latest 20 immutable policy
revisions. Available period values contain UTC `start`/exclusive `end`, and integer-string `spent`,
`held`, `remaining`; remaining is floored at zero and is not wallet balance. Bounded indexed reads
consider at most 10,000 relevant calls. Overflow or missing settlement evidence returns
`usage_state: "capacity"|"inconsistent"` with `periods: null`, never invented zero usage, and refuses
new calls. Configuration remains editable so the person can still disable their limits.

The set operation requires `unit_code`, `expected_revision`, explicit `enabled`, and
`daily_budget`, `weekly_budget`, `monthly_budget` as nonnegative int64 decimal strings.
It accepts no supplied payer identity. Revision conflicts cannot overwrite another editor.
Policy revision, successful operation receipt, personal audit and `agent.payer_budget.changed`
event commit in the existing capability transaction. Repeating the same request/key returns
its receipt; changed input conflicts. Failed persistence consumes no revision and no success event.
The event has no workspace and carries the unit/revision, never conversation content.

The Agent settings page has exact decimal inputs, current spending/holds, a concrete before/after
review, manual same-request recovery when a save outcome is unknown, and revision history.
Refresh/navigation does not automatically submit writes. Simplified Chinese, Traditional Chinese
and English share this contract. These controls grant no recurring payment authority.

Migration adds policy/admission tables and a partial settled-call index through Store only.
Existing money facts, credentials and configuration are preserved; existing users receive no
implicit enabled policy or funds. Reopen is idempotent. Deploy new consumers together: an older
paid consumer that does not enforce this new restriction must be stopped before admitting paid
work under these limits. Code rollback preserves policy and financial facts, and paid admission
must stay disabled until the restored consumer can enforce all current restrictions.
