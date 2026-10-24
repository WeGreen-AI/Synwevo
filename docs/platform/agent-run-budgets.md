# Personal Agent run budgets

This is the consent and admission slice for the existing metered gateway. It does **not**
enable a paid provider at startup, publish a price, fund a wallet or select a payment channel.
Deployment composition still needs governed provider configuration, current data-processing
policy, platform cost admission and commercial configuration before production enablement.

## User contract (send-and-go, owner decision 2026-10-23, DECISIONS D17)

A person types a request, presses send, and the Agent starts. There is no per-message budget,
quote or price dialog and no re-confirmation when prices change. Billing itself is unchanged:
prepaid CNY wallet, DeepSeek cost × the administrator's markup, per-call holds and settlement,
and the cost line under every answer.

**Automatic run authorization.** `agent.turn.start` from the person in the page (`via=ui`) with
neither `quote_id` nor `consent_id` calls `RunQuotes.issue_automatic` **inside the admission
transaction** and binds the result at once with the existing `bind`. One immutable
`agent_model_quotes` row is written per paid turn; its `terms_json.issued` is the audit record
(`{"mode": "automatic", "actor_user_id", "via": "ui", "source": "agent.turn.start", "op_run_id",
"bound": "default" | "personal_cap"}`). `agent.quote.get` and the account export say how each
run was authorized: `authorization: "automatic" | "confirmed" (legacy quote) | "consent"`.
A refused send stores nothing: no message, turn, authorization or hold. A replay of the
operation's idempotency key returns the same turn and the same single authorization.

**The bound of one answer.** `hold = upper_bound(route, sale)` (the full context-ceiling hold of
one call); `default = hold × MAX_CALLS` with `MAX_CALLS = 12`; `budget = default`, or
`min(personal cap, default)` when the person set a cap in Agent settings (`agent.settings.update
{run_budget_cap}` / `{clear_run_budget_cap: true}`, read back as `billing.run_budget_cap`). The cap
is stored with the sale unit it was set in (`agent_settings.run_budget_cap`, `run_budget_cap_unit`).
At send, a cap in another unit refuses `billing_run_cap_unit_changed` and a cap below one call's
hold refuses `billing_run_cap_below_call_hold` (detail `{per_call_hold, cap, unit}`): a cap is
never silently ignored. `max_calls = 12`; `run_seconds = min(3600, route.valid_until − now)`, and a
route with less than one send window left refuses `billing_route_confirmation_expired`. The
deadline is fixed at admission, so the legacy issue-to-bind gap (rr-money-1) does not exist on
this path. `agent.billing.status.run` shows the bound a send would get now (`maximum`, `default`,
`cap`, `source`, `per_call_hold`, `max_calls`, `run_seconds`, `usable`) and the price it would be
bound at (`price_id`, the sale price in force, and `route_version`, the route version in force:
the same values the automatic issue would bind now; `run` is null when no route is usable).

**Who pays.** `agent.turn.start` takes `payer_kind: "user" | "organization"` and `payer_id`. The
person pays by default; an organization only with its current grant (`quotes._payer`, rechecked
at every reservation and send). Payer fields together with `quote_id`/`consent_id` are
`billing_authorization_conflict`; without a configured paid model `billing_not_configured`; from
any caller but the person in the page 403 `person_required`.

**Admission order.** Owner check → Agent access and the provider data consent → the one-time
terms acceptance (`TermsAcceptances.missing` non-empty → 409 `terms_acceptance_required`,
detail `{documents}`) → input rules → the admission transaction (the existing checks, then
`issue_automatic`: for a personal payer the personal-limit policy is read here too — none →
`billing_payer_budget_not_configured`, disabled → `billing_payer_budget_disabled` — so a person
without limits is refused before anything is stored, not at the first call) → bind. Every
`BillingError` on this path is 409 `billing_<code>` with the message
本次回答未能开始，消息没有发送，也没有扣费. The automatic path never infers terms acceptance;
`record_paid_use` stays on the legacy `quote_id`/`consent_id` paths only.

**The one-time start step.** `agent.setup.get` (`need=personal`, `agent=never`) says what is
still missing before the first automatic paid run — the provider data consent (recorded by
`agent.provider_consent.accept`, unchanged), the terms, AI use rules and pricing at their current
versions (recorded by `agent.terms.accept`, context `agent_setup:<op run>`, 409
`terms_version_changed` with the current list when a stale, incomplete or duplicated list is
sent) and, for the personal payer, the spending limits (prefilled from
`synwevo_suite.agent.setup.SUGGESTED_LIMITS_FEN`: CNY 10 a day, 30 a week, 100 a month, converted
exactly with `fen_to_units`; `suggested` is null for a non-CNY sale unit) saved through
`agent.billing.budget.set`. A changed document or consent version reopens the step once; a price
change never does. Both operations are a person's own: unreachable by machine credentials, and
`agent.terms.accept` refuses every `via` but `ui`.

**The price the page showed is the price the send runs at (AD-1).** The page sends back the
`run.price_id` and `run.route_version` it displayed as `agent.turn.start`
`seen_price_id`/`seen_route_version` (strings, at most 200 characters; both omitted when `run` is
null). On the automatic path, when either field is present and differs from the price id or route
version the automatic issue would bind at admission (`RunQuotes.shown_price`, inside the admission
transaction, after the bound is computed), the send is refused with 409 `billing_price_changed`
and the message 价格或模型已更新，本次没有发送，也没有扣费。请查看新价格后重新发送。, with detail
`{"current": {"price_id", "route_version", "maximum", "per_call_hold", "unit": {code, scale}}}`
(`maximum` is the bound this person would get, their cap included). Nothing is stored: no message,
turn or authorization. The page then shows the new price and maximum and offers to send again at
it. A send without either field (an older cached page) is not checked, as before. The fields
belong to the automatic path: together with `quote_id` or `consent_id` they are
`billing_authorization_conflict`; on any other caller's path they change nothing.

