/** The portal's public addresses: the home, 数据与隐私 and 服务说明, each in three languages.
 *  Mirrors `PUBLIC_PATHS` in synwevo_portal/public.py, which server-renders the same pages. */
export type PublicPage = "home" | "privacy" | "service";
export type PublicLocale = "zh-Hans" | "zh-Hant" | "en";

const LOCALES: readonly PublicLocale[] = ["zh-Hans", "zh-Hant", "en"];
const PAGES: readonly PublicPage[] = ["home", "privacy", "service"];

/** `/`, `/en`, `/zh-Hant/privacy`, `/en/service` … — zh-Hans is the unprefixed canonical form. */
export function publicPath(page: PublicPage, locale: string): string {
  const prefix = locale === "zh-Hans" ? "" : `/${locale}`;
  if (page === "home") return prefix || "/";
  return `${prefix}/${page}`;
}

/** Which public page an address names, or null for every private address. `/zh-Hans/...` is
 *  accepted as an alias and normalised by the shell. */
export function publicRoute(pathname: string): { page: PublicPage; locale: PublicLocale } | null {
  const parts = pathname.replace(/\/+$/, "").split("/").filter(Boolean);
  let locale: PublicLocale = "zh-Hans";
  if (parts.length && (LOCALES as readonly string[]).includes(parts[0])) locale = parts.shift() as PublicLocale;
  if (parts.length === 0) return { page: "home", locale };
  if (parts.length === 1 && (PAGES as readonly string[]).includes(parts[0]) && parts[0] !== "home") return { page: parts[0] as PublicPage, locale };
  return null;
}

/** <title> and meta description of each public page, as zh-Hans catalogue keys. */
export const PUBLIC_META: Record<PublicPage, { title: string; description: string }> = {
  home: { title: "Synwevo · 工作流程自动化平台", description: "文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。" },
  privacy: { title: "数据与隐私 · Synwevo", description: "Synwevo 如何处理你的账户、文档与消息数据：存储位置、Cookie、保留期限、导出与删除。" },
  service: { title: "服务说明 · Synwevo", description: "Synwevo 是什么、有哪些应用与角色、如何注册、有哪些限制、遇到问题找谁。" },
};
