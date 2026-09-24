# Vultr Tokyo deployment — current record

2026-09-24. User explicitly authorized replacement of the old Docker service, public domain changes,
push and CI/CD. This deployment scope supersedes the earlier local-only development boundary.
The larger product roadmap remains paused; this record concerns deployment and its blockers.

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

## Still in progress

Final Linux regression with a writable test-only checkout, production cutover,
first-admin password set by the operator, and actual successful Actions deploy run.
Do not read this checkpoint as a completed production acceptance.

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

Source commits 599c24d and 8e652e1 were explicitly pushed to main. The operator added the three
Cloudflare routes; server DNS resolves all three origins. Public origin readiness awaits cutover.
Runtime version reference, checked 2026-09-24:
https://packages.debian.org/trixie/libreoffice-calc (25.2.3-2+deb13u6).

See [deployment guide](../../infra/production/README.md) for topology, commands and recovery boundaries.
