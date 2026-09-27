/** The messages application's header: the brand position (back to the list), then the shared
 *  platform entries — ← workspace, the workspace switcher, 消息 (this page), the bell, language,
 *  账户, 退出登录 — from `PlatformNav`, which reads every address from the server's auth state.
 *  Nothing here is a second copy of an account page. */
import { PlatformNav } from "@synwevo/auth-client";
import { Brand, useI18n } from "@synwevo/ui";

export function Topbar() {
  const { t } = useI18n();
  return <header className="topbar msg-topbar">
    <Brand href="/" product="Message" label={t("会话列表")} markBelow={720} />
    <span className="spacer" />
    <PlatformNav />
  </header>;
}
