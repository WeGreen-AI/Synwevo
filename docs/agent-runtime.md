# Personal Agent runtime and verification

This implementation is under acceptance testing. The production application switch defaults
to unavailable. On 2026-10-06 (Hong Kong), the governed DeepSeek route completed real tool calls
and sixteen metered Agent model calls/settlements in an isolated test wallet, including cross-workspace
reads and a confirmed Forms draft creation; see `docs/agent-work/EVIDENCE.md`. This proves
that bounded live path, not a complete cross-application workflow or production payment acceptance.

## Configuration and ownership

The portal role serves `/agent` with the existing workplace OIDC login. Enable `agent` in
`SYNWEVO_SUITE_APPS` only after its release gate passes. Conversation, preference, model-key
and memory rows belong to a stable user ID. Business operations name their workspace explicitly
and pass through the same capability registry used by the application's UI.

Set `SYNWEVO_AGENT_MODEL` to an actually available Anthropic model ID, and either set
`SYNWEVO_AGENT_ANTHROPIC_API_KEY` in the protected server runtime environment or configure a
personal key in Agent settings. There is no default model, provider proxy or alternate-model
fallback. Personal keys use the existing sealed storage/root key; operation inputs retain only
a key digest. Store migration redacts legacy plaintext key snapshots without replacing the
encrypted key. Never print environment values while diagnosing configuration.

The adapter uses the locked official Python SDK and the provider's official API origin. Host
Claude-tool credentials, custom headers and proxy API configuration are not product credentials.
The configured model's supported effort values still require a live verification before release.
Provider errors expose stable application codes, not raw response bodies.

With `SYNWEVO_AGENT_PROVIDER=deepseek` the platform route, key, prices and cost policy come only
from Admin governance (docs/deepseek-provider.md, "Gateway rules"). Host settings for it:
`SYNWEVO_BILLING_ENVIRONMENT` (`test` or `live`), `SYNWEVO_DEEPSEEK_MODEL_CHECK_SECONDS` (model-id
check interval, 60–86400, default 3600) and, in `test` only, `SYNWEVO_DEEPSEEK_UPSTREAM` pointing at a
loopback FIXTURE double for end-to-end tests:

    .venv/bin/python apps/suite/scripts/deepseek_test_double.py --port 31790 --models fixture-model
    SYNWEVO_BILLING_ENVIRONMENT=test SYNWEVO_DEEPSEEK_UPSTREAM=http://127.0.0.1:31790 ...

Any other override is refused at startup. When jobs run, the Suite starts the model-id check
worker next to the metering recovery worker; both stop with the application.

Organization workspace model-processing policy defaults to off. Owners/admins can permit the
platform credential or also personal credentials. Personal workspaces default to their owner's
choice. These policies govern model processing; normal application authorization continues to
apply to every operation. Platform API administration is not granted by personal Agent access.

## Payers (org-payments, 2026-10-05)

A paid run names its payer on the send itself (`agent.turn.start` input `payer_kind` = `user` |
`organization`, `payer_id`; send-and-go, see below). The person is the default and needs no field.
An organization pays only when its current grant lets this member charge it (`billing.org_payers`:
enabled, a monthly limit per member, UTC calendar month, spend plus active holds; a per-workspace
limit can be configured but no run carries a workspace, so it does not apply — money-3, an owner
decision). The run
authorization the server issues at the send, the run's `ChargeContext` (`payer_kind='organization'`,
`organization_id`) and every billing call record the payer; the ledger rechecks the grant and limits
under the writer lock at every reservation and before every send, so withdrawing a grant stops the
next send without any network call. The legacy `agent.turn.quote` still takes the same two fields,
and quotes and consents return `payer: {kind, id}`.

`GET /api/suite/agent/payers` lists the payers the signed-in person may charge — the personal wallet first
(`available`, `frozen`), then each granting organization with its wallet's available units and this
member's `monthly_limit`, `spent_this_month`, `held` and `remaining_this_month` (decimal strings of wallet
units, `unit.scale` places, CNY; for an organization `available` is the smaller of its wallet and this
member's remaining allowance, so a member never sees the whole organization balance — authz-4, server
side). Opening the Agent is its first use: this read also grants the one-time
welcome allowance when configured (docs/personal-wallet.md) and reports its state. Organizations manage
grants and limits on the accounts origin (`/api/auth/organizations/{oid}/billing`, permission
`org.billing.manage`; docs/platform/wallet-payments.md).

