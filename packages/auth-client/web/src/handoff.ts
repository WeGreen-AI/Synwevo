/**
 * Handing a signed-in person to the platform.
 *
 * The account service authenticates; the platform (Synwevo Workspace) is where the work is.
 * Someone who signs in without a particular page to return to is therefore taken to the
 * platform, not left on the account settings page — the settings stay one click away in the
 * top bar, while the workspace is what a fresh sign-in is almost always for.
 *
 * The destination is the portal's own sign-in start (`/auth/start`), not a plain link to it,
 * so that the portal obtains its session in the same hop and renders the workspace at once.
 * The portal origin comes from server-controlled configuration (`portal_url` in the auth state),
 * never from a query parameter: this cannot become an open redirect.
 */
export function platformHandoffUrl(
  portalUrl: string | null | undefined,
  opts: { next?: string; workspaceId?: string; lang?: string } = {},
): string | null {
  const base = (portalUrl || "").trim();
  if (!base) return null;
  // A configured value that is not an absolute http(s) origin is a deployment mistake, and a
  // loud one: a relative path here would hand the person to "the current host", which is not
  // the platform, so it is refused rather than guessed at.
  const url = new URL("/auth/start", base);
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new TypeError(`portal URL must be http(s): ${base}`);
  const params = new URLSearchParams();
  params.set("next", opts.next || "/workspace");
  if (opts.workspaceId) params.set("workspace_id", opts.workspaceId);
  if (opts.lang) params.set("lang", opts.lang);
  url.search = params.toString();
  return url.href;
}

/** `safeNext` answers "/" when nobody asked for a particular page: a sign-in for its own sake. */
export function isDefaultNext(next: string | null | undefined): boolean {
  return !next || next === "/";
}
