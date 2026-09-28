/**
 * The platform entries inside Synwevo Docs: back to the platform, which workspace you are in, and
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
import { useEffect, useState } from "react";
import { ApiError, get, post, setWorkspaceContext } from "./api";
import { useSession } from "./session";
import { platformHandoffUrl } from "./handoff";
import { LanguageMenu, Menu, useI18n, useToast } from "@synwevo/ui";
import { NotificationBell } from "./NotificationBell";
import { MessagesLink, messagesHref } from "./MessagesLink";
import "./session.messages";

/** The account page with `from=<app id>`, so its "back" link returns here. The id is only a hint
 *  the account service matches against the applications this person may open. */
export function accountHref(accountUrl: string | undefined, appId: string | null | undefined): string {
  const base = accountUrl || "/account";
  if (!appId) return base;
  try {
    const url = new URL(base, window.location.origin);
    url.searchParams.set("from", appId);
    return url.origin === window.location.origin ? url.pathname + url.search : url.href;
  } catch { return base; }
}

interface PlatformApp { id: string; name: string; icon: string | null; launch_url: string }

/** A launch address from the registry, used only when it is an http(s) address without
 *  credentials in it — the same rule the workspace's cards apply. */
function safeLaunch(url: string): string | null {
  try {
    const u = new URL(url, window.location.origin);
    return (u.protocol === "https:" || u.protocol === "http:") && !u.username && !u.password ? u.href : null;
  } catch { return null; }
}

export function PlatformNav() {
  const { me, state, signOut } = useSession();
  const { t, locale } = useI18n();
  const toast = useToast();
  const [switching, setSwitching] = useState(false);
  const [apps, setApps] = useState<PlatformApp[]>([]);
  const signedIn = !!me;
  useEffect(() => {
    // The application switcher (Q11): the applications this person can open, so Synwevo Docs and
    // Synwevo Message reach each other in one step instead of through the workspace.
    if (!signedIn) return;
    const abort = new AbortController();
    get<{ items: PlatformApp[] }>("/api/auth/apps", abort.signal).then(out => setApps(out.items || [])).catch(() => setApps([]));
    return () => abort.abort();
  }, [signedIn, me?.workspace_id]);

  // A relying party is, by definition, part of a platform. The role comes from the server.
  const onPlatform = state?.role === "app" || state?.role === "portal" || state?.role === "messages" || state?.role === "all";
  if (!onPlatform || !me) return null;

  const platformName = state?.platform_name || "Synwevo Workspace";
  const spaces = me.workspaces || [];
  // The session's own answer, or nothing. Falling back to the first workspace claimed a tenant
  // the session is not acting in (after losing a membership, or before choosing one) — and made
  // choosing that very workspace a no-op, because it already looked selected.
  const current = spaces.find((w) => w.id === me.workspace_id) || null;
  const others = spaces.filter((w) => w.id !== current?.id);

  async function switchTo(id: string) {
    if (!id || id === current?.id) return;
    setSwitching(true);
    try {
      await post("/api/auth/workspace", { workspace_id: id });
      setWorkspaceContext(id);
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
        // Back to the platform means back to the work: the workspace, reached through the portal's
        // own sign-in start so the portal has its session in the same hop (user feedback
        // 2026-09-27: this link used to land on the public home page).
        <a className="btn ghost sm" href={platformHandoffUrl(state.portal_url, { next: "/workspace", lang: locale }) ?? state.portal_url} title={t("返回工作空间")}>
          ← {platformName}
        </a>
      )}
      {spaces.length === 0 ? (
        <span className="muted sm desktop-only">{t("未加入任何工作空间")}</span>
      ) : current && !others.length ? (
        <span className="muted sm desktop-only" title={t("当前工作空间")}>{current.name}</span>
      ) : (
        <select className="input sm" style={{ maxWidth: 180, minWidth: 140, flex: "none" }} value={current?.id || ""}
                disabled={switching} aria-label={t("切换工作空间")}
                onChange={(e) => void switchTo(e.target.value)}>
          {!current && <option value="" disabled>{t("选择工作空间…")}</option>}
          {spaces.map((w) => <option key={w.id} value={w.id} disabled={w.status !== "active"}>{w.kind === "personal" ? t("个人") : w.organization_name || ""} · {w.name}</option>)}
        </select>
      )}
      {/* Conversations live on Synwevo Message (CI-66): the registry's address through its
          sign-in start, a plain path on the same origin, the account origin's /messages when the
          application is not registered. On the messages origin itself this is the current page. */}
      {apps.length > 0 && <Menu label={t("应用")} ariaLabel={t("切换应用")} items={[
        ...(state?.portal_url ? [{ label: t("工作空间"), onSelect: () => window.location.assign(platformHandoffUrl(state.portal_url!, { next: "/workspace", lang: locale }) ?? state.portal_url!) }] : []),
        ...apps.map(app => {
          const here = app.id === state?.app_id || (app.id === "messages" && state?.role === "messages");
          const href = safeLaunch(app.launch_url);
          return { label: app.name, disabled: here || !href, hint: here ? t("当前应用") : undefined,
                   onSelect: () => { if (href) window.location.assign(href); } };
        }),
      ]} />}
      <MessagesLink href={messagesHref(state, { lang: locale })} current={state?.role === "messages"} />
      <NotificationBell preferencesHref={(state?.account_url || "/account") + "#notifications"} />
      <LanguageMenu />
      <a className="btn ghost sm" href={accountHref(state?.account_url, state?.app_id)} title={t("账户与安全")}>{t("账户")}</a>
      <button type="button" className="btn ghost sm" onClick={() => void signOut().catch(() => undefined)}>{t("退出登录")}</button>
    </>
  );
}
