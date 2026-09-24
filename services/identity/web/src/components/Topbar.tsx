/**
 * The account shell's top bar, shared by the account page and the governance pages.
 *
 * On the account service there is no project list to go back to and the brand is the
 * platform's; where "back" leads is the server's answer (`state.portal_url`), never a query
 * parameter — this is the one origin that must not carry an open redirect. On the
 * single-process deployment it is still Synwevo Docs' own account page, so "back" is the
 * project list on the same origin.
 */
import { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useSession } from "@synwevo/auth-client";

export function Topbar({ title, children }: { title: ReactNode; children?: ReactNode }) {
  const location = useLocation();
  const { state, me } = useSession();
  const onAccountService = state?.role === "accounts";
  const platformName = state?.platform_name || "Synwevo Workspace";
  const portalUrl = state?.portal_url || "";
  return (
    <header className="topbar">
      {onAccountService ? (
        <>
          <span className="brand"><span className="logo" aria-hidden="true">◫</span>
            <span className="desktop-only">{platformName}</span></span>
          <span className="sep" />
          {portalUrl && <a href={portalUrl} className="btn ghost sm">← 返回{platformName}</a>}
        </>
      ) : (
        <>
          <a href="/" className="brand"><span className="logo" aria-hidden="true">S</span><span className="desktop-only">Synwevo Docs</span></a>
          <span className="sep" />
          <a href="/" className="btn ghost sm">← 返回项目</a>
        </>
      )}
      <span className="grow ellipsis"><b>{title}</b></span>
      {children}
      {me?.platform_role === "admin" && location.pathname !== "/admin" && <Link className="btn ghost sm" to="/admin">平台管理</Link>}
    </header>
  );
}
