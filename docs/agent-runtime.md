# Personal Agent runtime and verification

This implementation is under acceptance testing. The production application switch defaults
to unavailable. A working local conversation manager is not evidence of a verified live model
or a complete cross-application workflow.

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

Organization workspace model-processing policy defaults to off. Owners/admins can permit the
platform credential or also personal credentials. Personal workspaces default to their owner's
choice. These policies govern model processing; normal application authorization continues to
apply to every operation. Platform API administration is not granted by personal Agent access.

## Execution and recovery

The server persists messages, turns, tool calls and SSE events. Text is rendered from authorized
snapshots. Stream reconnection resumes by event sequence and never submits another turn.
Scope, operation version, input, resource basis and audience bind a confirmation; changes require
a new review. Regeneration creates a read-only answer branch and cannot repeat a write.

The current per-turn limits are 12 model steps, 20 tools per step, 120,000 accumulated tokens,
600,000 context characters and 64,000 output characters. Admission allows three active turns
per user and 16 overall. These are guardrails, not latency or cost measurements. Monthly cost
budgets and multi-instance worker leases are not implemented: run one portal Agent worker until
lease-based recovery is verified. Restarted/uncertain external actions require reconciliation;
they must not be blindly resubmitted.

History and cached results recheck current access. Generic run lists/details never expose raw
operation inputs or error detail. Synchronous read snapshots are hidden when re-reading the
registered operation produces a different projection. Mutation snapshots require an explicit
`Operation.result_guard`; lack of a safe guard prevents old-result access and does not repeat
the mutation. `run_result_unavailable` carries the original run ID for reconciliation.
This conservative version comparison can also hide content after ordinary source edits.

## Cost transparency in the chat

Each assistant turn in `agent.conversation.get` carries `turn.cost`, read only from the money
ledger (`synwevo_suite.agent.turn_cost`): `charged_minor` is the sum of the run's `sale` journal
entries (decimal string, smallest unit of `currency` at `scale`), `tokens` are the settled usage
facts those entries charged for (`input_cached`, `input_uncached`, `output`, and `reasoning`,
which is part of output and never added twice), `model`, `band` (the time band pinned on the
charged attempts, `mixed` when they differ, `null` while no band column exists), `state` and
`held_minor`. `state` is `pending` while a call's usage is unknown (never a guessed charge),
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
expandable breakdown (cache hit/miss, output with reasoning, model, band, state); the composer
shows the payer (personal wallet first; organizations only as listed by
`GET /api/suite/agent/payers`, whose absence means personal only), the balance, the tightest
remaining limit, the most this turn can cost (an active consent's per-run budget, else the
per-call hold) and a low-balance prompt linking to the account service's `/account/wallet`.
Amounts are formatted with `Intl` from decimal strings, never through a float. The model picker
lists the governed model(s) the server offers with their exact price per million tokens and
capabilities; there is no client-side model switch.

Every assistant answer carries the label "由 AI 生成，可能有误，仅供参考" (zh-Hant / en
translated; DeepSeek Open Platform terms 8.1, owner decision: global). "Copy answer" and any
selection copied from an answer append it once; the per-code-block copy button copies only the
code so it pastes cleanly.

### Refusals in the chat

A refused send, or a model call refused later in a running answer, is put into one sentence and
one next step (`refusals.ts`), never shown as a code: insufficient funds (top up), a personal
limit reached, paused or missing (Agent settings), an automatic spending brake, a suspension, the
paid-dispatch breaker or platform cost admission (`billing_cost_*`), the platform's per-call
maximum cost (`cost_call_limit_exceeded`, its own sentence: a policy limit, not an outage), the
staged-rollout allowlist, the paid service not configured, the separate DeepSeek data-sharing
consent (`provider_consent_*`, Agent settings; `provider_consent_version_changed` asks to agree to
the new version), a changed model or price (re-confirm), and content safety: a message blocked
by an input rule (`content_blocked_input`, never sent, nothing charged, the text stays in the
composer to edit) or an answer stopped by an output rule or the provider's filter
(`content_*_output`, whose charge is whatever the answer's ledger line shows, because the
admin's incomplete-call policy decides it). The risk, consent and content families are also
recognised by their stems (`risk_*allowlist*`, `*breaker*`, `*brake*`, `*suspend*`,
`content_(blocked|filtered)_(input|output)*`). An upstream 402 is the operator's DeepSeek
balance and is shown as the service being unavailable, never as the person's balance. Every
refusal before dispatch says that nothing was called or charged for the refused step.

When a continuing consent's model or price no longer applies (`model_changed`), the consent
panel and the quote dialog list what changed (model, route, price version, unit, each rate per
million tokens, per-call hold). The dialog offers "confirm the new price and send" in one step:
it quotes the previous per-run budget, call count and time limit at the new terms, and sends only
when the quote equals the terms shown; otherwise it stops at the review and says the quote
differs. The same applies after a confirmation is refused because the terms changed.

## Verification and remaining gates

Use isolated test identities/data and the worktree's import paths, never the real `data/`.
Run `pytest apps/suite/tests`, root module/design/brand guards, and suite TypeScript tests/build.
Browser evidence belongs in the coordinating checkout's `docs/agent-work/EVIDENCE.md` and binds
to the verified commit. Provider test doubles validate engine contracts only.

Before enabling production, verify an actual model/tool/artifact run, compatible model options,
legal cross-workspace transfer, attachments, personal automations, recovery and resource limits.
Update public privacy/data-processing descriptions before sending business content to a model.
CI and deployment must use `.github/workflows/production.yml`; missing billing or environment
approval is not permission to deploy through another route.

Provider references checked 2026-09-30: [models](https://platform.claude.com/docs/en/models/overview),
[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming),
[effort](https://platform.claude.com/docs/en/build-with-claude/effort),
[official SDK](https://github.com/anthropics/anthropic-sdk-python). The concrete behavior adopted
is server-side credential ownership, real streaming and a configured model without invented
fallbacks; SDK transport tests check the request origin, model ID, error handling and redaction.
