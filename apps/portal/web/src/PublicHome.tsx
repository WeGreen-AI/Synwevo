import { useI18n } from "@synwevo/ui";
import { DocumentIcon, Shell, signInUrl as signInBase } from "./Portal";

export function PublicHome({ signedIn, registerUrl: registerBase }: { signedIn: boolean; registerUrl: string }) {
  const { t, locale } = useI18n();
  const registerUrl = `${registerBase}${registerBase.includes("?") ? "&" : "?"}lang=${locale}`;
  const signInUrl = `${signInBase}&lang=${locale}`;
  const steps = [
    ["选择来源数据", "上传来源文件，选择需要使用的文字、单元格或表格区域。"],
    ["连接你的模板", "选择模板字段，建立来源与目标之间可复用的对应关系。"],
    ["检查，再批量生成", "明确每行、每文件或每组的样本规则，检查问题后生成并下载结果。"],
  ];
  return <Shell publicHome right={<><a className="pt-nav-detail" href="#workflow">{t("工作流程")}</a><a className="pt-nav-detail" href="#privacy">{t("数据与隐私")}</a><a className="pt-link" href={signedIn ? "/workspace" : signInUrl}>{t(signedIn ? "进入工作空间" : "登录")}</a><a className="pt-btn pt-btn-primary pt-nav-register" href={registerUrl}>{t("注册账户")}</a></>}>
    <section className="pt-hero" aria-labelledby="pt-hero-title"><div className="pt-hero-copy"><p className="pt-eyebrow"><span className="pt-live-dot" aria-hidden="true"/>Synwevo Docs</p>
      <h1 id="pt-hero-title">{t("你的数据。你的模板。")}<span>{t("下一份文档，从这里开始。")}</span></h1>
      <p className="pt-hero-lead">{t("把重复的文档工作交给清晰的流程。从选择来源、连接模板，到检查和批量生成，专注于内容本身。")}</p>
      <div className="pt-actions"><a className="pt-btn pt-btn-primary pt-btn-large" href={signedIn ? "/workspace" : registerUrl}>{t(signedIn ? "进入工作空间" : "创建你的账户")} <span aria-hidden="true">↗</span></a><a className="pt-text-action" href="#workflow">{t("了解工作流程")} <span aria-hidden="true">↓</span></a></div>
      <p className="pt-hero-note">{t("一个账户，连接你有权使用的工作空间和应用。")}</p>
    </div><div className="pt-flow-visual" role="img" aria-label={t("工作流程示意：来源数据经过模板映射与检查，生成可下载的文档。")}>
      <span className="pt-visual-label">{t("工作流程示意")}</span>
      <div className="pt-source-sheet"><span className="pt-sheet-type">XLSX · CSV · DOCX</span><h3>{t("来源数据")}</h3><div className="pt-sheet-grid" aria-hidden="true">{Array.from({length: 15}, (_, i) => <i key={i} className={i === 7 || i === 10 ? "is-mapped" : ""}/>)}</div><span className="pt-sheet-caption">{t("选择需要的内容")}</span></div>
      <div className="pt-visual-connector" aria-hidden="true"><span/>→<span/></div>
      <div className="pt-template-sheet"><span className="pt-sheet-type">Synwevo Docs</span><DocumentIcon/><h3>{t("你的模板")}</h3><div className="pt-template-lines" aria-hidden="true"><i/><i/><b/><i/><b/><i/></div><span className="pt-sheet-caption">{t("映射 · 检查 · 生成")}</span></div>
      <div className="pt-output-label"><span aria-hidden="true">↳</span>{t("可下载的文档")}</div>
    </div></section>
    <section className="pt-format-strip" aria-label={t("文档格式")}><span>{t("围绕你已有的文件工作")}</span><span>DOCX</span><span>XLSX</span><span>PDF</span><span>PPTX</span><span>CSV</span></section>
    <section id="workflow" className="pt-public-section"><div className="pt-section-intro"><p className="pt-eyebrow">{t("从来源到交付")}</p><h2>{t("三个步骤，让重复工作有章可循。")}</h2><p>{t("使用 Synwevo Docs 的真实文档流程。不同格式支持的字段与导出方式，以应用内可用选项为准。")}</p></div>
      <ol className="pt-workflow">{steps.map(([title, detail], index) => <li key={title}><span className="pt-step-number">0{index + 1}</span><h3>{t(title)}</h3><p>{t(detail)}</p></li>)}</ol>
    </section>
    <section className="pt-principles pt-public-section" id="privacy"><div className="pt-section-intro"><p className="pt-eyebrow">{t("数据与隐私")}</p><h2>{t("清楚知道，你正在什么空间工作。")}</h2></div><div className="pt-principle-grid"><article><span aria-hidden="true">01 /</span><h3>{t("身份统一，空间独立")}</h3><p>{t("登录与账户安全由 Synwevo Account 统一处理。空间成员资格和应用授权决定你可以进入哪里。")}</p></article><article><span aria-hidden="true">02 /</span><h3>{t("门户只展示入口")}</h3><p>{t("这里不汇总业务文档、项目标题或文件内容。打开应用后，仍须通过该应用的访问权限检查。")}</p></article><article><span aria-hidden="true">03 /</span><h3>{t("管理不等于读取")}</h3><p>{t("平台管理权限不授予业务文档访问权。")}</p></article></div></section>
    <section className="pt-service pt-public-section" id="service"><div><p className="pt-eyebrow">{t("服务说明")}</p><h2>{t("从你已有的内容开始。")}</h2><p>{t("Synwevo Docs 支持来源选择、模板映射、生成前检查和批量文档输出。可用应用由空间授权决定；部分格式转换依赖部署环境中的转换组件。")}</p><p className="pt-hint">{t("文档保存在当前部署中。数据保留、备份和服务可用性由该部署的管理者配置；此页不作额外承诺。")}</p></div><div className="pt-service-cta"><a className="pt-btn pt-btn-primary pt-btn-large" href={signedIn ? "/workspace" : registerUrl}>{t(signedIn ? "进入工作空间" : "注册账户")} <span aria-hidden="true">↗</span></a><a className="pt-link" href={signInUrl}>{t("已有账户？登录")}</a></div></section>
  </Shell>;
}
