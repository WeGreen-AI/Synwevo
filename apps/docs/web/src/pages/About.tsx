import { serverText, issueText } from "../lib/presentation";
import { errorText } from "../lib/perm";
import { t as tr, useI18n, LanguageMenu } from "@synwevo/ui";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "@synwevo/auth-client";

interface About {
  license: string;
  sdk_license: string;
  license_notice: string;
  pdf_engine: "pymupdf" | "pdfium";
  agpl_component_active: boolean;
  source_offer: boolean;
  notice: string;
  third_party: { name: string; license: string; role: string }[];
}

export default function AboutPage() {
  useI18n();
  const [a, setA] = useState<About | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get<About>("/api/about").then(setA).catch((e) => setErr(errorText(e))); }, []);
  return (
    <div className="page-narrow">
      <div className="row mb12"><Link className="btn ghost sm" to="/">{tr("← 首页")}</Link><LanguageMenu /><h1 className="grow" style={{ margin: 0 }}>{tr("关于与许可")}</h1></div>
      {err && <div className="notice err">{serverText(err)}</div>}
      {a && (
        <div className="col gap12">
          {/* AGPL-3.0 §13: the program's own licence and the offer of its Corresponding Source,
              first on the page that is about licences. The archive is built from the running files. */}
          <div className="card">
            <div className="card-h"><b>{tr("本程序的许可与源代码")}</b><span className="spacer" /><span className="tiny muted">{a.license}</span></div>
            <div className="card-b col">
              <p className="small" style={{ margin: 0 }}>{serverText(a.license_notice)}</p>
              <p className="small muted" style={{ margin: 0 }}>{serverText(a.notice)}</p>
              {a.source_offer
                ? <div className="row wrap"><a className="btn primary sm" href="/api/source-code" download>{tr("下载正在运行版本的源代码")}</a><span className="tiny muted">{tr("由本服务正在运行的文件生成（tar.gz），无需登录即可下载。")}</span></div>
                : <div className="notice small">{tr("本部署已关闭源代码下载：运营方以其他方式提供对应源代码。")}</div>}
            </div>
          </div>
          <div className="card">
            <div className="card-h"><b>{tr("第三方组件")}</b><span className="spacer" /><span className="tiny muted">{tr("均为开源组件")}</span></div>
            <table className="tbl">
              <thead><tr><th>{tr("组件")}</th><th>{tr("许可证")}</th><th>{tr("用途")}</th></tr></thead>
              <tbody>{a.third_party.map((t) => <tr key={t.name}><td>{serverText(t.name)}</td><td>{serverText(t.license)}</td><td className="muted">{serverText(t.role)}</td></tr>)}</tbody>
            </table>
          </div>
          <div className="notice small">{tr("Synwevo Docs 的解析、转换、文字识别与生成都在本服务的服务器上完成。只有使用个人 Agent 时，对话与你选择发送的文件内容会发送给所配置的模型提供方。")}</div>
        </div>
      )}
    </div>
  );
}
