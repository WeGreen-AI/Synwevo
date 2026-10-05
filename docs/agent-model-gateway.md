# Agent model gateway and metering boundary

Integration worktree: `feat/business-apps`; 2026-09-30. This is executable server-side integration,
not a configured paid service. No retail/cost price, currency, exchange, free allowance, merchant
or model route is seeded. The existing Agent factory remains on its previously configured
provider until route governance, wallet consent and payment policy are connected.

`MeteredDeepSeek` receives the shared Ledger, a required current model/data/run authority check,
and an approved `Route`. `MeteredTurnProvider` is the Agent engine port and requires an existing
budget authorization whose run ID equals the real turn ID and whose actor/payer is that user.
It cannot create grants, fund a wallet or select a fallback payer. Both callers and Ledger must
recheck current authority; a possessed platform key is insufficient.

`open_run` pins route, retail and cost versions. The configured input bound must cover the
provider's whole admitted context, including prompts, schemas and private history; it is not a
character estimate. Output is explicitly capped in the request. The hold uses the larger input
rate, with no assumed cache benefit, and allows one prior fractional unit. Until a tighter bound
is validated, the published context ceiling is deliberately conservative. An approved
`cost_basis_ref` must establish dispatch-time tariff selection; the code does not resolve the
conflicting Pro announcements or invent a peak/holiday rule. Missing/expired confirmation refuses
new dispatch. Actual prices and legally applicable terms still require owner configuration.

One logical call has a persistent request fingerprint and one physical attempt. Reservation and
the one-shot dispatch fence commit before network I/O. No database transaction surrounds the
stream. Same call/changed inputs conflicts; same call/same inputs also cannot send again. Unknown
consumption blocks further calls in that run while its hold remains active. A lease does not
permit another send. Expired holds release with no hidden late charge. An HTTP error answer is
settled at once as known zero usage; how unknown usage counts against the platform-wide unknown
limits (while in flight or within its hold; a locally stopped stream only while in flight) is in
`docs/platform/agent-run-budgets.md` and `docs/platform/model-risk.md` §1.

The restricted observation contains provider request ID, requested model, response label,
independently confirmed route reference, dispatch evidence, terminal reliability, necessary usage
counters and metering time. It contains no prompt, key or private reasoning. Invalid counter
strings/objects are stored as type errors, not untrusted private text. The observation is saved
before settlement; recovery replays local metering facts only. It never calls the model or
re-executes a business tool. Trusted terminal consumption on a failed provider result records
platform cost without a user sale in this first gateway policy. A complete response with missing
or invalid usage remains pending rather than recording zero.

The Agent saves lossless native assistant replay separately from public message bodies. Tool
confirmation resumes from recorded call IDs without stripping required reasoning or replaying a
completed mutation. Switching providers with native history requires a fresh conversation.
Known token counters exclude aliases and double-counted reasoning; unknown totals are exposed
as null with `state=pending`. These counters are diagnostic, not financial settlement authority.

Migration is additive through Store only. Billing facts/route snapshots/metering observations
are immutable; no balances/prices are created at startup. Rollback preserves facts. A final
billing recovery schedule, governed route/price publication, user quote/consent/wallet UI,
periodic/platform budgets, verified checkout/refund/dispute service, real model/merchant tests
and deployment gates remain outstanding. No production fallback or synthetic credit path exists.

Isolated acceptance uses the actual Store, Ledger, Agent and Forms services. Only external model
SSE and payment proof are named fixtures. It covers two separately settled model attempts around
a real form creation awaiting confirmation, private native history on resume, one resulting form,
recovery without resend, authority revocation, cancellation across the send fence, unknown usage
and finite release, and a crash between saved observation and settlement. This does not establish
actual DeepSeek routing, live inference, real payment, policy compliance or rollout completion.
