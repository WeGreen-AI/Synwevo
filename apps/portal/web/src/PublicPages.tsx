import { useEffect, useState, type ReactNode } from "react";
import { useI18n } from "@synwevo/ui";
import { Shell, signInUrl as signInBase } from "./Portal";
import { PublicNav, WeaveMotif } from "./PublicHome";
import { DOCUMENT_VERSIONS, publicPath, type PublicPage as PageId } from "./publicRoutes";

/** What `/public/deployment.json` reports: the account service's and Synwevo Docs' own answers
 *  about this deployment, or null where a service could not be read. */
type Facts = {
  checked_at: number;
  accounts: {
    malware_scanner: "on" | "off" | "down" | null; human_verification: boolean | null; external_providers: string[] | null;
    registration: "open" | "invite" | "closed" | null; mail_sender: { name: string; address: string } | false | null;
    member_import_cleanup?: boolean | null;
  };
  docs: { malware_scanner: "on" | "off" | "down" | null };
  /** Synwevo Agent: the registry's release state and the configured model (public.py agent_fact). */
  agent?: { released: boolean | null; provider: "deepseek" | "anthropic" | null; model_configured: boolean | null };
};

type Content = { eyebrow: string; heading: string; lead: string; summary: string[]; sections: Section[] };
const CONTENT: Record<Exclude<PageId, "home">, () => Content> = {
  privacy: privacyContent, service: serviceContent, terms: termsContent, "ai-use": aiUseContent, pricing: pricingContent,
};
/** The page each document links to next, and its label. */
const NEXT: Record<Exclude<PageId, "home">, Exclude<PageId, "home">> = {
  privacy: "service", service: "privacy", terms: "pricing", pricing: "terms", "ai-use": "terms",
};
const LABEL: Record<Exclude<PageId, "home">, string> = {
  privacy: "数据与隐私", service: "服务说明", terms: "服务条款", "ai-use": "AI 使用规范", pricing: "定价",
};

/** undefined while loading, null when the document itself could not be fetched. */
function useDeploymentFacts(): Facts | null | undefined {
  const [facts, setFacts] = useState<Facts | null | undefined>(undefined);
  useEffect(() => {
    const controller = new AbortController();
    fetch("/public/deployment.json", { signal: controller.signal, credentials: "same-origin" })
      .then(response => (response.ok ? response.json() : null))
      .then(value => { if (!controller.signal.aborted) setFacts(value && typeof value === "object" ? value as Facts : null); })
      .catch(() => { if (!controller.signal.aborted) setFacts(null); });
    return () => controller.abort();
  }, []);
  return facts;
}

type Block =
  | { kind: "p"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "defs"; items: [string, string][] }
  | { kind: "table"; head: [string, string]; rows: [string, string][] };
type Section = { id: string; title: string; blocks: Block[] };

export function PublicPage({ page, signedIn, registerUrl: registerBase, accountBase }: { page: Exclude<PageId, "home">; signedIn: boolean | undefined; registerUrl: string; accountBase: string }) {
  const { t, locale } = useI18n();
  const registerUrl = `${registerBase}${registerBase.includes("?") ? "&" : "?"}lang=${locale}`;
  const signInUrl = `${signInBase}&lang=${locale}`;
  const accountPrivacy = `${accountBase.replace(/\/$/, "")}/account#privacy`;
  const facts = useDeploymentFacts();
  const content = CONTENT[page]();
  const other = NEXT[page];
  const versioned = page === "terms" || page === "ai-use" || page === "pricing" ? DOCUMENT_VERSIONS[page] : null;
  return <Shell publicHome right={<PublicNav signedIn={signedIn} registerUrl={registerUrl} home={false} />}>
    <article className="pt-legal">
      <header className="pt-legal-head">
        <WeaveMotif className="pt-legal-motif" />
        <p className="pt-eyebrow">{t(content.eyebrow)}</p>
        <h1>{t(content.heading)}</h1>
        <p className="pt-legal-lead">{t(content.lead)}</p>
        {versioned ? <p className="pt-hint">{t("版本 {version}，自 {date} 起生效。", { version: versioned.version, date: versioned.effective })}</p>
          : <p className="pt-hint">{t("本页描述当前部署的实际做法，随代码与配置一起更新；它不是正式的法律条款。")}</p>}
      </header>
      <section className="pt-summary" aria-labelledby={`pt-${page}-summary`}>
        <h2 id={`pt-${page}-summary`}>{t("摘要")}</h2>
        <ul>{content.summary.map(line => <li key={line}>{t(line)}</li>)}</ul>
        <p className="pt-summary-links">
          {page === "privacy" ? <a className="pt-text-action" href={signedIn ? accountPrivacy : signInUrl}>{t("导出或删除我的账户数据")} <span aria-hidden="true">→</span></a>
            : <a className="pt-text-action" href={signedIn ? "/workspace" : registerUrl}>{t(signedIn ? "进入工作空间" : "创建账户")} <span aria-hidden="true">→</span></a>}
          <a className="pt-text-action" href={publicPath(other, locale)}>{t(LABEL[other])} <span aria-hidden="true">→</span></a>
        </p>
      </section>
      <LiveStatus facts={facts} page={page} />
      <nav className="pt-legal-toc" aria-label={t("本页目录")}>
        {content.sections.map((section, index) => <a key={section.id} href={`#${section.id}`}><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{t(section.title)}</a>)}
      </nav>
      {content.sections.map((section, index) => <section key={section.id} id={section.id} className="pt-legal-section" aria-labelledby={`${section.id}-title`}>
        <h2 id={`${section.id}-title`}><span aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>{t(section.title)}</h2>
        {section.blocks.map((block, n) => <Rendered key={n} block={block} t={t} />)}
      </section>)}
      <footer className="pt-legal-foot">
        <p>{t("账户与数据相关的请求在账户页发起；组织内的事务请联系组织管理员。")}</p>
        <div className="pt-actions">
          <a className="btn" href={publicPath("home", locale)}>{t("返回首页")}</a>
          <a className="btn" href={publicPath(other, locale)}>{t(LABEL[other])}</a>
        </div>
      </footer>
    </article>
  </Shell>;
}

