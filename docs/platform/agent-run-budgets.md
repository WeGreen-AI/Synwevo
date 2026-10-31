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
Saving checks that it is a positive integer no larger than the ledger's integer range
(2^63−1, `billing.models.MAX_INTEGER`); a larger value is 422 `invalid_input` and nothing is
stored, so the send-time `billing_run_cap_invalid` cannot be reached from the page (a value stored
before that check shows as `run.usable: false` with `run.cap: null`, and the send is refused).
At send, a cap in another unit refuses `billing_run_cap_unit_changed` and a cap below one call's
hold refuses `billing_run_cap_below_call_hold` (detail `{per_call_hold, cap, unit}`): a cap is
never silently ignored. `issue_automatic` called for anyone but the person in the page is an
internal invariant violation (`RuntimeError`), not a refusal anyone can meet. `max_calls = 12`; `run_seconds = min(3600, route.valid_until − now)`, and a
route with less than one send window left refuses `billing_route_confirmation_expired`. The
deadline is fixed at admission, so the legacy issue-to-bind gap (rr-money-1) does not exist on
this path. The automatic authorization's `expires_at` uses the legacy formula
`max(now+1, min(now+300, deadline − send_window))` (AD-3): the ledger clock is whole seconds, so
`now+1` could lapse between the issue's and the bind's clock reads and refuse a send at random.
It is bound in the same transaction and rolled back on any refusal, so this opens no reuse window. `agent.billing.status.run` shows the bound a send would get now (`maximum`, `default`,
`cap`, `source`, `per_call_hold`, `max_calls`, `run_seconds`, `usable`) and the price it would be
bound at (`price_id`, the sale price in force, and `route_version`, the route version in force:
the same values the automatic issue would bind now), with `price_key`, what the person would pay
(below; `run` is null when no route is usable).

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
本次回答未能开始，消息没有发送，也没有扣费. The automatic path never infers terms acceptance.
Neither does a legacy `quote_id`/`consent_id` send from the page (an older cached bundle, AD-4):
the same gate applies before anything is stored (409 `terms_acceptance_required`, detail
`{documents}`), so the person is asked to accept a new version in the start step instead of having
it recorded at their next paid run. `record_paid_use` stays only for a flow or automation starting
a paid run on the legacy quote path (a continuing consent is the page's alone), whose behaviour is
unchanged.

**The one-time start step.** `agent.setup.get` (`need=personal`, `agent=never`) says what is
still missing before the first automatic paid run — the provider data consent (recorded by
`agent.provider_consent.accept`, unchanged), the terms, AI use rules and pricing at their current
versions (recorded by `agent.terms.accept`, context `agent_setup:<op run>`, 409
`terms_version_changed` with the current list when a stale, incomplete or duplicated list is
sent) and, for the personal payer, the spending limits (prefilled from
`synwevo_suite.agent.setup.SUGGESTED_LIMITS_FEN`: CNY 10 a day, 30 a week, 100 a month, converted
exactly with `fen_to_units`; `suggested` is null for a non-CNY sale unit) saved through
`agent.billing.budget.set`. A changed document or consent version reopens the step once; a price
change never does. Each terms document (`terms.documents[]`: `id`, `version`, `effective`,
`accepted`) also carries `accepted_version` (addendum-2 S4): the version of that document the
person accepted last, null when never, and equal to `version` once that is accepted. An earlier
version there with `accepted: false` is a re-acceptance after an update, so the card words it as
an update; null is a first use. `agent.terms.accept` answers with the same list. Both operations are a person's own: unreachable by machine credentials, and
`agent.terms.accept` refuses every `via` but `ui`.

**The price the page showed is the price the send runs at (AD-1, addendum-2 S1/S2).**
`run.price_key` is a stable digest of what the person pays for one answer
(`RunQuotes.price_key`: `pk1:` and a SHA-256 hex digest, 68 characters) over exactly these facts:
the sale `price_id` with its unit, per-tokens, rates and off-peak rates (and, for a time-banded
price, the calendar and calendar revision that decide which of the two applies), one call's hold
`per_call_hold` (and with it the maximum), the model and confirmed route, and which incomplete
endings are charged (`charge_incomplete`). The route version, the route's validity and its
confirmation references are not in it: renewing the route at the same prices changes nothing the
person sees and refuses nothing. The page sends back `run.price_id` as `seen_price_id` and
`run.price_key` as **`seen_route_version`** — the field keeps its name for compatibility and
carries the price key, not the route version (strings, at most 200 characters). On the automatic
path both are **required**: no page in use reaches it without them (the earlier bundle always
sent `quote_id`/`consent_id`), so a send naming no displayed price was sent while the page showed
none (status loading, unreadable, or `run` null). When either is absent, or `seen_price_id`
differs from the sale price the automatic issue would bind at admission, or `seen_route_version`
from the price key it would bind (`RunQuotes.shown_price`, inside the admission transaction, after
the bound is computed), the send is refused with 409 `billing_price_changed` and the message
价格或模型已更新，本次没有发送，也没有扣费。请查看新价格后重新发送。, with detail
`{"current": {"price_id", "route_version", "price_key", "maximum", "per_call_hold", "unit": {code, scale}}}`
(`maximum` is the bound this person would get, their cap included). Nothing is stored: no message,
turn or authorization. The page then shows the new price and maximum and offers to send again at
it. The fields belong to the automatic path: together with `quote_id` or `consent_id` they are
`billing_authorization_conflict`; on any other caller's path they change nothing.

**Price change.** `agent.billing.status.price_notice` compares the price key of the person's last
bound run (recomputed from its quote terms) with `run.price_key` and carries the previous pricing
when they differ, `previous.price_key` included (a dismissal can be keyed on the two keys). A
route renewal at the same prices is no change and shows no notice. It is information only and
never blocks a send: a send from a page that shows the current price runs at it, and a page still
showing an earlier one is refused once as above, so nobody is bound at a price their page did not
show. A run in progress stops before its next model call whenever the route changes, a renewal
included (the turn's credential-binding fence, or `quotes.authorize` →
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

`agent.turn.start` with `quote_id` is the user's acceptance of that run's price and bound. From
the page it also needs the current terms, AI use rules and pricing accepted beforehand (above); it
never records them. The quote can authorize one turn only. Binding it and inserting the turn use
the **same Store instance and transaction**; failed admission rolls both back. A new key cannot
spend the same quote again. Changed text, branch, conversation revision/scope or route requires a
new quote. `agent.quote.get` and
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

The counters come from an exact, trigger-maintained **cost rollup** (`billing/cost_rollup.py`,
`suite_schema/model_cost_rollup.py`; docs/billing-ledger.md, "Cost rollup"), not from
the admission history. SQL triggers on the cost facts keep, inside the writer's own transaction,
per-UTC-day buckets of each provider account (admitted and resolved bounds, exact resolved cost),
the payer and route velocity buckets of the risk brake (1, 60 and 3,600 s), and the open set (an
admission still in flight, or whose call is still reserved without a recorded local stop). Costs
are summed exactly: integers in two int64 limbs; a cost on the 10^-15 grid (every production
tariff) also keeps its fraction on that grid; another canonical rational is summed per
denominator; anything else (a numerator or denominator beyond 2^63-1, a non-canonical text) is
kept as an exception row and parsed exactly as before. An admission reads at most 31 day rows
per unit and denominator, the open set, the exceptions in the periods and one index-only count
of today's pending rows; nothing grows with the month's settled volume. The only row limits left
refuse with `cost_accounting_capacity`: more than 10,000 open rows (impossible in band, because
admission enforces `unknown_count+1 <= max_unknown <= 10,000`) or more than 10,000 exception
facts in the periods (the Ledger writes one only for a reduced cost beyond 2^63-1).
`model_cost_pending` remains the derived pending projection (inserted with admission, removed
only with trusted usage); the rollup checks today's pending count against today's buckets.

The rollup is verified, never trusted blindly. A read-path check (bucket structure, a stale open
row, the number of exception rows, today's pending parity, a negative absorbed amount) refuses with `cost_rollup_inconsistent`
and is latched after the refusing transaction rolls back. The risk monitor recomputes the rollup
from the facts in a read snapshot hourly (last two days) and daily (from the previous month's
first day), and the restore rehearsal checks every day. A mismatch, a malformed or orphan
admission (a `fact:` fault), a value the read cannot decode or a latched read-path check opens a
fault for that provider account only: its paid calls refuse with `cost_rollup_inconsistent`. When
the derived rows were changed, an operator runs `python -m synwevo_core.billing.cost_rollup rebuild
--environment … --provider … --account-ref … --reason …` in a role container
(docs/platform/model-risk.md §13; locally `platform.py cost-rollup`), which replays that account's
facts, verifies the result and resolves its faults in one transaction (or changes nothing). A broken
cost fact fails closed: facts are immutable and only a write outside the product can break one, so
the rebuild names it and refuses, and the account stays refused until the database is restored from
a backup taken before that write. The administrators are told (`billing.cost.rollup_inconsistent`);
the rebuild is audited (`billing.cost.rollup_rebuilt`). Recovery is never automatic.
The internal snapshot exposes consistent counters only; it grants no dispatch authority and
is not a public Admin API. Disabling admission stops new calls while existing usage recording,
wallet release and settlement remain available.

RPM/TPM, fair queues, upstream balance alerts and an audited unknown-cost investigation
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
`held`, `remaining`; remaining is floored at zero and is not wallet balance.

Settled spend is read from exact **per-wallet day rows** (cost-capacity phase 2, decision D18;
`billing/spend_rollup.py`, `suite_schema/payer_spend_rollup.py`; docs/billing-ledger.md, "Per-wallet
spend"), not from the call history. SQL triggers keep, inside the writer's own transaction, per
wallet and UTC day of the call's creation time, the settled calls, how many of them have their
settlement and journal entry, and the exact sum of those journal amounts; whichever of the three
facts arrives last books the call, so each call counts once in any order. A check reads one fault
probe, at most 31 day rows (plus any future days) and the wallet's still-reserved holds through
their index; nothing grows with the person's settled volume. The only row bound left is more than
10,000 still-reserved calls of the wallet (`usage_state: "capacity"`, refusing
`payer_budget_capacity`); holds end at settlement, release or a deadline at most a day away. A
settled call in the periods without its settlement or journal entry (cleared when the fact
arrives), the day table's lost sentinel (cleared by the next start, which replays every wallet's
rows), a day row with a broken structure, or an open fault of the wallet returns
`usage_state: "inconsistent"` and refuses `payer_budget_inconsistent`; with both, `periods` is
`null`, never invented zero usage, and new calls are refused. Configuration remains editable so
the person can still disable their limits.

The day rows share the cost rollup's machinery. They are derived (a direct write refuses
`cost_rollup_derived`) and verified with it: hourly over the last two days, daily from the previous
month's first day (also comparing the read with the uncapped reference), in the restore rehearsal
over every day. A mismatch, a latched read (a broken day row found by a reservation or a dispatch; the
gateway hands either refusal to `after_refusal` once its transaction rolled back) or a settled call
whose creation time is not a non-negative integer (only a write outside the product leaves one;
the earlier read raised on a text time, counted a fractional one and ignored a negative one; the
bounded hourly and daily runs name it too, through the fault its write latched, whatever its time)
opens a fault of that wallet only (scope `(environment, '', wallet id)`): its paid calls refuse until an
operator runs `python -m synwevo_core.billing.cost_rollup rebuild --wallet <id> --reason …`
(docs/platform/model-risk.md §13), which replays its settled calls, verifies them and resolves its
faults in one transaction, or refuses and changes nothing while a settled call of it is broken (that
wallet stays refused until the database is restored from a backup taken before that write). The
administrators are told (`billing.payer_spend.rollup_inconsistent`, the wallet id only); the rebuild
is audited (`billing.payer_spend.rollup_rebuilt`). When several refusals apply, the check order
decides (fault, day rows, holds, unpaid). While the day table's sentinel is lost, the verifier reports
that store-wide only (`spend_sentinel_missing`) and compares no wallet's day rows, whether they went
with it or not, since the next start replays them all: no wallet is latched and none needs a rebuild
after the restart; a malformed settled call is still named (docs/billing-ledger.md "Per-wallet spend").

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
