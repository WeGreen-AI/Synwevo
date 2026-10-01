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
to `MeteredTurnProvider`. The constructor rejects mismatched callbacks or gateway instances.
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
One corrupt record is isolated and cursor rotation allows later records to be processed.

For ended turns, complete trusted step evidence replaces public token counters in one transaction
and emits `usage.reconciled` with only `turn_id`. Repeated passes or two worker instances do not
add usage or duplicate the event. An event-write failure rolls back the projection. Active turns
are left to the engine to avoid racing its incremental counters. The browser's event handler only
reloads an authorized snapshot. Detailed failures in `last_round` are internal metadata, with no
exception prose or content; a complete Admin recovery surface and operational alerting remain due.