## Send and go (owner decision 2026-10-23)

A person types a request, presses Send (or Enter), and the answer starts: the page issues exactly
one `agent.turn.start` per send or regenerate (after `agent.conversation.create` for a new
conversation), with the payer inline and never a `quote_id` or `consent_id`. There is no budget,
quote or price dialog and no re-confirmation when prices change; billing itself is unchanged
(prepaid CNY wallet, DeepSeek cost × the administrator's markup, per-call holds and settlement,
the cost line under each answer).

* **Run authorization.** For a send from the page (`via=ui`) the server issues one automatic run
  authorization inside its admission transaction and binds it to the turn at once
  (`agent_model_quotes`, `terms_json.issued.mode = "automatic"`; export field `authorization`).
  Its bound is one call's hold × 12 calls at the current price, or the person's lower
  "maximum per answer" from Agent settings (`agent.settings.update` `run_budget_cap` /
  `clear_run_budget_cap`); the run lasts at most 3600 s within the route's validity. Every money
  invariant stays in the ledger and gateway: reserved plus spent never exceeds the bound, a charge
  never exceeds its hold, nothing is charged after a hold's deadline, and the payer, funds, freeze,
  personal and organization limits, risk controls, cost admission, provider consent, access and
  source policy are checked at reservation and dispatch. Flows, automations, machine credentials and
  the Agent itself get no automatic path (`billing_authorization_required` without a quote).
