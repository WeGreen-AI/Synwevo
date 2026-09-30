import { setWorkspaceContext, workspaceContext } from "@synwevo/auth-client";

/**
 * After the assignment of the historical data is undone: a tab whose workspace context
 * (`X-Synwevo-Workspace`) names that workspace has every later request refused with
 * `workspace_context_invalid` — the overview's own reload, every other administration tab, the
 * next re-authentication. An administrator who assigned the data to themselves is in exactly that
 * tab: a new session starts in the oldest workspace a person belongs to, which is the legacy one.
 *
 * So the context is dropped when it names the workspace that was just unassigned. The server
 * clears the session's own preference on the next request, and SessionProvider's next
 * revalidation treats the change as the document boundary it is. Returns whether it was dropped.
 */
export function releaseWorkspaceContext(workspaceId: string): boolean {
  if (!workspaceId || workspaceContext() !== workspaceId) return false;
  setWorkspaceContext(null);
  return true;
}
