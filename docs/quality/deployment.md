# Vultr Tokyo deployment — current record

## Current release · 2026-10-08 15:21 UTC

**Running source: a41ea08a50b9047c8cf5bdd23ab982751b50d38c.** [Actions37792532752](https://github.com/WeGreen-AI/Synwevo/actions/runs/37792532752) verify/deploy succeeded. Runtime 5453 passed, 5 skipped, 242 deprecation warnings; original guards/build/type/frontend/SDK checks retained. All four application OCI revisions/current.json match; six containers healthy; five public domains, JavaScript assets, anonymous authorization boundaries and OIDC start verified. The Agent gateway/recovery/ledger file hashes match the locally verified source. Backup: `/opt/synwevo/backups/20261008T152122Z`.

Agent remains enabled with the existing allowlist and limits. Owner's personal spending remains paused. Payment settings v1 were last verified disabled; activation and a human real-card payment remain unverified. Additional read-only historical-usage audit did not execute because automatic approval timed out twice. Browser inspection was rejected because the active window contained unrelated private content; no bypass or payment mutation. User requested pause after this round. Evidence: `docs/agent-work/evidence/2026-10-08-agent-budget/release-result.json`. Older sections below are historical.


## 2026-10-07 — Webhook reliability and bounded attachment cleanup (8ba2956)

Running product source is `8ba2956750c2080d300fa17523bcc391e85cae40`. [Actions37619470838](https://github.com/WeGreen-AI/Synwevo/actions/runs/37619470838) verified the exact source and deployed through the existing receiver at13:11:50UTC. Runtime5328 passed/5 skipped, frontend500, Python SDK47 plus38 subtests, TypeScript SDK34 and remaining build/guard gates passed. Independent14:39UTC checks matched all four application OCI revisions and imageID `sha256:9b83c825a6594924aaff67e6b3a918f9de2beb4e4d6838cf691477800305337e`; five services and public roots/readiness/static assets passed, as did anonymous boundary and OIDC-start checks. Backup `/opt/synwevo/backups/20261007T131123Z` identifies08d6700; no restore drill was performed. Evidence: `docs/agent-work/evidence/2026-10-07-webhook-reliability`.

At14:58:27UTC, the legitimate administrator UI published the DeepSeek peak/off-peak calendar revision1 (`ao_d7da658ed75f`). On2026-10-08 at01:52UTC, read-only Store inspection confirmed this sole calendar version; provider/cost/price/payment configurations and billing calls/settlements were still empty. Agent remains absent from `SYNWEVO_SUITE_APPS`; no production model call, top-up or repeated calendar write was performed. The Mac subsequently became available; cost draft `mpd_3870d36aa953` revision1 was saved at02:00:45UTC. Publication awaits the existing password/security-key reauthentication in the administrator UI. This is a completed code release, not completed public Agent activation; current checkpoint: `docs/agent-work/STATE.md`.

## 2026-10-06 — DeepSeek Agent and billing code deployed; activation pending (961acbf)

**Running source:** `961acbf3ad3e363486261072552c5d91a7024c32`. [Actions 37362720332](https://github.com/WeGreen-AI/Synwevo/actions/runs/37362720332), attempt 2, deployed successfully at 01:40:51 UTC through the existing restricted SSH receiver. Attempt 1's deployment was cancelled by GitHub before any step because no hosted runner was assigned. On the owner's remote-deployment request, the deploy job alone was resumed after Actions recovered; the successful verification for the same SHA was reused, not rerun: runtime 5130 passed / 5 skipped, frontend 496 and infra 11, with SDK, guard, type and build checks.

At 01:41 UTC, all four application containers had image ID `sha256:1358526d4d1776dcf8b28cb115f65c0fb654fbbc2694a3ea3076278d3d8bd14e` and the expected OCI revision. Accounts, Docs, Portal, Messages and gateway were healthy. Pre-upgrade backup `/opt/synwevo/backups/20261006T014023Z` contains its manifest and database and identifies the previous a4b42c8 image; this check is not a restore drill. All five public hosts returned 200 for readiness, root and referenced JavaScript. The workspace sign-in start redirected to Accounts OIDC; anonymous provider and top-up-options requests returned 401.

DeepSeek/live/CNY(scale 6)/welcome 100 minor units settings are now loaded by Accounts and Portal. The five business applications remain enabled; **Agent and actual payment collection remain closed** pending legitimate administrator configuration and production acceptance. Provider/payment version tables and call/settlement tables exist and were empty at inspection. No production model request, grant, form creation or payment was replayed. Authenticated production business acceptance is still outstanding. Evidence: `docs/agent-work/evidence/2026-10-06-deepseek/{release-status,production-after-deploy,public-after-deploy}.json`; activation requirements: `docs/agent-work/DEEPSEEK_ENABLEMENT.md`.

## 2026-10-05 — business applications released; app icons; acceptance fixes (a4b42c8)

**Running source:** `a4b42c8da0cee86677cc7d6409e8eaff57543b2b`. **The five business applications are now open to
users:** on the owner's instruction (2026-10-04) the host's `/opt/synwevo/config/runtime.env` gained
`SYNWEVO_SUITE_APPS=forms,tables,dataprep,approvals,extract` (backup `runtime.env.bak-20261004T153728Z`) and the portal
role was recreated; the registry shows forms, tables, dataprep, approvals and extract active. The personal Agent stays
off (no provider model or key; the DeepSeek path also needs confirmed prices and a cost policy).

Release acceptance on an isolated instance running the deployed code with the same switch (real sign-in, headless Chrome
and the HTTP API, three languages, 390px, keyboard, memory; CODE_REVIEW CR-054) found Forms failing (publishing while
autosave failed dropped a new field; repeated-group cells showed "[object Object]") and medium defects in the other four;
all 30 acceptance defects and the icon review's findings were fixed with failing-first tests before this release.
Capacity: a 50,000-row DataPrep run peaked at 388 MB and a 12-page OCR at 493 MB for the whole container, so the portal
service's limit was raised to 1536 MiB in the host Compose (backup `compose.yaml.bak-20261004T174954Z`) and in
`infra/production/compose.yaml`. Also released: the Synwevo app icons (Docs, Message, the five applications, workspace,
account, admin, API console) from one generated source, with `app_icon_assets.py --check` now in verify.

The first attempt, [Actions 37225946982](https://github.com/WeGreen-AI/Synwevo/actions/runs/37225946982) on `6507785`,
failed verify on that new check only (it compared PNG bytes; the CI encoder writes different bytes for the same pixels);
nothing was deployed. `a4b42c8` compares pixels within 2 per channel, as `brand_assets.py` does.
[Actions 37226305708](https://github.com/WeGreen-AI/Synwevo/actions/runs/37226305708) (18:55 → 19:43 UTC) passed verify
and deployed; pre-upgrade backup `/opt/synwevo/backups/20261004T194321Z`. Checks: `/readyz` 200 on all five hosts;
`/forms`, `/tables`, `/dataprep`, `/approvals`, `/extract` 200 on workplace; read-only `docker ps`: accounts, docs, portal,
messages all `synwevo:a4b42c8d…` and healthy, portal limit 1.5 GiB, gateway recreated, ClamAV unchanged; no exceptions in
the portal log; the public bundle serves the icon files (200, image/png). Local gates on the same tree: pytest 4817
passed, 2 skipped; guards, brand and app-icon checks, typecheck, build, npm tests, Python SDK 40, infra 8.

## 2026-10-04 — business applications deployed dark (6f463ba)

**Running source:** `6f463ba12ef81ec5880117e8e77f8ed965db5b56` — `feat/business-apps` (PR #1, now merged) fast-forwarded
onto `main@7d690f1`: the five business applications (Forms, DataPrep, Approvals, Extract, Tables), flows, the personal
Agent, governed administration and billing foundations, with every fix of the third to sixth adversarial reviews
(CODE_REVIEW CR-051..053: one high, a removed member kept receiving submission notifications; data-flow, correlation,
webhook and flow-engine mediums). **Nothing new is open to users:** read-only checks after the deploy show the app
registry holds only `docweave` and `messages`, `SYNWEVO_SUITE_APPS` is unset (each application stays unavailable until
named there, a host-side setting to be agreed first), no Agent model or key is configured, and no billing environment is
set (paid calls and payment collection stay closed). The 55 suite tables were created empty by the additive schema.

The first attempt, [Actions 37201117273](https://github.com/WeGreen-AI/Synwevo/actions/runs/37201117273) on `f6df024`,
failed verify on one test fixture only (3256 passed): a 5000-deep object encoded with `json.dumps`, which raises
`RecursionError` on the image's Python 3.12; nothing was deployed. `6f463ba` writes that fixture as text.
[Actions 37202855430](https://github.com/WeGreen-AI/Synwevo/actions/runs/37202855430) (12:39 → 13:20 UTC) passed verify
and the restricted SSH deployment; pre-upgrade backup `/opt/synwevo/backups/20261004T132023Z`. Checks at ~13:22 UTC:
`/readyz` 200 on synwevo.com, accounts, docwave, workplace and message (database, storage, worker, malware scanner `ok`
where the role has them); read-only `docker ps`: `synwevo-accounts-1`, `-docs-1`, `-portal-1`, `-messages-1` all
`synwevo:6f463ba1…` and healthy, gateway recreated, ClamAV unchanged.

Local gates on the same code (`a47b148`, plus the fixture change): pytest 4788 passed, 2 skipped; five guards, typecheck,
build, npm tests, Python SDK 40, TypeScript SDK 25, infra unittest 8. Owner decisions still open: R3-FE-04 (status checks
counted against a flow's call budget), the shared webhook resolver pool cap (R3-WH-2 remainder, R4-webhooks-merge-1/2),
D3, D5 remainder, D7 step 2, the ClamAV host step; and which applications to enable, after their acceptance.

## 2026-10-04 — admin governance fixes deployed (7ae3527)

**Running source:** `7ae35276bfb285c1462718631d31f21cc35394db` (fast-forward `9bf71b9..7ae3527`, 13 commits from
`claude/admin-d9`). The verified Admin defects of CR-050 that need no owner decision, each with a regression test that
fails on the old code:

* D6 `5d49d48` + `f7f3c7f` + `730a2c2` — CLI `app disable --workspace` / `app grant` go through the service rules; every
  app switch-off (per workspace and, new, platform-wide: journal kind `app_disabled`) is journalled inside its
  transaction, so a journal failure rolls it back and a restore keeps it off.
* D7 step 1 `1d5c03b` — disabling Synwevo Message never reopens the account origin's conversation routes (rollback note
  `c9896b6`). Ending existing Message sessions on disable stays an owner decision.
* D5 (the parts needing no owner decision) `7695c54` + `5d59857` — a service account can only be renamed or narrowed; a
  user client cannot become one (its secret would follow); the workspace directory needs a live owner approval.
  Read-only production count before the push: 0 platform service accounts, 0 directory calls in the call window.
* D9 `3255ad8` — `keys retire` refuses the active key, an unknown kid, and a retiring key until 360 s after rotation
  (`--force --yes` for a compromised key, audited as forced).
* D12 `49701fe` + `7ae3527` — the five organization role writes require and record a reason; adding or taking away a role
  needs every permission it carries; an accepted import invitation adds roles beside the ones held.
* D13 `e730e0f` + `74aecd9` — a report is claimed in the transaction that removes the message (the losing decision is 409);
  the decision tells every reporter it settled and closes every manager's to-do.

Local gates on `7ae3527`: pytest 2996 passed, 2 skipped (testpaths); infra unittest 8 OK; npm tests, typecheck, build and
the five guards on `c9896b6` (no front-end change since); refusal catalogue 7/7. SDK unchanged from `main`, not rerun.
Two adversarial reviews (wf_08d6102e-b4a, wf_38b4ee2e-238) found nothing high or medium introduced by these commits; the
pre-existing mediums they confirmed in the touched paths were fixed in the same range. Open lows are listed in CR-050.
Rollback note: an older image reading a security journal that contains `app_disabled` refuses the restore (unknown
kind) rather than dropping the entry.

[Actions 37185330301](https://github.com/WeGreen-AI/Synwevo/actions/runs/37185330301) (07:17 → 07:56 UTC) passed verify
(full suite in the 3 GB image) and the restricted SSH deployment; pre-upgrade backup `/opt/synwevo/backups/20261004T075543Z`.
Checks at ~07:58 UTC: `/readyz` 200 on synwevo.com, accounts, docwave, workplace and message (database, storage, worker,
malware scanner `ok` where the role has them; docwave's first probe two minutes after the restart took over 30 s, the
following ones answered in under a second); read-only `docker ps`: `synwevo-accounts-1`, `-docs-1`, `-portal-1`,
`-messages-1` all `synwevo:7ae35276…` and healthy, gateway recreated, ClamAV unchanged. Still not deployed:
`feat/business-apps` (PR #1), whose third review confirmed 1 high and 9 medium findings now being fixed on that branch.

## 2026-10-04 — production leaves aadcb9e (GitHub Actions billing block cleared)

**Running source:** `b8cb31a8d3add65a855fa3cea04dd71af325bb0d`. Production had stayed on `aadcb9e` since 2026-09-28, when
GitHub Actions stopped starting jobs for the account's spending limit; every `main` push since was queued and never ran.
After the October reset the rerun of `4b74796` (Actions 36688073906) reached verify and failed there on two latent test
defects, both fixed on `main` before this deploy:

* `37cd253` — `tests/integration/test_gate_api_errors.py` asserted the workspace rate-limit budget at exactly `r=1197`;
  the GCRA bucket refills one request every 50 ms, so the slower CI container read `r=1198`. The assertion now bounds the
  remaining budget by the elapsed time (reproduced locally with a delayed client).
* `b8cb31a` — the resumable-upload routes left their 413/416 response descriptions to FastAPI, which takes them from the
  running Python; 3.13 renamed both (RFC 9110), so the contract generated on the local 3.14 venv differed from the CI image's
  3.12 (`test_openapi_file_is_in_sync`, contract i18n). The descriptions are now declared; the regenerated
  `docs/integration/openapi.yaml` is identical under both phrase sets.

[Actions 37171551868](https://github.com/WeGreen-AI/Synwevo/actions/runs/37171551868) (created 02:35 UTC, finished 03:09 UTC)
passed verification (full suite in the 3 GB image) and the restricted SSH deployment; pre-upgrade backup
`/opt/synwevo/backups/20261004T030921Z` (`{"deployed": "synwevo:b8cb31a…"}`). Checks at ~03:12 UTC:
`/readyz` 200 on synwevo.com, accounts, docwave, workplace and message (database, storage, worker, malware scanner `ok`
where the role has them); read-only `docker ps` on the host: `synwevo-accounts-1`, `-docs-1`, `-portal-1`, `-messages-1`
all `synwevo:b8cb31a8…` and healthy, gateway recreated, ClamAV unchanged.

The deployed range `aadcb9e..b8cb31a` includes the security fixes recorded as CI-118 (no cross-workspace join into Docs
collaboration rooms) and the refusal of encrypted archives the scanner cannot inspect, and the API platform work up to
`161f9cb` (signed resource cursors — an old unsigned cursor now answers 400 `invalid_cursor` with a restart instruction —,
typed resumable uploads, source lifecycle and signed download errors). Not deployed: `feat/business-apps` (PR #1, the five
applications, Agent and data-flow contract), which stays off `main` until its second adversarial review has no open high or
medium finding.

## 2026-09-25 continuous iteration — CI-01 deployed, CI-02/03/04 in flight

**Running source:** `9b10602d362feffa8af05079703162adf15727b0` (CI-01: WCAG 2.2 reflow and target-size minimums on every
shell). [Actions 36130548667](https://github.com/WeGreen-AI/Synwevo/actions/runs/36130548667) passed verification and the
restricted SSH deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T115851Z` (`{"deployed": "synwevo:9b10602…"}`).
Anonymous checks ~12:05 UTC: `/readyz` ready on all three hosts, the account shell's bundles 200 and the shipped stylesheet
carries the new 24 px rule.

**Running source:** `ccdd95005e8d50c91b923bcf12da023c03a87a12` (CI-02 security headers on every response; CI-03 mapping-dialog
wording). [Actions 36132038032](https://github.com/WeGreen-AI/Synwevo/actions/runs/36132038032) (created 11:55 UTC, finished
~12:20 UTC) passed verification and deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T121944Z`. Anonymous checks
~12:22 UTC: all three hosts answer HTML and JSON with `Strict-Transport-Security: max-age=31536000; includeSubDomains`,
`X-Content-Type-Options: nosniff`, the enforced `Content-Security-Policy`, `Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Resource-Policy: same-site`, `Permissions-Policy`, `Referrer-Policy: no-referrer`, `X-Frame-Options: SAMEORIGIN`.
Chromium (anonymous, 1280 and 375 px) loaded the public home, the signed-out workspace page, sign-in, register, forgot-password
and the Docs shell with no horizontal scroll and **one kind of CSP violation on every page: Cloudflare's edge injects
`static.cloudflareinsights.com/beacon.min.js` (Web Analytics is enabled on the zone) and the strict `script-src 'self'` blocks it.**
The application itself loaded and ran with zero violations. CI-06 (below) allows exactly that beacon, only on requests carrying
`CF-Ray`; until it lands, the zone's Web Analytics receives no page views. The `401` console entries are the anonymous
`/api/auth/me` probe, expected.

**Running source:** `1282e6c58013c484e361bae83dd2cfc3ff4cbdeb` (CI-04 content-state phone fixes and CI-05 dialog focus in WebKit;
the CI-04 run for `409152f` was cancelled by the concurrency rule and its content shipped here).
[Actions 36134202891](https://github.com/WeGreen-AI/Synwevo/actions/runs/36134202891) (created 12:19 UTC, finished ~12:41 UTC)
passed verification and deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T124027Z`. Anonymous checks ~12:50 UTC:
`/readyz` ready on all three hosts, the account shell's bundles 200.

**Running source:** `cee2da36126ea7193b876c039566b79481ff7765` (CI-06 Cloudflare-aware CSP, CI-08 WebKit parity proxy,
CI-09 phone toasts off the dialog footer; the run for `59aa880` was superseded).
[Actions 36136181121](https://github.com/WeGreen-AI/Synwevo/actions/runs/36136181121) (created 12:39 UTC, deploy finished
~13:01 UTC) passed verification and deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T130142Z`. Anonymous checks
~13:03 UTC: the portal's policy now reads `script-src 'self' https://static.cloudflareinsights.com` (the request carried `CF-Ray`);
Chromium at 1280 and 375 px loaded the public home, the signed-out workspace page, sign-in, register and the Docs shell with
**0 CSP violations** and the Cloudflare beacon tag present, so the zone's Web Analytics receives page views again.

**Running source:** `4147d3b5169639b98300337955e3d202af1649fb` (CI-10/CI-11 route-split account and Docs shells; the run for
`32726cd` was superseded). [Actions 36137305594](https://github.com/WeGreen-AI/Synwevo/actions/runs/36137305594) (created 12:50 UTC,
deploy finished ~13:23 UTC) passed verification and deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T132231Z`.
Anonymous Chromium at 375 px ~13:58 UTC: `/login` loads the entry, the shared and the runtime chunk only; `/register` adds its own
chunk; the Docs shell loads its entry plus the shared chunk; 0 CSP violations on all four pages.

**Running source:** `9578158b3c7e32545737234f95de682bda2e7aa4` (CI-12 language-composed device names and workspace naming, CI-13
top bar wrap, CI-14 SDK retry of the gate's rate limit, CI-16 primary-button hover colour; the runs for `6fe46b0` and `6e1faa2`
were superseded). [Actions 36140073009](https://github.com/WeGreen-AI/Synwevo/actions/runs/36140073009) passed verification and
deployment; pre-upgrade backup `/opt/synwevo/backups/20260925T134315Z`. Anonymous check: the production sign-in button under the
pointer renders white on `#1D4ED8` (CI-16 live).

**Running source:** `88dd2795741e3f6b4937eeaed02aa5f852bb23e1` (CI-17 phone fixes on a workspace with mappings, CI-18
whole-page target sizes; the run for `0727348` was superseded). [Actions 36142441078](https://github.com/WeGreen-AI/Synwevo/actions/runs/36142441078)
(created 13:40 UTC, verify and deploy both passed, finished 14:05 UTC); pre-upgrade backup `/opt/synwevo/backups/20260925T140436Z`.

**Running source:** `729d6945077db9ab2fc41e736e68784bdb7ca19a` (CI-21 risk-triggered human verification, Turnstile verified
server-side; the composed server's `malware_scanner` readiness component; the challenge test's sleep patch scoped to itself).
The first CI-21 run, 36158250147, was cancelled by the 45-minute job timeout — that test had replaced `time.sleep` for the rest of
the session and every later polling loop spun on the two-CPU verify container. Run 36163781043 for `729d694` passed verify and
deploy (created 16:54 UTC, deployed 17:11 UTC); pre-upgrade backup `/opt/synwevo/backups/20260925T171110Z`. Anonymous checks:
`/api/auth/state.challenge` is `null` (no Turnstile keys in the runtime), Docs `/readyz` now lists `malware_scanner: off`.

**Running source:** `a02baf0bd57706f9b9d9199862ce552fd7c03d19` (CI-22 platform inbox: schema v9 `notifications`/
`notification_deliveries`/`notification_preferences`, inbox routes in every role, the account service's e-mail dispatcher, bell
and preferences UI). Run 36166772633 passed verify (21 min) and deploy (created 17:23 UTC, deployed 17:45 UTC); pre-upgrade backup
`/opt/synwevo/backups/20260925T174518Z`. Anonymous checks: `/api/auth/notifications/unread-count` answers 401 (session required)
on the accounts and Docs origins, i.e. the routes are served by both roles; all three `/readyz` ready.

**Deployed (CI-23 organization custom roles, run 36169761487, 2026-09-25 18:13 UTC):** `{"deployed": "synwevo:07af1f08d6f2203318a88c9a1c7230d5e0c0d052", "backup": "/opt/synwevo/backups/20260925T181327Z"}`; verify and deploy jobs both succeeded (22 min). The paragraph below was written before the run landed.

**Deployed (CI-25 member import, run 36180128756, 2026-09-25 19:52 UTC):** `{"deployed": "synwevo:33f17c62db029e5a38fe6dca046ee1a6990d58b0", "backup": "/opt/synwevo/backups/20260925T195245Z"}`; verify and deploy succeeded. Live `/readyz` on the account origin now lists `malware_scanner` (reported `off` until the operator merges the scanner variables into the account service).

**Deployed (CI-27 project copy with target approval, run 36189260515, 2026-09-25 21:22 UTC):** `{"deployed": "synwevo:bcf8729f192956ed3dfb0cc9b9f2c969072929c0", "backup": "/opt/synwevo/backups/20260925T212138Z"}`; verify and deploy succeeded (20 minutes); schema v13 applied by the Docs role's Store on start.

**Deployed (CI-30 phone-width check and compact inbox toolbar, run 36198746759):** `{"deployed": "synwevo:2d77f08f4a3acbd0015c0518c7f8d1053bb7bbd5", "backup": "/opt/synwevo/backups/20260925T231552Z"}`; verify and deploy succeeded (the verify job took longer than usual, 40 minutes in the Docker step).

**Deployed (CI-31 Docs links on the account side, run 36200404143, 2026-09-25 23:36 UTC):** `{"deployed": "synwevo:0c73abbb9af4d213f302444b988b4cf3f432aed7", "backup": "/opt/synwevo/backups/20260925T233622Z"}`; verify and deploy succeeded. Notification mails and account-side bell items now link to the Docs address registered by the Docs container.

**Deployed (CI-32 Web Push, run 36201832754, 2026-09-25 23:57 UTC):** `{"deployed": "synwevo:c39b04abacc7d406ac11fd9c352c585ca42ec365", "backup": "/opt/synwevo/backups/20260925T235729Z"}`; verify and deploy succeeded; schema v15 applied on start; the VAPID key pair is created on the first push configuration request. Remaining manual step: enable push on a real device from the account page and press 发送测试.

**Pushed next (CI-47 table-template collaboration):** no schema change; browser saves of table templates from a stale version merge cell by cell (the machine API keeps its whole-document refusal); table templates get a presence-only room on the same `/api/collab/` prefix, so the pending nginx operator step covers it as well.

**Deployed (CI-57 no-account mode answers 403 accounts_disabled instead of a phantom sign-in, run 36304552064, 2026-09-27 08:19 UTC):** `{"deployed": "synwevo:e51ceb01ae871cca9d8f78b586256bb137f7cacb", "backup": "/opt/synwevo/backups/20260927T081842Z"}`; verify and deploy succeeded; no schema change. Production runs in session mode, so its behaviour is unchanged; this release also carries the records of the CI-53–56 deployments and the 9.16 matrix. The milestone regression sweep on the current build (shell in both engines, Docs desktop and mobile, messaging, project copy, member import, roles, onboarding, org templates, push, preflight, link cards, moderation, docs upgrades, template versions, phone copy, comments, component names) passed; details in verification.md §16.59.

**Deployed (CI-58 a sign-in without a return target lands on the platform, run 36313988930, 2026-09-27 11:19 UTC):** `{"deployed": "synwevo:ad2eeebdc535ead3adaf319dc374c728a1b21adc", "backup": "/opt/synwevo/backups/20260927T111919Z"}`; verify and deploy succeeded (created 10:53, finished 11:19 UTC); front-end only, no schema change. Read-only production check from the workstation: all three origins `readyz` ready; the account shell now serves `/account-assets/index-p6x56oPx.js` (carries the new strings) and `/account-assets/AuthFlow-9bRyBH2L.js` (carries the portal handoff), served with `cache-control: no-cache, must-revalidate` on the HTML so no client cache clearing is needed; `/api/auth/state` publishes `portal_url = https://synwevo.wegreen.ltd`. The behavioural check in production (sign in at `accounts.wegreen.ltd/login` with no target → `synwevo.wegreen.ltd/workspace`) needs a controlled production account, which this workstation does not hold; the account holder's next sign-in is that check. This release also carries `ad2eeeb`'s companion test fix (the credentials CLI test counts printed key lines, not the `dw_` substring).

**Deployed (CI-59 signing out ends on the signed-out page in every engine, run 36315903038, 2026-09-27 11:53 UTC):** `{"deployed": "synwevo:8184dc2a8b3356e1434636d8dd0df1737af15fef", "backup": "/opt/synwevo/backups/20260927T115350Z"}`; verify and deploy succeeded (created 11:29, finished 11:53 UTC); shared auth-client only, no schema change. Read-only production check: all three origins `readyz` ready; the account shell's shared source chunk now carries the "正在退出…" state. The behavioural check (sign out at `accounts.wegreen.ltd/account` in Chromium → `/logged-out`) needs a controlled production account; the account holder's next sign-out is that check.

**Deployed (CI-60 signing out of a relying party signs out of the platform, run 36318459107, 2026-09-27 12:40 UTC):** `{"deployed": "synwevo:33c3e1fe0d8225b375c6b64722415c92bca05b45", "backup": "/opt/synwevo/backups/20260927T123955Z"}`; verify and deploy succeeded (created 12:16, finished 12:40 UTC); shared auth-client, the Docs and portal shells and the core anonymous-path table; no schema change. Read-only production check: all three origins `readyz` ready; `docwave.wegreen.ltd/signed-out` and `synwevo.wegreen.ltd/signed-out` serve the shells (200); the Docs shared chunk `/assets/perm-Bl4dqY9f.js` carries the signed-out page and the platform sign-out call; an anonymous `POST /api/auth/sign-out?everywhere=true` on Docs answers `scope=platform` with the account service's `/oidc/logout` address (nothing revoked, the redirect was not followed). This release also carries the sealed-link test's deterministic flip positions. The behavioural check (sign out inside Docs → `docwave…/signed-out`, then the portal and account service no longer know the person) needs a controlled production account; the account holder's next sign-out from Docs is that check.

**Deployed (CI-61 the platform home has its own sign-out, run 36320325701, 2026-09-27 13:14 UTC):** `{"deployed": "synwevo:e90077497e58b4b058c056c28a2bf840ad7c852a", "backup": "/opt/synwevo/backups/20260927T131414Z"}`; verify and deploy succeeded (created 12:50, finished 13:14 UTC); portal shell only, no schema change. Read-only production check: all three origins `readyz` ready; the portal entry chunk `/portal-assets/index-DZ-_PBa1.js` carries the sign-out action and the signed-out route. The behavioural check (portal header / account menu 退出登录 → `synwevo.wegreen.ltd/signed-out`, then Docs and the account service no longer know the person) needs a controlled production account; the account holder's next sign-out from the portal is that check.

**Deployed (CI-62 "← Synwevo" returns to the workspace, run 36323605133, 2026-09-27 14:08 UTC):** `{"deployed": "synwevo:41e6f4f7d804ba8efb612a7f045c8dbf52d1ecf1", "backup": "/opt/synwevo/backups/20260927T140828Z"}`; verify and deploy succeeded (created 13:47, finished 14:08 UTC); shared auth-client and account shell only, no schema change. Read-only production check: all three origins `readyz` ready; the Docs shared chunk `/assets/perm-gjy2Lp-P.js` carries the renamed link (返回工作空间). The behavioural check (click ← Synwevo on `docwave.wegreen.ltd` → `synwevo.wegreen.ltd/workspace`) is the account holder's next click; the link is the one reported this morning.

**Deployed (CI-63 the workspace header has a 消息 entry, run 36328222361, 2026-09-27 15:28 UTC):** `{"deployed": "synwevo:0c43018950dddfd9cd017f02eff1ef8a281cced1", "backup": "/opt/synwevo/backups/20260927T152847Z"}`; verify and deploy succeeded; portal shell only, no schema change. Read-only production check: all three origins `readyz` 200 and `healthz` ok; the portal entry chunk `/portal-assets/index-DvQhO_-z.js` carries the unread-count call of the shared `MessagesLink`; an anonymous `GET synwevo.wegreen.ltd/api/auth/messaging/unread-count` answers 401 (the route is gated, and on this release still 404-fallback for a signed-in person on the portal origin — CI-64, in the next release, moves the handler into the shared session router); `/signed-out` serves the shell (200). The behavioural check (signed-in portal header shows 消息 with a real count) is verified in the isolated instance (verification.md §16.66) and becomes real in production with CI-64.

**Deployed (CI-64 the messages badge answers on every origin, CI-65 the account pages always have a way back, CI-67 domain migration repository side, run 36330242865, 2026-09-27 16:00 UTC):** `{"deployed": "synwevo:d2dde9951c557f4b552dad9f7d28a2a747601d6b", "backup": "/opt/synwevo/backups/20260927T160051Z"}`; verify and deploy succeeded (created 15:37, finished 16:00 UTC); shared session router, account shell, core anonymous-path table and the password service-term list; no schema change. The compose/nginx references of CI-67 are host-installed files and are **not** applied by this deployment: production still serves the `*.wegreen.ltd` hosts until the operator runs docs/platform/domain-migration.md §3. Read-only production check: all three origins `readyz` 200; the account shared chunk `/account-assets/src-C0w0NAIz.js` carries 返回工作空间 and the portal `/auth/start` handoff; an anonymous `GET /api/auth/messaging/unread-count` answers 401 on the portal and on Docs (the route now exists on every origin; a real count needs a signed-in person — verified in the isolated instance, verification.md §16.67). The behavioural check (account bar back link → `synwevo.wegreen.ltd/workspace`; portal header badge shows the real count) is the account holder's next visit.

**Deployed (CI-68 private-message mail is opt-in, CI-69 Docs copy, CI-70 passkey explanation, run 36332638476, 2026-09-27 16:37 UTC):** `{"deployed": "synwevo:f5fb5b14495fa259424189d40138ff6760aa6600", "backup": "/opt/synwevo/backups/20260927T163701Z"}`; verify and deploy succeeded; schema v22 → v23 (the conversation-mail reset found 0 preference rows). Read-only host check (masked, `mode=ro`): `schema_version` 23, 0 preference rows, 2 live passkeys bound to `accounts.wegreen.ltd`.

**Domain cutover (2026-09-27 16:39 UTC, host-side, docs/platform/domain-migration.md §3):** performed over the administrator channel right after the release above, from the host's own compose file with only the seven host-bound values changed (issuer, portal URL, RP ID, the three public/app URLs, the gateway health-check Host) and the repository gateway config (`nginx -t` passed in the gateway image on the host); the previous files are kept as `config/compose.before-domain-20260927T163826Z.yaml` and `config/nginx.before-domain-20260927T163826Z.conf`. `portal-web` and `docweave-web` were re-registered with both the old and the new callback / post-logout URIs and the new back-channel URI; the three roles and the gateway were recreated (`up -d --wait`, all healthy); `SYNWEVO_PUBLIC_HOSTS` set to `synwevo.com accounts.synwevo.com docwave.synwevo.com`. Checks through the tunnel: `readyz` 200 on the three new hosts; `synwevo.wegreen.ltd`, `accounts.wegreen.ltd`, `docwave.wegreen.ltd` answer 308 with path and query preserved (`/workspace?x=1`, `/projects/abc?tab=x`, `/login`); `workplace.synwevo.com/` → `/workspace`, other paths kept; OIDC discovery issuer `https://accounts.synwevo.com`; `/api/auth/state` on all three origins carries `portal_url https://synwevo.com`, the account URL on the new host and `passkeys_elsewhere: true`; an anonymous Docs sign-out answers the new account host and `docwave.synwevo.com/signed-out`; canonical and sitemap on synwevo.com. Anonymous browser (Chrome, 1280 and 390): the old `/workspace` lands on `synwevo.com/workspace`, 登录 opens the sign-in form on `accounts.synwevo.com`, Docs asks for sign-in on the new account host, no console errors beyond the signed-out probe. Not done: the compose file keeps the host's service set (no ClamAV service; that remains a separate operator decision — 4.5 GiB free on the host), `dw.wegreen.ltd` is not a tunnel hostname (TLS handshake fails at the edge, as before), `message.wegreen.ltd` has no tunnel hostname yet. The signed-in checks (workspace → Docs SSO → account bar back link, old passkey → explanation → password sign-in → new passkey) are the account holder's next visit; every browser is signed out by the host change.

**Deployed (CI-71 public pages, run 36334221149, 2026-09-27 17:06 UTC):** `{"deployed": "synwevo:569e1dfcaff14813d467ca71c4c06c1cdadc9b2e", "backup": "/opt/synwevo/backups/20260927T170633Z"}`; the first release whose health check ran against `SYNWEVO_PUBLIC_HOSTS` (the three synwevo.com hosts); verify and deploy succeeded. Read-only production check: `readyz` 200 on the three hosts; `synwevo.com/` serves 让工作，自然相连。, `/en` Work that comes together., `/zh-Hant/privacy` and `/service` their headings; sitemap on synwevo.com. Found: `/public/deployment.json` answered all-null (the runtime facts on the privacy/service pages read 无法读取) because the edge refuses the default `Python-urllib` User-Agent with 403 — CI-72 names the client; fixed in the next release.

**Deployed (CI-72 the deployment-facts reader names itself, run 36335854504, 2026-09-27 17:29 UTC):** `{"deployed": "synwevo:2a1043af2345ca0fb3eda6dd2732882d463b98b2", "backup": "/opt/synwevo/backups/20260927T172916Z"}`; verify and deploy succeeded. Read-only production check: `readyz` 200 on the three hosts; `synwevo.com/public/deployment.json` now carries real values (see the line recorded in verification.md §16.73 follow-up) instead of nulls.

**Deployed (CI-66 Synwevo Message as its own application, with the account-origin fallback, run 36337254408, 2026-09-27 17:57 UTC):** `{"deployed": "synwevo:c48f625cf51541fa0f398438faaaba6e69d0e5b3", "backup": "/opt/synwevo/backups/20260927T175745Z"}`; verify and deploy succeeded; the image carries `synwevo_messages` and the messages shell; no schema change from CI-66 itself. The host compose has **no** `messages` service yet (the tunnel hostname `message.wegreen.ltd` does not exist; it is dashboard-managed and only the account holder can add it), so the application is unregistered in production: read-only check — `readyz` 200 on the three hosts; `/api/auth/state` `messages_url` is null on the portal and the account service; `accounts.synwevo.com/messages` serves the messages shell (200, `messages-assets/`) against the legacy `/api/auth/messaging` routes (anonymous 401 — gated), so conversations keep working where they were; the portal home shows the Synwevo Message card. The go-live of the own origin is staged as a host script (scratchpad `messages-golive.sh`, mirrored in docs/platform/domain-migration.md §6) and runs once the hostname resolves through the tunnel.

**Deployed (records + CI-77 test-only change, run 36339110754, 2026-09-27 18:21 UTC):** `{"deployed": "synwevo:5f5d608adc920bed17aa3a12ad866ed282828f04", "backup": "/opt/synwevo/backups/20260927T182115Z"}`; verify and deploy succeeded; no product change (documentation and the collab change-log tests). `readyz` 200 on the three hosts.

**Deployed (CI-76 account shell copy, run 36343083810, 2026-09-27 19:29 UTC):** `{"deployed": "synwevo:9789b61ff9145ac75f2cb919a0514ea4d5d7583e", "backup": "/opt/synwevo/backups/20260927T192939Z"}`; verify and deploy succeeded; account shell only, no schema change. Read-only production check: `readyz` 200 on the three hosts; `accounts.synwevo.com/login` serves the new account bundle (`index-C2tPlFM8.js`).

**Deployed (CI-74 messaging capabilities + CI-75 Docs↔Message linkage, schema v25, run 36344727517, 2026-09-27 19:57 UTC):** `{"deployed": "synwevo:63638c681f2e9aefa92216b88139e0d6c52d670c", "backup": "/opt/synwevo/backups/20260927T195727Z"}`; verify and deploy succeeded. Read-only host check (masked, `mode=ro`): `schema_version` 25; the additive tables `messaging_settings`, `message_hides`, `messages_fts`, `messaging_outbox`, `object_conversations`, `message_cards`, `app_accounts` exist; 0 application accounts yet (the messages process, which creates them, is not running in production until the tunnel hostname exists). `readyz` 200 on the three hosts. The new routes live on the messages role; in production the account origin's legacy prefix serves the same router, so the fallback shell keeps working; the bridge worker and the Docs 在消息中讨论 control stay inactive until the application registers.

**Deployed (CI-73 Synwevo Message chat UI + microphone policy, run 36348079448, 2026-09-27 20:52 UTC):** `{"deployed": "synwevo:2de395d2dfa82e52f4a6e7b333e8bd49d6cc9537", "backup": "/opt/synwevo/backups/20260927T205238Z"}`; verify and deploy succeeded; no schema change. Read-only production check: `readyz` 200 on the three hosts; `accounts.synwevo.com/messages` serves the rebuilt shell (`messages-assets/index-VAWTTvil.js`) as the fallback while the application is unregistered; `Permissions-Policy` keeps `microphone=()` on the account service and Docs (the `microphone=(self)` exception belongs to the messages origin, which is not running in production yet — voice recording becomes possible with the `message.wegreen.ltd` go-live; until then the shell reports the refusal honestly).

**Deployed (records only, run 36349741798, 2026-09-27 21:17 UTC):** `{"deployed": "synwevo:ea57a1d3a67a5bc4f728c53a48fbc40e8e29342e", "backup": "/opt/synwevo/backups/20260927T211755Z"}`; verify and deploy succeeded; documentation only.

**Deployed (CI-79 unsaved-changes guard, run 36359665130, 2026-09-28 00:09 UTC):** `{"deployed": "synwevo:8ba4e29e0f705e5c0951a568799659433bcc03f2", "backup": "/opt/synwevo/backups/20260928T000944Z"}`; verify and deploy succeeded; account shell only, no schema change. Read-only production check: `readyz` 200 on the three hosts; walking the account bundle's imports (20 chunks) finds the leave handler in `account-assets/hooks-BetueTC8.js`.

**Deployed (CI-80 share summary, run 36372245098, 2026-09-28 03:30 UTC):** `{"deployed": "synwevo:4fc58e5e50b9c737de3b5a664f6633a864fa5b97", "backup": "/opt/synwevo/backups/20260928T033043Z"}`; verify and deploy succeeded; no schema change. Read-only production check: `readyz` 200 on the three hosts; `https://synwevo.com/share-card.png` 200 image/png; `https://synwevo.com/en` carries `og:title`, `og:url https://synwevo.com/en` and `og:image https://synwevo.com/share-card.png`.

**Deployed (CI-82 five-host support, run 36374085179, 2026-09-28 03:58 UTC):** `{"deployed": "synwevo:1919248dbe2df9214b267f6b160674b586573f38", "backup": "/opt/synwevo/backups/20260928T035855Z"}`; verify and deploy succeeded; no schema change.

**Round-10 host switch (2026-09-28 04:00 UTC, host-side):** the holder had added `message.synwevo.com` (and `workplace.synwevo.com`) to the tunnel (all five hosts → `http://127.0.0.1:18760`). Script `golive-r10.sh` from the host's own compose: `SYNWEVO_PORTAL_URL=https://workplace.synwevo.com`, new `SYNWEVO_SITE_URL=https://synwevo.com`, portal `APP_URL/PUBLIC_URL` → workplace, a `messages` service (`https://message.synwevo.com`, `messages-web`); the repository gateway config of 1919248 (`nginx -t` in the gateway image on the host); `portal-web` re-registered with workplace + synwevo.com + synwevo.wegreen.ltd callbacks (back-channel on workplace) and `messages-web` registered; host `receive.py` SERVICES += messages; previous files kept as `config/compose.before-r10-20260928T040008Z.yaml`, `config/nginx.before-r10-…conf`, `receive.before-r10-….py`. **Lesson:** the gateway mounts `nginx.conf` as a single file, so installing a new file (new inode) is invisible to a running container and `up -d` does not recreate an unchanged service — the first check showed the old routing; `up -d --force-recreate gateway` fixed it (the runbook now says so). Checks through the tunnel: `readyz` 200 on all five hosts; `/api/auth/state` on every origin names `portal_url https://workplace.synwevo.com`, `site_url https://synwevo.com`, `messages_url https://message.synwevo.com`; `synwevo.com/workspace` and `synwevo.wegreen.ltd/workspace` → 308 `workplace.synwevo.com/workspace`, `workplace.synwevo.com/en` → 308 `synwevo.com/en`, `accounts.synwevo.com/messages` → 303 the messages origin's sign-in start; the workplace root carries the workspace-surface marker and `noindex`; the messages origin answers `microphone=(self)`; registry: `messages` active at `https://message.synwevo.com/`. `SYNWEVO_PUBLIC_HOSTS` set to the five hosts. Anonymous browser pass (Chrome, 1280 and 390): site home renders the brand hero; workplace root shows the signed-out workspace with 登录/注册账户 and 返回首页 → site; accounts, Docs and Message send an anonymous visitor to the sign-in form on accounts.synwevo.com; `synwevo.wegreen.ltd/workspace` lands on the workplace host; 0 page errors. `message.wegreen.ltd` is not a tunnel hostname (the gateway would 308 it). Signed-in checks need a controlled production account (the holder's next visit).

**Deployed (CI-78 messaging completeness + CI-81 export and hand-over, run 36376271734, 2026-09-28 04:32 UTC):** `{"deployed": "synwevo:5d27528f9dcfe2d2bb538858cb581fc9e9a4865b", "backup": "/opt/synwevo/backups/20260928T043146Z"}`; verify and deploy succeeded. Read-only check on the host: `meta.schema_version` = 26, tables `conversation_avatars` and `message_blocks` present; `readyz` 200 on all five hosts, the messages origin reports its messaging counters. Per-origin sign-in check through the tunnel (anonymous): `workplace`, `docwave` and `message` each start sign-in with their own `/auth/callback`, PKCE, and a host-only `dw_authreq` cookie (`HttpOnly; Secure; SameSite=lax`, no `Domain`); `synwevo.com/auth/start` → 308 workplace; the account service answers 400 to a foreign `redirect_uri`; no origin returns `Access-Control-Allow-*` to an unknown `Origin`. Signed-in checks of the new messaging features (mentions, blocking, group pictures, export format 2) need a controlled production account (the holder).

**Deployed (CI-83 refusal wording + CI-84 desensitize scale, run 36378434109, 2026-09-28 05:02 UTC):** `{"deployed": "synwevo:4b9d161f4610d90b50a5f450926650a801d7df51", "backup": "/opt/synwevo/backups/20260928T050226Z"}`; verify and deploy succeeded; no schema change.

**Production malware scanning (CI-87, 2026-09-28 05:11 UTC, host-side):** available memory 4 402 MiB before the change (runbook floor 2 GiB). Two trials of the reference `clamav` service on the host (not on Synwevo's network) found it could not start as written: with every capability dropped the entrypoint exits at `chown /var/lib/clamav`; with CHOWN/SETUID/SETGID freshclam cannot open its log (root without DAC_OVERRIDE, file owned by clamav) and clamd never opens its socket; with CHOWN, SETUID, SETGID and DAC_OVERRIDE it starts (~950 MiB, ClamAV 1.4.6, database 28136 of 2026-09-27). Script `golive-clamav.sh` on the host's own compose: previous file kept as `config/compose.before-clamav-20260928T051134Z.yaml`; `clamav` service added (those four capabilities, no extra init, 2048m limit, `/opt/synwevo/clamav` for the database); `SYNWEVO_CLAMD_ADDRESS=tcp://clamav:3310` and `SYNWEVO_MALWARE_SCAN=required` for accounts, docs and messages; clamav healthy after ~30 s, then the three roles recreated (healthy). Checks: `/readyz` `malware_scanner: ok` on accounts, docwave and message (the site and workplace hosts accept no files and report none); inside docs, the scanner client flags the standard EICAR test string `infected` (`Eicar-Test-Signature`) and a plain CSV `clean`. Files stored before this point carry `scan.status not_configured` and remain readable; rescanning them is the next batch (docs/security/file-acceptance.md §8). Rollback: restore the kept compose and `up -d accounts docs messages`.

**Incident and fix (CI-89, 2026-09-28 05:12–05:22 UTC):** the scanner switch recreated accounts, docs and messages; the gateway had resolved the service names once at its start (05:02, during the 4b9d161 deploy) and kept sending each host to the old addresses — found while probing the collaboration socket: an ordinary GET on `docwave.synwevo.com/api/collab/…` was answered by the messages process ("该接口不在消息应用上"). Deploys never hit this because `receive.py` force-recreates the gateway after the roles; the manual script did not. Contained at ~05:22 by recreating the gateway (each host's `/api/auth/state` then named its own role). Every process checks sessions and its role gate itself, so misrouted requests were refused, not served across users. Fix: `infra/production/nginx.conf` resolves upstreams per request through Docker's DNS (`resolver 127.0.0.11 valid=5s`, `proxy_pass $<role>_upstream`); the host's file was identical to the previous repository version, kept as `config/nginx.before-ci89-20260928T052501Z.conf`, new file `nginx -t` in the gateway image, installed, gateway recreated 05:25. Checks through the tunnel: five hosts answer with their own role, old-host 308s unchanged, the messages stream answers 401 JSON from messages, the collaboration handshake reaches Docs (403 before accept, as for any anonymous socket). Local proof: throwaway containers where docs and messages swap addresses — the new gateway follows within 5 s without a restart and answers 502 for a role that is down while the others keep working.

**Deployed (CI-87 reference compose, CI-88 public Message facts, state files; run 36381481013, 2026-09-28 05:47 UTC):** `{"deployed": "synwevo:4bf699a23ccbd03d1a80ea5aa73f849375b7923a", "backup": "/opt/synwevo/backups/20260928T054646Z"}`; all five hosts answer with their own role afterwards (the deploy recreates the gateway; CI-89 resolution in place).

**Deployed (CI-85 account refusals, CI-86 Docs refusals, CI-89 gateway file, CI-91 home FAQ, audit cap; run 36383152410, 2026-09-28):** `{"deployed": "synwevo:4e752b4611692e7f2db045514fc605c36e1803cd", "backup": "/opt/synwevo/backups/20260928T061433Z"}`; five hosts answer with their own role; anonymous Chrome on https://synwevo.com (English and 简体, 1280 and 390): the FAQ has its eight questions, the Message presence sentence is the corrected one, the old claim is gone; 0 page errors. Signed-in checks of the account and Docs refusal wording need a controlled production account.

**Deployed (CI-92 mail preview and logo, CI-93 tab sync, CI-94 role copy, record cleanup; run 36384935792, 2026-09-28 06:41 UTC):** `{"deployed": "synwevo:4597ae6a52488a37fa9bfe3d1018cb973e2e492d", "backup": "/opt/synwevo/backups/20260928T064102Z"}`; five hosts answer with their own role; `POST …/roles/{rid}/copy` answers 401 anonymously (the route is live). The container serves the re-cut 373x84 mail logo, but Cloudflare kept the old 360x83 copy (`cf-cache-status: HIT`, `max-age=86400`, age ~7 h), so mails sent until it expires would have drawn the old file in the new 124x28 box; the follow-up versions the logo address by the file digest (`?v=`) so new mails fetch the new bytes while old mails keep theirs (next release).

**Deployed (CI-95 TODAY()/NOW() in the submitter's time zone, records; run 36386879486, 2026-09-28):** `{"deployed": "synwevo:9704e10130014c15dfb72ff8fe678b4dd6db59e2", "backup": "/opt/synwevo/backups/20260928T070813Z"}`; five hosts answer with their own role; the preview route takes `tz` (401 anonymously). No schema change: jobs submitted before this keep the process clock (`as_of` absent).

**Deployed (CI-96 member-import clean-up, off by default; mail logo address versioned; run 36389628592):** `{"deployed": "synwevo:170dac42719de0aaf0d45f91391abbf438b5f3a6", "backup": "/opt/synwevo/backups/20260928T073459Z"}`; `/api/auth/state` on accounts reports `member_import_cleanup: false`; five hosts answer with their own role.

**Deployed (CI-97 application switcher, CI-95 record, checkpoint; run 36391285672):** `{"deployed": "synwevo:dc884f6fbc1a92672ca29e7fec1cb770deec3493", "backup": "/opt/synwevo/backups/20260928T080142Z"}`; five hosts answer with their own role and `/api/auth/apps` answers 401 anonymously on each. Last deployment of the 2026-09-28 continuous-iteration session (stopped at the user's request); CI-90 rescan not merged, no rescan run in production.

**Found and fixed (CI-98, 2026-09-28 08:04 UTC, host-side):** the final check showed `message.synwevo.com/api/auth/apps` answering 404 from an old process: `synwevo-messages-1` still ran `synwevo:4b9d161…` while every other role ran `dc884f6`. The deploy key's forced command runs `/opt/synwevo/bin/receive.py`, whose `SERVICES` never listed `messages`; the round-10 switch had edited `/opt/synwevo/receive.py`, a different copy that no deploy runs. So since the Message service started (04:00 UTC) it only changed when a host-side step recreated it (the ClamAV switch at 05:11 left it on 4b9d161), and the Message-side parts of CI-78 (until 05:11), CI-93 and CI-97 were not live on message.synwevo.com until now. Fixed: `bin/receive.py` `SERVICES` += `messages` (previous file kept as `bin/receive.before-messages-20260928T080356Z.py`, syntax checked), `up -d --wait messages` → `dc884f6`; through the tunnel all five hosts answer with their own role, `/api/auth/apps` 401, `readyz` ready, scanner ok on accounts/docwave/message. Every later deploy now recreates Synwevo Message with the others.

**Deploy readiness for the waiting batch (aadcb9e → main, CI-104…CI-125; checked 2026-09-29 on 06b218a):**
* *Storage:* the only schema change is two nullable columns, `collab_docs.epoch` (CI-115) and `collab_docs.model` (live tables),
  added by the store's guarded `ALTER TABLE … ADD COLUMN` at start-up; no data rewrite, older code ignores them, so an application
  rollback needs no data restore. The workflow's pre-deploy backup still runs.
* *Host step (CI-125):* mount the ClamAV config with `AlertEncryptedArchive yes` as in `infra/production/compose.yaml` /
  `docs/operations.md` §8 (the host's `/opt/synwevo/config/compose.yaml` is the operator's file — merge, don't copy), recreate
  `clamav`, then `docker compose exec -T clamav clamconf -n </dev/null | grep -i alertencrypted`. Without it encrypted RAR/7z that the
  detector cannot classify still rely on the engine; everything else in CI-125 works without the mount.
* *Leave off:* `SYNWEVO_SHEET_LIVE` stays unset (live tables need the trial switch and a named trial workspace first).
* *Behaviour users will notice:* XLSX detail rows — running totals like `SUM($B$5:B5)` now fill down like Excel (CI-123); COUNTA counts
  formulas returning ""; password-protected uploads are refused with a sentence (CI-125); PPTX tables can continue on copies of their
  slide (CI-120); document-editor drafts are written at most 5 s after the first unwritten change.
* *After the deploy:* compare every role's image with `docker ps` (all five on the new commit), `/readyz` on the five hosts, each
  host's `/api/auth/state` role, then with a test account: open a document template in two tabs (live editing still works), upload a
  password-protected ZIP as a Message attachment in a test group (refused with the sentence), and generate one XLSX with a detail row.

**Host-side data change — legacy data claim undone (2026-09-29, at the account holder's request):** the platform administrator liyanpei2004@outlook.com (u_3f48e19e364e) had used 「指派历史数据」 in the admin overview to claim the legacy workspace `default` (3 projects, 5 templates) for their own account; the overview card then disappeared by design, and they asked for it back. Read-only first (the claim's audit line, the workspace, its one owner membership, the Docs enablement and grant, all stamped 1790684375.454307 by that account; two sessions, one OAuth code and one Docs token issued after it; jobs, previews and API keys older), then one guarded transaction in `synwevo-accounts-1` (aborts unless every row is exactly as read): the membership, the Docs entitlement and the Docs enablement created by the claim removed; the workspace back to 「历史数据（待认领）」 / `pending_claim`; that account's Docs token for `default` revoked (`membership_removed`, as leaving a workspace does); an `admin.workspace.unclaim` audit event (actor `system`, meta with requester, what it reverts and the reason). Sessions were left alone: membership is re-checked on every request and a session pointing at a workspace the person left is cleared. Verified read-only: legacy `pending_claim`, 3 projects and 5 templates intact, the account's personal and two organization workspaces unchanged. Before/after rows are in the session scratchpad `prod_unclaim_output.txt`; re-applying the claim from the admin overview restores the previous state. No other data touched.

**Housekeeping (same day):** a diagnostic container `blissful_neumann` (clamav/clamav:1.4, `--rm`) left running since 2026-09-28 05:00 by an earlier scanner-image inspection (its `grep -r /` never finished) was stopped; the six Synwevo containers were not touched.

**Security fix waiting for the billing block (CI-118, 2026-09-29):** an open live-editing room could be joined from another workspace by someone who knew the template id (read and write). Fixed on `main` (8e0d529) and verified; production still runs aadcb9e and keeps the hole until a deploy runs. Nothing was deployed by hand (the release path is the workflow). **Re-running the newest `main` run is the fastest way to close it once billing is fixed.** Newest refused run: **36513461278** (ffc2442, 2026-09-29: CI-118…CI-124, the XLSX and live-table merges; pre-push 2617 passed, 1 skipped) — neither job started (billing); production still aadcb9e. Earlier refused: 36504737000 (b15f7d1), 36487070888 (c9ec717).

**Security fix not yet deployed — password-protected files (requirement §8.7, 2026-09-29):** ClamAV answers `OK` for what it cannot decrypt, so an encrypted ZIP/7z/RAR, Office document or PDF was accepted as clean; a Synwevo Message attachment of that kind could be downloaded by other members. Fixed on a local branch (not pushed): such uploads are not accepted (`encrypted_file`; a file whose structure cannot be checked within the detector's bounds is refused the same way), rescans report historical encrypted files without changing their access (docs/security/file-acceptance.md §3.1, §8.1); the independent security review's findings on the first version (crafted layouts accepted, an upload able to stall the event loop, structure-protected XLS refused) are fixed with regression tests (§7.4). Production keeps the gap until a deploy runs. **Host-side step after that deploy** (nothing on the host was touched): create `/opt/synwevo/config/clamd.conf` from the `clamav/clamav:1.4` image's own file plus `AlertEncryptedArchive yes`, merge the new read-only mount of the reference compose, recreate `clamav` — docs/operations.md §8. The applications detect top-level encrypted files without it; the option adds encrypted archives nested inside documents and archives.

**Not deployed — GitHub Actions billing (CI-104, run 36429047601, 2026-09-28 13:53 UTC):** verify succeeded (ecdbf87 = CI-104 nested details + records); the deploy job was **not started** by GitHub: "The job was not started because recent account payments have failed or your spending limit needs to be increased" (annotation on job 108959850520, no steps, no logs). Production is unchanged: every role still runs aadcb9e, the five hosts answer `/readyz` 200. Nothing was deployed by hand: the release path is the workflow, and billing is the owner's to settle. **To resume:** fix *Billing & plans* for the organization, then re-run the newest `main` run (it deploys everything pushed up to then); afterwards verify the five hosts and record it here. Later pushes were refused the same way: run 36432359515 (89fefc1, CI-105) and run 36437452354 (51f59f3, CI-106…108) — **re-run 36437452354** (or whichever `main` run is newest).

**Deployed (CI-102 template history wording, CI-103 nested-repeat guard, run 36421027638, 2026-09-28 12:47 UTC):** `{"deployed": "synwevo:aadcb9e839c3c13386f1831a942e8449852dab15", "backup": "/opt/synwevo/backups/20260928T124718Z"}` (aadcb9e = the CI-101 record on top of 24abe53 CI-102, d134e10 checkpoint, 6deddd3 CI-103 and af384f0 the nested-details design); verify and deploy succeeded. Every role (accounts, docs, portal, messages) runs aadcb9e; the five hosts answer `/readyz` 200 with their own shells; the Docs bundle served on docwave.synwevo.com carries the CI-102 history wording (「在编辑器之外保存了 {v1}（替换了 {v2}…」) and the CI-103 refusal; `/api/projects` 401 anonymously. No template is edited in production for this (no controlled workspace there).

**Deployed (CI-101 organization audit, run 36417841203, 2026-09-28 12:15 UTC):** `{"deployed": "synwevo:f96aa0bea58aa95dd84c208c8a516f0fde4a3979", "backup": "/opt/synwevo/backups/20260928T121517Z"}` (f96aa0b = the CI-100 record on top of 77a579a, CI-101; the schema gains the indexes `ix_audit_object` and `ix_audit_action`, created on start); verify and deploy succeeded. Every role (accounts, docs, portal, messages) runs f96aa0b; the five hosts answer `/readyz` 200 with their own shells; the account shell's `Organizations` chunk served on accounts.synwevo.com carries the 组织审计 section (`org.audit.read`, `/audit?limit=50`); `GET /api/auth/organizations/{oid}/audit` answers 401 anonymously. Signed-in reading is not run in production (no controlled organization there); the browser proof is on the isolated instance (issues.md CI-101).

**Deployed (CI-100 OCR rows checked by a person, run 36413915020, 2026-09-28 11:36 UTC):** `{"deployed": "synwevo:af1dfa3bad173c184f4fdd8b0ccea3b9d90c4d5e", "backup": "/opt/synwevo/backups/20260928T113611Z"}` (af1dfa3 = the CI-99 record on top of 39fa58d, CI-100); verify and deploy succeeded. Every role runs af1dfa3; the five hosts answer `/readyz` 200 with their own shells; the account shell's `Organizations` chunk served on accounts.synwevo.com carries `ocr_unchecked`. No member import is run in production for this (no controlled organization there); the browser proof is on the isolated instance (issues.md CI-100).

**Deployed (CI-99 @所有人 and own-action system lines, run 36410877290, 2026-09-28 11:05 UTC):** `{"deployed": "synwevo:3dc3d6287c3449323111c8e7f7d4c4ef41841401", "backup": "/opt/synwevo/backups/20260928T110530Z"}`; verify and deploy succeeded. Every role runs 3dc3d62 (messages included); through the tunnel the five hosts answer `/readyz` 200 with their own shell titles; the Message bundle served on message.synwevo.com carries `can_mention_all`. Signed-in checks of @所有人 are not run in production (no controlled account there); the browser proof is on the isolated instance (issues.md CI-99).

**Deployed (CI-90 rescan and quarantine, run 36405360733, 2026-09-28 10:12 UTC):** `{"deployed": "synwevo:bcfa9c7dca7678cf3add6000e9b3d3f69563943b", "backup": "/opt/synwevo/backups/20260928T101200Z"}`; verify and deploy succeeded. Every role (accounts, docs, portal, messages) runs bcfa9c7; through the tunnel each of the five hosts serves its own shell (site and workplace: portal, accounts: account, docwave: Docs, message: Message), `/readyz` ready, `malware_scanner` ok on accounts/docwave/message, `/api/auth/apps` 401 anonymously. **Production rescan (10:15–10:20 UTC):** `docker compose exec -T docs python -m synwevo_docs.cli rescan` (preview) — sources 3 contents / 5 rows (all `unscanned`), templates 5 / 31 distinct contents, no organization publications, no regression cases, scanner ClamAV 1.4.6/28136; `messages … rescan` (preview) — 1 attachment (`not_configured`). `rescan --apply` in docs (exit 0: 3 sources and 5 templates — 34 contents counted per template — clean), then in messages (exit 0: 1 attachment clean); a second docs `--apply` found nothing to rescan; `quarantine list`: empty. No row was rejected and nobody was notified. Only ids, digests and counts were printed.

**Deployed (CI-55 bounded frames and draft recovery + CI-56 setting before/after, run 36255489490, 2026-09-26 16:51 UTC):** `{"deployed": "synwevo:1d15665ee420939f562aad54eb4c3f023a569d3a", "backup": "/opt/synwevo/backups/20260926T165059Z"}`; verify and deploy succeeded; no schema change. This is the last release of the round-8 collaboration batches (CI-45…56); the handoff lists the conditions that reopen the queue.

**Deployed (CI-53 change-log diffs with schema v22 + the 9.16 matrix + CI-54 version dialog, run 36253921338, 2026-09-26 16:25 UTC):** `{"deployed": "synwevo:11202c33be2a53ca202eefb04bd8fb2ceea29073", "backup": "/opt/synwevo/backups/20260926T162519Z"}`; verify and deploy succeeded; the v22 migration (one additive column, `collab_changes.before`) runs at start and the deploy job's readiness checks passed. Rollback to a v21 release leaves the column unused.

**Deployed (CI-50 traceability + CI-51 restart resilience + CI-52 named checkpoints, run 36252593410, 2026-09-26 15:59 UTC):** `{"deployed": "synwevo:b46a403f621442ca99c22260625b4b569a2c2457", "backup": "/opt/synwevo/backups/20260926T155850Z"}`; verify (containerised suite included) and deploy succeeded; no schema change in these three (v22 follows with CI-53). Comments now travel through the project room and into the activity feed; the activity drawer filters, marks what is new since the last look and jumps to objects; a save may name its version; a process restart mid-edit loses nothing.

**Deployed (CI-49 version operations never discard unsaved live edits unasked, run 36250980928, 2026-09-26 15:33 UTC):** `{"deployed": "synwevo:ba539af5578591ecc656aafb1364029319117830", "backup": "/opt/synwevo/backups/20260926T153321Z"}`; verify (containerised suite included) and deploy succeeded; no schema change. Restore, undo and redo now answer 409 `live_edits` while the editing room holds unsaved edits unless `discard_live` is given; the project page's version dialog names who is there before asking; phones show at most two toasts.

**Deployed (CI-48 process shutdown + CI-47 table-template collaboration, run 36249289199, 2026-09-26 15:00 UTC):** `{"deployed": "synwevo:cb2090e11aca545551a029d63b5a31cc0439a563", "backup": "/opt/synwevo/backups/20260926T150028Z"}`; the verify job's containerised suite — the step that hit the 45-minute limit in run 36236626210 — completed, deploy succeeded, and the job's public readiness checks answered `ready` on all three hosts (accounts: database/storage/worker ok, malware scanner off; portal and docs likewise). This release carries the CI-47 code (`a0c3f18`) that never reached production. Local post-deploy probes from this workstation reached `accounts.wegreen.ltd/readyz` (ready) but the sandbox proxy refused `portal.` and `docs.` (HTTP 000 within 0.2 s), so the anonymous WebSocket-upgrade probe of the gateway could not be repeated from here; the nginx `Upgrade` blocks for `/api/collab/` remain the recorded operator step (CI-45).

**Not deployed (CI-47 table-template collaboration, `a0c3f18`, run 36236626210, 2026-09-26 10:43–11:29 UTC):** the verify job's "Verify the deployment runtime" step was cancelled at the 45-minute job limit; the containerised suite (`--memory 3g`) slowed ten-fold from 81 % and stalled at 92 % with no failing test. Root cause (verification.md §16.49): every test's application left its worker threads — and through them its store — alive, so memory grew with the number of tests until the runner thrashed (a local run with the same flags was OOM-killed at 34 %). The deploy job did not run; production stayed at `e6197c1` (CI-46). CI-48 fixes the process shutdown path (worker registry, lifespan shutdown hooks, per-test teardown, `--init` on the CI containers) and carries CI-47 into production.

**Deployed (CI-46 project-page collaboration, run 36219721565, 2026-09-26 05:30 UTC):** `{"deployed": "synwevo:e6197c18189b42d123913b86af32f44fd32a068b", "backup": "/opt/synwevo/backups/20260926T052959Z"}`; verify and deploy succeeded; schema v21 applied on start; `/readyz` ready afterwards. Mapping saves from a stale revision now merge per mapping on production (lists without ids and empty lists keep the whole-list refusal). The live project room shares the `/api/collab/` prefix with CI-45, so the same **pending operator step** applies: until `/opt/synwevo/config/nginx.conf` carries the upgrade blocks from `infra/production/nginx.conf` and nginx is reloaded, the project page shows 「连接已断开，重试中」 and refreshes on its own actions only; saves, merges and the activity list work regardless. The round was paused here at the user's request.

**Deployed (CI-45 Docs live co-editing of document templates, run 36218166471, 2026-09-26 05:00 UTC):** `{"deployed": "synwevo:c2d78d06ff01dacb8d9196c60bf5512496c59a64", "backup": "/opt/synwevo/backups/20260926T050048Z"}`; verify and deploy succeeded; schema v20 applied on start; `pycrdt` and `websockets` are in the image. Anonymous read-only check ~05:05 UTC: `/readyz` ready; an upgrade-shaped GET to `/api/collab/templates/…` and `/api/collab/projects/…` answers 401 JSON from the session gate (the route is not reachable anonymously). **Still pending on the host:** `/opt/synwevo/config/nginx.conf` is hand-managed and does not yet carry the `map $http_upgrade` / `location /api/collab/` blocks from `infra/production/nginx.conf`; until an operator copies them in and reloads nginx, a browser's WebSocket handshake does not reach uvicorn as an upgrade, the editor shows 「无法连接实时协同」 after ten seconds and works alone on the saved version (proven in the browser), and the project page shows 「连接已断开，重试中」 while everything else works. No signed-in check was made on production.

**Deployed (CI-44 system-mail design system, run 36214183403, 2026-09-26 03:39 UTC):** `{"deployed": "synwevo:e04ff013a6cb8c6eb605d935fdd5be33cdb3302a", "backup": "/opt/synwevo/backups/20260926T033927Z"}`; verify and deploy succeeded; schema v19 (`mail_outbox.html`) applied on start. Anonymous check ~04:20 UTC: `https://accounts.wegreen.ltd/brand/email-logo.png` answers 200 `image/png` (24 872 B), so mails sent from production now carry the confirmed logo; the first real mails on the new layout are whatever the platform sends next (verification, invitation, notification digests) — no test mail was sent to a real inbox from this session (no authorised test inbox).

**Pushed next (CI-43 logo launch):** the confirmed logo in all three shells (fingerprinted PNG icons, `/favicon.ico` with `no-cache`, top bars and footers), `scripts/brand_assets.py --check` added to the verify job; no backend schema change (one response-header change for root files).

**Pushed next (CI-42 machine API for template usage and restore):** two `/api/v1` operations, OpenAPI and coverage manifest regenerated, SDK/CLI additions; no schema change.

**Deployed (CI-41 template version restore, CI-42 machine API/SDK/CLI parity for usage and restore, CI-43 confirmed-logo launch, run 36212722189, 2026-09-26 03:07 UTC):** `{"deployed": "synwevo:30a5cfdc2a996e1763a09107ed4511864680d7ba", "backup": "/opt/synwevo/backups/20260926T030742Z"}`; verify (including the new `brand_assets.py --check` step) and deploy succeeded. Anonymous read-only checks ~03:10 UTC: `/favicon.ico` answers 200 `image/vnd.microsoft.icon` (5336 B, `Cache-Control: no-cache`) on all three hosts; each shell's HTML links fingerprinted `icon-16`, `icon-32` and `apple-touch-icon` PNGs and all of them answer 200 `image/png` (Docs under `/assets/`, the portal under `/portal-assets/`, the account service under `/account-assets/`). The signed-in top bars and footers were proven on the isolated instance (verification.md §16.44); no production account was signed in for screenshots. `/brand/email-logo.png` still falls through to the SPA shell on production until CI-44 lands.

**Deployed (CI-38 SDK acceptance person leg, CI-39 preflight fix path, CI-40 comments refresh and deep-link notice, run 36211498761, 2026-09-26 02:45 UTC):** `{"deployed": "synwevo:efd3259ab1fe9bbf7221de79af80ced44abfa834", "backup": "/opt/synwevo/backups/20260926T024442Z"}`; verify and deploy succeeded; no schema change.

**Deployed (CI-37 value-semantics kit and XLSX verifier fix, run 36210142096, 2026-09-26 02:21 UTC):** `{"deployed": "synwevo:d30d9f4869e69aa80a77bb0a783b7f29ef2ebfb5", "backup": "/opt/synwevo/backups/20260926T022042Z"}`; verify and deploy succeeded; no schema change.

**Deployed (CI-36 machine API for shared templates, run 36208724682, 2026-09-26 01:56 UTC):** `{"deployed": "synwevo:0f1c8100024d0f4c878c0333dc55f582788161a5", "backup": "/opt/synwevo/backups/20260926T015619Z"}`; verify and deploy succeeded; no schema change.

**Deployed (CI-35 organization-shared templates, run 36207199003, 2026-09-26 01:29 UTC):** `{"deployed": "synwevo:81f113fbb102a69e578ea92d573dda291bb4c208", "backup": "/opt/synwevo/backups/20260926T012909Z"}`; verify and deploy succeeded; schema v18 applied on start.

**Deployed (CI-34 group announcements and message reports, run 36205658531, 2026-09-26 01:03 UTC):** `{"deployed": "synwevo:48996f3a54d1863c3f0c6421392b4460959df1cf", "backup": "/opt/synwevo/backups/20260926T010332Z"}`; verify and deploy succeeded; schema v17 applied on start.

**Deployed (CI-33 project comments and mentions, run 36203384969, 2026-09-26 00:28 UTC):** `{"deployed": "synwevo:89c706b62fe16340ce044fcc1497e996925932cd", "backup": "/opt/synwevo/backups/20260926T002750Z"}`; verify and deploy succeeded; schema v16 applied on start.

**Pushed next (CI-32 Web Push):** schema v15 adds `push_keys`, `push_subscriptions`, `push_queue` (additive, no migration); two new Python dependencies pinned in the lock (`py-vapid`, `http-ece`, both `cryptography`-only); the account service now serves `/sw.js` and `/manifest.webmanifest`; the dispatcher gains a push pass. The VAPID key pair is generated on first use and stored in the database; `SYNWEVO_PUSH=off` disables the channel. Rollback to v14 code is safe (tables ignored).

**Pushed next (CI-31 Docs links on the account side):** notification links to Docs objects now come from the app registry's Docs address in every role (the account service's own `SYNWEVO_APP_URL` produced `accounts…/p/<id>` links in mails); new `GET /api/auth/messaging/link-card` and message cards. No schema change.

**Pushed next (CI-30 phone-width check of the copy surfaces):** one shared stylesheet change (compact inbox toolbar inside drawers) plus a new browser proof; no schema or API change.

**Deployed (CI-29 Docs edit safety and outcome notifications, run 36196271384, 2026-09-25 22:44 UTC):** `{"deployed": "synwevo:cd90aa3d3b6c47e2de50153fa1ec26b2bc87c69d", "backup": "/opt/synwevo/backups/20260925T224359Z"}`; verify and deploy succeeded in 23 minutes; schema v14 applied on start; all three public origins report ready afterwards.

**Pushed next (CI-29 Docs edit safety and outcome notifications):** schema v14 adds `projects.settings_rev` and `source_files.created_by` (additive, defaults keep old behaviour); `PATCH /api/projects/{pid}` gains the optional `base_settings_rev` (409 `stale_project` on mismatch), the Docs role now writes `docs.job_finished` / `docs.file_rejected` inbox rows, and project details carry `copied_from`. Rollback to v13 code is safe.

**Deployed (CI-28 component names, run 36191490199, 2026-09-25 21:48 UTC):** `{"deployed": "synwevo:8ac8e5508dd90ea517fb187c4ca7f3e79e0adf66", "backup": "/opt/synwevo/backups/20260925T214811Z"}`; verify (with the new `check_component_names.py` step) and deploy succeeded in 23 minutes.

**Pushed next (CI-28 component names):** text and output-metadata changes only, no schema change; the job verification `engine` field now carries a format family (`docx/xlsx/pdf/pptx/csv/image`) instead of a library name; converted PDF outputs are stamped `Producer/Creator = Synwevo Docs` in the full-mode PDF engine (production runs the full-mode engine). New guard `scripts/check_component_names.py` runs in the verify job.

**Pushed next (CI-27 project copy with target approval):** schema v13 adds one table (`project_copies`), no data migration; the Docs role starts a copy runner thread (10 s tick) and the account role gains `/api/auth/organizations/{oid}/approvals*`; five inbox kinds. Rollback to a v12 build is safe (the table is ignored; created copies are ordinary projects). Verified before push: 2057 backend tests (three suites, 0 failed), four guards, typecheck, build, front-end tests, infra tests, and the two-engine browser proof on the isolated instance.

**Deployed (CI-26 messaging, run 36186406037 for the build fix 7d4d567, 2026-09-25 20:55 UTC):** `{"deployed": "synwevo:7d4d567dc0efc125ce986db95f8304c8d2d25bea", "backup": "/opt/synwevo/backups/20260925T205442Z"}`; verify and deploy both succeeded. The CI-26 commit's own run (36183366185) failed in verify: the account shell's `main.tsx` had been staged whole and carried a teammate's in-progress hunk that imports `PageState` from `@synwevo/ui`, which exists only in their uncommitted work, so `tsc` failed and deploy was skipped (production stayed at CI-25 for 52 minutes). 7d4d567 changed the committed file only (three lines back to `div.empty`), leaving the working tree untouched; the fix was checked before pushing by exporting the committed tree with node_modules that resolve `@synwevo/*` to the export and running typecheck/build/tests there. Lesson recorded in the pre-push checklist: verify against the committed tree, not the working tree, when shared files are staged by hunk.

**Pushed next (CI-26 messaging):** schema v11 + v12 are additive; the account role now reports `malware_scanner` on `/readyz` and the repository compose gives it the scanner address (operator merges it); messaging adds `/messages` on the account origin and a server-sent-events stream that the gateway passes as-is (buffering off, 300 s read timeout, 25 s heartbeat). Recorded here with run and backup when the receiver reports it.

**Deployed (CI-24 institutional mailbox registration, 2026-09-25 19:01 UTC):** `{"deployed": "synwevo:544f6074c47af9e1cbb474cbb7270752090354c3", "backup": "/opt/synwevo/backups/20260925T190122Z"}`; verify and deploy jobs succeeded. The paragraph below was written before the run landed.

**Pushed next (CI-24 institutional mailbox registration):** no schema change; `valid_email` no longer raises on malformed domains, account mail bodies are quoted-printable, `/api/auth/state` publishes `mail_sender`, the verify-email screen carries sender/gateway/resend guidance in three languages, and registering an address that already has an active account mails an "account already exists" notice. Production DNS/relay state and the operator steps that remain (SMTP2GO domain verification, SPF include, tracking off, From domain) are in `docs/operations.md` §6.1. Recorded here with run and backup when the receiver reports it.

**Pushed next (CI-23 organization custom roles):** schema v10 (`organization_roles`, `organization_role_assignments`), the
permission catalogue behind every organization-level check, roles routes and UI. Recorded here with run and backup when the
receiver reports it.

**Running source:** `af81bcfcebc552e92dcd91f54e772afe5bd34201` (CI-19 onboarding browser regression; CI-20 malware scanning
chain: quarantine-first sources and templates, clamd adapter, schema v7→v8 `scan` columns, degradable `malware_scanner`
readiness component; plus the five scan error messages' resources). The first push, `4061731`, failed verify on the Docs
shell's message-resource parity test and deployed nothing; `af81bcf` fixed it. [Actions 36152940549](https://github.com/WeGreen-AI/Synwevo/actions/runs/36152940549)
verify and deploy passed (created 15:15 UTC, finished 15:35 UTC); pre-upgrade backup `/opt/synwevo/backups/20260925T153500Z`.
Production runtime has no `SYNWEVO_CLAMD_ADDRESS`, so every file's `scan.status` is `not_configured` and behaviour is otherwise
unchanged; the ClamAV service is an operator decision (memory headroom, compose merge) recorded in
`docs/security/file-acceptance.md` §8 and `docs/operations.md` §8.

## 2026-09-25 navigation, layers, phone layouts and step-up admin auth — deployed

Authorized by the user's standing instruction for this round (commit, push and deploy through the
existing CI/CD once verified). No DNS, tunnel, mail routing, provider credentials, cookie scope, RP ID
or session clocks were changed; no data was migrated (the Store schema stays at v7).

**Running source (first cutover of this round):** `a342994deb11d185ef7c3066f3deec2fb5a6f81f` on `main`.
[Actions 36105544422](https://github.com/WeGreen-AI/Synwevo/actions/runs/36105544422) (created 07:01 UTC,
finished 07:16 UTC) passed verification (boundaries, brand and design guards, three shells, the Python
suites in the isolated runtime image, deployment regressions) and the restricted SSH deployment; the
receiver rebuilt the image, took the pre-upgrade backup `/opt/synwevo/backups/20260925T071558Z`
(`{"deployed": "synwevo:a342994…", "backup": …}` in the run log) and the public readiness step saw
`database/storage/worker: ok` on all three hosts.

**Running source (current):** `61f6354b1c5d01d6e497afaeaa9c56be9fdc18e2` on `main` — `93777c3` (the
portal fetches its dashboard once per cold load instead of twice) plus this record.
[Actions 36106993829](https://github.com/WeGreen-AI/Synwevo/actions/runs/36106993829) (created 07:18 UTC,
finished 07:37 UTC) passed the same verification and deployment; pre-upgrade backup
`/opt/synwevo/backups/20260925T073728Z` (`{"deployed": "synwevo:61f6354…"}`). Anonymous checks at
~07:40 UTC: `/readyz` ready on all three hosts (`database/storage/worker: ok`; the portal has no worker),
`/api/auth/state` `app_id` = `null` / `platform` / `docweave`, every bundle referenced by `/`, `/workspace`,
`/login` and the Docs shell 200, `GET /api/admin/identity-providers` anonymous 401. One request to the
portal's `/api/auth/state` in the first seconds after the cutover returned an empty body; three retries
a few seconds later all returned 200 with the full document — the gateway switched containers at that
moment, nothing was found wrong afterwards. The documentation-only commit that follows carries `[skip ci]`.

### What changed for production

* Session probes leave in parallel; returning to a tab revalidates at most every 30 s in the
  background; the portal keeps its dashboard mounted while revalidating; the public home never shows
  the sign-in link to a signed-in visitor while the session is still unknown.
* Account pages carry "← 返回工作空间 / 返回 Synwevo Docs" and "账户概览"; a drawer below 720 px.
  Targets come from `/api/auth/me.return_targets`; `from=` is an id, never a URL.
* Every row/overflow menu renders at the document root with viewport placement (flip, height cap,
  edge shift); the last member row's menu is no longer clipped. Narrow tables keep the selection and
  action columns sticky; coarse pointers get 44/40 px targets; Docs ≤ 900 px shows one panel at a time.
* Administrators who sign in with an e-mail code or an external provider get a one-factor session:
  account features work, `/api/admin/*` and the API console answer `403 step_up_required` until the
  password is confirmed on the account page (`amr` becomes `email+password` / `idp:<id>+password`).
  Password sign-in is unchanged. **The production administrator's own TOTP enrolment is still theirs to
  do** on the account page; nothing was enrolled, seeded or flagged on their behalf.
* New read-only administrator view "登录方式" (`GET /api/admin/identity-providers`): masked provider
  configuration, the exact callback URIs, missing variables, live discovery check. It shows Google and
  Microsoft as **not configured** on production — no provider credentials exist in `runtime.env`, and
  the sign-in page shows no provider buttons. Configuring them is the account holder's step
  ([operations.md](../operations.md) §7.1–7.3).

### Post-deploy checks (a342994, 2026-09-25 ~07:20 UTC, anonymous, no production sign-in)

| Check | Result |
|---|---|
| `/healthz` on `synwevo.` / `accounts.` / `docwave.wegreen.ltd` | `{"status":"ok"}` ×3 |
| `/api/auth/state` | accounts `app_id=null`, portal `app_id=platform`, docs `app_id=docweave` (the new field is live on all three); `external_providers=[]`; `portal_url=https://synwevo.wegreen.ltd` |
| Shell HTML + referenced assets | `/`, `/workspace`, `/login`, docs `/` each 200 with both referenced bundles 200 (no missing chunk) |
| `GET /api/admin/identity-providers` anonymous | 401 |
| `/admin` anonymous | 404 (unchanged shape: nothing learned about what exists) |
| `/account`, `/login` anonymous | 200 shell |

Not verified on production: any signed-in path (members menu, back link, step-up, mobile). Those were
verified on the isolated instance in Chromium and WebKit ([verification.md](verification.md) §16); the
production administrator can confirm them from a phone and a desktop after signing in.

## 2026-09-25 unified white-blue design system and sign-in methods — deployed

Authorized by the user's standing instruction for this round (commit, push and deploy through the
existing CI/CD once verified). No DNS, tunnel, mail routing or provider credentials were changed.

**Running source (first cutover):** `4ecdfb0985ec46542159f8dd8433aef439756282` on `main`.
[Actions 36092928683](https://github.com/WeGreen-AI/Synwevo/actions/runs/36092928683) passed
verification (boundaries, brand, the new design guard, three shells, Python suites in the isolated
runtime image, deployment regressions), the restricted SSH deployment and public health checks.
The image is labelled with that revision; the receiver rebuilt it on the host, took the pre-upgrade
backup `/opt/synwevo/backups/20260925T042037Z`, and the accounts, docs and portal containers reported
healthy before the gateway cutover (`{"deployed": "synwevo:4ecdfb0…", "backup": …}` in the run log).

**Running source (current):** `dbd4c941c63f5ae5a754856497a3ccd12bf8a831` on `main` (shared
not-found / signed-out page, in-account password change, remember-me checkbox never pre-checked).
[Actions 36093474438](https://github.com/WeGreen-AI/Synwevo/actions/runs/36093474438) passed the same
verification, deployed through the restricted SSH receiver with pre-upgrade backup
`/opt/synwevo/backups/20260925T044056Z` (`{"deployed": "synwevo:dbd4c94…"}`), and the accounts, docs,
portal and gateway containers reported healthy; public readiness passed at 2026-09-25 ~04:45 UTC.
The pending run for `ba56412` was superseded by GitHub's concurrency rule, so its content shipped
with `dbd4c94`. The documentation-only commits that follow carry `[skip ci]` and do not redeploy.

### What changed for production

- Store schema **6 → 7**, additive only: `sessions.persistent`, `sessions.auth_provider`,
  `mfa_tickets.first_factor`, and the empty `login_codes`, `external_identities`,
  `external_auth_requests`, `external_link_offers` tables. Existing sessions keep working as
  non-remembered sessions; the receiver's pre-upgrade backup precedes the migration as before.
- All three shells serve the unified tokens (`--bg #f8fafc`, `--accent #2563eb`, pressed
  `#1e40af`); the portal stylesheet no longer carries its private green palette.
- Sign-in page offers "keep me signed in" and e-mail code sign-in; **no external provider is
  configured**, so `/api/auth/state.external_providers` is `[]` and no provider button is shown.
  E-mail codes use the existing production SMTP; the platform administrator account has no
  TOTP enrolled, so it is deliberately not offered an e-mail code (documented policy).

### Post-deploy checks (first cutover, 2026-09-25 ~04:25 UTC)

| Check | Result |
|---|---|
| `/readyz` on synwevo / accounts / docwave | `ready`; database, storage (and worker on accounts/docs) `ok` |
| `/api/auth/state` on accounts | `mode=session`, `registration=open`, `bootstrap_required=false`, `external_providers=[]` |
| Served account CSS | contains `--accent:#2563eb`, `--bg:#f8fafc`, `--accent-pressed:#1e40af`; no dark token block |
| Served account JS | contains the remember-me and e-mail code strings and the `external_providers` field |
| Served portal CSS | zero occurrences of the old `--pt-green` palette; shared `--accent` present |
| Browser (Chrome, anonymous) | homepage and sign-in page render in the unified system with the new controls; no provider button |

### Post-deploy checks (current cutover `dbd4c94`, 2026-09-25 ~04:47 UTC)

| Check | Result |
|---|---|
| `/readyz` on synwevo / accounts / docwave | `ready`; database, storage (and worker) `ok` |
| Served account JS | contains the password-change form strings, `/api/auth/password` and the shared status page |
| Served portal / Docs JS | contain their not-found messages |
| Browser (Chrome, anonymous) | `accounts…/does-not-exist` and `synwevo…/does-not-exist` render the shared not-found page (brand bar, message, one way forward), in the unified system |
| `/api/auth/state` | unchanged: `registration=open`, `external_providers=[]` |

Not exercised on production in this round: authenticated flows, e-mail code delivery to a real
inbox, and any external provider (none configured). Those were verified on the isolated local
instance recorded in [handoff.md](handoff.md).

## 2026-09-25 product and workflow-platform homepage — deployed

The user explicitly requested “部署好” after the local-only product round, then corrected the
homepage positioning: **Synwevo is the workflow automation platform; Synwevo Docs is one application**.
That later deployment authorization governs this release. No DNS, tunnel, mail routing or other
hosted application was changed.

**Running source:** `1a48359df0936ccaff129df27757eaacfdcba44a` on `main`.
[Actions 36086079530](https://github.com/WeGreen-AI/Synwevo/actions/runs/36086079530) passed verification,
restricted SSH deployment and public health checks; cutover completed at 2026-09-25 02:43 UTC.
Image ID: `sha256:3460b3d451a737e4e09546d4a345c529816cff8f77b3b03e08e89513f82d6db7`.
The following documentation-only `[skip ci]` commit records this result without redeploying unchanged code.

### Public entry points and homepage behavior

- Platform: https://synwevo.wegreen.ltd — teams, applications, inputs/rules, tasks/results and scoped automation.
- Account: https://accounts.wegreen.ltd — shared registration, login and account management.
- Docs: https://docwave.wegreen.ltd — existing independent document application.
- https://dw.wegreen.ltd retains its HTTP 308 redirect to the Docs origin.

The homepage hero is “把重复的工作，交给流程。”; the application section introduces Synwevo Docs
and its document formats separately. The illustrated flow is explicitly an illustration, not an
advertised visual workflow builder. No fictional application or usage statistics were introduced.
Simplified/Traditional Chinese and English cover the React page, SSR titles/descriptions,
navigation and calls to action. AGENTS.md and the architecture map preserve this product distinction.

### Current verification

| Check | Result in this deployment round |
|---|---|
| Production-runtime Python suite | **1893 passed / 0 failed / 2 skipped**, 178 dependency warnings, 945.85 seconds; isolated Linux container, synthetic fixtures |
| Frontend | All typechecks/builds pass; shared UI 11, auth-client 31, Docs 45 tests and Account 19 component/contract validations pass |
| Deployment regressions | 6 passed; archive rejection, idle admission and candidate failure behavior |
| Targeted homepage/shell regressions | 20 passed / 0 failed / 0 skipped after fixing the initial CI failure |
| Boundaries/branding | Passed without relaxing checks |
| Public HTTPS smoke | All three readiness URLs and built JS/CSS pass; expected anonymous 401 and cross-role 404; API console rejects anonymous access |
| Identity routing | Registration open at all roles; existing administrator means bootstrap is not offered; host-only Secure/HttpOnly OIDC state, S256, account login destination and issuer unchanged |
| Production browser | Chrome 153: Simplified desktop 1440×1000, English/Traditional mobile 390×844; workflow copy, actual language switch, English registration link reaches the English Account form; no horizontal overflow or observed JavaScript exception |
| Existing SMTP | Actual Account container authenticates to mail.hikosphere.com:587 over certificate/hostname-verified STARTTLS, TLSv1.3; fixed sender mail@hikosphere.com; **no mail sent this round** |
| Running services | Accounts, portal, Docs and gateway healthy; zero observed ERROR/traceback lines since their final restart |

The two Python skips remain the operator-unselected project LICENSE and a nested Docker check
unavailable inside the isolated verifier (no host Docker socket). They are not represented as passes.
Earlier attempt [36084520872](https://github.com/WeGreen-AI/Synwevo/actions/runs/36084520872) failed:
1888 passed / 1 failed / 4 setup errors / 2 skipped. The verification image omitted root `conftest.py`,
and one shell assertion still expected the previous homepage title. Commit `1a48359` copies the
fixture/config file only into the test derivative and updates the exact title assertion; no test
assertion or security gate was removed. The full rerun above supersedes that failure.
Run 36083449711 was cancelled before deployment to incorporate the homepage correction.

Local browser checks additionally covered all three locales at both sizes, language persistence
on reload, keyboard focus/skip link and reduced motion. Browser console entries were expected
anonymous `/api/auth/me` 401 responses. These checks do not assert full WCAG certification.

### Data preservation and configuration

The receiver made the offline pre-upgrade backup `/opt/synwevo/backups/20260925T024313Z` before
Store migrated schema **5 → 6**. On-server read-only comparison checked every old column value by
primary key, preserving 2 users/password rows, 3 spaces/memberships, 5 projects, 4 sources,
5 templates, 17 template versions, 9 mapping revisions, 6 jobs/24 items, 36 artifacts,
1 OIDC key and 2 OAuth clients. All **57 existing blob files** have identical bytes.
Root-secret bytes match; historical `default` remains `pending_claim`. Production content and
secret values never left the server. No accounts, data or keys were rebuilt.

After the healthy image cutover, the reviewed host-owned Compose was installed under the deployment
lock. The gateway was stopped, active jobs/mail/Webhook queues checked idle, and only Synwevo's
three application roles and gateway restarted. Registration is now **open** with mailbox verification;
it grants no platform administration. A later persisted administrator policy still overrides the
environment setting. Previous Compose: `/opt/synwevo/config/compose.before-open-20260925T024541Z.yaml`.
No data rollback was performed. Once new user traffic is admitted, do not restore this backup over
later writes as an automatic rollback.

Existing `/opt/synwevo/config/runtime.env` and `smtp.env` remain mode 0600; data/backup directories
remain 0700. Only Account receives SMTP secrets. Docs/portal remain without SMTP passwords.
The authenticated SMTP check did not submit a message; it proves transport/authentication, not
mailcow queueing, relay acceptance, remote acceptance or final inbox visibility in this round.
Prior external-mail evidence below is explicitly historical.

### Evidence and limits

Private evidence directory: `/private/tmp/synwevo-deploy-20260925/`:
`actions-final.log`, `server-data-verification.json`, `registration-activation.log`,
`server-final.json`, `public-smoke-final.json`, `smtp-tls-final.json`, local test logs and
`homepage-local-evidence.json`. The corresponding read-only verifier scripts are retained there.
Screenshots: `output/playwright/homepage-automation-{language}-{desktop|mobile}.png` (six local)
and `homepage-production-zh-Hans-desktop.png`, `homepage-production-en-mobile.png`,
`homepage-production-zh-Hant-mobile.png`. Evidence/screenshots are ignored by Git.

Authenticated registration/invitation/Docs/admin/API flows were exercised with isolated accounts,
file-mail and synthetic documents in the preceding product round ([handoff](handoff.md)); this
production pass did not create users, impersonate the administrator or send test mail.
Physical Passkey devices, another external inbox delivery and operator legal/contact information
remain external acceptance inputs. There is no claim that anonymous production smoke alone proves
all authenticated workflows. Local preview remains at ports 28769/28770/28765 with isolated data;
no recurring agent or hidden background deployment loop was established.

Operational commands and recovery limits: [production guide](../../infra/production/README.md).

## Historical deployment and SMTP acceptance — 2026-09-24

2026-09-24. User explicitly authorized replacement of the old Docker service, public domain changes,
push and CI/CD. This deployment scope supersedes the earlier local-only development boundary.
The larger product roadmap remains paused; this record concerns deployment and its blockers.

**Outcome of the 2026-09-24 round: deployed, SMTP enabled, CI/CD verified.** Its release was
`d97823a06e5cb8c7e578c3a948a2978240655326`. The subsequent evidence-only documentation commit
does not change that running release. The first administrator was created by the user.
This SMTP round is complete; earlier cutover results below remain historical. No agent loop remains.

## Verified before cutover

- Existing SSH target: vultr_tokyo; existing service: `docweave`, port 127.0.0.1:8765,
  data `/opt/docweave-data`, image `docweave:local`. Other hosted services remain separate.
- Existing Cloudflare tunnel is reused. No new public TCP port or second tunnel is needed.
- Candidate production image built successfully on Linux amd64; dependency installation and pip check pass.
- Server-local migration copy preserves 4 projects, 4 source files, 5 templates, 6 completed jobs,
  57 blob files; old and candidate blob manifests match. Historical workspace remains pending_claim;
  neither instance has a unified user before bootstrap. No production data left the server.
- Three role frontends and readiness URLs return 200 through the isolated candidate gateway;
  unauthenticated document API returns 401. A candidate nginx temporary-directory ownership defect
  was found and fixed; gateway now has an actual HTTP health check.
- Local frontend typecheck/build and module/brand checks pass. Original pytest baseline:
  104 failed / 1452 passed / 1 skipped. Of failures, 78 were sandbox socket restrictions,
  24 sandbox LibreOffice failures and 2 actual API contract gaps. Normal-permission reproduction
  separated these; missing page-render/locator routes were fixed without relaxing authorization.
  A subsequently reproduced numeric desensitization leading-zero defect was fixed with regression.
- Latest local Python suite: 1579 passed / 1 skipped (project LICENSE not selected), 0 failed.
  Logs under `/private/tmp/synwevo-deploy-20260924/`; these are this turn's results, not old counts.
- Deployment archive rejection, idle-state admission and failed-candidate backup rollback: 6 tests pass.
- GitHub production environment is limited to main and uses a new restricted deployment key;
  attempted arbitrary `id` over that key is denied. Private key is encrypted in GitHub Secrets.

## Production cutover

The operator migration completed at 2026-09-24 11:26 UTC with release
`e45518f86333b16c20bc288eadf61941b7be7b3e`. The old `docweave` container is stopped;
its image and `/opt/docweave-data` remain intact. The consistent migration backup is
`/opt/synwevo/backups/legacy-20260924T112503Z`.

Comparison before and after migration confirmed stable IDs, ownership, source/blob references,
statuses and root-secret bytes: 4 projects, 4 source files, 5 templates, 6 jobs, 36 artifacts,
1 historical workspace and 57 blob files. No human user or membership was invented. The blob
manifest used by the deployment verifier is
`c50907739a08768ced9e0a7548a83475dbc29583017a545a4dc97e4ef5c301a9`.

All four production containers are healthy. Public HTTPS readiness, each role's built JS/CSS,
unauthenticated API rejection, role boundaries, host-only secure OIDC state cookies, S256 redirect
to account login, discovery and the old-host HTTP 308 redirect passed the HTTP smoke check.
Evidence: `first-migration.log` and `public-smoke-first.json` in the local evidence directory.

Actions run [35991298452](https://github.com/WeGreen-AI/Synwevo/actions/runs/35991298452)
passed verification: **1579 passed / 0 failed / 2 skipped**, 178 warnings, 705.50 seconds in the
isolated production-runtime derivative. Frontend typecheck/build/tests, boundaries, branding and
6 deployment regressions also passed. The first deploy attempt correctly refused the uninitialized
production data directory; after the operator migration, only that deploy job was rerun.
That rerun **succeeded**, including the restricted SSH receiver, backup, application/gateway
restart and public readiness checks. Its pre-upgrade backup is
`/opt/synwevo/backups/20260924T112659Z`. The deployed image ID is
`sha256:b739d8d180e3f46c502693a4cb2285ddb7a749193d9ca18aeca2f0aa429f4e97`.
The two frontend test groups passed 6 and 22 tests with no failures/skips.

The exact release was rehearsed on the server-local copy before cutover. Recreating applications
can change container IPs; nginx keeps its resolved upstream addresses until restarted. Both the
operator migration and CI receiver force-recreate the gateway after the roles are healthy. This
was verified by checking role identity at all three hostnames after the rehearsal gateway restart.

## Browser and identity acceptance

- A separate real Chrome session loaded the portal, followed its login button to Synwevo Account,
  and followed the actual protected Docs route `/capabilities` to the same account login service.
  `/projects` is not a Docs UI route; its expected not-found page was not mistaken for a login failure.
- Desktop and 390 × 844 account screenshots were inspected. The password/login/Passkey controls
  render without horizontal clipping. Browser console inspection showed expected anonymous 401
  responses, with no observed JavaScript exception. This is not an authenticated document-editing
  or physical-Passkey acceptance claim.
- The user created the administrator and chose their own password through the interactive CLI.
  A read-only aggregate check confirmed one active administrator, three live sessions, and one
  active client session each for `portal-web` and `docweave-web`. The assistant did not read or
  enter the password, borrow the user's cookies, or create a replacement account.
- Historical `default` remains `pending_claim`. After signing in, use
  `https://accounts.wegreen.ltd/admin` → “待认领的历史数据” → select the intended existing account →
  “指派历史数据”; the existing recent-reauthentication and reason requirements apply. Then choose
  “历史数据” in the workspace switcher. The migration did not silently grant document access.
- At the initial cutover SMTP was disabled. The later SMTP integration and its separate acceptance
  evidence are recorded below; physical security-key validation is still not claimed.

Final HTTP check: `public-smoke-final.json`; browser evidence: `output/playwright/`;
identity aggregate: `identity-status.json`; server state: `server-final.log`; successful Actions
logs: `actions-final-verify.log` and `actions-final.log`, all under
`/private/tmp/synwevo-deploy-20260924`. Evidence contains no production database or document body.
The HTTP capture preceded the user's administrator creation; its `bootstrap_required: true` is
historical, superseded by the later identity aggregate. `server-log-check.json` reports zero
observed error/traceback lines after the automatic deployment and verifies private directory/file
modes (data/backup 0700, runtime configuration 0600).
Runtime document correctness tests used clearly identified synthetic/compatibility fixtures;
production user documents were preserved and hashed on the server, not uploaded for testing.

The rehearsal stack and this task's stopped test container were removed; their private data copy
and backups remain on the server. Other hosted applications were not changed. The temporary local
deployment private key was deleted after the successful Actions deployment; GitHub retains its
encrypted environment secret. Production services continue normally; no agent loop remains.

## Earlier failures and fixes

The first Linux test attempt was stopped: the non-root runtime image correctly owns no source
write permissions, while the test fixture generator writes to its checkout. Only the verification
image grants source-write permission; production keeps its read-only filesystem and non-root user.

The first Actions run (35983508407) failed: 8 failed / 1548 passed / 24 skipped. Investigation
found an old LibreOffice version (XLOOKUP returned #NAME?), a lazy TTC font reader used after
its file was closed, implicit page geometry in a layout fixture, an asynchronous parsing race in
an authorization test, and a fixture assuming a local .venv. The isolated container also needed
git for its repository-tooling tests and explicit host-mode selection for the listen guard test.
Original assertions remain; fixtures now state and await their prerequisites. The font reader
has a real synthetic TTC regression. New LibreOffice formula check: 44 passed; repaired Linux
layout/font/image/guard set: 54 passed. Full rerun is still required.

Trixie Actions run 35989630759: 1578 passed / 1 failed / 2 skipped. The remaining snapshot-test
fixture left its SQLite writer unclosed before hashing the main database file. A separate Linux
reproduction showed garbage collection checkpoints its WAL and changes the main-file hash even
without a restore; explicit close before the baseline preserves it. The original unchanged-file
assertion remains. The production verifier only uses its isolated copy and reads the live journal.
The two skips are the unselected project LICENSE and a nested Docker check unavailable inside the
isolated verification container (no host Docker socket is mounted).

Source commits 599c24d, 8e652e1, 17b7416 and e45518f were explicitly pushed to main. The operator
added the three Cloudflare routes; server DNS resolves all three origins and public readiness passes.
Runtime version reference, checked 2026-09-24:
https://packages.debian.org/trixie/libreoffice-calc (25.2.3-2+deb13u6).

See [deployment guide](../../infra/production/README.md) for topology, commands and recovery boundaries.

## SMTP integration — 2026-09-24

Source `06fc4cf` adds secure SMTP transport/configuration and business acceptance tooling;
`9cb3aa2` isolates that tooling's test environment; `42c2277` makes SMTP command assertions portable
across Python versions. [Run 35998263744](https://github.com/WeGreen-AI/Synwevo/actions/runs/35998263744)
passed all **1600 tests / 2 skipped / 178 warnings (712.92 s)** but failed deployment.
The private source extraction preserved root-owned 0700 directories in the image, so the non-root
account service could not import its own package. Automatic rollback restored the previous image
and data; SMTP was then explicitly disabled on that older transport.

Source `d97823a` makes only shipped application source readable in the image, preserving non-root,
read-only execution and private data/configuration. CI now reproduces the private archive extraction
and checks the unmodified runtime before granting test-fixture source write access. An isolated
non-root startup with the real SMTP settings passed without network or a production data mount.
[Run 36000389218](https://github.com/WeGreen-AI/Synwevo/actions/runs/36000389218) **passed verification
and deployment**: 1600 passed / 0 failed / 2 skipped, 178 warnings, 630.07 seconds for the Python
runtime suite. The existing skips are the unselected project LICENSE and unavailable nested Docker;
no SMTP regression is skipped. Frontend typecheck/build and both test groups (6 and 22), boundary/
brand checks, 6 deployment regressions, private-source image startup and public readiness passed.

Release `d97823a06e5cb8c7e578c3a948a2978240655326` deployed at 12:53 UTC. Its consistent pre-upgrade
backup is `/opt/synwevo/backups/20260924T125251Z`; the runtime image ID is
`sha256:2e6f8220a84ccc95e502f88eb9159362b44bc11859d2baadfbb15fa4e910636a`.
Only after that successful deployment, an operator check verified the release, matching mail code
and zero queued production mail, enabled SMTP, and recreated only accounts and the gateway.
All three roles and the gateway are healthy. Public HTTPS readiness returned 200 for all three origins.
The actual account container authenticated over **TLSv1.3**, with system certificate and hostname
verification, without issuing MAIL/RCPT/DATA. Docs and portal have no SMTP password. Runtime mail
code matches both local source and the two-message acceptance image:
`30dfbd1ff5a5ebd01e86b6536a6fca21c3eb5da66d44f9ec456341702f5b512b`.

The earlier run 35996002911 was intentionally cancelled before deployment to fix the test's
environment leakage; it is not counted as passing.
Run 35996533284 completed with 1598 passed / 2 failed / 2 skipped: the two failures compared SMTP
command word capitalization emitted by Python 3.12 against Python 3.14's spelling. The correction
normalizes only protocol command words; envelope sender/recipient, TLS and delivery assertions are
unchanged. All 20 transport tests then passed on both Python 3.12 (isolated Linux runtime container,
no external network) and Python 3.14 locally. The Linux container also reported a harmless pytest
cache permission warning because application source remains owned by root in the runtime image.

The user explicitly confirmed **both username and From `mail@hikosphere.com`** (the original
message's displayed address differed from its mailto target). Only that mailbox's password was
extracted on the VPS from the authorized credentials JSON. No other mailbox/admin/relay credential
was used; no real password was copied to the development machine or repository.
Host key verification remained enabled for every SSH operation.

Configuration is `/opt/synwevo/config/smtp.env`, root-owned mode **0600**, parent **0700**.
Reviewed Compose injects it only into `accounts`, using raw env values; the portal and Docs receive
no SMTP password. Endpoint: `mail.hikosphere.com:587`, STARTTLS with system CA/hostname verification,
15-second socket timeout, From `Synwevo <mail@hikosphere.com>`. Verified authentication did not send
any mail. Port 465 is supported by the code with mutually exclusive implicit TLS, and was tested
only against the synthetic local receiver. No MX, Cloudflare inbound rule, mailcow routing,
SMTP2GO configuration, BDR state or unrelated hosted application was changed.

### Actual business acceptance (two messages total; no resend)

An isolated, non-root container built from `06fc4cf` ran the real account factory and loopback HTTP
server, with new **synthetic** owner/recipient accounts and workspace. It had no production data
mount and borrowed no production cookies. `verify_smtp.py` logged in, called the real invitation
and email-verification routes, checked the outbox recipient/count, then executed one queue pass.
Only the user's explicitly authorized Outlook recipient was permitted for this run; its exact
address is in private acceptance evidence. The private fixture directory refuses reruns.

| Business flow | Message-ID | mailcow queue | SMTP2GO acceptance ID |
|---|---|---|---|
| Workspace invitation | `ml_cef6d914689e@hikosphere.com` | `7335D51134B` | `1x9i6Y-AIkwcC8sd6r-2N6z` |
| Email verification | `ml_6cef2594d7f8@hikosphere.com` | `1BD5451134C` | `1x9i6Y-AIkwcC8liLJ-8MXy` |

Both were queued by HTTP and changed to `sent`, **one attempt each**, at 11:59:09 UTC. mailcow's
DATA response was 250; filtered Postfix logs confirm TLSv1.3 submission, the fixed sender and
sole authorized recipient. At 11:59:10/11 UTC it relayed both to `mail.smtp2go.com:587`, each with
`dsn=2.0.0 status=sent (250 OK ...)`, and removed both queue entries.
This proves application submission, mailcow acceptance and relay acceptance. **Outlook SMTP
acceptance was not independently observed; inbox/junk-folder visibility awaits recipient
confirmation.** No third message was sent. Test links point to a temporary local service and do
not grant access to a production account; the recipient need not click them.

### Changed files and ownership

| Area | Files changed this SMTP round |
|---|---|
| Existing account mail and business feedback | `services/identity/synwevo_identity/mail.py`, `services/identity/synwevo_identity/auth_routes.py`, `services/identity/web/src/pages/Recover.tsx` |
| Explicit business acceptance runner | `services/identity/scripts/verify_smtp.py` |
| Shared configuration and local process isolation | `packages/core/synwevo_core/env.py`, `scripts/platform.py` |
| Regression coverage | `services/identity/tests/test_mail_transport.py`, `services/identity/tests/test_invites.py`, `tests/integration/test_platform_tooling.py` |
| Secret exclusion and placeholder examples | `.gitignore`, `.dockerignore`, `.env.example`, `infra/production/smtp.env.example` |
| Runtime and deployment verification | `infra/production/compose.yaml`, `infra/Dockerfile`, `.github/workflows/production.yml` |
| Current operating records | `docs/operations.md`, `docs/development/local-platform.md`, `docs/security/permissions.md`, `docs/quality/deployment.md`, `infra/production/README.md` |

The existing identity module remains the sole mail implementation. No application imports another
application's internals; no mail management page or new product navigation was added. Production
configuration changes are limited to the reviewed Synwevo Compose file and its private SMTP env file.

### Local validation and evidence

- Account + integration suites: 761 passed, 0 failed, 106 dependency deprecation warnings (199.78 s).
- SMTP/invitation/recovery/config targeted set: 92 passed, 0 failed. After adding the external-runner
  rehearsal and local launcher isolation: 56 passed in the transport/tooling set. Follow-up order
  regression with SSO and auth gates: 73 passed. These sets overlap; counts are not added together.
- Synthetic local TLS covers STARTTLS/implicit TLS, untrusted certificate and missing STARTTLS
  refusal before AUTH, fixed sender, bounded retries, stable Message-ID, concurrent claims, lost
  DATA acknowledgment, post-acceptance database failure, and redacted error/config diagnostics.
- Frontend typecheck/build, module boundaries and branding pass; deployment safety suite: 6 passed.
- Real in-app browser submitted an unknown synthetic address to a loopback file-mode account
  service. The page correctly states conditional queueing, with no observed console warning/error
  and zero queued mail. It no longer claims an email was already sent. The temporary service and
  browser tab were closed. External acceptance container also exited and was removed.
- Secrets stay outside Git; `smtp.env` is ignored by Git and Docker context, examples have only
  placeholder passwords. Local CLI/env injection instructions are in `docs/operations.md` and
  `docs/development/local-platform.md`; host-specific instructions are in `infra/production/README.md`.

Sanitized evidence stays under `/private/tmp/synwevo-smtp-20260924/`: `acceptance.log`,
`mailcow-receipts.log`, `delivery-summary.json`, `identity-integration.xml`, targeted/runner logs,
`browser-check.json`, `archive-check.json`, build/typecheck logs, `actions-permissions-final.log`,
`production-activation.log`, `runtime-final.json`, `production-smtp-auth.json`, and `public-health.json`.
A separate Python urllib public probe received HTTP 403; curl and the final CI public checks returned
200. No edge policy was changed to obtain these results; the urllib result is not counted as passing.
Server-side fixture evidence is
`/opt/synwevo/smtp-acceptance-20260924/data/acceptance.json`; `/opt/synwevo/smtp-review/` holds the
acceptance log and source build, with no SMTP password. The production queue was empty before
activation, so no historical mail is being replayed to enable SMTP.

For a rollback to a release before `06fc4cf`, restore `config/compose.before-smtp.yaml` as well,
keeping SMTP disabled for that older transport implementation. Do not run the new SMTP credentials
through the pre-fix transport, which lacked explicit certificate verification.


## 2026-10-08 Agent 首次受控启用准备

操作者：所有者授权的Codex协调者。已核对生产25b084f完整CI/CD成功。当前待发布5ffec9d相对25b084f的apps/suite、apps/portal、infra/server及core/billing无差异；新发布只涉及支付/通知恢复、测试维护及blob分类。为优先完成真实Agent链路，先在已验证的25b084f沿operations§11.1执行二级开关恢复：备份runtime.env、保留五个应用并添加agent、持有现有deploy.lock、仅重建portal，不更新镜像/治理/业务数据。成本策略、提供方、价格与指定所有者放行名单已真实发布。付款入口仍关闭，等待5ffec9d完整CI和部署；公开放量仍受§11.3观察门槛。以下追加真实执行回执，准备不表示已成功。

12:35:19Z执行成功：已持有deploy.lock并确认准确25b084f镜像，备份/opt/synwevo/config/runtime.env.bak-agent-20261008T123519Z；portal force-recreate后Healthy。证据agent-activation/activation-applied.json；没有重复部署其它角色、没有运行模型或付款。浏览器与健康验证继续。

## 2026-10-11 e7c1f05 发布（CR-078 个人附件 + f919d2f CI 阻断修复）

操作者：Claude Code 协调者会话 13b6809f（接自 Codex 交接）。f919d2f 的 Actions 38054090562 在 Docs refusals 门禁失败、未部署；修复提交 e7c1f054e97a724e19ceaadbae6dd0ce74507858 推送 main 后，Actions 38146440152 verify（14:15–15:07Z）与 deploy（15:07–15:09Z）成功。
15:10Z 只读核验（`docs/agent-work/evidence/2026-10-11-ci-refusals/production-release.json`）：`/opt/synwevo/current.json` 镜像 `synwevo:e7c1f05…`、前镜像 b1c8a0b、备份 `/opt/synwevo/backups/20261011T150904Z`；accounts/docs/portal/messages 四容器 OCI revision 均 e7c1f05，含 gateway/clamav 六容器 healthy；portal 容器内 `synwevo_docs.ingest`、`synwevo_suite.agent.files`、`agent.engine`、`billing.ledger` 源码 sha256 与该提交一致；五域 `/readyz` 与根页 200。未调用生产模型或支付，未改任何配置。随该版本上线：Agent 个人文件上传/严格解析/预览/下载/删除（CR-078）、文件 worker 的 `context_limit_exceeded`/`parse_cancelled` 三语言呈现、Docs 门禁决定与 Suite 测试缓存隔离（仅测试）。

## 2026-10-11 962b375 发布（CR-079/CR-082 报价前来源审阅 + CR-083 取消/恢复修复）

操作者：Claude Code 协调者会话 13b6809f。e7c1f05 核验上线后推送 862b079（CR-079，含 CR-082 首次键盘打开焦点修复）、ea047ad（CR-083）与部署记录提交，main 顶端 962b3755a92a7b0f350e2ac907497928239017bd；Actions 38150240686 verify（15:11–16:10Z，运行时验证约 55 分钟）与 deploy（16:10–16:12Z）成功。
16:16Z 只读核验（`docs/agent-work/evidence/2026-10-11-agent-budget-sources/production-release.json`，CI 回执同目录 `ci-release.json`）：`/opt/synwevo/current.json` 镜像 `synwevo:962b375…`、前镜像 e7c1f05、备份 `/opt/synwevo/backups/20261011T161149Z`；accounts/docs/portal/messages 四容器 OCI revision 均 962b375，含 gateway/clamav 六容器 healthy；portal 容器内 `synwevo_docs.ingest`、`synwevo_suite.agent.files`、`agent.engine`、`billing.ledger` 源码 sha256 与该提交一致；五域 `/readyz` ready 与根页 200。未调用生产模型或支付，未改任何配置。随该版本上线：Agent 报价前来源审阅（报价对话框列出每项所选资料的名称、类别与来源空间或「个人文件」，来源变化即阻断，CR-079/CR-082）；用户已停止的回答在重启后保持 cancelled、取消立即收尾时返回 cancelled、聊天区「停止中」反馈（CR-083）。

## 2026-10-11 e99212d 发布（CR-084 Office 附件 + CR-085 测试缓存隔离 + CR-086 业务资料类型扩展）

操作者：Claude Code 协调者会话 13b6809f。962b375 核验上线后推送 b5b9c56（路线续期文档）、7edda10（CR-084）、9bc5600（CR-085）、db77298（CR-086）与部署记录 e99212d8e9c379f43376335194cde3b8bd3f741c；推送前在实施树复跑五守卫、根 typecheck/build、identity 1549 与 integration 1352。Actions 38155466408 verify（16:26–17:23Z）与 deploy（17:23–17:25Z）成功。
17:27Z 只读核验（`docs/agent-work/evidence/2026-10-11-agent-context-kinds/production-release.json`，CI 回执同目录 `ci-release.json`）：`/opt/synwevo/current.json` 镜像 `synwevo:e99212d…`、前镜像 962b375、备份 `/opt/synwevo/backups/20261011T172455Z`；accounts/docs/portal/messages 四容器 OCI revision 均 e99212d，含 gateway/clamav 六容器 healthy；portal 容器内被检查模块源码 sha256 与该提交一致；五域 `/readyz` ready 与根页 200。未调用生产模型或支付，未改任何配置。随该版本上线：Agent 个人文件读取 DOCX/XLSX（完整文字或拒绝，CR-084）；审批模板/请求、提取模板/任务、数据处理方案/运行进入 Agent 业务资料选择器（CR-086）；账户前端测试缓存隔离仅影响测试（CR-085）。

## 2026-10-12 2066a035 已推送、未部署（GitHub Actions 计费阻断仍在）

操作者：Claude Code 协调者会话 13b6809f。11:38Z `git push origin HEAD:main`，`393b066e..2066a035`（27 个提交：CR-089～CR-108，含管理员中心 A/D/G 系列、API traceparent、Forms 宽屏三语言走查、G24 统一前后值、推送前对抗式审查的 14 项修复，以及 DECISIONS D6/D7）。推送前在本机跑完与 verify 作业等价的清单：五守卫 + `app_icon_assets --check`、根 typecheck/build（五个外壳）、`npm test --workspaces`（613 项）、`SYNWEVO_SHEET_CLIENTS=required` sheet 客户端测试（98）、infra/production unittest（12）、SDK Python（55）/TypeScript（48）、仓库根 `pytest` 全量 5791 通过 2 跳过。Actions 运行 [38243994779](https://github.com/WeGreen-AI/Synwevo/actions/runs/38243994779)：verify 作业 2 秒内失败，注解同前（"The job was not started because recent account payments have failed or your spending limit needs to be increased"），deploy 跳过。生产仍为 `synwevo:e99212d`（`/healthz` ok）。**需要组织所有者处理** WeGreen-AI 的 GitHub「Billing & plans」；恢复后重跑该运行（`gh run rerun 38243994779 --failed`）即可验证并部署 2066a035，无需再推送。此前 38164472254 的 13 次重跑不再需要。

12:14Z 推送 ae93e86d（CR-109）与 12:36Z 推送 b8989b93（CR-110）各自触发运行 [38247806809](https://github.com/WeGreen-AI/Synwevo/actions/runs/38247806809) 与 [38250048637](https://github.com/WeGreen-AI/Synwevo/actions/runs/38250048637)：verify 作业同样未启动（0 个步骤，注解相同），deploy 跳过。12:50Z 对 38250048637 重跑（attempt 2，累计第 14 次计费重试）仍未启动，注解相同。生产仍为 `synwevo:e99212d`。计费恢复后只需重跑最新运行（`gh run rerun 38250048637 --failed`，或之后更新的运行）即可验证并部署 main 头部，无需再推送。
12:59Z 推送 72ab793a（CR-111 + 部署记录）触发运行 [38252548239](https://github.com/WeGreen-AI/Synwevo/actions/runs/38252548239)，verify 同样未启动。15:58Z 对 38250048637 再重跑（attempt 3，累计第 15 次计费重试）仍未启动，注解相同；生产仍为 `synwevo:e99212d`。
16:53Z `git push origin HEAD:main`，`72ab793a..efc5db63`（5 个提交：CR-112 WP4 项目/来源审计 604ce96e、CR-113/114 WP5+WP6 模板/映射/定义/任务审计 6b0d550d、解析启动修复 cb157e83、记录 2 个）。推送前：完整根套件 5821 通过/2 失败（已修复后 Docs 套件重跑 1655 通过）/2 跳过，五守卫 + 图标检查通过；前端未改动。运行 [38280521415](https://github.com/WeGreen-AI/Synwevo/actions/runs/38280521415)：verify 作业同样未启动（0 个步骤，注解相同），deploy 跳过；生产仍为 `synwevo:e99212d`。计费恢复后重跑最新运行（`gh run rerun 38280521415 --failed`）即可验证并部署 main 头部。 17:47Z 第 16 次重跑（attempt 2）：verify 仍 0 个步骤、注解相同（“recent account payments have failed or your spending limit needs to be increased”），deploy 跳过；下一次重跑≈18:50Z。

18:08Z `git push origin HEAD:main`，`efc5db63..ed838244`（6 个提交：CR-115 WP7 上传/链接/回归用例审计 fde1750b、CR-116 WP8 Suite 能力中心审计 71ef3af5、CR-117 WP9 Suite 旁路路径审计 e53dee01、CR-118 WP10 Webhook 操作审计与签名元数据 0826a9e5、CR-119 WP11 审计页应用动作目录 221a59b6、记录 ed838244）。推送前：完整根套件 5848 通过/0 失败/2 跳过（17:45Z–18:07Z），五守卫 + 图标检查通过，根 typecheck/build 与 615 项前端 node 测试通过。运行 [38288895744](https://github.com/WeGreen-AI/Synwevo/actions/runs/38288895744)：verify 作业未启动（0 个步骤，计费注解相同），deploy 跳过；生产仍为 `synwevo:e99212d`。计费恢复后重跑最新运行（`gh run rerun 38288895744 --failed`）即可验证并部署 main 头部（含 D7 全部十一个工作包）。

19:01Z `git push origin HEAD:main`，`a36c1fa3..69698ae0`（1 个提交：CR-120 A37 平台受控所有权恢复 69698ae0）。推送前：最终树完整根套件 5886 通过/0 失败/2 跳过（18:37Z–19:00Z），五守卫 + 图标检查、根 typecheck/build、六个前端工作区 620 项 node 测试通过，对抗性审查发现项已修正。运行 [38294835626](https://github.com/WeGreen-AI/Synwevo/actions/runs/38294835626)：verify 作业未启动（0 个步骤，计费注解相同），deploy 跳过；生产仍为 `synwevo:e99212d`。同时 19:01Z 对 38288895744 第 17 次重跑（attempt 2）仍失败。计费恢复后重跑最新运行（`gh run rerun 38294835626 --failed`）即可验证并部署 main 头部。2026-10-13 00:30Z 第 18 次重跑（38294835626 attempt 2）：verify 仍 0 个步骤、注解相同，deploy 跳过（19:30Z–00:30Z 本会话用量暂停，期间未重跑）。

2026-10-13 01:10Z `git push origin HEAD:main`，`2eb6fc02..abfff7d4`（1 个提交：CR-121 A35 组织生命周期的平台作用域路径）。推送前：完整根套件 5916 通过/0 失败/2 跳过（审查修正前），修正后账户服务 + 集成 3038 通过，六个前端工作区 627 项 node 测试、五守卫、图标与 Logo 检查、根 typecheck/build 通过。运行 [38330842322](https://github.com/WeGreen-AI/Synwevo/actions/runs/38330842322)：verify 作业未启动（0 个步骤，计费注解相同），deploy 跳过；生产仍为 `synwevo:e99212d`。计费恢复后重跑最新运行（`gh run rerun 38330842322 --failed`）即可验证并部署 main 头部。01:26Z 第 19 次重跑（attempt 2）：verify 仍 0 个步骤、注解相同，deploy 跳过；下一次≈02:30Z。

2026-10-13 02:03Z `git push origin HEAD:main`，`ceb86e2b..8ff68c90`（1 个提交：CR-122 A18 批量停用与批量撤销会话）。推送前：完整根套件 5934 通过/0 失败/2 跳过，修正后账户服务 + 集成 3062 通过，六个前端工作区 636 项 node 测试、五守卫、图标与 Logo 检查、根 typecheck/build 通过。运行 [38335084868](https://github.com/WeGreen-AI/Synwevo/actions/runs/38335084868)：verify 作业未启动（0 个步骤，计费注解相同），deploy 跳过；生产仍为 `synwevo:e99212d`。计费恢复后重跑最新运行（`gh run rerun 38335084868 --failed`）即可验证并部署 main 头部。

## 2026-10-11 393b066 未部署（GitHub Actions 计费阻断）

操作者：Claude Code 协调者会话 13b6809f。57bebb7（CR-087 + e99212d 部署记录）的 Actions 38159846815 在运行时验证因计时敏感的配额测试失败（见 BACKLOG 18:35Z），deploy 跳过；修复 393b066（配额测试前置改为显式 6/min 预算）与 86d74b0（CR-088 图片/扫描 PDF 本地文字识别附件）推送后，Actions 38164472254 verify 成功（18:35–19:34Z，全部套件通过），但 **deploy 作业未启动**：GitHub 注解「The job was not started because recent account payments have failed or your spending limit needs to be increased. Please check the 'Billing & plans' section in your settings」（作业 114556258462，0 个步骤）。
19:35Z 只读核验：生产仍为 `synwevo:e99212d…`，四应用容器 revision 一致、六容器 healthy、五域 ready（`scratchpad/prod-after-failed-deploy.json`，未写入仓库）。未做手动部署（仅沿既有 CI/CD 发布）。**需要组织所有者处理**：WeGreen-AI 的 GitHub「Billing & plans」付款/支出上限；恢复后重跑该运行的 deploy 作业（`gh run rerun 38164472254 --job 114556258462`）或重新推送，部署后再做只读核验并记录。CR-087（Admin A13 账户详情）与 CR-088 均已通过验证，等待这次部署一起上线。

21:05Z 第三次重跑（`gh run rerun --failed`，attempt 3）：deploy 作业再次未启动，注解相同；此前 20:06Z 的 attempt 2 同样失败。22:08Z 第四次重跑（attempt 4，作业 114588008608）同样未启动，注解相同。23:17Z 第五次重跑（attempt 5，作业 114601310975）同样未启动，注解相同。2026-10-12 00:02Z 第六次重跑（attempt 6）同样失败。00:45Z 第七次重跑（attempt 7）同样失败。01:45Z 第八次重跑（attempt 8）同样失败。02:28Z 第九次重跑（attempt 9）同样失败。03:31Z 第十次重跑（attempt 10）同样失败。2026-10-12 04:38Z 第十一次重跑（attempt 11）同样失败，注解相同；此时本地未推送提交已累计 20 个（至 CR-106），仍只沿既有 CI/CD 发布。05:29Z 第十二次重跑（attempt 12）同样失败，注解相同。09:59Z 第十三次重跑（attempt 13）同样失败，注解相同。期间本地提交累计 7 个未推送（dc480cc WP8 证据、9fbd03a 阻断记录、65bf805 CR-089、cf14e73 CR-090、60291f8 CR-091、b9d77f1 CR-092、CR-093 A20），每个均在本树通过对应套件、守卫与隔离浏览器验收；计费恢复后先推送并跟踪新运行，再只读核验生产。
