# Vultr Tokyo deployment — current record

2026-09-24. User explicitly authorized replacement of the old Docker service, public domain changes,
push and CI/CD. This deployment scope supersedes the earlier local-only development boundary.
The larger product roadmap remains paused; this record concerns deployment and its blockers.

**Current outcome: deployed and CI/CD verified.** Production runs release
`e45518f86333b16c20bc288eadf61941b7be7b3e`; the subsequent evidence-only documentation
commit does not change that running release. The first administrator was created by the user.
This round stops after the deployment handoff, with no development agent or continuation loop left running.

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
- SMTP remains disabled. Real invitation/recovery mail requires actual SMTP configuration; no
  email delivery or physical security-key validation is claimed by this deployment.

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