function Rendered({ block, t }: { block: Block; t: (s: string) => string }): ReactNode {
  if (block.kind === "p") return <p>{t(block.text)}</p>;
  if (block.kind === "list") return <ul>{block.items.map(item => <li key={item}>{t(item)}</li>)}</ul>;
  if (block.kind === "defs") return <dl className="pt-defs">{block.items.map(([term, detail]) => <div key={term}><dt>{t(term)}</dt><dd>{t(detail)}</dd></div>)}</dl>;
  return <table className="pt-legal-table"><thead><tr><th scope="col">{t(block.head[0])}</th><th scope="col">{t(block.head[1])}</th></tr></thead>
    <tbody>{block.rows.map(([name, value]) => <tr key={name}><th scope="row">{t(name)}</th><td>{t(value)}</td></tr>)}</tbody></table>;
}

/** The statements that depend on how this deployment is configured, read from the services
 *  themselves rather than written into the page. */
function LiveStatus({ facts, page }: { facts: Facts | null | undefined; page: PageId }) {
  const { t, locale } = useI18n();
  const unread = t("暂时无法读取");
  const scanner = (value: Facts["accounts"]["malware_scanner"] | undefined) => facts === undefined ? t("读取中…")
    : value === "on" ? t("已启用") : value === "off" ? t("未启用") : value === "down" ? t("已配置，当前不可用") : unread;
  const yesNo = (value: boolean | null | undefined) => facts === undefined ? t("读取中…") : value === true ? t("已启用") : value === false ? t("未启用") : unread;
  const providers = facts === undefined ? t("读取中…") : facts?.accounts.external_providers == null ? unread
    : facts.accounts.external_providers.length ? facts.accounts.external_providers.join(" · ") : t("未启用");
  const registration = facts === undefined ? t("读取中…") : facts?.accounts.registration === "open" ? t("开放注册：邮箱验证后即可使用")
    : facts?.accounts.registration === "invite" ? t("仅凭邀请注册") : facts?.accounts.registration === "closed" ? t("已关闭") : unread;
  const sender = facts === undefined ? t("读取中…") : facts?.accounts.mail_sender === false ? t("未配置")
    : facts?.accounts.mail_sender ? `${facts.accounts.mail_sender.name} <${facts.accounts.mail_sender.address}>`.trim() : unread;
  const rows: [string, string][] = [
    [t("消息附件与成员导入文件的恶意软件扫描"), scanner(facts?.accounts.malware_scanner)],
    [t("Synwevo Docs 上传文件的恶意软件扫描"), scanner(facts?.docs.malware_scanner)],
    [t("登录与注册的人机验证"), yesNo(facts?.accounts.human_verification)],
    [t("外部账户登录"), providers],
    [t("成员导入到期删除（上传 7 天后，含上传文件）"), yesNo(facts?.accounts.member_import_cleanup)],
    ...(page === "service" ? [[t("注册策略"), registration], [t("系统邮件发件人"), sender]] as [string, string][] : []),
  ];
  const agent = facts?.agent;
  const agentState = facts === undefined ? t("读取中…") : !agent || agent.released === null ? unread
    : !agent.released ? t("未开放：不会向 AI 模型发送任何内容")
    : agent.model_configured === null ? unread
    : !agent.model_configured ? t("已上线，模型服务尚未开放：不会向 AI 模型发送任何内容")
    : agent.provider === "deepseek" ? t("已开放：经你单独同意后，发送给模型提供方 DeepSeek")
    : agent.provider === "anthropic" ? t("已开放：发送给模型提供方 Anthropic") : unread;
  rows.unshift([t("Synwevo Agent（AI 助手）"), agentState]);
  const at = facts?.checked_at ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(facts.checked_at * 1000) : null;
  return <section className="pt-live" aria-labelledby={`pt-${page}-live`}>
    <div className="pt-live-head"><h2 id={`pt-${page}-live`}>{t("本部署的当前状态")}</h2><p className="pt-hint">{t("直接读取运行中的服务，每分钟刷新。")}{at ? ` · ${at}` : ""}</p></div>
    <dl className="pt-live-list">{rows.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
    {facts === null && <p className="pt-hint" role="status">{t("当前无法读取部署状态；上面的项目以「暂时无法读取」显示，而不是默认值。")}</p>}
  </section>;
}

function privacyContent(): Content {
  return {
    eyebrow: "数据与隐私",
    heading: "你的数据，说清楚。",
    lead: "Synwevo 保存哪些数据、谁能看到、放在哪里、留多久，以及你如何导出或删除。",
    summary: [
      "账户数据可以随时导出，也可以申请删除；删除有 7 天缓冲期，期间可以撤回。",
      "数据保存在位于日本东京的单台服务器上；传输经 HTTPS 加密，服务器端不做端到端加密。",
      "只用本站自己的 Cookie 维持登录；界面偏好与消息草稿留在你的浏览器里。",
      "平台管理员读不到业务文档与对话；组织的举报处理只看到被举报的那一条消息。",
      "只有 Synwevo Agent 会把内容发给 AI 模型：在你使用它并单独同意后，发送给模型提供方 DeepSeek；组织工作空间的内容默认不发送。其它应用不把你的内容交给 AI 模型。",
      "在线状态只给同一会话的成员看，可以关闭；已读回执默认关闭，双方都开启才显示。",
      "生产环境的页面经由边缘网络服务商提供，其匿名访问统计脚本不使用 Cookie，也不识别个人。",
    ],
    sections: [
      { id: "privacy-data", title: "我们保存什么", blocks: [{ kind: "defs", items: [
        ["账户", "邮箱地址、显示名、头像、界面语言与时区；登录凭据的派生值（口令哈希、通行密钥公钥、两步验证种子与恢复码）；登录设备与会话；安全活动记录，含 IP 地址与时间。"],
        ["组织与工作空间", "成员关系与角色、邀请（收件邮箱）、成员导入的文件与解析结果、组织设置。"],
        ["文档", "Synwevo Docs 里的项目、来源文件、模板、生成结果、任务记录、评论与协作历史。"],
        ["消息", "会话与成员、消息正文、附件、表情回应、你自己的阅读位置、举报时固定的消息快照。"],
        ["通知", "收件箱条目（消息类通知带简短预览）、通知偏好、浏览器推送订阅（推送地址与密钥，只对你自己的浏览器有效）。"],
        ["接口平台", "服务凭据及其调用元数据（方法、路径、状态码、耗时），保留 30 天。"],
      ] }] },
      { id: "privacy-access", title: "谁能看到", blocks: [{ kind: "list", items: [
        "你自己：账户页里的资料、登录的设备、安全活动与数据导出。",
        "工作空间成员：按角色访问该空间的文档；切换工作空间不会合并内容。",
        "组织管理者：成员与角色、邀请、导入记录。举报处理只看到被举报消息的快照、举报人与原因，看不到会话的其它内容。",
        "会话成员：消息只对会话成员可见。新成员只看到加入之后的消息；退出或被移除后不再可见，重新加入也不恢复。",
        "平台管理员：治理账户与平台配置，读不到业务文档与对话。",
        "机器访问：服务凭据受当前有效的工作空间批准、资源范围与权限范围的交集限制。",
        "在线状态与正在输入只给同一会话的成员看，不保存，可以在消息设置里关闭；已读回执默认关闭，双方都开启才显示。",
      ] }] },
      { id: "privacy-ai", title: "AI 与模型提供方", blocks: [
        { kind: "p", text: "Synwevo Agent 是你自己的 AI 助手。它是唯一会把内容发给 AI 模型的地方，只在你使用它时发送；它是否在本部署开放，见上方「当前状态」。" },
        { kind: "defs", items: [
          ["单独同意", "第一次发消息之前，Agent 会请你单独同意把内容发送给模型提供方，并记录你同意的版本。说明更新后会再次询问；你可以随时在 Agent 设置里撤回，撤回后不再发起任何模型调用。"],
          ["发送什么", "你在这次对话里写的消息、同一对话中较早的消息、你让 Agent 记住的偏好，以及 Agent 为你读取的工作空间内容——仅限 AI 处理政策允许的工作空间。组织工作空间默认不允许；个人工作空间由你决定。"],
          ["模型提供方（受托处理方）", "杭州深度求索人工智能基础技术研究有限公司（DeepSeek）。我们以其开放平台用户的身份调用模型服务，与 DeepSeek 没有合作或背书关系。"],
          ["跨境传输与存储地", "Synwevo 的服务器在日本东京；经 Agent 发送的内容会传输给 DeepSeek，按 DeepSeek 的隐私政策存储在中国境内。"],
          ["是否用于改进模型", "按 DeepSeek 的隐私政策，内容可能被用于改进其模型。我们尚未取得 DeepSeek 关于 API 数据使用与保留期限的书面确认；获得确认后将更新本页。"],
          ["你自己的模型密钥", "如果你在 Agent 设置里使用自己的模型密钥，内容以你自己的账户发送给该密钥所属的服务商，适用你与该服务商之间的条款。"],
          ["内容安全", "消息与回答经过管理员维护的内容安全规则检查，模型提供方也有自己的过滤。被拦下时只记录命中的规则、方向与长度，不记录内容。平台管理员读不到你的对话；你举报一条回答时，只有在你选择附上时，回答原文才会交给审核人员。"],
          ["AI 生成内容的标识", "每条回答与导出内容都注明由 AI 生成，可能有误，仅供参考。"],
        ] },
      ] },
      { id: "privacy-storage", title: "存储、传输与备份", blocks: [{ kind: "list", items: [
        "数据库与文件都保存在位于日本东京的单台服务器上。",
        "浏览器与服务器之间经 HTTPS 加密；公开域名由边缘网络服务商终止 TLS，再经隧道到达源站。服务器端以明文存储，没有端到端加密。",
        "上传的文件先经过大小与格式检查；恶意软件扫描是部署选项，上方「当前状态」显示它是否启用。",
        "被判定为恶意的文件不再提供给任何人。扫描启用前存入、之后补扫判定的文件移入服务器上的隔离目录（没有任何页面或接口提供它），由运维复核：误报在扫描引擎撤回该特征后恢复，确认后永久删除。",
        "每次发布前生成一份完整备份，保留在同一台服务器上，目前不自动清理；备份包含全部业务数据与密钥材料。",
      ] }] },
      { id: "privacy-cookies", title: "Cookie 与浏览器存储", blocks: [
        { kind: "p", text: "四个 Cookie 都只发给本站自己的域名，不跨站，也不用于统计。" },
        { kind: "table", head: ["名称", "用途与期限"], rows: [
          ["dw_session", "登录会话；HttpOnly。闲置 12 小时失效，选择「保持登录」后为 14 天，无论如何最长 30 天。"],
          ["dw_csrf", "防跨站请求伪造：页面读取后随每个修改请求回传。"],
          ["dw_authreq", "登录往返的一次性标识，10 分钟内有效。"],
          ["dw_extreq", "外部账户登录往返的一次性标识。"],
        ] },
        { kind: "defs", items: [
          ["本地存储（留在你的浏览器）", "界面语言、界面布局偏好、消息草稿、Enter 键发送偏好、退出登录标记。"],
          ["会话存储（关闭标签页即清除）", "当前工作空间、登录后的返回目标、登录流程状态。"],
          ["第三方脚本", "生产环境的页面由边缘网络服务商提供，其网站分析脚本收集页面浏览与加载性能数据。按该服务商的说明，它不使用 Cookie 或浏览器存储，在最近的节点丢弃 IP 地址，也不做指纹识别。本站没有其它第三方脚本。"],
        ] },
      ] },
      { id: "privacy-retention", title: "保留多久", blocks: [{ kind: "table", head: ["数据", "期限"], rows: [
        ["账户删除", "申请后 7 天缓冲期，到期执行。"],
        ["会话记录", "结束后 7 天内清理。"],
        ["登录与注册的频率计数", "最后一次尝试后 24 小时。"],
        ["邀请", "默认 7 天有效，可设 1–30 天；邮箱验证链接 24 小时。"],
        ["组织删除", "7 天内可恢复。"],
        ["接口调用元数据", "30 天。"],
        ["Agent 对话与记忆", "保留到你删除它们，或账户删除时清除。模型提供方一侧的保留，按 DeepSeek 的隐私政策。"],
        ["内容安全记录", "只有命中的规则、方向与长度，不含内容；长期保留。"],
        ["计费、充值与付款记录", "账户删除后不再关联你的姓名与邮箱，按法定期限保留。"],
        ["成员导入记录", "上传 7 天后到期；到期的导入记录与上传文件是否自动删除，见上方「当前状态」。"],
        ["安全活动记录、系统邮件投递记录、通知", "目前长期保留，没有自动清理。"],
        ["消息与文档", "删除的消息对他人显示「消息已删除」，正文清空、序号保留。删除的文档项目在应用内标记为已删除，底层文件目前不做自动回收。"],
      ] }] },
      { id: "privacy-rights", title: "导出与删除", blocks: [{ kind: "defs", items: [
        ["导出", "账户页「数据与账户」→ 导出，需要当前口令。得到一份 JSON：资料、工作空间与组织成员关系、通行密钥名称、安全活动、头像；你发出的消息（正文、时间、所在会话，附件只记文件名）、你所在的会话、消息设置、屏蔽列表与通知偏好。不含他人的消息与附件文件，也不含文档内容——文档在 Synwevo Docs 内按项目下载。也包含 Agent 对话的可见文字、记忆、设置、数据同意、报价、接受的条款版本与钱包流水；不含模型的思考过程、工具读取的业务数据或任何密钥。"],
        ["删除", "在同一处申请，需要当前口令。申请后立即退出所有登录、撤销待接受的邀请，并进入 7 天缓冲期，期间邮件里的链接可以撤回。到期后删除口令、通行密钥、两步验证与恢复码，清空资料与邮箱；账户标识与该邮箱的保留记录不再分配给他人，以免旧邀请被重新领取。你退出全部会话：你是群主的群交给最早加入的管理员（没有管理员则交给最早加入的成员），只剩你一人的群归档。你在组织里留下的消息、评论与文档保留，不再关联你的资料。Agent 的对话、记忆与个人模型密钥被删除；钱包中未用完的余额作废，不退还（充值一经完成不可退款）；计费与付款记录不再关联你的资料，按法定期限保留。"],
        ["前提", "若你是某个组织的唯一所有者，或最后一位平台管理员，需要先移交。"],
      ] }] },
      { id: "privacy-third", title: "第三方与外部服务", blocks: [{ kind: "defs", items: [
        ["外部账户登录", "是否可用见上方「当前状态」。只在你选择时使用，用于确认你的邮箱地址与显示名。"],
        ["人机验证", "是否启用见上方「当前状态」。启用时，登录、注册等动作前由验证服务判断请求是否来自自动程序。"],
        ["AI 模型提供方", "DeepSeek，只在你使用 Synwevo Agent 并单独同意后；见上文「AI 与模型提供方」。"],
        ["支付", "为钱包充值时，付款由支付服务商处理；本站不接触你的卡号，只记录订单、金额与支付状态。"],
        ["系统邮件", "由自行运营的邮件服务器发出，经第三方投递服务中转到你的邮箱；「已发送」指邮件服务器已接收。"],
        ["浏览器推送", "你开启后，通知经浏览器厂商的推送服务送达；内容加密，只含通知标题、正文与链接。退出登录即撤销该浏览器的订阅。"],
      ] }] },
    ],
  };
}

function serviceContent(): Content {
  return {
    eyebrow: "服务说明",
    heading: "这项服务是什么，怎么用。",
    lead: "Synwevo 目前提供什么、怎样注册、有哪些限额、出了问题找谁。",
    summary: [
      "Synwevo 是一个工作流程自动化平台：统一账户、工作空间、组织与通知，加上 Synwevo Docs 与 Synwevo Message 两个应用。",
      "注册经邮箱验证后生效，机构邮箱同样可用；当前的注册策略以下方「当前状态」为准。",
      "平台管理、组织、工作空间与服务凭据四层角色，各有边界；管理权不等于内容访问权。",
      "限额写在代码里：消息 4000 字、附件 20 MB、每个工作空间每天 50 个邀请等，见下表。",
      "单台服务器部署，每次发布前备份。",
      "账户可随时申请删除（7 天缓冲）；组织删除 7 天内可恢复。",
    ],
    sections: [
      { id: "service-scope", title: "服务内容", blocks: [{ kind: "defs", items: [
        ["平台", "统一账户与登录、工作空间、组织与角色、通知收件箱与浏览器推送，以及面向自动化集成的接口平台。"],
        ["Synwevo Docs", "文档自动化：选择来源数据、接入模板、检查后批量生成。支持 DOCX、XLSX、PDF、PPTX、CSV 等格式，各格式的能力以应用内选项为准。"],
        ["Synwevo Message", "组织内消息：私聊与群组、引用回复、@提及、表情回应、附件与语音、文档链接卡片、群公告、举报处理。独立打开，也可以从工作空间顶栏的「消息」进入。"],
        ["Synwevo Agent", "你自己的 AI 助手：在你有权使用的工作空间里查询与办理，写入前先经你确认。按实际用量从人民币钱包扣费，见「定价」；使用规则见「AI 使用规范」。是否在本部署开放，见上方「当前状态」。"],
        ["没有的部分", "没有其它应用，也没有流程编排器；这里写的就是全部。"],
      ] }] },
      { id: "service-roles", title: "角色", blocks: [{ kind: "defs", items: [
        ["平台管理员", "治理账户、注册策略与平台配置；读不到业务文档与对话。接口平台的管理控制台只对获授权的平台管理员开放，组织或工作空间的管理员不因此获得。"],
        ["组织", "所有者、管理员、成员，并可按能力委派自定义角色。组织成员身份不自动授予每个工作空间的访问权。"],
        ["工作空间", "所有者、管理员、编辑者、查看者。文档权限按工作空间授予，切换不会合并内容。"],
        ["服务凭据", "属于某个工作空间，须经该空间批准，只能在获准的资源与权限范围内调用；管理权不授予内容访问权。"],
      ] }] },
      { id: "service-signup", title: "注册与登录", blocks: [{ kind: "list", items: [
        "注册策略见上方「当前状态」。开放时，任何邮箱验证后即可使用，学校与公司等机构邮箱同样可用；验证链接 24 小时内有效。",
        "登录方式：口令、通行密钥、邮箱验证码；外部账户登录是否可用见「当前状态」。可以开启两步验证并保存恢复码。",
        "会话闲置 12 小时失效；选择「保持登录」后为 14 天；无论如何最长 30 天。敏感操作需要重新输入口令。",
        "验证、邀请与安全通知由「当前状态」中的发件人发出。「已发送」指邮件服务器已接收；能否进入收件箱取决于对方的邮件系统。",
      ] }] },
      { id: "service-limits", title: "限额", blocks: [
        { kind: "p", text: "这些数字来自代码本身，超过时会得到明确的提示与可重试的时间。" },
        { kind: "table", head: ["项目", "限额"], rows: [
          ["消息", "每条最多 4000 字；每人每分钟 60 条。"],
          ["附件", "每条消息 1 个，最大 20 MB；每人每小时 30 个。"],
          ["群组", "每群最多 200 人；每人每天最多创建 20 个群；群公告最多 1000 字。"],
          ["举报", "每人每天 20 条。"],
          ["邀请", "每个工作空间 24 小时内最多 50 个；同一邮箱 60 秒内不重复发送；默认 7 天有效，可设 1–30 天。"],
          ["注册", "同一来源 15 分钟内 10 次；同一邮箱每小时 3 次。"],
          ["成员导入", "单个文件最大 20 MB。"],
          ["文档", "各格式支持的字段、导出与转换以应用内选项为准。"],
        ] },
      ] },
      { id: "service-support", title: "遇到问题", blocks: [{ kind: "defs", items: [
        ["登录与账户", "账户页的「登录的设备」与「安全活动」可以自查；忘记口令用登录页的找回。"],
        ["组织与工作空间", "邀请、角色、应用启用由组织管理员或工作空间管理员处理，请直接联系他们。"],
        ["应用内的问题", "任务页显示真实的状态与错误原因；不当的消息用消息里的「举报」交给组织处理。"],
      ] }] },
      { id: "service-operations", title: "运行与维护", blocks: [{ kind: "list", items: [
        "服务运行在位于日本东京的单台服务器上；发布新版本时可能有短暂中断。",
        "每次发布前生成完整备份并保留在服务器上；发布出现问题时以该备份回滚。",
      ] }] },
      { id: "service-termination", title: "终止与删除", blocks: [{ kind: "list", items: [
        "你可以随时在账户页申请删除账户：7 天缓冲期内可撤回；到期后凭据与资料删除，你在组织里的贡献保留。若你是组织的唯一所有者或最后一位平台管理员，需要先移交。",
        "组织所有者可以冻结、删除组织，删除后 7 天内可恢复；被冻结的组织及其工作空间暂不可用。被移出组织即刻失去该组织的会话与工作空间访问。",
        "平台管理员可以停用账户；停用即刻结束该账户的全部登录。",
      ] }] },
    ],
  };
}

function termsContent(): Content {
  return {
    eyebrow: "服务条款",
    heading: "使用 Synwevo 的约定。",
    lead: "账户、钱包与充值、按量计费、免费额度、组织付费，以及 AI 生成内容的性质。",
    summary: [
      "充值一经完成不可退款。",
      "钱包余额只能用于 Synwevo 的服务，不能转让、提现、兑换现金，也不计利息。",
      "Synwevo Agent 按实际用量扣费；每次运行前显示价格并由你确认最高预算，价格变化后需要重新确认。",
      "AI 生成的内容可能有误，仅供参考；涉及重要决定时请自行核实。",
      "首次付费使用 Synwevo Agent 时，记录你接受的条款、AI 使用规范与定价版本。",
    ],
    sections: [
      { id: "terms-account", title: "账户", blocks: [{ kind: "list", items: [
        "一个人一个账户；请保管好登录方式，账户下的操作视为你本人所为。",
        "组织与工作空间的管理者决定其成员能使用哪些应用与内容。",
      ] }] },
      { id: "terms-wallet", title: "钱包与充值", blocks: [{ kind: "defs", items: [
        ["币种", "钱包以人民币计价，金额精确到分。"],
        ["充值", "充值经支付服务商完成，到账以支付服务商的确认为准。充值入口未开放时，页面会如实说明。"],
        ["不可退款", "充值一经完成不可退款，未用完的余额也不退还；除非适用法律强制要求。"],
        ["只用于本服务", "余额只能用于 Synwevo 的服务，不能转让给他人、提现、兑换现金，也不计利息。"],
        ["拒付与争议", "如果一笔充值被拒付或发起争议，钱包会被冻结，相关金额记入账目；余额不会因此变为负数。冻结期间不能发起新的付费调用。"],
      ] }] },
      { id: "terms-charges", title: "计费", blocks: [{ kind: "list", items: [
        "Synwevo Agent 按每次模型调用的实际用量扣费，单价见「定价」。",
        "每次运行前，你会看到当前单价、价格版本与本次的最高预算，确认后才会扣费；实际扣费不超过你确认的上限。",
        "价格或模型变化后，之前的确认不再有效，需要重新确认；不会按你未确认的价格扣费。",
        "被截断、被内容安全过滤、被模型提供方中断或被你停止的调用是否计费，按管理员公布的计费策略执行；每条回答的费用记录会写明。",
      ] }] },
      { id: "terms-allowance", title: "免费额度", blocks: [{ kind: "list", items: [
        "在本部署开放时，每个账户在验证邮箱后可获得一次 1 元人民币的免费额度，同一邮箱只能领取一次。",
        "免费额度只能用于 Synwevo 的服务，不能提现或转让。",
      ] }] },
      { id: "terms-organization", title: "组织付费", blocks: [{ kind: "list", items: [
        "组织可以为成员付费：组织所有者或获授权的管理员为组织钱包充值，并可设置成员的额度。",
        "成员在对话中选择由个人还是所属组织付费；组织看到按成员汇总的用量与金额，看不到成员的对话内容。",
        "组织钱包的充值同样不可退款。",
      ] }] },
      { id: "terms-ai", title: "AI 生成的内容", blocks: [{ kind: "list", items: [
        "Synwevo Agent 的回答由 AI 模型生成，可能不准确、不完整或过时，仅供参考，不构成专业意见。",
        "Agent 改动数据或联系他人之前会先请你确认；确认后的操作由你负责。",
        "使用 Agent 须遵守「AI 使用规范」。",
      ] }] },
      { id: "terms-end", title: "暂停、删除与条款变更", blocks: [{ kind: "list", items: [
        "违反「AI 使用规范」时，平台管理员可以暂停你的 Agent 使用权限，并说明理由。",
        "删除账户时，钱包中未用完的余额作废，不退还；计费与付款记录不再关联你的资料，按法定期限保留。",
        "条款更新时会发布新版本并注明生效日期；新版本生效后的下一次付费使用会记录你接受的新版本。",
      ] }] },
    ],
  };
}

function aiUseContent(): Content {
  return {
    eyebrow: "AI 使用规范",
    heading: "负责任地使用 AI。",
    lead: "使用 Synwevo Agent 时不允许的内容与行为，内容安全如何执行，以及 AI 生成内容的标识。",
    summary: [
      "不得用 Agent 生成或传播违法、暴力、涉及未成年人的色情、仇恨、诈骗、侵犯隐私或恶意软件等内容。",
      "消息发送前、回答生成时都会检查；命中规则的消息不会发送，命中的回答会停止并不再显示。",
      "不得转售或分享 Agent 的使用权，不得尝试绕过内容安全检查。",
      "每条回答与导出内容都注明由 AI 生成，可能有误，仅供参考。",
    ],
    sections: [
      { id: "ai-use-forbidden", title: "不允许的内容", blocks: [{ kind: "list", items: [
        "违反法律法规的内容，或协助违法活动。",
        "宣扬暴力、恐怖主义或极端主义的内容。",
        "任何涉及未成年人的色情内容；其它色情内容。",
        "鼓励自残或自杀的内容。",
        "基于民族、宗教、性别等的仇恨或歧视内容。",
        "诈骗、钓鱼、冒充他人，或伪造文件与记录。",
        "未经同意收集、公开他人的个人信息。",
        "编写或改进恶意软件、攻击他人系统。",
        "侵犯他人知识产权的内容。",
      ] }] },
      { id: "ai-use-behaviour", title: "不允许的行为", blocks: [{ kind: "list", items: [
        "转售、出租或分享 Agent 的使用权；本服务不提供可直接调用模型的接口。",
        "尝试绕过、试探或关闭内容安全检查，或诱导模型泄露其它用户的内容。",
        "用自动化手段大量发送消息。",
      ] }] },
      { id: "ai-use-enforcement", title: "如何执行", blocks: [{ kind: "defs", items: [
        ["规则检查", "平台管理员维护内容安全规则（关键词与模式，带版本与审计记录）。你的消息在发送前检查，命中即不发送、不扣费；回答在生成时检查，命中即停止，已显示的部分也会撤下。"],
        ["模型提供方的过滤", "模型提供方也会过滤内容；被过滤的回答同样停止并不再显示。"],
        ["只记录必要的信息", "被拦下时只记录命中的规则、方向与长度，不记录消息或回答的内容。"],
        ["举报", "每条回答都可以举报。只有在你选择附上时，回答原文才会交给审核人员。"],
        ["暂停", "多次或严重违规时，平台管理员可以暂停你的 Agent 使用权限并说明理由；暂停期间不能发送消息，也不会发起模型调用。"],
      ] }] },
      { id: "ai-use-label", title: "AI 生成内容的标识", blocks: [{ kind: "list", items: [
        "Agent 的每条回答与导出的对话都注明「由 AI 生成，可能有误，仅供参考」。",
        "把 AI 生成的内容用于对外发布时，请保留或自行加注相应标识。",
      ] }] },
    ],
  };
}

function pricingContent(): Content {
  return {
    eyebrow: "定价",
    heading: "用多少，付多少。",
    lead: "Synwevo Agent 如何计费、怎样充值，以及免费额度与组织付费。",
    summary: [
      "目前只有 Synwevo Agent 按用量收费，以人民币计价；其它应用不按用量收费。",
      "按每次模型调用的实际用量（输入与输出的 token 数）扣费；运行前显示单价与价格版本，并由你确认最高预算。",
      "充值一经完成不可退款；余额只能用于 Synwevo 的服务。",
      "在本部署开放时，每个账户在验证邮箱后可获得一次 1 元人民币的免费额度。",
    ],
    sections: [
      { id: "pricing-how", title: "怎样计价", blocks: [{ kind: "defs", items: [
        ["单价", "按模型分别定价：输入（命中缓存与未命中缓存分开）和输出各有单价。售价由模型提供方的成本价乘以管理员设定的倍率得出，按版本发布；部分模型在高峰与非高峰时段价格不同。"],
        ["在哪里看", "本页不列出具体单价：单价由管理员发布并带版本号，可能调整。每次运行前，Agent 会显示当前单价、价格版本与本次的最高预算。"],
        ["扣费", "按实际用量结算，不超过你确认的上限；每条回答都写明扣费金额与用量。"],
        ["价格变化", "不会静默改价。价格或模型变化后，之前的确认失效，需要你重新确认。"],
        ["未完成的调用", "被截断、被内容安全过滤、被中断或被你停止的调用是否计费，按管理员公布的计费策略执行；默认由平台承担。"],
      ] }] },
      { id: "pricing-wallet", title: "钱包与充值", blocks: [{ kind: "list", items: [
        "钱包以人民币计价。充值金额与方式以充值页面显示为准；充值入口未开放时，页面会如实说明。",
        "充值一经完成不可退款，未用完的余额也不退还；余额不能转让、提现或兑换现金。",
        "你可以设置每日、每周与每月的个人支出上限，并查看每次调用的用量与扣费。",
      ] }] },
      { id: "pricing-allowance", title: "免费额度", blocks: [{ kind: "list", items: [
        "在本部署开放时，每个账户在验证邮箱后可获得一次 1 元人民币的免费额度，同一邮箱只能领取一次。",
        "免费额度只能用于 Synwevo 的服务，不能提现或转让。",
      ] }] },
      { id: "pricing-organization", title: "组织付费", blocks: [{ kind: "list", items: [
        "组织所有者或获授权的管理员可以为组织钱包充值，并设置成员的额度。",
        "成员在对话中选择付款方；组织看到按成员汇总的用量与金额，看不到对话内容。",
      ] }] },
    ],
  };
}
