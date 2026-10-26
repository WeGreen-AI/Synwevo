# 审阅发现清单

> **历史基线，已被取代（historical baseline, superseded；2026-10-25 标注）。** 本清单是 2026-09-24 对基线 `51b31fe`
> 一次性审阅的原始结果，之后没有逐条回填：下面的「未处理」与汇总表的 0 是**当时**的状态，不是现在的状态，
> 位置行号指向基线代码，已与当前文件对不上。2026-10-25 开源前的抽查在当前 `main` 上确认已修复的包括：
> P0 的 F027（compose 的 `environment` 覆盖 `env_file`）、F098/F128/F219/F247/F293（回归用例按工作空间隔离）、
> F195、F196；P1 的 F001（只接受 RS256）、F002、F006、F050、F052、F053/F076 等，以及抽样的一批 P2 安全条目；
> 其余条目（P0 的 F155、其余 P1 与约 300 条 P2/P3）没有逐条复核。当前缺陷与修复状态以 [issues.md](../quality/issues.md)、提交记录和回归测试为准；
> 安全问题不要写进公开 issue、PR 或提交说明：在仓库 Security 页用「Report a vulnerability」私下报告；该入口未开启时，开一个只写「请求私下联系」、不含任何细节的 issue，由维护者回复联系方式。

来源：本轮对基线 `51b31fe` 的全量代码审阅（见 [codebase-review.md](codebase-review.md)）。严重度以复核者的重评为准；
P0/P1 每条都经过独立复核（`复核成立` / `复核推翻`），P2/P3 未逐条复核，由实施者修复前再核对。
**状态**：`已修复`（附验证位置）、`非缺陷`（核对后不成立，附理由）、`暂缓`（附理由）、`未处理`。

P0：数据错误/越权/认证绕过/不可启动；P1：严重功能或安全缺口；P2：中等；P3：轻微。


## 汇总

| 严重度 | 已修复 | 非缺陷 | 暂缓 | 未处理 |
|---|---:|---:|---:|---:|
| P0 | 0 | 0 | 0 | 9 |
| P1 | 0 | 0 | 0 | 41 |
| P2 | 0 | 0 | 0 | 167 |
| P3 | 0 | 0 | 0 | 157 |

## P0（9 条）

| ID | 类别 | 位置 | 发现 | 复核 | 状态 | 说明 |
|---|---|---|---|---|---|---|
| F027 | reliability | `infra/docker-compose.yml:16` | Compose deployment overrides the image ENV with .env.example: binds loopback inside the container and resolves the data dir to /app/data | 复核成立 | 未处理 |  |
| F098 | tenant-isolation | `apps/docs/synwevo_docs/api/app.py:70` | Regression-case routes are global: any signed-in user can list, download (raw source files), run and delete every workspace's cases | 复核成立 | 未处理 |  |
| F128 | tenant-isolation | `apps/docs/synwevo_docs/regression.py:196` | Regression cases (raw source files) are listable, downloadable, runnable and deletable from any workspace | 复核成立 | 未处理 |  |
| F155 | correctness | `apps/docs/synwevo_docs/engine/resolve.py:350` | OCR correction for one block is applied to every per_block sample from the same file (cross-sample leakage) | 复核成立 | 未处理 |  |
| F195 | correctness | `apps/docs/synwevo_docs/engine/builtin.py:123` | Built-in DOCX repeat block wraps the wrong paragraphs (id() of transient lxml proxies) | 复核成立 | 未处理 |  |
| F196 | correctness | `apps/docs/synwevo_docs/engine/pptx_engine.py:489` | PPTX cell/region slots use template row indices after repeat rows were expanded | 复核成立 | 未处理 |  |
| F219 | tenant-isolation | `apps/docs/synwevo_docs/api/app.py:70` | Regression cases are global: any signed-in user in any workspace can list, download (source files + template), run and delete other workspaces' cases | 复核成立 | 未处理 |  |
| F247 | tenant-isolation | `apps/docs/synwevo_docs/api/app.py:70` | Regression cases (captured from RegressionDialog) are listed, downloaded, run and deleted across all workspaces | 复核成立 | 未处理 |  |
| F293 | tenant-isolation | `apps/docs/synwevo_docs/api/app.py:70` | Regression-case routes are global: any workspace can list, download (raw source files), run and delete another workspace's captured cases | 复核成立 | 未处理 |  |

## P1（41 条）

| ID | 类别 | 位置 | 发现 | 复核 | 状态 | 说明 |
|---|---|---|---|---|---|---|
| F001 | security | `packages/core/synwevo_core/oidc.py:484` | Back-channel logout token verification accepts alg=none (unsigned) tokens | 复核成立 | 未处理 |  |
| F002 | security | `packages/core/synwevo_core/identity.py:175` | Per-address login throttle is disabled for good once 15 minutes pass after an address's first failure | 复核成立 | 未处理 |  |
| F003 | authz | `packages/core/synwevo_core/http.py:252` | Workspace roles and export permission are never enforced for the Synwevo Docs browser API | 复核成立 | 未处理 |  |
| F004 | authz | `packages/core/synwevo_core/http.py:254` | Browser sessions skip app entitlement, workspace app enablement and workspace status on every request | 复核成立 | 未处理 |  |
| F005 | authz | `packages/core/synwevo_core/machine.py:110` | Delegated tokens are checked against the issuing client's app, not Synwevo Docs; audience and client api_scopes are ignored | 复核成立 | 未处理 |  |
| F006 | security | `packages/core/synwevo_core/oidc.py:162` | Re-registering an OIDC client prints a new secret that is never stored; the old secret stays valid and there is no rotation path | 复核成立 | 未处理 |  |
| F007 | data-loss | `packages/core/synwevo_core/auth.py:117` | One unversioned root secret protects both MFA seeds and links; setting SYNWEVO_SECRET as the docs recommend silently invalidates every TOTP seed | 复核成立 | 未处理 |  |
| F028 | reliability | `scripts/platform.py:35` | `python scripts/platform.py setup` cannot run on a fresh clone | 复核成立 | 未处理 |  |
| F030 | authz | `services/identity/synwevo_identity/app.py:59` | Every account-service start re-grants revoked entitlements and re-enables per-workspace disabled apps | 复核成立 | 未处理 |  |
| F050 | security | `services/identity/synwevo_identity/mfa.py:97` | TOTP 'begin' silently disables an already-enabled TOTP without a current code (MFA strip/takeover) | 复核成立 | 未处理 |  |
| F051 | authz | `services/identity/synwevo_identity/app.py:59` | App entitlement revocation and workspace app-disable are undone on every account-service start; docweave revoke is a no-op | 复核成立 | 未处理 |  |
| F052 | authz | `services/identity/synwevo_identity/provider_routes.py:82` | /oidc/authorize ignores the client's registered scopes, so any public client (portal-web, docweave-web) can mint docweave.write and offline_access tokens | 复核成立 | 未处理 |  |
| F053 | correctness | `services/identity/synwevo_identity/passkeys.py:239` | Passkey sign-in spends the newest anonymous challenge, not the caller's, so concurrent sign-ins fail and anyone can DoS passkey login | 复核成立 | 未处理 |  |
| F055 | reliability | `services/identity/synwevo_identity/cli.py:310` | Setting SYNWEVO_SECRET, which `cli info` recommends, silently makes every stored TOTP seed undecryptable | 复核成立 | 未处理 |  |
| F076 | security | `services/identity/synwevo_identity/passkeys.py:241` | Passkey sign-in spends the globally newest anonymous challenge, not the caller's, so concurrent logins fail and anyone can block passkey login | 复核成立 | 未处理 |  |
| F099 | authz | `apps/docs/synwevo_docs/api/app.py:144` | Workspace roles and can_export are never enforced: viewers can write, delete, run jobs and download everything | 复核成立 | 未处理 |  |
| F100 | authz | `packages/core/synwevo_core/http.py:254` | Docs session gate and workspace switch skip app entitlement and workspace status; revocation or freeze does not stop UI access | 复核成立 | 未处理 |  |
| F101 | fake-or-placeholder | `apps/docs/synwevo_docs/api/v1.py:133` | Template version pinning is silently ignored (API and definition import); generation always uses the head version | 复核成立 | 未处理 |  |
| F129 | correctness | `apps/docs/synwevo_docs/views.py:218` | Template view crashes (ImportError) for PDF/PNG/JPEG templates that have any field | 复核成立 | 未处理 |  |
| F132 | authz | `apps/docs/synwevo_docs/jobs.py:489` | No workspace role or can_export enforcement for writes, job runs, deletes or downloads | 复核成立 | 未处理 |  |
| F156 | performance | `apps/docs/synwevo_docs/engine/records.py:174` | record_context rebuilds the whole table view for every record, so plan building is O(N²) | 复核成立 | 未处理 |  |
| F174 | security | `apps/docs/synwevo_docs/engine/detect.py:82` | Zip bomb in format detection: [Content_Types].xml / mimetype read fully with no size bound before any limit check | 复核成立 | 未处理 |  |
| F175 | security | `apps/docs/synwevo_docs/engine/sources.py:193` | Quadratic HTML regexes let a small .html source freeze the whole process | 复核成立 | 未处理 |  |
| F176 | security | `apps/docs/synwevo_docs/engine/tabular.py:245` | XLSX row gaps and blank CSV lines bypass max_cells: a 4.8 KB XLSX expands to millions of rows (OOM) | 复核成立 | 未处理 |  |
| F177 | correctness | `apps/docs/synwevo_docs/engine/docx_blocks.py:175` | Repeat content blocks fail every record when the DOCX template has any header or footer | 复核成立 | 未处理 |  |
| F179 | correctness | `apps/docs/synwevo_docs/engine/layout.py:143` | fit-mode delivery strips namespace declarations still referenced by mc:Ignorable, producing non-conformant DOCX | 复核成立 | 未处理 |  |
| F180 | correctness | `apps/docs/synwevo_docs/services.py:403` | Macro-enabled templates (xlsm/docm/pptm) are delivered as .xlsx/.docx/.pptx with macroEnabled content types | 复核成立 | 未处理 |  |
| F197 | correctness | `apps/docs/synwevo_docs/engine/image_raster.py:93` | Raster image fill leaves template pixels in fields whose value is empty | 复核成立 | 未处理 |  |
| F198 | reliability | `apps/docs/synwevo_docs/engine/pdfium_shim.py:364` | pdfium_shim still makes unserialised PDFium calls (DW-052 crash class not fully fixed) | 复核成立 | 未处理 |  |
| F199 | security | `apps/docs/synwevo_docs/engine/desensitize.py:218` | DOCX desensitization silently leaves sensitive text in place | 复核成立 | 未处理 |  |
| F200 | security | `apps/docs/synwevo_docs/engine/desensitize.py:179` | XLSX desensitization skips numeric cells and non-cell content | 复核成立 | 未处理 |  |
| F201 | security | `apps/docs/synwevo_docs/engine/pdf_engine.py:197` | PDF page rendering has no pixel budget (memory DoS through page size and OCR) | 复核成立 | 未处理 |  |
| F220 | correctness | `apps/docs/web/src/pages/Home.tsx:214` | Deleting a template from the library makes every project using it unopenable | 复核成立 | 未处理 |  |
| F221 | security | `apps/docs/synwevo_docs/regression.py:42` | Regression capture writes workspace source files into the source tree (git-tracked, outside data/), and the UI calls them desensitized | 复核成立 | 未处理 |  |
| F315 | security | `packages/core/synwevo_core/oidc.py:162` | Service-account secrets cannot be rotated: re-registering prints a new secret that is never stored | 复核成立 | 未处理 |  |
| F332 | fake-or-placeholder | `apps/docs/synwevo_docs/services.py:83` | Template version pinning (PUT /projects/{pid}/template version, definition import) is silently dropped; jobs always use template head | 复核成立 | 未处理 |  |
| F333 | authz | `packages/core/synwevo_core/machine.py:110` | Delegated tokens minted by any non-Docs client (e.g. public portal-web, app 'platform') are accepted by the Docs API without a docweave entitlement | 复核成立 | 未处理 |  |
| F335 | authz | `docs/security/permissions.md:234` | Workspace role matrix (viewer read-only, can_export) is not enforced anywhere in Synwevo Docs UI routes | 复核成立 | 未处理 |  |
| F338 | reliability | `scripts/platform.py:339` | `scripts/platform.py setup` installs a nonexistent package path, so the documented local platform setup fails | 复核成立 | 未处理 |  |
| F357 | reliability | `scripts/platform.py:339` | Documented install `python scripts/platform.py setup` cannot run on a clean checkout | 复核成立 | 未处理 |  |
| F359 | reliability | `infra/docker-compose.yml:16` | Compose env_file (.env.example) overrides the image's listen address and data dir; the documented container deployment is unreachable or loses its volume | 复核成立 | 未处理 |  |

