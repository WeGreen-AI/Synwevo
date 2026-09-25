# Claude Code in the Synwevo repository

The working rules for this repository live in `AGENTS.md` and apply to Claude Code unchanged —
one set of rules, not two:

@AGENTS.md

## Only what Claude Code needs in addition

* **Real data is in `data/`.** Tests build their own data directory under `tmp_path`; E2E scripts go
  through `apps/docs/scripts/_testenv.py`, which refuses a server whose `/healthz` does not report
  `instance=test`. Never point a test, benchmark or script at `data/`, and never copy it out of the
  machine. Backups and restore rehearsals use `python scripts/platform.py backup` /
  `verify-restore`, which work on isolated copies.
* **The API console is platform-admin-only.** The current user-authorized exception permits its UI
  under platform administration, guarded by `api_platform.manage` on every management request.
  Organization/workspace roles do not grant that permission. Management authority never bypasses
  current workspace approval, scoped machine credentials, or business object authorization.
  CLI/SDK/console share the existing service layer; ordinary Docs business APIs remain available.
* **Do not push, publish packages or create releases** unless the user asks in the conversation.
  Local commits are fine; keep unrelated changes out of them.
  `main` pushes trigger production deployment. Local-only rounds must not push or access the VPS.
  Deploy only when explicitly authorized in the current conversation; preserve host data and keys.
  The latest deployment status is recorded in `docs/quality/deployment.md`.
* **Previewing the product:** `.claude/launch.json` names the three origins (`docweave`, `accounts`,
  `portal`). Start them with `python scripts/platform.py up` (it refuses to create an empty database
  in `data/` unless given `--init`), then open them in the preview browser.
* **Where the current state is recorded:** `docs/quality/progress.md` (what is done and verified),
  `docs/quality/issues.md` (defects with evidence), `docs/engineering/codebase-review.md` (code map
  and review coverage). Resume interrupted work from `docs/quality/handoff.md`; the per-file
  current content hashes and honest reading states are in `docs/engineering/review-coverage.json`.
  A "done" in an old record is a lead to re-verify, not evidence.

## Verification before claiming anything works

```bash
.venv/bin/python -m pytest                    # all three suites (from the repository root)
.venv/bin/python scripts/check_boundaries.py
.venv/bin/python scripts/check_brand.py
npm run typecheck && npm run build
```

Report passed / failed / skipped / not run separately. Expected permission denials and validation
errors are behaviour to assert, not noise to silence.
