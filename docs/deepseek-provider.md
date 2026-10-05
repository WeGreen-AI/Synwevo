> Coordinator integration update (2026-09-30): httpx is now explicitly declared and the protocol suite was independently rerun (148 adapter + existing Agent checks). MeteredDeepSeek and MeteredTurnProvider connect the transport to durable billing evidence and the real Agent tool loop; production route/quote, user wallet and price governance are not enabled. The original handoff below records the transport's bounded scope.

# DeepSeek adapter: bounded transport, not an enabled paid route

## Gateway rules (2026-10-05, agent billing programme)

These rules are implemented and tested with FIXTURE transports and the labelled loopback double
only. No real DeepSeek request has been made; the owner-approved real-upstream check (brief §5)
must still confirm the SSE shape, usage, tool fragments, error bodies, `reasoning_effort`
placement and failed-turn replay against the live API, and the input bound below.

| Rule | Where | Behaviour |
| --- | --- | --- |
| Reasoning replay | `engine.py` `_api_history`, `deepseek.py` `_history` | Failed and cancelled turns replay their private `reasoning_content` (never unrun tool calls); with thinking on every earlier assistant message carries a reasoning string (empty when none was produced), because a tool-carrying request that omits one is refused with 400. |
| Reasoning effort | `RouteSpec.reasoning_efforts`, `deepseek.py` | Values `none`, `low`, `high`, `max`, governed per route (default all). `none` sends `thinking: {type: disabled}` and no `reasoning_effort`; otherwise `enabled` plus the value. A level the route does not offer is refused before reserving or sending. |
| Model ids | `agent/model_check.py`, `provider_settings.record_health` | Never constants. `GET /models` with the sealed key at start, on each newly published revision (polled every 60 s) and every `SYNWEVO_DEEPSEEK_MODEL_CHECK_SECONDS` (default 3600). A missing id closes paid dispatch on that revision (`model_provider_health`, append-only; system audit `admin.model_provider.closed`; inbox/mail `billing.model.route_missing` to holders of `platform.config.manage`). Sticky until an administrator publishes again (the same settings may be republished). A failed check closes nothing. Turns are refused unsent with `billing_provider_model_missing`. The closure belongs to the provider revision, so it stops flat routes (`deepseek:<env>:<rev>`) and banded ones (`deepseek:<env>:<rev>:bands:<calendar rev>`) alike; a route version naming no revision is refused unsent (`model_configuration_changed`), never read as open. |
| Upstream 402 | `gateway._open_platform_breaker` | The operator's balance, never the person's. The turn fails with `provider_balance` and "temporarily unavailable, not charged" (three languages); nothing is sold, the uncertain hold ends unsold at its deadline. Paid dispatch closes through this one seam: `synwevo_core.billing.risk.trip_platform_breaker(source="upstream_402")` when that module is installed, otherwise the provider revision is closed (state `upstream_balance`, audited, `billing.model.upstream_balance` alert) and later turns are refused unsent with `risk_breaker_open`, the breaker's own code. |
| 429 / 503 | `gateway._throttled`, `_await_upstream` | The refused attempt reached the provider and is never resent (the ledger's dispatch fence holds it). Later calls wait out a process-local cool-down while still provably unsent (nothing reserved, no fence): Retry-After plus up to 20 % jitter, otherwise equal jitter over 1, 2, 4 … s capped at 30 s. A wait over 30 s or past the run deadline refuses the call unsent and unreserved (`provider_rate_limited` / `provider_overloaded`, `retry_after`). Success resets the backoff. One gateway process serves the deployment; several would each keep their own cool-down. |
| `user_id` | `MeteredDeepSeek.pseudonym` | HMAC-SHA256 of `environment:namespace:payer` under a key derived from the server root secret (label `synwevo/model-user-pseudonym/v1`); 64 hex characters, within `[a-zA-Z0-9-_]{1,512}`, stable per payer, not recomputable from a guessed id. A root rekey changes every pseudonym. |
| Incomplete-call charging | `ProviderSpec.incomplete_charging`, `gateway._settle` | Admin-set booleans `length`, `content_filter`, `provider_interrupted` (`aborted`, `insufficient_system_resource`) and `client_cancelled`; default all false (the platform absorbs the recorded cost). Published with the provider revision (reason, audit, immutable history), pinned on the run with the quote (a change invalidates quotes and consents). A charged ending sells its trusted terminal usage as a completed call, never beyond the hold; invalid or rejected output and unknown usage are never charged. A person who stops after the final usage chunk arrived (before `[DONE]`) leaves trusted usage (`terminal_usage_before_cancel`); stopping earlier leaves usage unknown. |
| Upstream override | `deepseek.upstream_base`, `SYNWEVO_DEEPSEEK_UPSTREAM` | Only with `SYNWEVO_BILLING_ENVIRONMENT=test` and an `http(s)://127.0.0.1|[::1]|localhost:<port>` origin (no path, credentials or query); anything else fails startup, and the gateway rechecks it against its ledger environment, so the platform key cannot be sent elsewhere. The rule lives in one place, `synwevo_core.billing.upstream.upstream_base`, and covers every request that carries the key: chat, `GET /models` and the risk monitor's `GET /user/balance` (the Agent composition starts `RiskMonitor` with the gateway's origin, so an isolated test instance polls the double's balance, never api.deepseek.com). `apps/suite/scripts/deepseek_test_double.py` is the labelled FIXTURE double (loopback only). |
| Hold input bound | `MeteredDeepSeek.input_bound` | No official tokenizer is in the lock. DeepSeek's tokenizer is byte-level BPE, so a text never has more ordinary tokens than UTF-8 bytes. The bound is the UTF-8 size of the request JSON (system, tool schemas, history with replayed reasoning, tool results) + 64 template tokens + 1024 when tools are offered, capped at the route's context ceiling. The per-call hold, the cost admission and the traffic estimate use it; quotes and run budgets keep the ceiling. The observation records the bound; if a provider count ever exceeds it, the ledger caps the charge at the hold and the platform absorbs the rest. |

The transport now also makes `GET /models` (same origin rules, bounded body, no redirects or
retries); everything below about the single chat POST still holds for chat requests.

Verified against public official documentation on **2026-09-30**. Implementation base:
`codex/deepseek-provider`, `8e8fcec01b26f9677e0951cf294212b23e5b7a09`.
No credentials, authenticated account inspection, live inference, browser changes,
deployment, payment, commit or push were used. The protocol fixtures are synthetic,
isolated tests, not evidence of a production model call.

## Official facts and unresolved differences

Coordinator recheck on 2026-09-30: the [Open Platform Terms](https://cdn.deepseek.com/policies/en-US/deepseek-open-platform-terms-of-service.html)
state release 2026-04-22 and effectiveness 2026-04-29. They describe downstream integration for internal and external users, require server-side key protection and the downstream operator's own end-user/data-processing obligations. This is not blanket permission for unrelated resale or brand endorsement. Their own privacy policy does not cover Synwevo's downstream data processing. The actual contracting entity, operating markets and API-specific retention settings remain unconfirmed; no zero-retention or tax/refund conclusion is inferred. The provider's own prepaid refund clause is not Synwevo's retail refund policy. A later fetch of the pricing page timed out; the earlier successful same-day reading below remains the dated evidence, not a newly verified price import.

| Topic | Findings and source |
| --- | --- |
| API and usage | Chat Completions uses streamed JSON deltas and `[DONE]`. Terminal usage is on the last content chunk with one choice and a finish reason, **not** a separate empty-choices chunk. Input is cache hit plus miss; `cached_tokens` aliases cache hits; reasoning is within output. See [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/). |
| Models and pricing | The [pricing table](https://api-docs.deepseek.com/quick_start/pricing/) lists `deepseek-flash` and `deepseek-v4-pro`, and describes retired `deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` aliases routing to Flash. It lists different Pro and Flash prices. Peak windows are weekdays 01:00–04:00 and 06:00–10:00 UTC, excluding Chinese public holidays; off-peak rates are half. No prices or holiday calendar are encoded here. |
| Conflicting Pro route evidence | The [September 10 announcement](https://api-docs.deepseek.com/news/news260910/) says Pro requests switch to V4.1-Flash at Flash rates from September 14, 04:00 UTC. The [current changelog](https://api-docs.deepseek.com/updates/) instead says Pro service and billing continue unchanged after September 14. The pricing table supports the latter distinction. These sources conflict; neither actual account routing nor weights can be proven from a response model string. |
| Tool protocol | [Tool calls](https://api-docs.deepseek.com/guides/tool_calls/) use assistant function calls followed by tool messages correlated by call ID. Strict schema mode is a beta endpoint feature. This adapter uses ordinary function tools and the standard endpoint, never beta strict mode. Arguments must still pass registry schema and authorization checks before execution. |
| Thinking | [Thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/) requires preserving all prior assistant `reasoning_content` when sending tools, including previous turns without calls. Without tools the server ignores replayed reasoning. Explicit thinking on/off is supported; medium/xhigh map to high, low to low, max to max. The adapter keeps reasoning private and preserves native replay messages. |
| Limits and isolation | [Rate limits and isolation](https://api-docs.deepseek.com/quick_start/rate_limit/) documents account-wide concurrency (Flash 2500, Pro 500), shared across keys; excess requests return 429. Expanded-account quotas also constrain `user_id`. SSE comments keep connections alive and do not prove inference began; upstream may close after ten minutes without inference. These are published values, **not verified account allocations or an RPM/TPM promise**. |

No fallback, alias rewrite, pricing tier, route guarantee, exact request-usage recovery
API, privacy/retention guarantee or commercial approval is inferred. The coordinator
must resolve actual routing/cost applicability with authoritative account evidence or
provider confirmation. Cross-boundary price timestamps, holiday rules, account quotas,
API terms/data handling and live tool/usage behavior remain unverified. Publication
of code does not enable charging (master requirements §§28, 37, 39).

## Interface and limits

`apps/suite/synwevo_suite/agent/deepseek.py` exposes `DeepSeekProvider.step(...)` for
synchronous workers and `await ...astep(...)` for asynchronous callers. Both accept
the existing explicit `Credential`, system text blocks, tool definitions, history,
effort, text callback and cancellation predicate. Neither resolves credentials nor
reads environment model/key/URL settings. `Credential.label` still says `anthropic`;
the coordinator must not use it to identify this provider.

The only network operation is one POST to `https://api.deepseek.com/chat/completions`.
TLS verification stays on; environment proxies, redirects and transport retries are
disabled. Errors never cause another request. No caller/model/tool URL, custom client,
beta path or response redirect can alter the destination. `transport=` is a trusted
Python test dependency, never a product configuration or tool argument. Injected
transports must honor async cancellation and must not retry or redirect internally.

Default bounds (constructor validates positive finite limits and hard ceilings):

| Resource | Default |
| --- | --- |
| Serialized request, including system, schemas, results and private history | 1 MiB |
| Response stream / framed event / event count | 2 MiB / 256 KiB / 20,000 |
| Visible text / private reasoning | 64,000 / 262,144 UTF-8 bytes |
| Tool arguments, per call / combined / calls per step | 65,536 / 262,144 bytes / 20 |
| Explicit generated-token limit | 8,192 (hard ceiling 32,768) |
| Total deadline / read idle / connect and write / cancellation poll | 120 s / 15 s / 10 s / 50 ms |

The total deadline also checks continuously arriving keep-alives; an idle timeout
alone is insufficient. Cancellation interrupts pending async reads and header waits,
closes the response/client and joins the request task. Callbacks must return promptly;
Python cannot preempt a blocking synchronous callback. Closing a local socket does
not establish zero upstream cost or successful provider-side cancellation. Byte and
token bounds are resource controls, not a verified monetary reservation formula.

SSE parsing supports split UTF-8, LF/CRLF/CR, comments and multiline data. It requires
one consistent completion ID/model, one choice, an allowed finish reason and framed
`[DONE]`. Empty-choice usage frames are rejected under this versioned DeepSeek
contract. Partial/duplicate/malformed tool calls, non-object arguments, duplicate JSON
keys, nonfinite numbers, undeclared names and excessive depth/size cannot execute.
Only visible content reaches `on_text`; no reasoning callback exists.

Native DeepSeek assistant messages preserve exact argument strings and reasoning.
Existing text/tool_use/tool_result blocks translate to native messages while retaining
call IDs and order. Foreign thinking signatures, images, orphan/duplicate tool results
and unresolved calls are rejected before dispatch. Plain user text and tool outputs
remain message data. An `is_error` flag has no native field; its textual error result
is preserved. This adapter never executes a tool or inserts invented tool history.

## Attempt and usage contract for the coordinator

The result is deliberately **not** a drop-in `StepResult`:

```text
DeepSeekResult
  step: StepResult                 visible text, tool objects, mapped stop reason
  replay_message: dict             private, lossless native assistant history
  attempt: Attempt                 network and metering evidence
DeepSeekError / DeepSeekCancelled
  attempt: Attempt                 same evidence on failure or cancellation
```

`replay_message` and raw usage are excluded from repr. They still need private
storage and access control; never serialize the entire envelope into UI, events,
logs, billing diagnostics or exports. The existing public `step.content` contains
no reasoning. `step.usage` stays empty: the coordinator must consume `attempt.usage`
and its reliability, rather than passing missing usage through the current engine's
zero-default counters.

| Dispatch evidence | Exact meaning |
| --- | --- |
| `definitely_not_requested` | Local validation, cancellation or fence failure occurred before handing this request to HTTPX. |
| `unknown` | Transport handoff began, but no completed-body trace or response was observed. Even connect errors remain conservative unknowns. |
| `observed` | HTTPX/httpcore observed completion of sending the request body, or a response arrived. This does not prove inference, completion, payment or zero cost on an HTTP error. |

`before_dispatch()` runs once after validation and before handoff. It must durably
record/fence the attempt and verify budget/reservation, current authority and policy,
or raise to prevent sending. Cancellation is rechecked after it returns. The callback
is optional only to keep this isolated transport usable in deterministic tests; a
paid gateway must supply it and own the durable lifecycle. A crash after intent but
before observable send remains unknown on recovery; a lease expiry is not permission
to resend. This module's in-memory evidence cannot solve the remote-accept/local-crash
window. All failure objects set `retryable=False`; bounded numeric `Retry-After` is
advisory metadata only, never an automatic retry instruction.

`provider_request_id` is the documented completion body `id` (absent before a valid
chunk), not an assumed HTTP header or a promise of a query-by-ID recovery endpoint.
`requested_model`, `returned_model` and `capability_version` are separate;
`effective_route` remains `None` until independently established by the coordinator.

Usage is `trusted_terminal` only after a valid terminal chunk and `[DONE]`, with strict
nonnegative integer categories (booleans, floats, strings and oversized integers are
refused), matching sums and aliases. No field is backfilled. `TokenUsage` keeps cache
hit/miss separate; completion already includes its optional reasoning breakdown.
An explicit complete all-zero provider record is valid; absent usage is not zero.
Nonterminal usage poisons reliability; malformed terminal categories are `unreliable`;
missing usage or interrupted framing stays `pending`. Raw terminal evidence can remain
available without `[DONE]`, but is not settlement authority. Trusted usage survives
an invalid final tool argument or an upstream aborted/resource finish; trustworthy
metering does not make a failed task successful. Over-limit reported consumption is
retained, never clamped or silently charged beyond authorization.

Required integration work, intentionally outside this file ownership:

1. Declare the already pinned `httpx` dependency in `apps/suite/pyproject.toml`.
   The dependency checker currently rejects this direct import; do not evade it with
   dynamic imports. No new package/version or lock change is required.
2. Extend provider/engine contracts and persistence for attempt ID, dispatch evidence,
   request ID, usage reliability/raw evidence/normalized counts, private native replay,
   requested/returned/confirmed route and capability/price versions. Never mix old
   provider thinking history into DeepSeek or discard required reasoning on replay.
3. Persist a fenced dispatch intent before send; reserve/authenticate every paid
   attempt, bound total run budgets/loops/queues, and atomically settle at most once.
   Pending/unreliable usage needs finite reconciliation and platform-loss handling,
   not invented counters, immediate blanket release or charging the full reservation.
4. Supply a privacy-preserving scoped `user_id`; enforce space data policies and
   per-account/actor/payer admission independently of transport. Validate tools through
   the existing capability registry and confirmation/idempotency service.
5. Map stop/error codes and usage-pending states to the product; keep hidden reasoning
   out of all UI/export/diagnostic surfaces. Account for cancellation after a completed
   response before updating the UI, and never treat `retryable` as billing permission.
6. Verify actual account routing, costs, quotas and a separately authorized minimal
   live inference/tool/ledger flow before enabling a paid route. This module registers
   no provider, credentials, configuration, schema, exports or routes.

## Verification

Run from the designated worktree with the existing repository virtualenv interpreter
(set `PYTHONPATH=apps/suite:packages/core:packages/auth-client/python`, as suite AGENTS
requires for worktrees):

```sh
python -m pytest apps/suite/tests/test_deepseek.py
python -m pytest apps/suite/tests/test_agent.py apps/suite/tests/test_agent_contracts.py apps/suite/tests/test_agent_stream.py
python scripts/check_boundaries.py
python scripts/check_brand.py
python scripts/check_design.py
python scripts/check_component_names.py
python scripts/brand_assets.py --check
```

Adapter tests cover wire fragmentation, terminal usage on a nonempty choice,
interleaved tool fragments, private reasoning replay, strict accounting categories,
missing/invalid/intermediate usage, truncation/interruptions, size/depth limits,
pre-send fences, cancellation before dispatch/during headers/during blocked reads,
continuous keep-alive deadlines, redirect refusal, HTTP failures, lost responses,
dispatch trace evidence and no retries/secrets in error messages. An autouse guard
rejects any attempt to use a real HTTP transport. Existing Agent regressions verify
that the unwired adapter does not change the currently installed provider path.

Actual results on 2026-09-30, using the repository virtualenv (Python 3.14.5,
HTTPX 0.28.1) with the worktree package paths above:

- Adapter suite: **109 passed**; existing Agent suites: **39 passed**.
- Both pytest runs reported the existing Starlette/AnyIO BlockingPortal deprecation warning.
- Brand, design, component-name and confirmed-logo checks passed.
- Boundary check failed solely on the suite's undeclared direct `httpx` dependency;
  the coordinator-owned manifest edit above is still required. No check was weakened.
- Only the three assigned new files exist in the worktree diff; HEAD remains unchanged.

This is isolated protocol verification only. Full frontend/build, live browser,
production DeepSeek, paid gateway/ledger and deployment acceptance are not claimed.
