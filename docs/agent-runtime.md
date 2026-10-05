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
* **Output rules.** Streamed pieces are checked with a carried window; the completing piece is
  never shown, `should_stop` ends the model call, what was shown is withdrawn from the message and
  the event stream, the step is not kept for replay, and the turn fails `content_blocked_output`.
  DeepSeek `finish_reason: content_filter` carries `stop_details.category = content_filter` and
  fails `content_filtered_output` (withheld the same way); other refusals stay `provider_refused`.
  Whether a stopped call is charged is the gateway's incomplete-call policy.
* **Records.** A stop is stored as rule id/version, direction and length — never text.
  `agent.message.report` files a report on an answer; its text is attached only when the person
  sets `include_excerpt`. Administrators (`platform.users.*`) close reports and suspend or
  restore a person's Agent access with a reason: 403 `agent_access_suspended`, running turns are
  cancelled, journalled `agent_access_suspended`.
* **Terms.** A paid run records the terms / AI use / pricing versions in force once per version
  (`synwevo_core/terms.py`); the account export lists them, together with the Agent's visible
  conversation text, memories, settings, consents, quotes and the wallet statement
  (`synwevo_core/agent_records.py`); account deletion erases the Agent's content.

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
