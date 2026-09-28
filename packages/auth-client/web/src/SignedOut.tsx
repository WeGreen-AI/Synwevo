import { StatusPage, useI18n } from "@synwevo/ui";
import { signInUrl, useSession } from "./session";

/**
 * Where the account service's logout returns to on a relying party (`/signed-out`): the unified
 * sign-in and every linked application session on this device have ended. "重新登录" goes through
 * the relying party's own sign-in start, which is the only working sign-in on this origin; the
 * platform home is offered when the deployment has a portal. The same words as the account
 * service's own signed-out page, because it is the same event seen from another origin.
 */
export function SignedOut({ product }: { product: string }) {
  const { state } = useSession();
  const { t } = useI18n();
  return <StatusPage product={product} brandHref="/" title={t("已退出")}>
    <p>{t("当前设备的统一登录与关联应用会话已退出。其他设备不受影响。")}</p>
    <a className="btn primary" href={signInUrl(state, "/")}>{t("重新登录")}</a>
    {(state?.site_url || state?.portal_url) && <a className="btn" href={state?.site_url || state?.portal_url}>{t("返回平台首页")}</a>}
  </StatusPage>;
}
