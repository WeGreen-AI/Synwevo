import { useI18n } from "@synwevo/ui";
import { DocumentIcon, Shell, signInUrl as signInBase } from "./Portal";

export function PublicHome({ signedIn, registerUrl: registerBase }: { signedIn: boolean; registerUrl: string }) {
  const { t, locale } = useI18n();
  const registerUrl = `${registerBase}${registerBase.includes("?") ? "&" : "?"}lang=${locale}`;
  const signInUrl = `${signInBase}&lang=${locale}`;
  const steps = [
    ["从同一个空间开始", "个人工作与团队协作各有归属。通过统一账户进入工作空间，找到你有权使用的应用。"],
    ["把重复步骤交给应用", "为具体任务选择应用，配置输入与处理规则，让可复用的流程减少重复操作。"],
    ["让结果进入下一步", "查看真实任务状态，检查并交付结果。经授权的自动化集成可调用应用能力，衔接后续处理。"],
  ];
  const stages = [
    ["输入与规则", "数据 · 文件 · 处理条件", "01"],
    ["应用处理", "在授权的工作空间中执行", "02"],
    ["任务与结果", "跟踪状态 · 检查 · 交付", "03"],
  ];
  return <Shell publicHome right={<>
    <a className="pt-nav-detail" href="#workflow">{t("工作流程")}</a>
    <a className="pt-nav-detail" href="#applications">{t("平台应用")}</a>
    <a className="pt-link" href={signedIn ? "/workspace" : signInUrl}>{t(signedIn ? "进入工作空间" : "登录")}</a>
    {!signedIn && <a className="pt-btn pt-btn-primary pt-nav-register" href={registerUrl}>{t("注册账户")}</a>}
  </>}>
    <section className="pt-hero" aria-labelledby="pt-hero-title">
      <div className="pt-hero-copy">
        <p className="pt-eyebrow"><span className="pt-live-dot" aria-hidden="true"/>{t("工作流程自动化平台")}</p>
        <h1 id="pt-hero-title">{t("把重复的工作，")}<span>{t("交给流程。")}</span></h1>
        <p className="pt-hero-lead">{t("连接团队、应用与数据，让重复工作成为清晰、可追踪的自动化流程。")}</p>
        <div className="pt-actions">
          <a className="pt-btn pt-btn-primary pt-btn-large" href={signedIn ? "/workspace" : registerUrl}>{t(signedIn ? "进入工作空间" : "创建你的账户")} <span aria-hidden="true">↗</span></a>
          <a className="pt-text-action" href="#workflow">{t("了解工作流程")} <span aria-hidden="true">↓</span></a>
        </div>
        <p className="pt-hero-note">{t("一个账户，连接你有权使用的工作空间和应用。")}</p>
      </div>
      <div className="pt-automation-visual" aria-label={t("工作流程示意")}>
        <div className="pt-automation-header"><span>Synwevo</span><span>{t("工作流程示意")}</span></div>
        <ol className="pt-automation-stages">{stages.map(([title, detail, number]) => <li key={number}>
          <span className="pt-automation-number" aria-hidden="true">{number}</span>
          <div><h3>{t(title)}</h3><p>{t(detail)}</p></div>
          <span className="pt-automation-node" aria-hidden="true"/>
        </li>)}</ol>
        <p className="pt-automation-caption">{t("统一身份 · 空间隔离 · 明确授权")}</p>
      </div>
    </section>
    <section className="pt-format-strip" aria-label={t("平台能力")}>
      <span>{t("让每一步，都有清晰的归属")}</span>
      {["统一账户", "团队空间", "应用授权", "可追踪任务", "受控集成"].map(label => <span key={label}>{t(label)}</span>)}
    </section>
    <section id="workflow" className="pt-public-section">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("从协作到自动化")}</p><h2>{t("把人、应用和工作连接起来。")}</h2><p>{t("Synwevo 提供统一的工作入口与授权基础，应用承载具体业务流程，让日常工作可以重复执行、检查和交付。")}</p></div>
      <ol className="pt-workflow">{steps.map(([title, detail], index) => <li key={title}><span className="pt-step-number">0{index + 1}</span><h3>{t(title)}</h3><p>{t(detail)}</p></li>)}</ol>
    </section>
    <section id="applications" className="pt-public-section pt-public-apps">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("平台应用")}</p><h2>{t("一个平台，应用各司其职。")}</h2><p>{t("在共同的账户与工作空间中，使用面向具体业务的独立应用。Synwevo Docs 是其中的文档自动化应用。")}</p></div>
      <article className="pt-featured-app">
        <div className="pt-featured-heading"><span className="pt-doc-icon"><DocumentIcon/></span><div><p className="pt-eyebrow">{t("文档自动化")}</p><h3>Synwevo Docs</h3></div></div>
        <p>{t("选择来源数据，连接模板，完成检查与批量生成。把重复的文档制作交给可复用的规则。")}</p>
        <div className="pt-app-formats" aria-label={t("文档格式")}>{["DOCX", "XLSX", "PDF", "PPTX", "CSV"].map(format => <span key={format}>{format}</span>)}</div>
        <div className="pt-featured-bottom"><p className="pt-hint">{t("不同格式支持的能力以应用内选项为准。可用应用由工作空间授权决定。")}</p><a className="pt-btn" href={signedIn ? "/workspace" : signInUrl}>{t("进入空间，打开应用")} <span aria-hidden="true">↗</span></a></div>
      </article>
    </section>
    <section className="pt-principles pt-public-section" id="privacy">
      <div className="pt-section-intro"><p className="pt-eyebrow">{t("数据与隐私")}</p><h2>{t("协作有边界，自动化有依据。")}</h2></div>
      <div className="pt-principle-grid">
        <article><span aria-hidden="true">01 /</span><h3>{t("身份统一，空间独立")}</h3><p>{t("登录与账户安全由 Synwevo Account 统一处理。空间成员资格和应用授权决定你可以进入哪里。")}</p></article>
        <article><span aria-hidden="true">02 /</span><h3>{t("自动化遵循授权")}</h3><p>{t("自动化调用使用受限服务身份，按获准的工作空间和操作范围执行。每次访问仍须通过权限检查。")}</p></article>
        <article><span aria-hidden="true">03 /</span><h3>{t("管理不等于读取")}</h3><p>{t("平台管理权限不授予业务文档访问权。")}</p></article>
      </div>
    </section>
    <section className="pt-service pt-public-section" id="service">
      <div><p className="pt-eyebrow">{t("服务说明")}</p><h2>{t("从一个工作空间开始。")}</h2><p>{t("创建账户，选择个人使用或加入团队，再打开适合当前任务的应用。你的账户、空间和应用权限在平台中统一管理。")}</p><p className="pt-hint">{t("业务数据保存在当前部署中。数据保留、备份与可用能力由实际部署配置决定。")}</p></div>
      <div className="pt-service-cta"><a className="pt-btn pt-btn-primary pt-btn-large" href={signedIn ? "/workspace" : registerUrl}>{t(signedIn ? "进入工作空间" : "注册账户")} <span aria-hidden="true">↗</span></a>{!signedIn && <a className="pt-link" href={signInUrl}>{t("已有账户？登录")}</a>}</div>
    </section>
  </Shell>;
}
