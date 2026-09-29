# Vultr Tokyo production

Three roles share one existing database and blob directory. The image contains the built React
shells, Python API, document workers, LibreOffice, Tesseract and CJK fonts.

The 2026-09-25 product release enables verified public registration (`SYNWEVO_REGISTRATION=open`).
An explicit persisted administrator registration policy still takes precedence; deployment does
not overwrite it. Existing users, workspace IDs, historical claims and identity keys remain intact.
The user has authorized deploying the previously local-only product round. Because host Compose
is not replaced by CI, install the reviewed Compose change after the new application is healthy,
then recreate accounts, portal and docs (the shared registration setting reaches all three),
followed by gateway. Keep the private SMTP configuration unchanged.

| Public origin | Role | Tunnel target |
|---|---|---|
| `https://synwevo.wegreen.ltd` | Workspace portal | `http://127.0.0.1:18760` |
| `https://accounts.wegreen.ltd` | Account/OIDC/Passkey | same gateway |
| `https://docwave.wegreen.ltd` | Synwevo Docs | same gateway |
| `https://dw.wegreen.ltd` | HTTP 308 to the Docs origin | existing `http://127.0.0.1:8765` alias |

`docwave.wegreen.ltd` is the user-selected hostname; the product name remains Synwevo Docs.
The existing Cloudflare Tunnel supplies public HTTPS. Do not expose backend ports, change other
tunnel routes, occupy the mail server's 80/443 ports, or widen cookie domains. RP clients retain
`portal-web` / `docweave-web`; callbacks are each origin's `/auth/callback`, logout return is
`/signed-out`, and backchannel logout is `/auth/backchannel-logout`.
Gateway and Uvicorn access logs are disabled so request-line queries do not record one-use
authorization/invitation/reset proofs; application audit and error logs remain enabled.

## Host layout and operations

- `/opt/synwevo/config/compose.yaml`, `nginx.conf`, `runtime.env`, `.env`: root-owned configuration.
- `/opt/synwevo/data`: migrated server data, UID/GID 1000. Never populate this with a developer's data.
- `/opt/synwevo/current.json`: deployed image, previous image and verified pre-upgrade backup.
- `/opt/synwevo/backups`: private, contains real data and keys; stays on the server.
- `/opt/synwevo/releases/<commit>`: source received through CI, without runtime data or secrets.
- `/opt/synwevo/bin/receive.py`: root-owned deployment receiver, installed separately from releases.
- The old `docweave` container/image and `/opt/docweave-data` are retained for migration rollback.

```sh
ssh vultr_tokyo
cd /opt/synwevo/config
docker compose ps
docker compose logs --tail 100 accounts docs portal gateway
docker compose exec accounts python -m synwevo_identity.cli bootstrap status
# First administrator chooses their own password interactively; no default or logged password:
docker compose exec accounts python -m synwevo_identity.cli bootstrap admin --email '<administrator-email>'
```

The first admin's personal workspace is separate from historical `default` data. Claim that
workspace through the existing protected admin flow after signing in; first login never claims it.
Account mail uses `/opt/synwevo/config/smtp.env` (root-owned, mode 0600, private parent directory).
Only `accounts` receives this file; `docs` and `portal` do not receive mailbox credentials.
The later file overrides `SYNWEVO_MAIL_MODE=disabled` in the shared `runtime.env`.
Compose 2.30+ `format: raw` preserves literal password characters, including `$` and `#`.
Do not quote or shell-source this raw env file, print expanded `docker compose config`, or put it
in a deployment archive. The committed [example](smtp.env.example) contains only a placeholder.

The configured mailbox and fixed From are `mail@hikosphere.com`, display name `Synwevo`, connecting
to `mail.hikosphere.com:587` with STARTTLS. Certificate chain and hostname verification are mandatory;
credentials are transmitted only after TLS. Port 465 is an alternative only with `SMTP_SSL=1` and
`SMTP_STARTTLS=0`; there is no automatic downgrade/fallback. The project connects to mailcow;
mailcow handles its existing SMTP2GO relay. MX, inbound routing and relay configuration are unchanged.

For a future credential update, edit the private file on the host through an authorized channel,
run `docker compose config --quiet`, then recreate **only** the account role and gateway (nginx
caches upstream container IPs):

```sh
cd /opt/synwevo/config
docker compose up -d --no-deps --force-recreate --wait accounts
docker compose up -d --no-deps --force-recreate --wait gateway
```