## P2（167 条）

| ID | 类别 | 位置 | 发现 | 复核 | 状态 | 说明 |
|---|---|---|---|---|---|---|
| F008 | security | `packages/core/synwevo_core/sessions.py:111` | Admin sessions lose their 1-hour idle limit after the first request | — | 未处理 |  |
| F009 | api-contract | `packages/core/synwevo_core/http.py:180` | The catch-all exception handler returns None, so unexpected errors become bare 500s without the JSON contract or X-Request-ID | — | 未处理 |  |
| F010 | security | `packages/core/synwevo_core/env.py:85` | env.docs() lets a leftover legacy DOCWEAVE_ value override an explicit SYNWEVO_ value, even for critical keys | — | 未处理 |  |
| F011 | tenant-isolation | `packages/core/synwevo_core/auth.py:170` | Legacy dw_ keys skip workspace existence, status and app-enablement checks; the CLI default tenant is the unclaimed legacy workspace | — | 未处理 |  |
| F012 | performance | `packages/core/synwevo_core/http.py:227` | The async gate runs synchronous SQLite writes on the event loop for every request, static assets included | — | 未处理 |  |
| F013 | authz | `packages/core/synwevo_core/machine.py:124` | The machine principal carries no role or can_export, so API downloads cannot honour the export permission | — | 未处理 |  |
| F029 | authz | `packages/auth-client/python/synwevo_auth_client/session_routes.py:127` | Workspace switch and the per-request gate check only membership: Docs usable in workspaces where the app is disabled, the entitlement revoked or the workspace not active | 复核成立 | 未处理 |  |
| F031 | fake-or-placeholder | `apps/portal/synwevo_portal/routes.py:56` | Portal workspace switcher has no effect on the apps shown or the workspace Docs opens in | — | 未处理 |  |
| F032 | ui | `packages/auth-client/python/synwevo_auth_client/routes.py:77` | RP redirects to /no-access, /sign-in-failed and /signed-out, which no shell renders | — | 未处理 |  |
| F033 | security | `packages/auth-client/python/synwevo_auth_client/rp.py:140` | Unauthenticated /auth/start inserts rows forever; RP and session sweepers are never invoked | — | 未处理 |  |
| F034 | reliability | `infra/server/synwevo_server/app.py:22` | Unknown SYNWEVO_ROLE silently yields an empty server with healthy probes | — | 未处理 |  |
| F035 | correctness | `infra/server/synwevo_server/app.py:49` | The Docs process never registers its own launch URL, so portal cards point at the hard-coded dev URL | — | 未处理 |  |
| F036 | reliability | `packages/auth-client/web/src/session.tsx:80` | Session context turns every non-401 error into 'signed out', causing sign-in redirect loops | — | 未处理 |  |
| F037 | ui | `packages/auth-client/web/src/api.ts:73` | Server 500s are reported to users as 'blocked by a gateway, never reached Synwevo' | — | 未处理 |  |
| F038 | a11y | `packages/ui/src/ui.tsx:98` | Shared Modal/Dialog is not IME- or keyboard-safe and has no accessible name or focus management | — | 未处理 |  |
| F039 | a11y | `packages/ui/src/styles.css:22` | Design tokens fail WCAG AA contrast (muted text, dark primary buttons, menu focus indicator) | — | 未处理 |  |
| F040 | reliability | `scripts/platform.py:356` | `platform.py test docs/identity/integration` crashes with KeyError | — | 未处理 |  |
| F041 | security | `infra/Dockerfile:45` | No .dockerignore: the build context includes data/, .local/backups and .env, and the image bakes host artifacts | — | 未处理 |  |
| F054 | security | `services/identity/synwevo_identity/cli.py:256` | Re-registering a client prints a new client_secret that is never stored; old secret stays valid and there is no rotate command | 复核成立 | 未处理 |  |
| F056 | security | `services/identity/synwevo_identity/mail.py:102` | Raw invitation, reset and verify tokens are persisted in plaintext in mail_outbox.body and never purged | — | 未处理 |  |
| F057 | security | `services/identity/synwevo_identity/provider_routes.py:285` | Back-channel logout is marked done before delivery and never retried, so the Docs session survives 'sign out everywhere' | — | 未处理 |  |
| F058 | security | `services/identity/synwevo_identity/auth_routes.py:241` | Ending a session (logout, revoke device, revoke others) leaves OIDC access and refresh tokens alive; refresh does not check the central session | — | 未处理 |  |
| F059 | security | `services/identity/synwevo_identity/auth_routes.py:54` | With TRUST_PROXY on, the leftmost X-Forwarded-For entry (client-controlled) is used, so the IP rate-limit bucket can be bypassed | — | 未处理 |  |
| F060 | security | `services/identity/synwevo_identity/auth_routes.py:187` | No throttling of second-factor guesses across tickets; the per-ticket limit is non-atomic | — | 未处理 |  |
| F061 | security | `services/identity/synwevo_identity/auth_routes.py:220` | Re-auth password checks are unthrottled and outside the login backoff | — | 未处理 |  |
| F062 | reliability | `services/identity/synwevo_identity/auth_routes.py:94` | Anonymous /api/auth/bootstrap creates rows before checking the token and leaves orphans on failure | — | 未处理 |  |
| F063 | reliability | `services/identity/synwevo_identity/app.py:41` | No garbage collection is ever scheduled for auth state tables | — | 未处理 |  |
| F064 | security | `packages/core/synwevo_core/sessions.py:111` | Admin session idle limit (1 h) is extended to 12 h on the first request | — | 未处理 |  |
| F065 | governance | `services/identity/synwevo_identity/cli.py:188` | Privileged CLI operations (clients, secrets, app grants, keys, bootstrap admin) leave no audit record | — | 未处理 |  |
| F066 | correctness | `services/identity/synwevo_identity/cli.py:215` | `client show` crashes with NameError (json not imported) | — | 未处理 |  |
| F067 | security | `services/identity/synwevo_identity/auth_routes.py:156` | Passkey sign-in bypasses TOTP for MFA-enrolled accounts, even without user verification | — | 未处理 |  |
| F077 | security | `services/identity/web/src/Admin.tsx:263` | Admin dangerous actions collect the admin password via window.prompt (plaintext, outside the design system, no impact statement) | — | 未处理 |  |
| F078 | security | `services/identity/synwevo_identity/admin_routes.py:218` | Suspending an account does not revoke its OIDC tokens, so restoring it brings pre-suspension refresh and access tokens back to life | — | 未处理 |  |
| F079 | security | `services/identity/synwevo_identity/auth_routes.py:220` | Reauth endpoints check the password without throttling, so anyone holding a session can guess the password indefinitely | — | 未处理 |  |
| F080 | security | `services/identity/synwevo_identity/mail.py:101` | The mail outbox keeps raw reset, invite and verify tokens in cleartext indefinitely | — | 未处理 |  |
| F081 | security | `services/identity/synwevo_identity/auth_routes.py:411` | /api/auth/forgot is unthrottled and every call invalidates the previous reset link, so a victim can be mail-bombed and locked out of recovery | — | 未处理 |  |
| F082 | ui | `services/identity/web/src/pages/Recover.tsx:155` | A too-short password on the reset page burns the one-time link | — | 未处理 |  |
| F083 | fake-or-placeholder | `services/identity/web/src/main.tsx:64` | Email verification is unreachable: nothing in the UI sends it, and the emailed /verify-email link lands on the SPA 404 | — | 未处理 |  |
| F084 | ui | `services/identity/web/src/Admin.tsx:163` | Admin claim target list is capped at the 50 newest accounts, with no search and swallowed errors | — | 未处理 |  |
| F085 | security | `services/identity/synwevo_identity/auth_routes.py:156` | A possession-only passkey sign-in skips an account's enrolled TOTP entirely | — | 未处理 |  |
| F086 | docs-mismatch | `services/identity/web/src/pages/Login.tsx:154` | The bootstrap screen tells the operator to run a command that does not exist | — | 未处理 |  |
| F102 | api-contract | `packages/core/synwevo_core/http.py:180` | Unhandled-exception handler returns None: 500s become plain text with no JSON envelope or request_id | — | 未处理 |  |
| F103 | api-contract | `packages/core/synwevo_core/http.py:169` | /api/v1 validation, 404 and 405 errors bypass the documented error envelope | — | 未处理 |  |
| F104 | security | `apps/docs/synwevo_docs/api/v1.py:355` | Signed download links are not re-authorized: key revocation, user removal and workspace freeze don't invalidate them, and they are reusable | — | 未处理 |  |
| F105 | reliability | `apps/docs/synwevo_docs/api/app.py:547` | Upload and editor size limits apply only after the whole body has been received and parsed | — | 未处理 |  |
| F106 | security | `apps/docs/synwevo_docs/jobs.py:925` | report.csv (UI, v1 and inside every ZIP) writes source-derived labels unescaped: CSV/DDE formula injection | — | 未处理 |  |
| F107 | authz | `apps/docs/synwevo_docs/cli.py:43` | Legacy dw_ API keys bypass workspace status and app entitlement; CLI mints keys for any tenant string without validation or audit | — | 未处理 |  |
| F108 | tenant-isolation | `apps/docs/synwevo_docs/webhooks.py:151` | Webhooks are deployment-global: every workspace's job events go to every configured endpoint; no subscriptions, rotation or redelivery | — | 未处理 |  |
| F109 | data-loss | `apps/docs/synwevo_docs/definitions.py:317` | Definition imports are not atomic and bypass mapping concurrency | — | 未处理 |  |
| F110 | api-contract | `apps/docs/synwevo_docs/definitions.py:27` | Documented PPTX text-by-position fields cannot be created; several selector errors become 500s | — | 未处理 |  |
| F111 | docs-mismatch | `docs/integration/api.md:288` | Webhook header names in docs were mangled by the rebrand ("X-Synwevo Docs-Signature"); code sends X-DocWeave-* | — | 未处理 |  |
| F112 | security | `apps/docs/synwevo_docs/engine/blocks.py:44` | User-supplied regex in per_block rules runs unbounded in the shared process (ReDoS) | — | 未处理 |  |
| F113 | tenant-isolation | `apps/docs/synwevo_docs/api/v1.py:395` | Path-import roots are shared by all tenants and leak file existence outside the roots | — | 未处理 |  |
| F114 | correctness | `apps/docs/synwevo_docs/api/app.py:120` | Desensitized copies cannot be downloaded unless 'add to project' is checked | — | 未处理 |  |
| F130 | api-contract | `apps/docs/synwevo_docs/services.py:83` | Template version pinning is accepted but silently ignored (not stored, not used) | 复核成立 | 未处理 |  |
| F131 | authz | `apps/docs/synwevo_docs/jobs.py:781` | Queued/running/retried jobs never re-check the submitter's authorization or workspace/project state | 复核成立 | 未处理 |  |
| F133 | security | `apps/docs/synwevo_docs/regression.py:42` | Regression capture writes real user source files into the repository tree, outside SYNWEVO_DATA_DIR | 复核成立 | 未处理 |  |
| F134 | correctness | `apps/docs/synwevo_docs/jobs.py:117` | Job creation silently drops sources still parsing or in error | — | 未处理 |  |
| F135 | reliability | `apps/docs/synwevo_docs/services.py:138` | Sources stuck in 'parsing' forever after a restart | — | 未处理 |  |
| F136 | reliability | `apps/docs/synwevo_docs/jobs.py:365` | A job orphaned by a runner-thread crash is never recovered in the same process | — | 未处理 |  |
| F137 | security | `apps/docs/synwevo_docs/jobs.py:925` | Report CSV (生成清单.csv / report.csv) is vulnerable to formula/DDE injection | — | 未处理 |  |
| F138 | performance | `apps/docs/synwevo_docs/jobs.py:786` | Selecting the next pending item is quadratic in job size | — | 未处理 |  |
| F139 | performance | `apps/docs/synwevo_docs/jobs.py:698` | No per-workspace admission, quotas or fairness: one runner serves every tenant oldest-first | — | 未处理 |  |
| F140 | concurrency | `apps/docs/synwevo_docs/services.py:441` | Template field edits and mapping saves lose concurrent updates (no compare-and-set) | — | 未处理 |  |
| F141 | correctness | `apps/docs/synwevo_docs/services.py:260` | Desensitized copies cannot be downloaded unless they are added to the project | — | 未处理 |  |
| F142 | api-contract | `apps/docs/synwevo_docs/jobs.py:507` | An invalid project-level output.layout is silently downgraded to 'off' | — | 未处理 |  |
| F143 | reliability | `apps/docs/synwevo_docs/services.py:281` | SourceDoc cache can hold dozens of large parsed documents per version | — | 未处理 |  |
| F157 | correctness | `apps/docs/synwevo_docs/engine/generate.py:45` | Pagination policy (warn/fit) is silently not applied when page counting is unavailable | 复核成立 | 未处理 |  |
| F158 | data-loss | `apps/docs/synwevo_docs/engine/records.py:60` | Source files that fail sample splitting are silently left out of jobs | — | 未处理 |  |
| F159 | security | `apps/docs/synwevo_docs/engine/generate.py:153` | CSV export writes formula/DDE payloads from source data verbatim | — | 未处理 |  |
| F160 | correctness | `apps/docs/synwevo_docs/engine/generate.py:149` | CSV export fails with formula_uncomputed when a formula correctly evaluates to an empty string | — | 未处理 |  |
| F161 | correctness | `apps/docs/synwevo_docs/engine/transforms.py:124` | prefix/suffix are lost when followed by trim/upper/lower/collapse_spaces | — | 未处理 |  |
| F162 | security | `apps/docs/synwevo_docs/engine/transforms.py:65` | Transform validation ignores op parameters; pad_left width is unbounded and bad parameters escape as non-ValueError | — | 未处理 |  |
| F163 | correctness | `apps/docs/synwevo_docs/engine/formula.py:971` | Formula engine returns wrong values for RIGHT, exact-match lookups and XLOOKUP modes | — | 未处理 |  |
| F164 | reliability | `apps/docs/synwevo_docs/engine/formula.py:1307` | Errors from formula edge cases abort the whole document instead of producing Excel error values | — | 未处理 |  |
| F165 | performance | `apps/docs/synwevo_docs/engine/limits.py:32` | Preflight is not bounded by max_records_per_job and plans every record synchronously | — | 未处理 |  |
| F178 | fake-or-placeholder | `apps/docs/synwevo_docs/engine/layout.py:226` | fit-mode 'content lost / truncated' integrity check can never fire; its test is tautological | 复核成立 | 未处理 |  |
| F181 | security | `apps/docs/synwevo_docs/engine/converters.py:160` | LibreOffice runs untrusted documents without network, environment or memory isolation; timeout does not kill the real process | — | 未处理 |  |
| F182 | correctness | `apps/docs/synwevo_docs/engine/docx_core.py:288` | DOCX fill crashes on XML-illegal control characters in source values | — | 未处理 |  |
| F183 | correctness | `apps/docs/synwevo_docs/engine/xlsx_cache.py:52` | LibreOffice recalc fallback overwrites correct caches with strings for date/time and error results | — | 未处理 |  |
| F184 | correctness | `apps/docs/synwevo_docs/engine/detect.py:48` | Content sniffing misclassifies text files starting with 'BM' as BMP (and any '%PDF' in the first KB as PDF) | — | 未处理 |  |
| F185 | correctness | `apps/docs/synwevo_docs/engine/generate.py:48` | layout=fit/warn silently turns into no check at all when LibreOffice is unavailable | — | 未处理 |  |
| F186 | security | `apps/docs/synwevo_docs/engine/generate.py:159` | CSV export writes source-derived values without formula/DDE neutralisation | — | 未处理 |  |
| F187 | performance | `apps/docs/synwevo_docs/engine/xlsx_doc.py:168` | XLSX fill is quadratic in sheet rows: every set_cell rescans all rows and cells | — | 未处理 |  |
| F188 | performance | `apps/docs/synwevo_docs/engine/blocks.py:156` | Per-block DOCX split keeps a full copy of the package (all media and parts) for every block in memory | — | 未处理 |  |
| F189 | security | `apps/docs/synwevo_docs/engine/ooxml.py:92` | No per-part size limit on OOXML XML parsing with huge_tree enabled | — | 未处理 |  |
| F190 | security | `apps/docs/synwevo_docs/engine/blocks.py:46` | User-supplied block-marker regex runs unbounded over every line/row (ReDoS) | — | 未处理 |  |
| F202 | correctness | `apps/docs/synwevo_docs/engine/pdf_engine.py:504` | A box whose font size is below min_font_size always fails, even for empty values | — | 未处理 |  |
| F203 | reliability | `apps/docs/synwevo_docs/engine/image_template.py:154` | Image-template preview, page render and PDF export require PyMuPDF, although image templates are advertised without it | — | 未处理 |  |
| F204 | correctness | `apps/docs/synwevo_docs/engine/pptx_engine.py:519` | PPTX fill never checks text fit and ignores some table policies, contrary to its docstring | — | 未处理 |  |
| F205 | correctness | `apps/docs/synwevo_docs/engine/pptx_engine.py:427` | PPTX multi-line value in an empty template cell produces schema-invalid a:br | — | 未处理 |  |
| F206 | correctness | `apps/docs/synwevo_docs/engine/pdf_engine.py:544` | pdf_field fill handles only one widget and treats every widget type as text | — | 未处理 |  |
| F207 | correctness | `apps/docs/synwevo_docs/engine/pdf_engine.py:130` | Pages with an OCR text layer are classified "text", so pixel clearing is not the default | — | 未处理 |  |
| F208 | api-contract | `apps/docs/synwevo_docs/services.py:259` | Desensitized copies cannot be downloaded unless they are added to the project | — | 未处理 |  |
| F209 | performance | `apps/docs/synwevo_docs/engine/image_raster.py:107` | Raster fill repeats heavy full-resolution pixel analysis per record and discards the result | — | 未处理 |  |
| F210 | performance | `apps/docs/synwevo_docs/engine/pptx_engine.py:209` | render_pptx base64-encodes every picture even for text-only callers, including per-item job verification | — | 未处理 |  |
| F211 | performance | `apps/docs/synwevo_docs/engine/desensitize.py:66` | Desensitize pseudonym allocation is quadratic and the word list is a linear scan per word | — | 未处理 |  |
| F222 | correctness | `apps/docs/web/src/pages/JobPage.tsx:45` | Job page stops polling when a job enters 'cancelling' and offers a retry the server rejects | — | 未处理 |  |
| F223 | performance | `apps/docs/web/src/pages/JobPage.tsx:30` | Job items capped at 5000 and filtered client-side; polling re-downloads up to 5000 items every 900ms | — | 未处理 |  |
| F224 | a11y | `apps/docs/web/src/pages/Home.tsx:96` | Home tbody keyboard handler swallows Enter from row '⋯' menus and opens a different project | — | 未处理 |  |
| F225 | data-loss | `apps/docs/web/src/pages/SheetEditor.tsx:149` | SheetEditor allows saving before the template has loaded, overwriting it with a blank sheet | — | 未处理 |  |
| F226 | data-loss | `apps/docs/web/src/pages/DocEditor.tsx:266` | Template editors lose unsaved edits on in-app navigation; SheetEditor has no unload guard at all | — | 未处理 |  |
| F227 | concurrency | `apps/docs/synwevo_docs/builtin_service.py:42` | Builtin template saves are last-writer-wins with no base version | — | 未处理 |  |
| F228 | ui | `apps/docs/web/src/pages/SheetEditor.tsx:212` | Enter handlers in the sheet cell editor and the source find box ignore IME composition | — | 未处理 |  |
| F229 | a11y | `apps/docs/web/src/pages/SheetEditor.tsx:125` | SheetEditor grid traps Tab (and Shift+Tab) focus | — | 未处理 |  |
| F230 | a11y | `apps/docs/web/src/pages/Workspace.tsx:855` | Workspace AreaDialog options and local modals are not keyboard operable | — | 未处理 |  |
| F231 | correctness | `apps/docs/web/src/pages/Workspace.tsx:182` | Workspace viewer and locate fetches have no stale-response guard | — | 未处理 |  |
| F232 | ui | `apps/docs/web/src/pages/DocEditor.tsx:321` | DocEditor repeat-row/block attribute inputs and selects show stale values after edits | — | 未处理 |  |
| F233 | performance | `apps/docs/web/src/pages/Home.tsx:31` | Project list has no pagination or server-side search/sort | — | 未处理 |  |
| F234 | docs-mismatch | `apps/docs/web/src/pages/About.tsx:21` | About page no longer offers the AGPL source or notice, although the backend notice says it does | — | 未处理 |  |
| F248 | data-loss | `apps/docs/web/src/components/RegressionDialog.tsx:34` | UI regression capture writes real user files into the tracked source tree and tells users to commit them | 复核成立 | 未处理 |  |
| F249 | security | `apps/docs/web/src/views/DocView.tsx:157` | Untrusted DOCX shading/highlight values are injected into CSS background, so uploaded documents can make the browser fetch external URLs | — | 未处理 |  |
| F250 | correctness | `apps/docs/web/src/components/DesensitizeDialog.tsx:59` | Desensitized copies cannot be downloaded when 'add to project' is unchecked | — | 未处理 |  |
| F251 | ui | `apps/docs/web/src/components/Transforms.tsx:46` | Value-map transform textarea cannot take a new line by typing | — | 未处理 |  |
| F252 | performance | `apps/docs/web/src/components/GenerateDialog.tsx:22` | Every naming keystroke or format toggle re-runs full preflight over all records on the server | — | 未处理 |  |
| F253 | security | `apps/docs/web/src/components/RuleDialog.tsx:154` | User-supplied regex block anchors are compiled with Python re and evaluated on every debounced keystroke (ReDoS) | — | 未处理 |  |
| F254 | a11y | `apps/docs/web/src/components/FilePanel.tsx:69` | Uploading sources/templates and switching files are mouse-only; reordering is drag-only | — | 未处理 |  |
| F255 | a11y | `apps/docs/web/src/views/DocView.tsx:268` | Document, PDF, slide and text selection have no keyboard or single-pointer alternative | — | 未处理 |  |
| F256 | a11y | `packages/ui/src/ui.tsx:32` | Shared Modal re-focuses its first field on every parent render; no focus trap, restore or accessible name | — | 未处理 |  |
| F257 | correctness | `apps/docs/web/src/views/GridView.tsx:168` | Stale grid windows / PDF word layers from a previous sheet, file or preview record are merged into the new view | — | 未处理 |  |
| F258 | correctness | `apps/docs/web/src/components/MappingDialog.tsx:114` | Merging a second source into an existing mapping silently drops the transforms/aggregation/value mode/style set in the dialog | — | 未处理 |  |
| F259 | performance | `apps/docs/web/src/views/TextView.tsx:22` | Text sources render every line (server returns the whole file) with no virtualization | — | 未处理 |  |
| F274 | test-gap | `apps/docs/tests/test_api_v1.py:202` | Rate-limit test patches a nonexistent hook and never exercises HTTP 429 | — | 未处理 |  |
| F275 | docs-mismatch | `apps/docs/tests/test_api_docs.py:64` | Shipped example definition leaves template text '示例' in every output; its test cannot notice | — | 未处理 |  |
| F276 | security | `apps/docs/synwevo_docs/engine/generate.py:158` | CSV export and job manifest write source-derived values verbatim (formula/DDE injection), untested | — | 未处理 |  |
| F277 | authz | `apps/docs/synwevo_docs/api/v1.py:355` | Signed download links outlive the credential that minted them; no test covers revocation | — | 未处理 |  |
| F278 | test-gap | `apps/docs/tests/test_api_v1.py:48` | Machine-API tenant isolation is asserted only for /projects | — | 未处理 |  |
| F279 | test-gap | `apps/docs/tests/test_cache_headers.py:36` | Security tests in test_cache_headers are skipped whenever the frontend is not built | — | 未处理 |  |
| F280 | security | `apps/docs/tests/test_api_v1.py:211` | Default UI mode 'none' is enshrined: every /api route anonymous for the legacy tenant, no CSRF and no Host check | — | 未处理 |  |
| F295 | fake-or-placeholder | `apps/docs/synwevo_docs/engine/layout.py:226` | fit-mode content-integrity check (content_lost/content_truncated) can never fire; its test is tautological | 复核成立 | 未处理 |  |
| F297 | concurrency | `apps/docs/synwevo_docs/jobs.py:808` | A worker that lost its lease can still fail an item another worker is processing (and can claim items without holding the lease) | — | 未处理 |  |
| F298 | api-contract | `apps/docs/synwevo_docs/jobs.py:658` | Concurrent retries with the same idempotency key: the loser gets 409 job_active instead of an idempotent replay | — | 未处理 |  |
| F299 | reliability | `apps/docs/synwevo_docs/webhooks.py:152` | Webhook cursor is not advanced while webhooks are disabled; first enablement backfills every historical event of every workspace | — | 未处理 |  |
| F300 | security | `apps/docs/scripts/snapshot.py:203` | Backup restore extraction checks member names only; symlink/hardlink members can write outside the target on the Python 3.12 image | — | 未处理 |  |
| F301 | test-gap | `apps/docs/tests/test_snapshot.py:112` | Restore overwrite-refusal test is tautological: passes even if restore overwrites the database | — | 未处理 |  |
| F302 | test-gap | `apps/docs/tests/test_job_reliability.py:75` | Cancel-vs-completion arbitration test is nondeterministic and contains a tautology | — | 未处理 |  |
| F313 | reliability | `apps/docs/scripts/_testenv.py:113` | Isolated test-server harness is broken, so every browser E2E script crashes in its default mode | 复核成立 | 未处理 |  |
| F314 | security | `apps/docs/scripts/snapshot.py:261` | Restore brings back revoked access; it only prints advice, and the advice is wrong | 复核成立 | 未处理 |  |
| F316 | authz | `packages/core/synwevo_core/machine.py:53` | Legacy dw_ API keys ignore workspace freeze/deletion and app disablement | 复核成立 | 未处理 |  |
| F317 | security | `packages/core/synwevo_core/machine.py:72` | Docs API accepts platform tokens without checking the audience or the issuing app | — | 未处理 |  |
| F318 | data-loss | `apps/docs/scripts/snapshot.py:218` | snapshot restore --force overwrites in place, keeps stale WAL/SHM files and does not check for a running server | — | 未处理 |  |
| F319 | test-gap | `apps/docs/scripts/ui_e2e_keyboard.py:162` | Keyboard E2E theme check writes the pre-rebrand storage key, which the app ignores | — | 未处理 |  |
| F320 | api-contract | `tests/integration/automation_example.py:190` | automation_example's --idempotency-key does not stop duplicate generation when the script is re-run | — | 未处理 |  |
| F321 | correctness | `tests/integration/automation_example.py:149` | automation_example downloads and verifies only the first 2000 artifacts but reports success | — | 未处理 |  |
| F322 | correctness | `docs/integration/example-definition.json:4` | The documented automation definition produces wrong contract text, and the example never checks content | — | 未处理 |  |
| F323 | test-gap | `tests/integration/test_authz_isolation.py:173` | Cross-workspace isolation tests skip artifacts, ZIPs, events, templates and previews; the job test has no positive control | — | 未处理 |  |
| F324 | test-gap | `services/identity/tests/test_admin.py:193` | The only 'platform admin cannot read documents' check runs where the business route does not exist | — | 未处理 |  |
| F334 | authz | `packages/core/synwevo_core/http.py:254` | Docs browser sessions never re-check app entitlement, app status or workspace status; session rows from any role are accepted | 复核成立 | 未处理 |  |
| F336 | docs-mismatch | `docs/integration/automation.md:134` | Webhook header names documented as `X-Synwevo Docs-*` (invalid, contains a space); server sends `X-DocWeave-*`, so the documented receiver fails on every delivery | 复核成立 | 未处理 |  |
| F337 | authz | `apps/docs/synwevo_docs/jobs.py:697` | Running and retried jobs never re-check the submitter's permissions; signed links survive key revocation, contradicting permissions.md §6 | 复核成立 | 未处理 |  |
| F339 | security | `docs/security/identity-migration.md:184` | Restore guidance: 'rotate signing keys to invalidate all tokens' does not work, and the list of resurrected credentials is incomplete | — | 未处理 |  |
| F340 | api-contract | `packages/core/synwevo_core/http.py:180` | Unhandled-exception handler returns None, so 500s have no JSON envelope or X-Request-ID (api.md promises both) | — | 未处理 |  |
| F341 | api-contract | `docs/integration/api.md:86` | Request validation errors return FastAPI's 422 `{"detail": [...]}` instead of the documented /api/v1 error envelope | — | 未处理 |  |
| F342 | security | `packages/core/synwevo_core/sessions.py:111` | Admin 1-hour idle timeout is lost after the first request; load() extends every session by the 12h default | — | 未处理 |  |
| F343 | authz | `apps/docs/synwevo_docs/cli.py:293` | Legacy dw_ keys bypass workspace freeze and app entitlement, and the CLI default binds keys to the unclaimed legacy workspace | — | 未处理 |  |
| F344 | tenant-isolation | `apps/docs/synwevo_docs/webhooks.py:158` | Webhooks are deployment-global: every workspace's job events go to every configured URL with one shared secret | — | 未处理 |  |
| F345 | docs-mismatch | `docs/integration/api.md:271` | Webhook body is not the documented shared event structure (no seq/job_id; adds tenant/job) | — | 未处理 |  |
| F346 | correctness | `packages/core/synwevo_core/oidc.py:162` | Re-registering an OIDC client prints a new client_secret that is never stored; service-account secrets cannot be rotated | — | 未处理 |  |
| F347 | correctness | `apps/docs/synwevo_docs/definitions.py:322` | Project definition import is non-atomic and validates fields against head instead of the requested version | — | 未处理 |  |
| F348 | security | `apps/docs/synwevo_docs/jobs.py:925` | report.csv / 生成清单.csv writes user-controlled values without formula-injection neutralisation | — | 未处理 |  |
| F358 | test-gap | `apps/docs/scripts/_testenv.py:113` | Browser E2E harness crashes in its default isolated mode; docs still report the suites as passing | 复核成立 | 未处理 |  |
| F360 | docs-mismatch | `docs/operations.md:24` | operations.md production runbook commands point at paths, services and tools that no longer exist | — | 未处理 |  |
| F361 | security | `docs/operations.md:48` | operations.md backup/restore procedure can bring revoked credentials back and re-run jobs; it contradicts DEPLOY.md and identity-migration.md | — | 未处理 |  |
| F362 | docs-mismatch | `docs/operations.md:79` | Documented artifact/blob cleanup procedure has no implementation | — | 未处理 |  |
| F363 | docs-mismatch | `apps/docs/synwevo_docs/engine/limits.py:22` | Legacy DOCWEAVE_* limit variables are silently ignored, contrary to the rebrand compatibility promise | — | 未处理 |  |
| F364 | docs-mismatch | `docs/DEPLOY.md:53` | DEPLOY.md and ARCHITECTURE.md describe the pre-platform auth model (no accounts, three UI modes, no-auth refused) | — | 未处理 |  |
| F365 | docs-mismatch | `docs/quality/verification.md:273` | Release and current-status claims say '0 confirmed defects' while open defects are recorded elsewhere and missing from the issue register | — | 未处理 |  |

