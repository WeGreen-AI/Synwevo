import { messagesHref, useSession } from "@synwevo/auth-client";
import { Logo, useI18n } from "@synwevo/ui";
import { DocumentIcon, Shell, signInUrl as signInBase } from "./Portal";
import { publicPath } from "./publicRoutes";

/** The header of every public page. `signedIn` is three-valued: undefined while the session is
 *  still being confirmed. The header then shows neither "登录" nor "进入工作空间" — a signed-in
 *  person must never see a sign-in link flash before the page knows who they are. The anchor
 *  keeps the `pt-link` class: services/identity/scripts/ui_e2e_shell.py samples it. */
export function PublicNav({ signedIn, registerUrl, home }: { signedIn: boolean | undefined; registerUrl: string; home: boolean }) {
  const { t, locale } = useI18n();
  const signInUrl = `${signInBase}&lang=${locale}`;
  const base = home ? "" : publicPath("home", locale);
  return <>
    <a className="pt-nav-detail" href={`${base}#applications`}>{t("应用")}</a>
    <a className="pt-nav-detail" href={`${base}#together`}>{t("协作方式")}</a>
    <a className="pt-nav-detail" href={`${base}#faq`}>{t("常见问题")}</a>
    {signedIn === undefined ? <span className="pt-nav-pending" aria-hidden="true" /> : <a className="pt-link" href={signedIn ? "/workspace" : signInUrl}>{t(signedIn ? "进入工作空间" : "登录")}</a>}
    {signedIn === false && <a className="btn primary pt-nav-register" href={registerUrl}>{t("注册账户")}</a>}
  </>;
}

/** Abstract decoration: strands converging into one woven band. Static on purpose — the words
 *  never wait for it, and there is nothing for prefers-reduced-motion to stop. */
export function WeaveMotif({ className = "" }: { className?: string }) {
  return <svg className={`pt-weave ${className}`} viewBox="0 0 320 160" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round">
    <path d="M0 18 C 110 18, 150 80, 320 80" />
    <path d="M0 49 C 110 49, 150 80, 320 80" opacity=".75" />
    <path d="M0 80 H 320" opacity=".5" />
    <path d="M0 111 C 110 111, 150 80, 320 80" opacity=".75" />
    <path d="M0 142 C 110 142, 150 80, 320 80" />
    <path d="M196 72 q 16 12 32 0 t 32 0 t 32 0 t 32 0" opacity=".9" />
    <path d="M196 88 q 16 -12 32 0 t 32 0 t 32 0 t 32 0" opacity=".9" />
  </svg>;
}

