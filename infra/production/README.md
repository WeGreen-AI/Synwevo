# Vultr Tokyo production

Three roles share one existing database and blob directory. The image contains the built React
shells, Python API, document workers, LibreOffice, Tesseract and CJK fonts.

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
SMTP is disabled until actual SMTP settings are supplied. Invitation/recovery requests report the
missing configuration; do not pretend an email was sent. Configure account mail only in the private
runtime.env, following the root `.env.example`, and restart the accounts service.

## CI/CD

`.github/workflows/production.yml` runs on pull requests, main pushes and manual dispatch. Hosted
Ubuntu runners install pinned Python/Node dependencies, check boundaries/brand/types, build all
frontends, run frontend suites and deployment safety regressions. The Python suite runs in a
test-only derivative of the actual deployment image, with no production volume or external network.
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
connection. Pinning npm/Python dependencies and Actions commits does not make apt/base image tags
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

A queued webhook waiting for retry can block deployment. Resolve it through authorized operations;
the deployment script must not discard deliveries merely to pass. Backups are not pruned automatically.
Monitor disk use and apply an explicit retention policy after restore verification.

First migration is operator-controlled: snapshot the old instance, rehearse on a server-local copy,
compare IDs/counts/blob hashes, stop the old writer for the final consistent copy, register the two
RP clients, then start the new stack and add only the three new hostname routes (the old route keeps port 8765). Keep the old service
and original directory until acceptance. The old database must not continue accepting writes after
cutover. Do not restart it after new writes without a reviewed reconciliation/restore plan.

Current execution evidence and remaining setup are recorded in `docs/quality/deployment.md`.
