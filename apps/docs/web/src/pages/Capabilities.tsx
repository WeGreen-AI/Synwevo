import { serverText, issueText } from "../lib/presentation";
import { errorText } from "../lib/perm";
import { t as tr, useI18n, LanguageMenu, BrandMark } from "@synwevo/ui";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "@synwevo/auth-client";

const COLS: [string, string][] = [
  ["source", "来源导入与提取"], ["preview", "预览与可选择对象"], ["template", "作为模板 / 创建字段"], ["fill", "可填充类型"],
  ["native_export", "原生导出"], ["pdf_export", "PDF 导出"],
];

export default function Capabilities() {
  useI18n();
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get("/api/capabilities").then(setD).catch((e) => setErr(errorText(e))); }, []);
  return (
    <div className="app">
      <div className="topbar">
        <LanguageMenu />
        <Link to="/" className="brand"><BrandMark product="Docs" /></Link>
        <span className="sep" /><b>{tr("格式能力矩阵")}</b>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home" style={{ maxWidth: 1400 }}>
          {err && <div className="notice err">{serverText(err)}</div>}
          {d && (
            <>
              <div className="notice info mb8">{tr("当前部署：排版引擎 ")}<b>{d.environment.libreoffice ? tr("已启用") : tr("未启用")}</b>{tr("；本地文字识别 ")}<b>{d.environment.tesseract ? tr("已启用") : tr("未启用")}</b>{tr("（语言：")}{d.environment.ocr_languages.join(", ") || tr("无")}{tr("）。未经验证的格式不标为支持。")}</div>
              <div className="row wrap gap4 mb8">{Object.entries(d.legend).map(([k, v]) => <span key={k} className={`badge ${cls(k)}`}>{lab(k)}：{serverText(v as string)}</span>)}</div>
              <div className="card" style={{ overflow: "auto" }}>
                <table className="tbl small" style={{ minWidth: 1700, tableLayout: "fixed" }}>
                  <colgroup><col style={{ width: 150 }} />{COLS.map(([k]) => <col key={k} style={{ width: 190 }} />)}<col style={{ width: 230 }} /><col style={{ width: 130 }} /><col style={{ width: 130 }} /></colgroup>
                  <thead><tr><th>{tr("格式")}</th>{COLS.map(([k, v]) => <th key={k}>{tr(v)}</th>)}<th>{tr("格式保留 / 损失")}</th><th>{tr("处理位置")}</th><th>{tr("批量 / 重试 / 校验")}</th></tr></thead>
                  <tbody>
                    {d.rows.map((r: any) => (
                      <tr key={r.format}>
                        <td><b>{serverText(r.format)}</b></td>
                        {COLS.map(([k]) => (
                          <td key={k}><span className={`badge ${cls(r[k][0])}`}>{lab(r[k][0])}</span><div className="tiny" style={{ marginTop: 3 }}>{serverText(r[k][1])}</div></td>
                        ))}
                        <td className="tiny">{serverText(r.fidelity)}</td>
                        <td className="tiny">{serverText(r.processing)}</td>
                        <td className="tiny">{serverText(r.batch)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
const cls = (k: string) => ({ verified: "ok", conversion: "info", partial: "warn", unavailable: "err", no: "" } as any)[k] || "";
const lab = (k: string) => ({ verified: tr("已验证"), conversion: tr("转换"), partial: tr("部分"), unavailable: tr("缺依赖"), no: tr("不支持") } as any)[k] || k;
