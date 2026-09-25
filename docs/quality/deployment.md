# Vultr Tokyo deployment — current record

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
