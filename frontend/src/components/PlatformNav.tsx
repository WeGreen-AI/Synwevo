/**
 * The platform entries inside DocWeave: back to the platform, which workspace you are in, and
 * where the account settings live.
 *
 * It renders nothing on a deployment that is not part of a platform. The single-process install
 * has no portal to return to, and a link labelled "back to the platform" that led nowhere would
 * be worse than no link at all.
 *
 * Both addresses come from the server (`/api/auth/state`), never from anything this page works
 * out for itself. The account link deliberately leaves for the account service: a second
 * password and MFA screen here would mean two places to change a password, and two that could
 * disagree about whether a second factor is on.
 *
 * Used on the project list only, not inside a project. Switching workspace while a project is
 * open is the race the design is meant to prevent: the open views, the upload in progress and
 * the running job all belong to the workspace they started in, and a switcher sitting beside
 * them invites exactly the "this tab is quietly a different tenant now" problem.
 */
import { useState } from "react";
import { ApiError, post } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/ui";

export function PlatformNav() {
  const { me, state } = useSession();
  const toast = useToast();
  const [switching, setSwitching] = useState(false);

  // A relying party is, by definition, part of a platform. The role comes from the server.
  const onPlatform = state?.role === "app" || state?.role === "portal";
  if (!onPlatform || !me) return null;

  const platformName = state?.platform_name || "工作平台";
  const spaces = me.workspaces || [];
  const current = spaces.find((w) => w.id === me.workspace_id) || spaces[0];
  const others = spaces.filter((w) => w.id !== current?.id);

  async function switchTo(id: string) {
    if (!id || id === current?.id) return;
    setSwitching(true);
    try {
      await post("/api/auth/workspace", { workspace_id: id });
      // A full reload, not a local state update. Which projects, templates and jobs exist
      // depends on the workspace, so every open view and cached list belongs to the old one;
      // repointing the page in place would leave a tab showing another tenant's list.
      window.location.assign("/");
    } catch (e) {
      setSwitching(false);
      toast(e instanceof ApiError ? e.message : String(e), "err");
    }
  }

  return (
    <>
      {state?.portal_url && (
        <a className="btn ghost sm" href={state.portal_url} title="返回平台首页">
          ← {platformName}
        </a>
      )}
      {current && (others.length ? (
        <select className="input sm" style={{ maxWidth: 180 }} value={current.id}
                disabled={switching} aria-label="切换工作空间"
                onChange={(e) => void switchTo(e.target.value)}>
          {spaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      ) : (
        <span className="muted sm desktop-only" title="当前工作空间">{current.name}</span>
      ))}
      <a className="btn ghost sm" href={state?.account_url || "/account"} title="账户与安全">账户</a>
    </>
  );
}
