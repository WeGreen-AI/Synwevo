# Portal public home and workspace delivery

Local work and verification: 2026-09-24–25 (Asia/Hong_Kong). No deployment, remote access,
production-data access, external mail or push was performed. Final local commit and integration evidence are recorded in `docs/quality/handoff.md`.

## Ownership

Changed existing files:

- `synwevo_portal/app.py`, `synwevo_portal/routes.py`
- `web/index.html`, `web/package.json`
- `web/src/main.tsx`, `web/src/Portal.tsx`, `web/src/portal.css`

Added:

- `synwevo_portal/public.py`
- `web/src/PublicHome.tsx`, `web/src/messages.ts`
- `../../tests/integration/test_portal_public.py`
- This delivery record.

This module record describes the Portal slice. The parent integration synchronized the root lock,
shared account routing and workspace handoff; current cross-module evidence is in
`docs/quality/handoff.md`.

## Behavior and route contract

`portal_app.install(api, plat)` installs the public router itself, before the composer's catch-all
shell. **No additional composer wiring is required for the Portal routes.** Public pages are
`/`, `/zh-Hans`, `/zh-Hant`, `/en`; the authenticated entry is `/workspace`. `/robots.txt` allows
only those exact pages and the public hashed assets. `/sitemap.xml` lists only canonical public
pages. Canonical URLs/hreflang/sitemap use validated `SYNWEVO_PORTAL_URL`, never Host or request
query parameters. With no valid configured origin, canonical tags and sitemap URLs are omitted.
The neutral build is noindex by default; server-rendered public titles, descriptions, language,
fallback introduction and registration/sign-in links precede React. All nonpublic Portal
responses except hashed assets carry `X-Robots-Tag: noindex, nofollow`.

Existing `/auth/start?next=%2Fworkspace` remains the sign-in flow. No password form is added to
Portal. Registration goes to the server-specified Account origin `/register`. The parent owns
Account routes and RP redirects for `/register`, `/onboarding`, `/organizations/{id}`, and `/admin/api-platform`.

`SessionProvider` supplies `/api/auth/me`, account locale synchronization and per-tab workspace
context. Portal uses shared `get`/`post` so `X-Synwevo-Workspace` is included. Switching calls
`POST /api/auth/workspace`, then `setWorkspaceContext(newID)`, then full-page `/workspace`
navigation. Returning to a tab refreshes its session; BFCache restoration reloads it.

`GET /api/portal/home` adds:

- `organizations: [{id, name, status, role}]`, using `synwevo_core.organizations.Organizations`.
- Optional workspace `organization_id`, `organization_name` (also tolerates additive `org_id`,
  `org_name`), with existing workspace ID/tenant semantics unchanged.
- `pending_invitations: [{id, workspace_id, workspace_name, role, expires_at}]`. This read-only
  query requires the current user's verified email and filters accepted, revoked, expired,
  deleted-workspace and unavailable-workspace/organization rows. It never returns a token or
  token hash. Invitations do not become accessible workspaces until Account accepts them.

Invitations link to Account onboarding to review status; copy explicitly tells the recipient
to use the invitation email link to join. Portal cannot reconstruct an email token from its
stored hash and does not bypass that ceremony.

API management appears only when both `/me` and home identify a platform admin **and** `/me`
contains `api_platform.manage`; destination is Account origin `/admin/api-platform`. Organization
admins and workspace owners/admins do not receive that link or business document access.

## Verification

Passed:

- `npm run typecheck -w @synwevo/workspace-web`
- `npm run build -w @synwevo/workspace-web`
- `.venv/bin/python -m pytest tests/integration/test_portal_public.py`: **15 passed**; 7 existing
  Starlette/httpx/Swig deprecation warnings. Tests use `tmp_path`, file email configuration,
  explicitly labelled fixture identities, real Store/Identity/Invites/Sessions, and the composed
  server. No production paths or mocked API responses.
- Browser checks against a loopback-only server backed by a temporary directory: English public
  page, Traditional Chinese language switch and metadata, 390×844 layout (scroll width = 390),
  signed-in owner layout, real pending invitation, real empty state, platform-admin API link,
  ordinary workspace owner exclusion, organization-admin exclusion, organization/workspace
  visibility, shared account locale retained on reload, workspace switch retained on reload,
  and two tabs retaining different workspace contexts when the other tab switches.
- `scripts/check_brand.py`: passed (all old-brand strings classified).

Parent consolidation: dependency declarations and the root boundary check now pass. No checker was weakened.

## Reviewed sources and limits

Fully read: root `AGENTS.md`, `CLAUDE.md`, `docs/architecture/monorepo.md`, `packages/AGENTS.md`,
all original Portal Python/frontend/config/package files, shared frontend session/index/i18n
contracts, and the new/changed Portal implementation and its integration test.

Targeted read (not an assertion of whole-file review): shared HTTP role/session/shell gate,
Identity workspace/user methods, Store invitation schema, Apps eligibility/listing, Organizations
read/access methods, Sessions creation, RP callbacks/redirects, Account invitation/onboarding
contracts, composed server wiring, integration fixtures/shell/access tests, Docs format matrix
and plan checks. README capability statements were checked against format documentation; no
claim of a full Docs engine audit or end-user Office trial is made.

Parent integration completed and verified:

- Root lock and account redirect wiring are synchronized.
- Organization/onboarding and Portal-to-Docs entry carry an explicit, server-validated workspace
  through RP SSO. Existing portal sessions cannot silently retain the wrong tab workspace.
- The shared workspace selection endpoint checks current organization lifecycle/membership.
  Frontend request generations discard old JSON, XHR and binary results; changed identity/context
  clears the document rather than retaining an old business view.
- Real registered-user browser runs cover three-language organization/invitation/portal flows,
  then Docs upload/mapping/preview/generation/download; see the final evidence in the handoff.
- Public home under legacy Basic authentication remains subject to that authentication gate;
  the supported public-portal configuration verified this round is session/Account SSO.

## Official research evidence

These are documentation observations, **not product trials**. Retrieved 2026-09-24–25.

- [Notion: Create, join & leave workspaces](https://www.notion.com/help/create-delete-and-switch-workspaces).
  The current workspace name is the switcher entry and leaving removes the workspace from that
  list. Decision: show the current space explicitly and only list live membership. The page did
  not expose a publication/update date in the retrieved text; access date is recorded above.
- [Linear: Invite members](https://linear.app/docs/invite-members).
  Invitation flow selects an intended role and sends an email link with joining steps. Decision:
  show the actual invited workspace/role separately from established access and give a concrete
  Account/email next step. No automatic domain-join policy was copied. No visible publication
  date was returned; access date is recorded above.
- [Microsoft Entra: B2B invitation redemption](https://learn.microsoft.com/en-us/entra/external-id/redemption-experience).
  The document distinguishes pending acceptance from accepted state and explains tenant-context
  links. The page reports **last updated 2026-04-21**. Decision: never render a pending invitation
  as an accessible workspace; explicitly preserve/revalidate workspace context.