* **The composer** shows the payer, the balance, the tightest remaining limit and "this answer
  costs at most …" from `agent.billing.status.run.maximum` (`run: {maximum, default, cap, source,
  per_call_hold, max_calls, run_seconds, usable}`; a server without `run` shows the per-call hold,
  said as such). Send is never disabled for a money reason; the server's refusal says why.
* **One-time "Start using Synwevo Agent" step** (`AgentSetup.tsx`, an inline region, not a modal).
  When a send needs something the person has not done (`agent.setup.get`), the message stays in the
  composer, focus moves to the step's heading, and the step shows only what is missing: (a) the
  separate DeepSeek data-sharing consent — the same notice as Agent settings (`ProviderConsentFacts`)
  with its own unticked box, recorded by `agent.provider_consent.accept` for the version shown;
  (b) the terms of service, AI use rules and pricing — a second unticked box with the three pages
  linked in a new tab, recorded by `agent.terms.accept` with every document at its current version;
  (c) for the personal payer, the daily / weekly / monthly spending limits prefilled with the
  server's suggested values and editable, saved by `agent.billing.budget.set` with the setup's
  `expected_revision`. The button reads "Agree and send" with a message waiting, otherwise "Agree
  and start". The writes run in the order (a), (b), (c) and only then is the message sent; any
  failure stops there, keeps the message and says one step is unfinished (a newer document or notice
  version is shown for acceptance). The step returns only when a document or consent version
  changes; a price change never brings it back. An organization payer skips the limits.
* **Price notice** (`PriceNotice.tsx`). When `agent.billing.status.price_notice.previous` (the
  pricing of the person's last answer) differs from the current offer, the chat shows "Prices have
  changed. Answers from now on are charged at the new price." with what changed (model, route,
  price version, unit, each rate per million tokens, per-call hold) and "Got it". It never blocks a
  send. Its dismissal is remembered per viewer in `localStorage` for the (previous, current) price
  pair, every access inside `try/catch`, so blocked storage only means it shows again. An answer in
  progress when a price changes stops before its next model call (`price_changed`), calls already
  made are settled at the earlier price, and its next step is "send again at the new price".
* **Legacy quotes and continuing consents** (`agent.turn.quote`, `agent.quote.*`, `agent.consent.*`)
  stay for compatibility and audit; the page no longer creates or uses them, and existing consents
  are inert from it. The pre-quote source review of CR-079 is gone with the dialog: the server's
  `Contexts.prepare` in the same admission transaction stays authoritative, the composer's source
  chips (title · workspace · kind) are the review, and a refused source keeps the message and chips.
* **Known limitation.** Time spent waiting for a tool confirmation counts against the run's 3600 s;
  a run that runs out ends with `billing_run_time_too_short`, worded "send again to continue". There
  is no automatic re-authorization on resume.

## Conversation history pagination

`agent.conversation.list` reads only the caller's conversations. `status` and optional `q`
filter the result; search includes titles and personal user messages, never inaccessible generated
content. `limit` defaults to 50 and is bounded at 200. Results are ordered by `updated_at DESC,
id DESC`. Pass `next_cursor` as `cursor` for the next page; null ends the list. Cursors expire after
one hour and are sealed for the user, status and exact search text. Invalid or mismatched cursors
return 400 `invalid_cursor`; expired cursors return 410 `cursor_expired`. Reload without a cursor
in either case. A cursor carries no permission; every read still requires the current session.

This is a moving view, not a snapshot: new or edited conversations that move ahead of the boundary
appear on refresh. Deleting the boundary conversation does not lose the next page. The browser
keeps already loaded rows when a page fails, suppresses duplicate IDs, and discards late responses
from previous searches. The older `before`/`next_before` timestamp contract remains supported for
existing clients, with its historical exclusive-timestamp semantics; it cannot be combined with
`cursor`. New clients use the composite cursor so tied timestamps are not skipped. No migration,
conversation rewrite, model call or billing change is involved.

## Execution and recovery

The server persists messages, turns, tool calls and SSE events. Text is rendered from authorized
snapshots. Stream reconnection resumes by event sequence and never submits another turn.
Scope, operation version, input, resource basis and audience bind a confirmation; changes require
a new review. Regeneration creates a read-only answer branch and cannot repeat a write.

Stopping (`agent.turn.cancel`) is a request the running step honours at its next stop point: no
further step, tool or model call starts, pending confirmations are withdrawn, and a tool call that
was already running is recorded with its real outcome before the turn ends as `cancelled` (its
completed results stay). The reply says `cancelled` when nothing was running (final at once) and
`cancelling` otherwise; until then the conversation's turn carries `stopping: true`, which the chat
shows instead of offering a second stop. A restart while a stop is pending ends the turn as
`cancelled`, never as an `interrupted` failure inviting a retry.

The current per-turn limits are 12 model steps, 20 tools per step, 120,000 accumulated tokens,
600,000 context characters and 64,000 output characters. Admission allows three active turns
per user and 16 overall. These are guardrails, not latency or cost measurements. The governed paid DeepSeek route also
enforces the configured payer daily/weekly/monthly budgets and platform cost policy before dispatch
(`docs/platform/agent-run-budgets.md`, `docs/platform/model-risk.md`). Multi-instance Agent worker
leases are not implemented: run one portal Agent worker until lease-based recovery is verified. Restarted/uncertain external actions require reconciliation;
they must not be blindly resubmitted.

History and cached results recheck current access. Generic run lists/details never expose raw
operation inputs or error detail. Synchronous read snapshots are hidden when re-reading the
registered operation produces a different projection. Mutation snapshots require an explicit
`Operation.result_guard`; lack of a safe guard prevents old-result access and does not repeat
the mutation. `run_result_unavailable` carries the original run ID for reconciliation.
This conservative version comparison can also hide content after ordinary source edits. Forms list
proofs created from 734736a preserve their original item IDs: new unrelated forms do not invalidate
them, but original-item changes, deletion or lost access still do. Legacy hash-only proofs and other
operations retain the strict comparison; no old proof is guessed or migrated.

## Cost transparency in the chat

Each assistant turn in `agent.conversation.get` carries `turn.cost`, read only from the money
ledger (`synwevo_suite.agent.turn_cost`): `charged_minor` is the sum of the run's `sale` journal
entries (decimal string, smallest unit of `currency` at `scale`), `tokens` are the settled usage
facts those entries charged for (`input_cached`, `input_uncached`, `output`, and `reasoning`,
which is part of output and never added twice), `model`, `band` (the time band pinned on the
charged attempts, `mixed` when they differ, `null` while no band column exists), `state`,
`held_minor` and `payer` (who the run's ledger context charges: `{"kind": "user", "id"}` or,
for a run opened by organization payment, `{"kind": "organization", "id"}`; `null` when the
context names neither consistently). `state` is `pending` while a call's usage is unknown (never a guessed charge),
`held` while a call of the running turn still holds funds, otherwise `settled`. A turn without
a ledger run has `cost: null`; the engine's own token counters are never the bill. Another
person, or another ledger environment, never sees the figure. No cost price, margin, provider
request ID, credential or prompt leaves this read.

`agent.billing.status` (read-only, personal) gives the composer the governed offer's unit and
per-call hold, the person's wallet balance (`booked`, `held`, `available`) in that unit, and the
tightest remaining UTC spending limit (or that no limit is configured or spending is paused).
Without the paid provider it answers `available: false, reason: "not_configured"`; when the
offer or wallet cannot be read, `reason: "unavailable"`. `agent.billing.offer` adds the
documented capabilities of the two governed DeepSeek models (`vision` only for
`deepseek-flash`); an unregistered model ID is shown without capability claims.

In the page (`apps/suite/web/src/apps/agent/`): each answer shows its settled cost with an
expandable breakdown (cache hit/miss, output with reasoning, who paid, model, band, state); the
composer shows the payer, the balance, the tightest remaining limit, the most this answer can cost
(`agent.billing.status.run.maximum`, see "Send and go") and a low-balance prompt linking to the
account service's `/account/wallet`. `agent.billing.status` also carries `run` and `price_notice`
(send-and-go contract).

The payer choice reads `GET /api/suite/agent/payers` (organization payment) exactly as it is
sent: `{configured, environment, currency, unit: {code, scale} | null, payers: [...], welcome:
{state} | null}`, amounts being decimal strings of integer wallet units at `unit.scale`. The
personal wallet is always first and the default; an organization entry is offered only with a
valid `id` and `label`, and its figures (`available`, `frozen`, `monthly_limit`,
`spent_this_month`, `held`, `remaining_this_month`) are shown only when every one is well formed.
A 404 (organization payment not deployed) or `configured: false` means personal only. For an
organization the composer shows its wallet balance and this member's remaining monthly
allowance; "low" compares the smaller of the two with the per-call hold, and only when the units
match. A member cannot top up an organization, so its low or frozen state offers "pay with my
personal wallet" instead of a top-up link. A frozen personal wallet links to the wallet page.
The welcome allowance is mentioned when the list says it was just `granted`, or that verifying
the email address (`email_unverified`) would grant it. A send or regenerate carries `payer_kind` /
`payer_id` for an organization (nothing extra for the personal default); the server decides on
every send whether that payer may pay. The remembered choice lives in `localStorage` per viewer,
read and written inside `try/catch`.
Amounts are formatted with `Intl` from decimal strings, never through a float. The model picker
lists the governed model(s) the server offers with their exact price per million tokens and
capabilities; there is no client-side model switch.

Every assistant answer carries the label "由 AI 生成，可能有误，仅供参考" (zh-Hant / en
translated; DeepSeek Open Platform terms 8.1, owner decision: global). "Copy answer" and any
selection copied from an answer append it once; the per-code-block copy button copies only the
code so it pastes cleanly. "Export conversation" saves the branch on screen as Markdown in the
browser (nothing is sent or stored): questions and answers as shown, each answer followed by the
label, a hidden message as the hidden notice, and never reasoning, tool inputs or costs.

### Refusals in the chat

A refused send, or a model call refused later in a running answer, is put into one sentence and
one next step (`refusals.ts`), never shown as a code: insufficient funds (top up; for an
organization payer, pay personally instead), a frozen wallet (`wallet_frozen`: the wallet page,
or for an organization pay personally), an organization's monthly limit for the member or the
workspace (`org_*_monthly_limit_exceeded`) or a grant, membership or organization that no longer
lets the person charge it (`org_payer_not_granted`, `org_payer_not_member`,
`organization_unavailable`, `organization_billing_not_configured`, `payer_forbidden`,
`invalid_payer`; both: pay with the personal wallet — the organization sentence names the member's
monthly allowance only, since no per-workspace limit applies to a run), a personal
limit reached or paused (Agent settings) or missing (the one-time setup step), a per-answer maximum
below one call's hold or in a unit that changed (`run_cap_*`, Agent settings), an automatic
spending brake, a suspension, the
paid-dispatch breaker or platform cost admission (`billing_cost_*`), the platform's per-call
maximum cost (`cost_call_limit_exceeded`, its own sentence: a policy limit, not an outage), the
staged-rollout allowlist, the paid service not configured, the separate DeepSeek data-sharing
consent (`provider_consent_*`) and the terms not yet accepted (`terms_acceptance_required`), both
"finish setup" (the one-time step opens with the message kept; `provider_consent_version_changed`
asks to agree to the new version), a changed model or price ("send again at the new price"), a run
that used up its time (`run_time_too_short`) or a legacy run authorization that no longer opens
the run (`billing_quote_*`), both "send again", and content safety: a message blocked
by an input rule (`content_blocked_input`, never sent, nothing charged, the text stays in the
composer to edit) or an answer stopped by an output rule or the provider's filter
(`content_*_output`, whose charge is whatever the answer's ledger line shows, because the
admin's incomplete-call policy decides it). The risk, consent and content families are also
recognised by their stems (`risk_*allowlist*`, `*breaker*`, `*brake*`, `*suspend*`,
`content_(blocked|filtered)_(input|output)*`). An upstream 402 is the operator's DeepSeek
balance and is shown as the service being unavailable, never as the person's balance. Every
refusal that can also stop a running answer is worded so it is true mid-run ("no new model call
was made and nothing was charged for it"; rr-glue-2); only the one-time setup refusals are
admission-only. No refusal asks anyone to re-confirm a price, a run time or a quote.

An administrator's suspension of the person's Agent access (`agent_access_suspended`, 403 on a
send and the check before every model call of a running answer) has its own sentence: Agent
access is suspended, nothing new was called or charged, and the next step is to contact the
platform administrator (no top-up, settings, setup or send-again is offered). The same mapping
also words a reasoning effort the route does not offer (`model_effort_unavailable`, Agent
settings) and a request above the per-call size bound (`billing_model_request_too_large`). The
chat test (`apps/suite/web/tests/agent-chat.test.ts`) reads every error code
the agent, ledger, admission, risk, traffic, budget, organization-payer and safety modules raise
and requires each to be a refusal, a specific sentence (`errors.tsx`), or listed with the reason no
chat shows it; and no platform-side code (an upstream 402 or 429, a breaker) may mention a
balance or a used-up quota.

A price change is a notice, never a step to confirm (see "Send and go"); the refusal tests
(`agent-refusal-wording.test.ts`, `agent-send-and-go.test.ts`) pin the kinds, next steps and the
mid-run wording in three languages.

## Data-sharing consent and content safety

`synwevo_core/agent_safety.py` (shared: the workplace origin enforces, the account service
governs) and `apps/suite/synwevo_suite/agent/safety.py`.

* **Separate consent.** When the credential's provider is a named processor (today DeepSeek,
  `PROVIDER_TERMS`), no message is admitted and no model call is made until the person accepted
  that provider's current version (`agent.provider_consent.get/accept/withdraw`, Agent settings
  "Sending content to the model provider DeepSeek"). Missing or withdrawn: 409
  `provider_consent_required` `{provider, version}`; an older accepted version or a stale accept:
  409 `provider_consent_version_changed`. Checked at admission and in `_check_context`, i.e.
  before every model call, so a withdrawal stops a running answer's next call. A withdrawal is
  journalled (`agent_provider_consent_withdrawn`).
* **Input rules.** Administrators publish keyword/pattern lists as immutable versions
  (`/api/admin/agent-safety/rules`, `platform.config.manage`, audited). A matching message is
  refused before anything is stored or sent: 422 `content_blocked_input` `{category}`.
  Memories reach the model in every request's system prompt, so the same rules apply to them:
  adding or editing one (Agent settings, or a memory the model proposed and the person confirmed)
  is refused with 422 `content_blocked_memory` `{category}` and a safety record (rule, direction
  `input`, length — no text), shown as "This memory matched a content safety rule, so it was not
  saved" (three languages, Settings and the chat's action card). `agent.memory.add/update` own
  their transaction so that record survives the refused write. A memory saved before a rule
  matched it stays visible to the person but is left out of the prompt. Other person-authored
  text does not reach the model: conversation titles and settings are never sent. Workspace
  content read by tools is governed by the workspaces' AI-processing policies, not these rules.
* **Output rules.** Streamed pieces are checked with a carried window; the completing piece is
  never shown, `should_stop` ends the model call, what was shown is withdrawn from the message and
  the event stream, the step is not kept for replay, and the turn fails `content_blocked_output`.
  DeepSeek `finish_reason: content_filter` carries `stop_details.category = content_filter` and
  fails `content_filtered_output` (withheld the same way); other refusals stay `provider_refused`.
  Whether a stopped call is charged is the gateway's incomplete-call policy. Before a step is
  accepted the rules also read every tool call's arguments (each string key and value), so text
  the model puts into a confirmation card, a memory or workspace data instead of its visible
  answer stops the step the same way: no tool handler runs, no card is created, and on the paid
  path the step is recorded as platform cost, never sold.
* **Records.** A stop is stored as rule id/version, direction and length — never text.
  `agent.message.report` files a report on an answer; its text is attached only when the person
  sets `include_excerpt`, and only while the answer is visible to them: an answer hidden after an
  access change can be reported bare, never quoted (409 `report_excerpt_hidden`, the engine's
  `message_hidden`). In the chat every finished answer with text has a "Report" control
  (`ReportDialog.tsx`): a reason (harmful, illegal, privacy, wrong, other), an optional note
  (≤ 1000 characters) and an "attach this answer's text" box that is off by default; it ends
  "sent" (saying whether the text went with it), "already reported" (`report_exists`, not a
  failure) or "not sent" with the reason and a retry of the same request. Administrators
  (`platform.users.*`) close reports and suspend or
  restore a person's Agent access with a reason: 403 `agent_access_suspended`, running turns are
  cancelled, journalled `agent_access_suspended`.
* **Withdrawals survive a restore.** The consent withdrawal, the suspension, a workspace AI-policy
  narrowing (`ai_policy_narrowed`), a withdrawn platform model key (`model_credential_withdrawn`) and
  a provider switch-off (`model_provider_disabled`) are journalled before their transaction commits,
  so a backup can land between the line and the COMMIT. A restore therefore replays them whatever
  the backup's time (`journal.ALWAYS_REPLAYED`), and each replay keeps what the backup already holds
  after it: a suspension by id (a lifted one stays lifted), a consent withdrawal as of its time (a
  later consent stands), the key withdrawal and switch-off by the operation id of the provider
  version that committed with them, the policy narrowing by a receipt committed with it; a replay
  leaves its own receipt, so a key published later under a reused revision number survives the
  next restore (`apps/suite/tests/test_agent_withdrawal_restore.py`,
  `services/identity/tests/test_model_withdrawal_journal.py`).
* **Terms.** The terms of service, AI use rules and pricing (`synwevo_core/terms.py`, versioned)
  are accepted once, explicitly, in the one-time setup step (`agent.terms.accept`, every document
  at its current version; a newer version is 409 `terms_version_changed` and is shown for
  acceptance); a send without acceptance is refused before anything is stored (409
  `terms_acceptance_required`), and a new version asks once more. Agent settings list the accepted
  versions read-only. The step links the three pages on the public site: the server-controlled
  `site_url` of the auth state plus the portal's localized path (`/terms`, `/en/ai-use`,
  `/zh-Hant/pricing` …, `composer.ts` `publicHref`), opened in a new tab so the waiting message is
  kept; on a single-host install without a separate site the path stays on the same host. The
  data-sharing consent's privacy and AI-use links use the same origin; the account export lists the
  accepted versions, together with the Agent's visible conversation text, memories, settings,
  consents, run authorizations (with how each was authorized) and the wallet statement
  (`synwevo_core/agent_records.py`); account deletion erases the Agent's content, including the
  copies in the capability layer's run records (`op_runs` of app `agent` keep only their receipt:
  who, which operation, when, state, error code, object id). The export never
  carries a text the Agent may hide: the engine's hide rule re-reads sources through the business
  applications, which the account service cannot run, so an answer keeps its text only when it
  depends on no workspace or only on "reported by" sources in workspaces the person still belongs
  to; any other answer is exported `hidden` with an empty body (`hidden_notice` says why and points
  to the conversation), and a report's quote follows its answer.

## Verification and remaining gates

Use isolated test identities/data and the worktree's import paths, never the real `data/`.
Run `pytest apps/suite/tests`, root module/design/brand guards, and suite TypeScript tests/build.
Browser evidence belongs in the coordinating checkout's `docs/agent-work/EVIDENCE.md` and binds
to the verified commit. Provider test doubles validate engine contracts only.

Before enabling production, verify an actual model/tool/artifact run, compatible model options,
legal cross-workspace transfer, attachments, personal automations, recovery and resource limits.
The public privacy page now describes the model provider (docs/platform/public-pages.md §2.9);
keep it in step with `PROVIDER_TERMS` before sending business content to a model.
CI and deployment must use `.github/workflows/production.yml`; missing billing or environment
approval is not permission to deploy through another route.

Provider references checked 2026-09-30: [models](https://platform.claude.com/docs/en/models/overview),
[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming),
[effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[official SDK](https://github.com/anthropics/anthropic-sdk-python). The concrete behavior adopted
is server-side credential ownership, real streaming and a configured model without invented
fallbacks; SDK transport tests check the request origin, model ID, error handling and redaction.