**Price change.** `agent.billing.status.price_notice` compares the person's last bound run with
the current offer (route version, sale `price_id`, `per_call_hold`) and carries the previous
pricing when they differ. It is information only and never blocks a send: a send from a page that
shows the current price runs at it, and a page still showing an earlier one is refused once as
above, so nobody is bound at a price their page did not show. A run in progress stops before its
next model call (the turn's credential-binding fence, or `quotes.authorize` →
`billing_model_configuration_changed`); calls already made settle at the earlier price. Prices are
never changed retroactively.

**What the automatic path keeps exactly as it was.** Reserved plus spent never exceeds the run's
budget and calls never exceed `max_calls` (`ledger.reserve_call`); a charge never exceeds its hold
and nothing is charged after a hold's deadline; payer authorization is rechecked at open, reserve,
prepare, claim and dispatch (`quotes.authorize`); the risk brake and breaker, wallet freeze, staged
allowlist, per-call cost cap, platform cost windows, personal daily/weekly/monthly limits, provider
consent, access suspension and source/workspace policy are rechecked at reserve and dispatch. Funds
and limits are still checked at the call, with the same refusal codes and words: an empty or
frozen wallet refuses the first call with no send. For `flow`, `automation`, `system`, `api` and
`agent` callers nothing changes: no `quote_id`/`consent_id` → `billing_authorization_required`.
The recovery worker never sends. Known limitation (D-11): time spent waiting for a tool
confirmation counts against the run's `run_seconds`; an expired run ends `billing_run_time_too_short`
("send again to continue"), and there is no automatic re-authorization on resume.

### Legacy explicit quote and continuing consent (kept for compatibility and audit)

The operations `agent.turn.quote`, `agent.quote.*` and `agent.consent.*` and the
`quote_id`/`consent_id` paths of `agent.turn.start` stay as they were; the page no longer uses
them. The authenticated person reads `agent.billing.offer`, then asks `agent.turn.quote` to fix a
message (or a regeneration), conversation branch and scope, route, published sales price,
payer, maximum amount, maximum calls and deadline. No model request, funds reservation or charge
happens while quoting. A quote stays acceptable for `max(now+1, min(now+300, deadline −
send_window))` and `bind` refuses `run_time_too_short` when `deadline − now < send_window`
(rr-money-1): a quote cannot be accepted for a run that could no longer make one call.

`agent.turn.start` with `quote_id` is the user's acceptance. The quote can authorize one turn
only. Binding it and inserting the turn use the **same Store instance and transaction**;
failed admission rolls both back. A new key cannot spend the same quote again. Changed text,
branch, conversation revision/scope or route requires a new quote. `agent.quote.get` and
`agent.quote.revoke` require the owner and matching conversation. Revocation prevents future
dispatch, while already incurred usage remains eligible for the original settlement rules.
Account, app, source access, source processing policy, cancellation and payment authority remain
separate checks. A quote does not confer any business permission. The maximum is 12 model calls
and a one-hour run deadline. At most 100 unaccepted, unrevoked, unexpired explicit quotes may
coexist for a person (the automatic authorization is bound at once and is not counted).

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
to hide an overrun. An attempt without usage counts at its bound in the periods it was admitted
in, always. It also counts against the unknown count/budget while it is open exposure: in flight
(its connection expiry), or until the customer's hold ends, after which the ledger accepts no
later charge and DeepSeek offers no per-request usage to recover. An attempt the platform
stopped itself (`record_local_stop`: the person's Stop, the output guard, a shutdown) leaves
the unknown limits when its connection expiry passes; it stays the payer's exposure in the
cost periods and the payer's velocity window (`billing/risk.py`). An HTTP error answer is never
unknown: the gateway settles it as known zero usage (`docs/deepseek-provider.md`, "Error
answers"). Connection expiry is still not proof of zero cost or permission to resend; an
admission may not expire later than one day (the longest hold) after it is admitted.

The current implementation materializes at most 10,000 relevant admissions per provider
account (current calendar periods plus unknowns that can still be open, i.e. admitted within
the last day), using an overflow probe and refusing new work at capacity. Recent facts and
older pending ones use disjoint indexed ranges, bounded before joining usage; an unknown whose
hold ended before the current periods is final at its bound and never scanned again. `model_cost_pending` is a derived pending projection, inserted
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
and cost admission. Every paid turn runs under a bound run authorization: issued automatically
by `agent.turn.start` for the person's own send from the page (send-and-go, above), or by the
legacy explicit quote or continuing consent.

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
is enabled by these tests. Continuing payment consent (`consents.py`) shipped and is now a legacy
path the page no longer uses. Rate/fairness limits, actual checkout/refund integration and full
production acceptance remain in the unified backlog.

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

The governed DeepSeek factory installs `PayerBudgets` on its Ledger. Missing or disabled
personal limits refuse new paid calls, and — for a personal payer on the automatic path — the
send itself at admission. This is an additional restriction, separate from the run
authorization: each run still needs its bound authorization, current business permissions, wallet
funds, provider policy and platform cost admission. The limits are set once in the Agent's start
step (prefilled with the suggested values, confirmed by the person) and changed in Agent settings.
Only the authenticated person can read or set them through `agent.billing.budget.get` and
`agent.billing.budget.set`; they are unavailable to model tools, workflow steps and personal
unattended automation. Ordinary business API access is unchanged.

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