export function PublicHome({ signedIn, registerUrl: registerBase, accountBase }: { signedIn: boolean | undefined; registerUrl: string; accountBase: string }) {
  const { t, locale } = useI18n();
  const registerUrl = `${registerBase}${registerBase.includes("?") ? "&" : "?"}lang=${locale}`;
  const signInUrl = `${signInBase}&lang=${locale}`;
  // Synwevo Message has its own origin (CI-66/CI-82): the registry's address through its sign-in start
  const messagesUrl = messagesHref(useSession().state ?? { account_url: `${accountBase.replace(/\/$/, "")}/account` }, { lang: locale });
  const primary = signedIn ? "/workspace" : registerUrl;
  const openApp = (href: string) => (signedIn ? href : signInUrl);
  const together = [
    ["工作空间", "个人与团队的工作分开存放。应用与文档权限按工作空间分别授予，切换不会合并内容。"],
    ["组织", "组织汇集成员与团队空间。组织成员身份不自动授予每个工作空间的访问权，管理权也不等于内容访问权。"],
    ["角色与授权", "所有者、管理员、编辑者、查看者各有边界。自动化集成使用受限的服务凭据，只在获准的工作空间与范围内执行，每次访问都经过权限检查。"],
    ["通知", "邀请、@提及、私信与安全事件汇入同一个收件箱；按类别选择即时、汇总或关闭，也可以开启浏览器推送。"],
  ];
  const trust = [
    "账户数据可以随时导出，也可以申请删除；删除有 7 天缓冲期，期间可以撤回。",
    "传输经 HTTPS 加密，服务器端不做端到端加密；平台管理员读不到业务文档与对话，组织的举报处理只看到被举报的那一条消息。",
    "没有任何 AI 处理你的内容。在线状态只给同一会话的成员看，可以关闭；已读回执默认关闭，双方都开启才显示。",
    "只用本站自己的 Cookie 维持登录；数据保存在位于日本东京的单台服务器上，每次发布前做备份。",
  ];
  const faq: [string, string][] = [
    ["Synwevo 适合做什么？", "把文档制作和团队沟通放进同一个工作空间：用 Synwevo Docs 从来源数据批量生成文档，用 Synwevo Message 与组织成员私聊或建群。账户、组织和权限在各应用之间通用。"],
    ["个人可以用吗，还是必须建组织？", "都可以。注册时选择「个人使用」，会建立你自己的工作空间；选择「团队协作」，可以创建组织，再邀请成员。"],
    ["成员怎样加入团队？", "由组织或工作空间的管理者发出邀请，邀请通过邮件送达，用收到邀请的邮箱确认后加入。成员名单也可以批量导入，导入的人同样以邀请方式加入。"],
    ["谁能看到我的文档和消息？", "文档按工作空间和角色授权；消息只对会话成员可见，新成员只看到加入之后的消息。平台管理员治理账户与平台配置，读不到业务文档与对话。"],
    ["私信会给我发邮件吗？", "不会逐条发送。私信邮件默认关闭；需要时可以在账户的通知设置里开启未读摘要，按时间窗口合并发送。"],
    ["支持哪些文件格式？", "Synwevo Docs 的模板可用 DOCX、XLSX、PDF、PPTX 以及 PNG / JPEG 图片，来源数据可以是表格、文档或扫描件。各格式支持的能力以应用内说明为准。"],
    ["可以导出或删除我的数据吗？", "可以。账户页可以随时导出你的账户数据，包括你发出的消息；也可以申请删除账户，删除有 7 天缓冲期，期间可以撤回。"],
    ["数据保存在哪里？有 AI 处理我的内容吗？", "数据库与文件保存在位于日本东京的单台服务器上，每次发布前做备份，传输经 HTTPS 加密。没有任何 AI 处理你的内容。"],
  ];
  return <Shell publicHome right={<PublicNav signedIn={signedIn} registerUrl={registerUrl} home />}>
    <section className="pt-hero" aria-labelledby="pt-hero-title">
      <div className="pt-hero-copy">
        <p className="pt-eyebrow"><span className="pt-live-dot" aria-hidden="true"/>{t("工作流程自动化平台")}</p>
        <h1 id="pt-hero-title">{t("让工作，自然相连。")}</h1>
        <p className="pt-hero-lead">{t("文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。")}</p>
        <div className="pt-actions">
          <a className="btn primary lg" href={primary}>{t(signedIn ? "进入工作空间" : "创建账户")} <span aria-hidden="true">↗</span></a>
          <a className="pt-text-action" href="#applications">{t("了解应用")} <span aria-hidden="true">↓</span></a>
        </div>
        <p className="pt-hero-note">{t("一个账户，进入你有权使用的工作空间与应用。")}</p>
      </div>
      <aside className="pt-brand-card" aria-labelledby="pt-brand-title">
        <WeaveMotif className="pt-brand-motif" />
        <div className="pt-brand-head"><Logo version="wordmark" height={18} /><span id="pt-brand-title">{t("名字的由来")}</span></div>
        <p className="pt-brand-text">{t("Synwevo，取意于协同与编织。分散的文档、数据与沟通，在这里被织成有序的工作；想法在协作中成形，日常在持续完善中变得从容。")}</p>
      </aside>
    </section>
    <section className="pt-format-strip" aria-label={t("平台的基本概念")}>
      <span>{t("同一个入口")}</span>
      {["统一账户", "工作空间", "组织与角色", "文档", "消息", "通知"].map(label => <span key={label}>{t(label)}</span>)}
    </section>
    <section id="applications" className="pt-public-section">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("今天可用的应用")}</p><h2>{t("两个应用，各司其职。")}</h2><p>{t("Synwevo Docs 处理文档，Synwevo Message 负责组织内的沟通。它们共用同一个账户、同一套工作空间与授权；此外没有别的应用。")}</p></div>
      <div className="pt-app-grid">
        <article className="pt-featured-app">
          <div className="pt-featured-heading"><span className="pt-doc-icon"><DocumentIcon/></span><div><p className="pt-eyebrow">{t("文档自动化")}</p><h3>Synwevo Docs</h3></div></div>
          <p>{t("选择来源数据，接入模板，检查后批量生成。重复的文档制作交给可复用的规则，交付前的检查留给人。")}</p>
          <div className="pt-app-formats" aria-label={t("文档格式")}>{["DOCX", "XLSX", "PDF", "PPTX", "CSV"].map(format => <span key={format}>{format}</span>)}</div>
          <div className="pt-featured-bottom"><p className="pt-hint">{t("各格式支持的能力以应用内选项为准；可用应用由工作空间授权决定。")}</p><a className="btn" href={openApp("/workspace")}>{t(signedIn ? "进入工作空间" : "登录后打开")} <span aria-hidden="true">↗</span></a></div>
        </article>
        <article className="pt-featured-app">
          <div className="pt-featured-heading"><span className="pt-doc-icon"><MessageIcon/></span><div><p className="pt-eyebrow">{t("组织消息")}</p><h3>Synwevo Message</h3></div></div>
          <p>{t("与同一组织的成员私聊或建群：引用回复、@提及、表情回应、附件与文档链接卡片。新成员只看到加入之后的消息，离开后即止。")}</p>
          <div className="pt-app-formats" aria-label={t("消息能力")}>{["私聊", "群组", "公告", "举报处理"].map(label => <span key={label}>{t(label)}</span>)}</div>
          <div className="pt-featured-bottom"><p className="pt-hint">{t("独立打开，也可以从工作空间顶栏的「消息」进入。")}</p><a className="btn" href={openApp(messagesUrl)}>{t(signedIn ? "打开消息" : "登录后打开")} <span aria-hidden="true">↗</span></a></div>
        </article>
      </div>
    </section>
    <section id="together" className="pt-public-section">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("一起完成工作")}</p><h2>{t("各有归属，彼此看得见。")}</h2><p>{t("组织、工作空间与角色决定谁能进入哪里；通知把需要你处理的事送到面前。")}</p></div>
      <ol className="pt-workflow pt-together">{together.map(([title, detail]) => <li key={title}><h3>{t(title)}</h3><p>{t(detail)}</p></li>)}</ol>
    </section>
    <section className="pt-principles pt-public-section" id="trust">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("数据与隐私")}</p><h2>{t("说清楚做了什么，也说清楚没做什么。")}</h2></div>
      <ul className="pt-trust-list">{trust.map(line => <li key={line}>{t(line)}</li>)}</ul>
      <div className="pt-actions"><a className="pt-text-action" href={publicPath("privacy", locale)}>{t("完整的数据与隐私说明")} <span aria-hidden="true">→</span></a><a className="pt-text-action" href={publicPath("service", locale)}>{t("服务说明与限额")} <span aria-hidden="true">→</span></a></div>
    </section>
    <section className="pt-public-section" id="faq" aria-labelledby="pt-faq-title">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("常见问题")}</p><h2 id="pt-faq-title">{t("开始之前，你可能想知道。")}</h2></div>
      <div className="pt-faq">{faq.map(([q, answer]) => <details key={q}><summary>{t(q)}</summary><p>{t(answer)}</p></details>)}</div>
    </section>
    <section className="pt-next pt-public-section" id="next">
      <div><p className="pt-eyebrow">{t("下一步")}</p><h2>{t("从一个工作空间开始。")}</h2><p>{t("创建账户，选择个人使用或加入团队。邀请通过邮件送达，用当前邮箱确认即可加入。")}</p></div>
      <div className="pt-next-cta"><a className="btn primary lg" href={primary}>{t(signedIn ? "进入工作空间" : "注册账户")} <span aria-hidden="true">↗</span></a>{!signedIn && <a className="pt-link" href={signInUrl}>{t("已有账户？登录")}</a>}</div>
    </section>
  </Shell>;
}

function MessageIcon() {
  return <svg viewBox="0 0 32 36" width="32" height="36" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M5 8h22v16H14l-6 5v-5H5z" strokeLinejoin="round"/><path d="M11 14h10M11 18h6" strokeLinecap="round"/></svg>;
}