No SMTP password belongs in GitHub Actions: CI preserves these host-owned files. A code deployment
continues through the existing verified release/backup pipeline. SMTP readiness is checked by a
controlled send, not by `/readyz` (which checks the worker and database, not external delivery).
Queue behaviour, local configuration and error meanings: [operations](../../docs/operations.md#6-账户邮件仅发信).

## CI/CD

`.github/workflows/production.yml` runs on pull requests, main pushes and manual dispatch. Hosted
Ubuntu runners install pinned Python/Node dependencies, check boundaries/brand/types, build all
frontends, run frontend suites and deployment safety regressions. The Python suite runs in a
test-only derivative of the actual deployment image, with no production volume or external network.
CI first builds from the same private archive extraction used by the SSH receiver, then checks
account startup as the unmodified non-root runtime user. Shipped source is readable inside the
image even when the host release directories are private; data and secret permissions are unchanged.
This keeps LibreOffice/font behaviour aligned with production. The runtime uses Debian Trixie
(LibreOffice 25.2); Bookworm's LibreOffice 7.4 cannot evaluate the accepted XLOOKUP sample.
Only successful main runs
can use the `production` environment. Its branch policy permits `main` only.

Environment secrets: `SYNWEVO_DEPLOY_KEY`, `SYNWEVO_KNOWN_HOSTS`; environment variable:
`SYNWEVO_DEPLOY_HOST`. The key belongs to a separate `synwevo-deploy` host user with no Docker group,
no password, no general sudo. Its authorized_keys entry uses `restrict` and a forced command. Sudo
permits only the fixed receiver and passes `SSH_ORIGINAL_COMMAND`. The receiver accepts only
`deploy <40 hex SHA>`, rejects path traversal/links/devices/runtime files and archive bombs, builds
an image tagged by the commit, and serializes upgrades with a host lock. Main code can of course
change the application that handles production data: protect repository write access accordingly.
Never place a general SSH/root key, server root secret, database, or tunnel token in GitHub.

The host receiver and Compose are intentionally not overwritten by incoming releases. Infrastructure
changes require an operator to review and install those files using the normal administrative SSH
connection. Host-only settings live in `runtime.env` (and `smtp.env`), which such an install does not
replace — a live-table trial's `SYNWEVO_SHEET_LIVE` among them; after installing, run the checks of the
changed service (for Docs during a trial: `sheet-live`, below). Pinning npm/Python dependencies and Actions commits does not make apt/base image tags
bit reproducible; their resolved image IDs are part of the deployment evidence.

## Backup, upgrade and rollback

The receiver builds before downtime, stops the gateway, refuses pending jobs/mail/webhook deliveries,
stops all database writers, rechecks idle state, then copies the database, blobs and security journal
with ownership intact. SQLite quick_check and a whole-blob manifest verify the offline backup.
New roles must pass readiness checks before the gateway returns. A failure before public release
preserves the failed candidate data separately, restores the just-taken backup and restarts the
previous image. A failure after public traffic resumes does **not** automatically restore old data:
new writes and revocations may already exist. Investigate and use the documented journal-aware
recovery procedure in `docs/operations.md`; never blindly revive an old database.

Stopping is graceful and must stay so: `docker compose stop` sends SIGTERM (through the image's
`init`) and waits `stop_grace_period` (90 s, the shared service settings) before killing. Stopping the
gateway first closes every browser's live-editing socket, so each open room writes its draft as its
last person leaves; SIGTERM then runs Synwevo Docs' shutdown hook, which writes every room still open
and waits for those closing writes. Never deploy or restart Docs with `kill`, `-t 0` or a shorter
grace period: a live table keeps edits since its last draft write (at most a few seconds) only in
memory and in the open pages (`infra/production/tests` checks the grace period and the stop order).

### Live tables: starting and ending a trial (`SYNWEVO_SHEET_LIVE`)

Live table editing (docs/platform/docs-sheet-live.md §9, §15) is off unless Docs' environment says
otherwise: `off`, `on`, or a comma-separated list of workspace ids — the trial: only those workspaces'
table templates are edited live, every other workspace keeps save-and-merge. It is set in the host-only
`/opt/synwevo/config/runtime.env` (`SYNWEVO_SHEET_LIVE=<workspace id>`), never in `compose.yaml`: an
operator installing a reviewed `compose.yaml` (the procedure above) would otherwise end a running trial
silently, without the steps below, and a value under `environment` would override `runtime.env`. Docs
reads it when a room opens, so a change takes a graceful restart of Docs —
`docker compose -f /opt/synwevo/config/compose.yaml up -d --no-deps --force-recreate --wait docs` (the
90 s grace period above; never `kill` or `-t 0`). A release does not change it. Its reading is checked
with `docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli sheet-live </dev/null`
(read-only; uvicorn's logging configuration does not show Docs' INFO lines, so the start-up log shows
only a warning for a listed id that is no workspace).

* **Start**: add the line to `runtime.env`, restart Docs as above, check that `sheet-live` reads
  "试用：<id>（workspace name）" with no unknown id, open one table in that workspace (its top bar says
  实时协同中 / Live), and record the start in `docs/quality/deployment.md` (workspace id, UTC time,
  running commit, operator). While the trial runs, run `sheet-live` after every `docker compose up -d`
  (a deployment, an installed `compose.yaml`, any restart); if it no longer reads the trial, put the id
  back and restart Docs before anyone saves those tables.
* **End (and roll back)** — in this order, or unsaved live edits are lost: ask the trial members to save
  and close their live tables by an agreed time (a freeze); `sheet-live --workspace <id>` lists the
  tables with unsaved live edits — have each saved as a version in the live editor, or discarded by
  someone with the right (restore/undo with discard, or the machine API with `discard_live: true`) —
  until it shows 0 (an id that is no workspace is refused, exit 2, never "0"); take a backup with the
  one-off container of docs/operations.md §10 step 3 (the receiver's backup runs only inside a deploy);
  run `sheet-live --workspace <id>` again right before the restart (0, or back to the listing); remove
  the id and restart Docs; run `sheet-live` without `--workspace`: if that workspace still lists an
  unsaved table (someone typed after the last check), put the id back and restart Docs before anyone
  saves it, then start again from the freeze; record the end in `docs/quality/deployment.md`. Full
  steps: docs/operations.md §10.

A queued webhook waiting for retry can block deployment. Resolve it through authorized operations;
the deployment script must not discard deliveries merely to pass. Backups are not pruned automatically.
Monitor disk use and apply an explicit retention policy after restore verification.

First migration is operator-controlled: snapshot the old instance, rehearse on a server-local copy,
compare IDs/counts/blob hashes, stop the old writer for the final consistent copy, register the two
RP clients, then start the new stack and add only the three new hostname routes (the old route keeps port 8765). Keep the old service
and original directory until acceptance. The old database must not continue accepting writes after
cutover. Do not restart it after new writes without a reviewed reconciliation/restore plan.

Current execution evidence and remaining setup are recorded in `docs/quality/deployment.md`.
