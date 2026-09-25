import { serverText, issueText } from "../lib/presentation";
import { errorText } from "../lib/perm";
import { t as tr, useI18n, LanguageMenu } from "@synwevo/ui";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "@synwevo/auth-client";

interface About {
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
          <div className="card">
            <div className="card-h"><b>{tr("第三方组件")}</b><span className="spacer" /><span className="tiny muted">{tr("版本与费用见 docs/DEPENDENCIES.md；均为免费开源组件，未使用付费组件或外部云服务")}</span></div>
            <table className="tbl">
              <thead><tr><th>{tr("组件")}</th><th>{tr("许可证")}</th><th>{tr("用途")}</th></tr></thead>
              <tbody>{a.third_party.map((t) => <tr key={t.name}><td>{serverText(t.name)}</td><td>{serverText(t.license)}</td><td className="muted">{serverText(t.role)}</td></tr>)}</tbody>
            </table>
          </div>
          <div className="notice small">{tr("所有文件都在本服务所在的服务器上处理，不发送到外部服务；文字识别、格式转换与分页计算都由本机引擎完成。")}</div>
        </div>
      )}
    </div>
  );
}
