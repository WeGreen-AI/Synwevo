import { type ReactNode, useState } from "react";
import { ApiError, useSession } from "@synwevo/auth-client";
import { Brand, LanguageMenu, useI18n } from "@synwevo/ui";
import "./Topbar.messages";

/** Account and governance share one header. The server supplies the portal destination;
 * the current locale travels with links, including navigation to another origin. */
export function Topbar({ title, children }: { title: ReactNode; children?: ReactNode }) {
  const { t, locale } = useI18n();
  const { state, me, signOut } = useSession();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<{ error: unknown } | null>(null);
  const link = (destination: string) => {
    try {
      const url = new URL(destination, window.location.origin);
      if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) throw new Error("Invalid destination");
      url.searchParams.set("lang", locale);
      return url.origin === window.location.origin ? url.pathname + url.search + url.hash : url.href;
    } catch { return `/?lang=${locale}`; }
  };
  return <><header className="topbar wrap" style={{ height: "auto", minHeight: 48, paddingBlock: 8 }}>
    <Brand href={link(state?.portal_url || "/")} product="Account" label={t("返回平台首页")} />
    <b className="grow">{title}</b>{children}
    <nav className="row wrap gap4" aria-label={t("账户导航")}>
      <a className="btn ghost sm" href={link("/account")}>{t("账户与安全")}</a>
      <a className="btn ghost sm" href={link("/organizations")}>{t("组织")}</a>
      {me?.platform_role === "admin" && <a className="btn ghost sm" href={link("/admin")}>{t("平台管理")}</a>}
      {me?.platform_role === "admin" && me.permissions?.includes("api_platform.manage") && <a className="btn ghost sm" href={link("/admin/api-platform")}>{t("API 管理")}</a>}
      <LanguageMenu />
      <button type="button" className="btn ghost sm" disabled={busy} onClick={async () => {
        setBusy(true); setFailure(null);
        try { await signOut(); }
        catch (error) { setFailure({ error }); setBusy(false); }
      }}>{t(busy ? "正在退出…" : "退出登录")}</button>
    </nav>
  </header>{failure && <p className="notice err" role="alert">{failure.error instanceof ApiError ? failure.error.message : t("退出登录失败，请稍后重试。")}</p>}</>;
}