## P3（157 条）

| ID | 类别 | 位置 | 发现 | 复核 | 状态 | 说明 |
|---|---|---|---|---|---|---|
| F014 | reliability | `packages/core/synwevo_core/sessions.py:207` | Constant-time compares on attacker-controlled strings raise TypeError on non-ASCII input, returning 500 | — | 未处理 |  |
| F015 | docs-mismatch | `packages/core/synwevo_core/store.py:519` | Store(migrate=False) still changes the target, and Auth creates schema outside Store | — | 未处理 |  |
| F016 | correctness | `packages/core/synwevo_core/apps.py:227` | bootstrap_docs_app resets access_policy to 'members' on every start and falls back to a localhost launch URL | — | 未处理 |  |
| F017 | tenant-isolation | `packages/core/synwevo_core/context.py:30` | A falsy tenant falls back to the legacy 'default' workspace | — | 未处理 |  |
| F018 | security | `packages/core/synwevo_core/oidc.py:198` | The logout-delivery SSRF guard allows https to any internal address and http to loopback, despite its docstring | — | 未处理 |  |
| F019 | performance | `packages/core/synwevo_core/store.py:63` | Blob reachability and revocation queries have no supporting indexes | — | 未处理 |  |
| F020 | reliability | `packages/core/synwevo_core/sessions.py:154` | Nothing sweeps sessions, codes, tokens, rate buckets or blobs | — | 未处理 |  |
| F021 | correctness | `packages/core/synwevo_core/identity.py:268` | Last-owner guard counts suspended or deleted owners; change_role overwrites can_export | — | 未处理 |  |
| F022 | docs-mismatch | `packages/core/pyproject.toml:13` | Package metadata leaves out authlib/httpx, the boundary-check claim is false, and other docs and defaults disagree | — | 未处理 |  |
| F023 | api-contract | `packages/core/synwevo_core/http.py:176` | Every ValueError is reported as a client 400 with the raw exception text | — | 未处理 |  |
| F024 | reliability | `packages/core/synwevo_core/passwords.py:138` | Password hashing semaphore blocks threadpool workers, so floods starve other sync endpoints | — | 未处理 |  |
| F025 | security | `packages/core/synwevo_core/auth.py:206` | Signed download links are bearer URLs that are not bound to the credential and cannot be revoked | — | 未处理 |  |
| F026 | security | `packages/core/synwevo_core/http.py:136` | Audit IP ignores the trusted-proxy setting | — | 未处理 |  |
| F042 | reliability | `scripts/platform.py:345` | setup uses `npm install` rather than `npm ci` | — | 未处理 |  |
| F043 | correctness | `packages/auth-client/python/synwevo_auth_client/routes.py:169` | /login redirect leaves '&' unescaped in next, splitting it into extra /auth/start parameters | — | 未处理 |  |
| F044 | docs-mismatch | `.env.example:50` | .env.example claims to list all settings but omits every platform/identity key, and has stale paths | — | 未处理 |  |
| F045 | ui | `apps/portal/web/src/main.tsx:7` | Portal ignores the theme system and duplicates components; shared UI package carries Docs-only code | — | 未处理 |  |
| F046 | test-gap | `scripts/check_brand.py:62` | check_brand catch-all rules hide real residue: E2E scripts write a legacy localStorage key the app no longer reads | — | 未处理 |  |
| F047 | ui | `packages/auth-client/web/src/PlatformNav.tsx:35` | PlatformNav shows spaces[0] as current when the session has no workspace, and it cannot be selected | — | 未处理 |  |
| F048 | reliability | `scripts/platform.py:256` | platform.py down can kill an unrelated process via a stale PID file | — | 未处理 |  |
| F049 | performance | `scripts/check_boundaries.py:92` | check_boundaries walks data/, .git and node_modules in full and does not check web imports | — | 未处理 |  |
| F068 | correctness | `services/identity/synwevo_identity/mfa.py:202` | MFA state counts passkeys from a table nothing writes (webauthn_credentials) | — | 未处理 |  |
| F069 | correctness | `services/identity/synwevo_identity/passkeys.py:131` | Passkeys registered as non-discoverable can never be used | — | 未处理 |  |
| F070 | concurrency | `services/identity/synwevo_identity/admin_routes.py:215` | Last-admin protection on suspend is a non-transactional check-then-act | — | 未处理 |  |
| F071 | security | `services/identity/synwevo_identity/provider_routes.py:317` | GET /oidc/logout ends the session without confirmation and before validating post_logout_redirect_uri | — | 未处理 |  |
| F072 | api-contract | `services/identity/synwevo_identity/provider_routes.py:265` | /oidc/revoke does not check that the token belongs to the authenticated client | — | 未处理 |  |
| F073 | api-contract | `services/identity/synwevo_identity/provider_routes.py:87` | Provider ignores prompt and max_age that the RP forwards | — | 未处理 |  |
| F074 | docs-mismatch | `services/identity/synwevo_identity/mail.py:169` | Dev mail file sink path is cwd-relative and holds raw tokens | — | 未处理 |  |
| F075 | correctness | `services/identity/synwevo_identity/cli.py:64` | `bootstrap token --force` produces a token the web endpoint always refuses | — | 未处理 |  |
| F087 | ui | `services/identity/web/src/components/MfaSection.tsx:95` | MFA and Passkey sections hide load errors behind an endless loading state | — | 未处理 |  |
| F088 | ui | `services/identity/web/src/components/PasskeySection.tsx:95` | Deleting a passkey is impossible on browsers without WebAuthn | — | 未处理 |  |
| F089 | docs-mismatch | `services/identity/web/src/components/MfaSection.tsx:199` | MFA panel says passkeys are not live, and factor accounting reads a table the passkey code never writes | — | 未处理 |  |
| F090 | fake-or-placeholder | `services/identity/web/src/pages/Login.tsx:51` | The remaining-recovery-codes notice is stored but never shown | — | 未处理 |  |
| F091 | ui | `services/identity/web/src/pages/Account.tsx:22` | Session list shows the raw enum for recovery-code sign-ins, and revocation has no error handling | — | 未处理 |  |
| F092 | a11y | `services/identity/web/src/components/MfaSection.tsx:128` | Unlabelled security inputs, and a show/hide button inside the password label | — | 未处理 |  |
| F093 | ui | `services/identity/web/src/Admin.tsx:254` | Admin search fetches on every keystroke with no debounce or abort, so stale results can win | — | 未处理 |  |
| F094 | concurrency | `services/identity/synwevo_identity/admin_routes.py:140` | Two concurrent claims of the legacy workspace can each add an owner | — | 未处理 |  |
| F095 | concurrency | `services/identity/synwevo_identity/admin_routes.py:215` | Two admins suspending each other at the same time can leave no active admin | — | 未处理 |  |
| F096 | reliability | `services/identity/synwevo_identity/passkeys.py:262` | WebAuthn challenges and MFA tickets are never swept, and anonymous callers can grow the challenge table | — | 未处理 |  |
| F097 | test-gap | `services/identity/tests/test_admin.py:193` | Several admin and security tests pass without exercising what their names claim | — | 未处理 |  |
| F115 | authz | `apps/docs/synwevo_docs/authz.py:24` | blob_readable ignores removal and deletion, so bytes of removed sources and deleted projects/templates stay downloadable by blob id | — | 未处理 |  |
| F116 | api-contract | `apps/docs/synwevo_docs/api/v1.py:240` | v1 template content for image templates returns the wrapper PDF labelled .png/.jpeg with an image MIME type | — | 未处理 |  |
| F117 | api-contract | `apps/docs/synwevo_docs/api/v1.py:263` | v1 project job list is capped at 50 but reports total=50 and next_offset=null | — | 未处理 |  |
| F118 | reliability | `apps/docs/synwevo_docs/webhooks.py:155` | Webhook dispatcher replays the entire event history on first enablement and keeps retrying endpoints removed from config | — | 未处理 |  |
| F119 | reliability | `apps/docs/synwevo_docs/jobs.py:558` | Request, job and actor correlation is not recorded; business actions are not audited | — | 未处理 |  |
| F120 | security | `apps/docs/synwevo_docs/jobs.py:798` | Per-item failure traces (server stack traces) are returned to UI and API clients | — | 未处理 |  |
| F121 | performance | `apps/docs/synwevo_docs/capabilities.py:30` | Capabilities endpoints spawn soffice and tesseract subprocesses on every call | — | 未处理 |  |
| F122 | performance | `apps/docs/synwevo_docs/licensing.py:140` | Corresponding Source tarball is rebuilt in memory on every request and embeds the server's absolute path | — | 未处理 |  |
| F123 | api-contract | `apps/docs/synwevo_docs/jobs.py:505` | Project output settings are unvalidated; a mistyped layout silently becomes 'off' | — | 未处理 |  |
| F124 | docs-mismatch | `apps/docs/synwevo_docs/api/v1.py:452` | Generated OpenAPI misdescribes auth and omits key contracts | — | 未处理 |  |
| F125 | reliability | `apps/docs/synwevo_docs/cli.py:68` | CLI rotate has no overlap and drops expiry; `openapi` opens and migrates the real data directory | — | 未处理 |  |
| F126 | security | `apps/docs/synwevo_docs/api/main.py:3` | Documented standalone entry bypasses the listen guard; default UI auth 'none' exposes legacy tenant | — | 未处理 |  |
| F127 | docs-mismatch | `apps/docs/README.md:89` | README and API docs describe an outdated auth model and wrong paths | — | 未处理 |  |
| F144 | concurrency | `apps/docs/synwevo_docs/jobs.py:809` | A stale worker's failure write can overwrite an item another worker is running | — | 未处理 |  |
| F145 | reliability | `apps/docs/synwevo_docs/jobs.py:387` | Same-host PID liveness check misjudges workers in other PID namespaces | — | 未处理 |  |
| F146 | correctness | `apps/docs/synwevo_docs/views.py:285` | Preview does not use the same generation options as export (layout=warn, keep_fields) | — | 未处理 |  |
| F147 | api-contract | `apps/docs/synwevo_docs/jobs.py:654` | retry include_skipped can never succeed because the snapshot is immutable | — | 未处理 |  |
| F148 | tenant-isolation | `apps/docs/synwevo_docs/services.py:100` | Soft deletes do not revoke access or stop work | — | 未处理 |  |
| F149 | docs-mismatch | `apps/docs/synwevo_docs/jobs.py:877` | Artifact deletion and retention are dead code, and unpublished blobs are never cleaned | — | 未处理 |  |
| F150 | security | `apps/docs/synwevo_docs/jobs.py:799` | Per-item exception tracebacks are returned to API clients | — | 未处理 |  |
| F151 | api-contract | `apps/docs/synwevo_docs/jobs.py:558` | Jobs carry no correlation id | — | 未处理 |  |
| F152 | performance | `apps/docs/synwevo_docs/jobs.py:585` | Job polling and reports do avoidable heavy reads | — | 未处理 |  |
| F153 | api-contract | `apps/docs/synwevo_docs/jobs.py:509` | The idempotency fingerprint depends on mutable project defaults and not on the principal | — | 未处理 |  |
| F154 | performance | `apps/docs/synwevo_docs/views.py:183` | OCR runs synchronously in the request with an unvalidated page list | — | 未处理 |  |
| F166 | correctness | `apps/docs/synwevo_docs/engine/plan.py:151` | Slot-level format ops: warnings discarded and exceptions uncaught; format not validated on add_slot | — | 未处理 |  |
| F167 | correctness | `apps/docs/synwevo_docs/engine/plan.py:144` | Mapped values can be silently blank or crash on type edge cases (numeric default, mixed-kind shared binding, cross-file group columns) | — | 未处理 |  |
| F168 | correctness | `apps/docs/synwevo_docs/engine/formula.py:785` | Numeric edge cases: TRUNC float error, INDEX/REPLACE/SUBSTITUTE/LEFT invalid args not rejected, TEXT ignores date1904 | — | 未处理 |  |
| F169 | reliability | `apps/docs/synwevo_docs/engine/values.py:62` | as_decimal accepts NaN/Infinity text; per_group numeric sort then crashes with InvalidOperation and sums print "NaN" | — | 未处理 |  |
| F170 | correctness | `apps/docs/synwevo_docs/engine/records.py:134` | per_group record keys can collide because key values are joined with " / " | — | 未处理 |  |
| F171 | api-contract | `apps/docs/synwevo_docs/engine/templates.py:356` | Unknown slot options silently coerced or accepted at creation | — | 未处理 |  |
| F172 | correctness | `apps/docs/synwevo_docs/engine/templates.py:509` | XLSX region on a row deleted by an empty repeat crashes instead of reporting | — | 未处理 |  |
| F173 | correctness | `apps/docs/synwevo_docs/engine/plan.py:188` | Per-file locator overrides are ignored in per_block mode | — | 未处理 |  |
| F191 | correctness | `apps/docs/synwevo_docs/engine/xlsx_doc.py:626` | calcPr / definedNames inserted in schema-invalid positions | — | 未处理 |  |
| F192 | correctness | `apps/docs/synwevo_docs/engine/xlsx_doc.py:594` | Copied detail rows keep the template cell's array-formula ref | — | 未处理 |  |
| F193 | correctness | `apps/docs/synwevo_docs/engine/docx_core.py:680` | Every repeated DOCX table row forced cantSplit; rows taller than a page may be clipped in Word | — | 未处理 |  |
| F194 | reliability | `apps/docs/synwevo_docs/engine/converters.py:29` | LibreOffice profiles accumulate in shared /tmp and a pre-existing directory is trusted | — | 未处理 |  |
| F212 | correctness | `apps/docs/synwevo_docs/engine/desensitize.py:155` | Desensitized DOCM/XLSX copies do not match the originals | — | 未处理 |  |
| F213 | correctness | `apps/docs/synwevo_docs/engine/builtin.py:354` | Built-in editor forces same-name fields onto one binding | — | 未处理 |  |
| F214 | security | `apps/docs/synwevo_docs/engine/builtin.py:260` | Unbounded colspan/rowspan in built-in editor tables | — | 未处理 |  |
| F215 | security | `apps/docs/web/src/pages/DocEditor.tsx:80` | Stored built-in editor JSON is not sanitised, so cell background is injected into a style attribute | — | 未处理 |  |
| F216 | security | `apps/docs/synwevo_docs/engine/images.py:15` | Raster source limits are lax: Pillow allows up to 2× the limit, and n_frames decodes every GIF frame | — | 未处理 |  |
| F217 | performance | `apps/docs/synwevo_docs/engine/ocr.py:62` | Every OCR call spawns an extra `tesseract --list-langs` process, and OCR runs synchronously in requests | — | 未处理 |  |
| F218 | test-gap | `apps/docs/tests/test_formats.py:16` | Critical engine paths have no tests | — | 未处理 |  |
| F235 | ui | `apps/docs/web/src/lib/describe.ts:6` | per_block projects show a blank sample-mode label in the Workspace | — | 未处理 |  |
| F236 | a11y | `apps/docs/web/src/pages/Home.tsx:153` | Inline outline:none removes the design-system focus ring on the project list and sheet grid | — | 未处理 |  |
| F237 | a11y | `apps/docs/web/src/pages/Workspace.tsx:291` | Unmodified single-key global shortcuts cannot be turned off | — | 未处理 |  |
| F238 | fake-or-placeholder | `apps/docs/web/src/pages/JobPage.tsx:135` | Every artifact shows '✓ 已校验' even when no values were checked | — | 未处理 |  |
| F239 | ui | `apps/docs/web/src/main.tsx:59` | EditorSwitch reports any load failure as 'not a builtin template' | — | 未处理 |  |
| F240 | correctness | `apps/docs/web/src/pages/Workspace.tsx:740` | Block-scoped relocation overrides can be neither seen nor cleared | — | 未处理 |  |
| F241 | ui | `apps/docs/web/src/pages/Home.tsx:39` | Mutations on Home and template undo/redo buttons fail silently | — | 未处理 |  |
| F242 | concurrency | `apps/docs/web/src/pages/Workspace.tsx:234` | ⌘Z falls back to server undo stacks that have no revision check | — | 未处理 |  |
| F243 | a11y | `apps/docs/web/src/pages/JobPage.tsx:97` | Job progress is not exposed to assistive technology; many icon-only controls lack names | — | 未处理 |  |
| F244 | reliability | `apps/docs/web/vite.config.ts:10` | Documented Vite dev mode proxies only /api, so the SSO sign-in redirect can loop | — | 未处理 |  |
| F245 | docs-mismatch | `apps/docs/web/src/pages/Regression.tsx:40` | Regression page command path and issues.md DW-055 no longer match the code | — | 未处理 |  |
| F246 | correctness | `apps/docs/web/src/pages/Home.tsx:98` | Home keyboard cursor can point past the list after a delete or archive | — | 未处理 |  |
| F260 | a11y | `apps/docs/web/src/views/GridView.tsx:381` | Grid keyboard selection: cannot start without a mouse, Shift+Arrow cannot extend up/left, selection is not scrolled into view | — | 未处理 |  |
| F261 | ui | `apps/docs/web/src/views/PdfView.tsx:48` | PDF page-jump effect fights user scrolling on short pages (landscape/zoomed out) and can cascade to the last page | — | 未处理 |  |
| F262 | correctness | `apps/docs/web/src/views/PdfView.tsx:98` | Dragging a PDF region across pages creates a selection on the release page with the start page's coordinates | — | 未处理 |  |
| F263 | ui | `apps/docs/web/src/components/SlotDialog.tsx:113` | Enter/Escape handlers are not IME-safe everywhere (Safari commits IME with keyCode 229 and isComposing=false) | — | 未处理 |  |
| F264 | ui | `apps/docs/web/src/components/CommandPalette.tsx:55` | Command palette opens over an open dialog; Esc or a command then discards the dialog's unsaved state | — | 未处理 |  |
| F265 | a11y | `apps/docs/web/src/components/CommandPalette.tsx:58` | Palette and menu lack proper combobox/menu semantics and focus management | — | 未处理 |  |
| F266 | docs-mismatch | `apps/docs/web/src/components/Shortcuts.tsx:34` | Shortcut help lists shortcuts that are not implemented | — | 未处理 |  |
| F267 | a11y | `apps/docs/web/src/components/SidePanel.tsx:140` | Enter on action buttons inside the mapping list opens the editor for the cursor mapping instead of activating the button | — | 未处理 |  |
| F268 | correctness | `apps/docs/web/src/components/SidePanel.tsx:421` | Samples '显示更多' fails past 5000 records | — | 未处理 |  |
| F269 | a11y | `apps/docs/web/src/views/PdfView.tsx:57` | Programmatic smooth scrolling ignores prefers-reduced-motion | — | 未处理 |  |
| F270 | correctness | `apps/docs/web/src/components/PreviewPane.tsx:58` | Preview 'real pagination' view can show the previous record's layout after switching records | — | 未处理 |  |
| F271 | reliability | `apps/docs/web/src/components/GenerateDialog.tsx:18` | Closing the generate dialog during slow job creation lets the user start a duplicate job with a new request_id | — | 未处理 |  |
| F272 | ui | `apps/docs/web/src/components/FilePanel.tsx:50` | Silent failures: file reorder/remove/representative errors unhandled; template library errors shown as empty | — | 未处理 |  |
| F273 | correctness | `apps/docs/web/src/components/RuleDialog.tsx:27` | Rule dialog takes sheets/headers from the representative file even when it is not tabular | — | 未处理 |  |
| F281 | security | `apps/docs/synwevo_docs/api/v1.py:402` | Path import reveals whether any server path exists; test misses the case | — | 未处理 |  |
| F282 | test-gap | `apps/docs/tests/test_acceptance_9_14.py:369` | `testzip()` result is discarded, so the ZIP integrity check asserts nothing | — | 未处理 |  |
| F283 | fake-or-placeholder | `apps/docs/tests/test_formats.py:211` | 'Macros are detected and never executed' is backed by a capabilities 200 check | — | 未处理 |  |
| F284 | test-gap | `apps/docs/tests/test_formats.py:118` | CSV export 'formula results computed' check matches a static label | — | 未处理 |  |
| F285 | test-gap | `apps/docs/tests/test_cache_headers.py:119` | ETag authorization tests are a source-text check and a nonexistent-id probe | — | 未处理 |  |
| F286 | test-gap | `apps/docs/tests/test_acceptance_5_8.py:104` | Zero-row XLSX test locks in `#REF!` output without asserting the promised warning | — | 未处理 |  |
| F287 | test-gap | `apps/docs/tests/test_image_template.py:79` | Image-template end-to-end does not verify detail rows unless Tesseract is installed | — | 未处理 |  |
| F288 | reliability | `apps/docs/tests/test_acceptance_9_14.py:289` | Cancel test depends on wall-clock timing | — | 未处理 |  |
| F289 | test-gap | `apps/docs/tests/test_acceptance_5_8.py:154` | Stray duplicate job submitted in s8 | — | 未处理 |  |
| F290 | reliability | `apps/docs/tests/conftest.py:20` | Suite is not hermetic: ambient env and stale cached fixtures change results | — | 未处理 |  |
| F291 | reliability | `apps/docs/tests/test_formats.py:207` | Zip-bomb test allocates and deflates 700 MB in the test process | — | 未处理 |  |
| F292 | test-gap | `apps/docs/tests/test_engine.py:204` | Path-sanitiser assertion weakened by `or` | — | 未处理 |  |
| F294 | data-loss | `apps/docs/synwevo_docs/regression.py:41` | Regression capture writes customer files into the repository tree outside SYNWEVO_DATA_DIR; not gitignored, not backed up, never cleaned | 复核推翻 | 未处理 |  |
| F296 | authz | `packages/core/synwevo_core/machine.py:53` | Legacy dw_ API keys bypass workspace status, membership and entitlement checks, reaching the 'memberless' legacy workspace and frozen/deleted workspaces | 复核推翻 | 未处理 |  |
| F303 | test-gap | `apps/docs/tests/test_migration_v3.py:37` | Migration tests do not simulate a real v1 database and never check V4_COLUMNS | — | 未处理 |  |
| F304 | test-gap | `apps/docs/tests/test_migration_v3.py:88` | 'Info-style open does not upgrade' test captures DB bytes but never compares them; Store(migrate=False) does write | — | 未处理 |  |
| F305 | test-gap | `apps/docs/tests/test_instance_guard.py:34` | instance_kind test never exercises the 'directory already holds projects' branch | — | 未处理 |  |
| F306 | docs-mismatch | `packages/core/synwevo_core/auth.py:84` | Listen-guard refusal message tells operators to run a command that no longer exists; run.py test would hang inside a container | — | 未处理 |  |
| F307 | test-gap | `apps/docs/tests/test_licensing.py:104` | Dockerfile/source-offer test uses substring matching on a hand-picked list | — | 未处理 |  |
| F308 | api-contract | `apps/docs/synwevo_docs/jobs.py:509` | Idempotency fingerprint hashes server-derived defaults, so an identical replay can get 409 idempotency_conflict | — | 未处理 |  |
| F309 | reliability | `apps/docs/synwevo_docs/api/app.py:476` | Range 'resume' rebuilds a new ZIP per request; archive bytes are not guaranteed identical across builds | — | 未处理 |  |
| F310 | concurrency | `apps/docs/synwevo_docs/webhooks.py:174` | Webhook deliveries are sent without a claim; multiple processes sharing the data dir double-send | — | 未处理 |  |
| F311 | test-gap | `apps/docs/tests/test_snapshot.py:37` | Backup verify accepts blobs that the application cannot read; test fixture uses a non-real layout | — | 未处理 |  |
| F312 | test-gap | `apps/docs/tests/test_pdfium_threading.py:47` | PDFium concurrency guard silently skips when run before fixtures are generated | — | 未处理 |  |
| F325 | test-gap | `tests/integration/test_sso_chain.py:138` | The SSO chain test runs both origins as role=all; its cookie-isolation assertion proves nothing | — | 未处理 |  |
| F326 | security | `apps/docs/scripts/snapshot.py:182` | Backup archive with secrets is written world-readable before chmod, and extraction does not vet links | — | 未处理 |  |
| F327 | docs-mismatch | `apps/docs/scripts/seed_samples.py:20` | Script docstrings and help point at pre-restructure paths and commands; seed_samples documents an exit code it never returns | — | 未处理 |  |
| F328 | correctness | `tests/integration/automation_example.py:156` | automation_example's X-Content-SHA256 header check never runs | — | 未处理 |  |
| F329 | docs-mismatch | `apps/docs/scripts/blob_audit.py:266` | blob_audit says an authz coverage mismatch is a hard failure, yet it always exits 0 | — | 未处理 |  |
| F330 | security | `tests/integration/verify_sso.py:16` | verify_sso.py claims it writes nothing but logs in and logs the real account out everywhere, and takes the password on argv | — | 未处理 |  |
| F331 | reliability | `apps/docs/scripts/make_fixtures.py:24` | make_fixtures writes to apps/docs/testdata by default while every consumer reads <repo>/testdata | — | 未处理 |  |
| F349 | docs-mismatch | `docs/integration/app-onboarding.md:313` | Status sections contradict the code: app-onboarding §7, permissions §9 and §5.3 list implemented features as missing | — | 未处理 |  |
| F350 | docs-mismatch | `docs/integration/app-onboarding.md:203` | app-onboarding.md: wrong file paths, a nonexistent command, a wrong port, and wrong package and build descriptions | — | 未处理 |  |
| F351 | docs-mismatch | `docs/security/sessions.md:69` | sessions.md, permissions.md and identity-migration.md: wrong CSRF header name, removed manage.py commands, moved files and tests | — | 未处理 |  |
| F352 | docs-mismatch | `docs/integration/ports.md:153` | ports.md: stale startup path and measured probe bodies | — | 未处理 |  |
| F353 | correctness | `services/identity/synwevo_identity/cli.py:215` | `synwevo_identity.cli client show` crashes: json not imported | — | 未处理 |  |
| F354 | security | `apps/docs/synwevo_docs/cli.py:73` | `apikey rotate` silently drops the old key's expiry | — | 未处理 |  |
| F355 | api-contract | `docs/integration/openapi.yaml:1181` | openapi.yaml under-describes the real contract | — | 未处理 |  |
| F356 | reliability | `apps/docs/synwevo_docs/webhooks.py:89` | Webhook delivery treats a transient DNS failure as permanent | — | 未处理 |  |
| F366 | docs-mismatch | `docs/development/local-platform.md:110` | First-run and isolated-copy commands omit `--init`, so `up` exits 2 | — | 未处理 |  |
| F367 | docs-mismatch | `docs/ARCHITECTURE.md:44` | Stale module paths and data-model description after restructure | — | 未处理 |  |
| F368 | docs-mismatch | `docs/DEPENDENCIES.md:5` | Dependency and licence inventory omits identity and core runtime dependencies | — | 未处理 |  |
| F369 | docs-mismatch | `docs/ACCEPTANCE.md:6` | Contradictory test counts, verification counts and cross-references across docs | — | 未处理 |  |
| F370 | docs-mismatch | `docs/TESTING.md:6` | make_fixtures default output directory differs from where tests and examples read fixtures | — | 未处理 |  |
| F371 | docs-mismatch | `docs/DEPLOY.md:28` | Documented hot-reload dev mode cannot reach or authenticate against the `platform.py up` server | — | 未处理 |  |
| F372 | a11y | `packages/ui/src/ui.tsx:33` | Shared Modal closes on Escape during IME composition, contrary to design.md's IME rule | — | 未处理 |  |
| F373 | docs-mismatch | `docs/migrations/platform-restructure.md:72` | Restructure record's mapping table mixes historical and current names | — | 未处理 |  |
| F374 | docs-mismatch | `docs/architecture/monorepo.md:84` | monorepo.md 'Store is the only schema executor' is not strictly true | — | 未处理 |  |
