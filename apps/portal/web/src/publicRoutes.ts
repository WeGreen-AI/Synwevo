/** The portal's public addresses: the home, 数据与隐私, 服务说明, and the versioned 服务条款,
 *  AI 使用规范 and 定价 (synwevo_core.terms), each in three languages.
 *  Mirrors `PUBLIC_PATHS` in synwevo_portal/public.py, which server-renders the same pages. */
export type PublicPage = "home" | "privacy" | "service" | "terms" | "ai-use" | "pricing";
export type PublicLocale = "zh-Hans" | "zh-Hant" | "en";

const LOCALES: readonly PublicLocale[] = ["zh-Hans", "zh-Hant", "en"];
const PAGES: readonly PublicPage[] = ["home", "privacy", "service", "terms", "ai-use", "pricing"];

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
  terms: { title: "服务条款 · Synwevo", description: "使用 Synwevo 的条款：账户、钱包与充值（不可退款）、按量计费、免费额度、组织付费与 AI 输出。" },
  "ai-use": { title: "AI 使用规范 · Synwevo", description: "使用 Synwevo Agent 时不允许的内容与行为、内容安全如何执行，以及 AI 生成内容的标识。" },
  pricing: { title: "定价 · Synwevo", description: "Synwevo Agent 如何计费：人民币钱包、按实际用量扣费、每条回答的最高金额、免费额度与组织付费。" },
};

/** Version and effective date of the versioned documents — the same values as
 *  synwevo_core.terms.DOCUMENTS (tests/integration/test_public_ai_facts.py keeps them equal). */
export const DOCUMENT_VERSIONS: Record<"terms" | "ai-use" | "pricing", { version: string; effective: string }> = {
  terms: { version: "2026-10-24", effective: "2026-10-24" },
  "ai-use": { version: "2026-10-05", effective: "2026-10-05" },
  pricing: { version: "2026-10-24", effective: "2026-10-24" },
};

/** True on the workspace host (production: workplace.synwevo.com), where the server marks the
 *  shell with `<meta name="synwevo-surface" content="workspace">`: there "/" is the workspace,
 *  and the public pages live on the site host. Absent on a single-host deployment. */
export function workspaceSurface(): boolean {
  return typeof document !== "undefined" && document.querySelector('meta[name="synwevo-surface"]')?.getAttribute("content") === "workspace";
}

/** Whether this page is the workspace: `/workspace` anywhere, or `/` on the workspace host. */
export function isWorkspacePath(pathname: string): boolean {
  return pathname === "/workspace" || (workspaceSurface() && pathname === "/");
}

/** A public-site address from the workspace surface: absolute on the site's configured origin
 *  (server-controlled `site_url`), a plain path everywhere else. */
export function siteHref(siteUrl: string | undefined, path: string): string {
  if (!workspaceSurface() || !siteUrl) return path;
  try {
    const u = new URL(siteUrl);
    return u.protocol === "https:" || u.protocol === "http:" ? u.origin + path : path;
  } catch { return path; }
}
