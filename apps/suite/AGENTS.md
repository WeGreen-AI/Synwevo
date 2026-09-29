# Working in apps/suite (the business applications of the workplace origin)

Rules of the repository (`/AGENTS.md`) apply. This file adds what is specific to this module.
Decisions behind it: `docs/agent-work/DECISIONS.md` (D1–D5). Synwevo Forms (`synwevo_suite/forms`,
`web/src/apps/forms`) is the reference implementation: copy its shape, not its business rules.

## Shape of an application

| Piece | Where | Notes |
|---|---|---|
| Tables (DDL) | `packages/core/synwevo_core/suite_schema/<app>.py` (`SCHEMA`, optional `COLUMNS`, `INDEXES`) | The store is the only migration runner. Additive only. Avoid column names in `store.JSON_COLS` unless you want them auto-decoded (`output`, `data`, `summary`, `record`, `snapshot`, `issues`, `scan`, …). |
| Manifest + install | `synwevo_suite/<app>/__init__.py`: `MANIFEST` (`AppManifest`) and `install(ctx)` | `ctx` is `SuiteContext` (store, identity, capabilities, registry, api, start_jobs). |
| Business rules | `synwevo_suite/<app>/service.py` | Every lookup is scoped by `workspace_id` — an id from another workspace reads as missing (404). |
| Operations | `synwevo_suite/<app>/ops.py` → `registry.register(Operation(...))` | Pydantic input (`extra="forbid"`, bounded strings/lists), `need` (`read`/`write`/`manage`/`personal`), `side_effect` (`none`/`write`/`external`), `agent` (`auto` only for reads; `confirm` for writes; `never` for decisions a person must make in the page), `unattended=False` for person-only decisions, `events=(…)` declared. Handlers receive `(call, input)`; use `call.workspace_id`, `call.user`, `call.touches(kind, id)`, `call.emit(type, …)`. |
| Non-operation routes | only for streams, uploads, downloads, public links | Under `/api/suite/<app>/…`; authorize with `ctx.capabilities.check_workspace(user_id, app, workspace_id, need)`. Anonymous routes only under `/api/suite/public/<app>/…`. |
| Pages | `web/src/apps/<app>/` with `index.tsx` exporting `<app>Routes()`; register in `web/src/routes.tsx` | Use `WorkspaceApp` (workspace in the URL: `/<app>/w/<workspace>/…`), `op()`/`read()` from `shared/ops.ts` (never raw fetch for operations), `Loading`/`Failure`/`Empty`/`RunBadge` from `shared/states.tsx`. |
| Strings | `web/src/apps/<app>/messages.ts` (zh-Hans keys, `en` + `zh-Hant`) | Every CJK literal in the pages must be a key (the catalogue test fails otherwise). Server refusal messages are zh-Hans; the page words errors by `code`. |
| Styles | `web/src/apps/<app>/<app>.css` | Tokens only (`scripts/check_design.py`). Reuse `.suite-*` classes. Support narrow screens and `prefers-reduced-motion`. |
| Tests | `apps/suite/tests/test_<app>*.py` | Use the `suite` fixture (`conftest.py`): workspaces A/B and people alice/bob/vera/oscar/out. Test refusals (viewer, outsider, other workspace), idempotency, versions, events. |

## Invariants

* A sibling application is reached only through the registry (`ctx.capabilities.invoke(...)`), never
  by importing it. `scripts/check_boundaries.py` fails on `synwevo_suite.<other app>` imports.
* The workspace comes from the call, never from the browser's tab binding.
* Published versions are immutable; runs pin a version.
* No fake data, no placeholder pages, no success without a stored record. A capability that needs an
  external service that is not configured says "not configured" and does not pretend.
* New blob columns: register in `synwevo_core/blob_refs.py` and the writer function in
  `tests/integration/test_blob_references.py`. New `*_hash` columns: `blob_refs.LOOKALIKES`.
* New notification kinds: `synwevo_core/notifications.py` `CATALOGUE` (three languages), target
  `{"origin": "portal", "path": "/<app>/…"}`.

## Commands (from the repository root, inside a worktree set PYTHONPATH to the worktree packages)

```bash
.venv/bin/python -m pytest apps/suite/tests
.venv/bin/python scripts/check_boundaries.py && .venv/bin/python scripts/check_design.py && .venv/bin/python scripts/check_component_names.py
npm run typecheck -w @synwevo/suite-web && npm test -w @synwevo/suite-web && npm run build -w @synwevo/suite-web
```
