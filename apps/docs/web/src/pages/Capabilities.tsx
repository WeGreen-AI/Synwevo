import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get } from "@synwevo/auth-client";

const COLS: [string, string][] = [
  ["source", "来源导入与提取"], ["preview", "预览与可选择对象"], ["template", "作为模板 / 创建字段"], ["fill", "可填充类型"],
  ["native_export", "原生导出"], ["pdf_export", "PDF 导出"],
];

export default function Capabilities() {
  const [d, setD] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { get("/api/capabilities").then(setD).catch((e) => setErr(e.message)); }, []);
  return (
    <div className="app">
      <div className="topbar">
        <Link to="/" className="brand"><span className="logo">S</span>Synwevo Docs</Link>
        <span className="sep" /><b>格式能力矩阵</b>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home" style={{ maxWidth: 1400 }}>
          {err && <div className="notice err">{err}</div>}
          {d && (
            <>
              <div className="notice info mb8">
                本表由当前服务端实时生成：已安装 LibreOffice：<b>{d.environment.libreoffice || "否"}</b>；Tesseract：<b>{d.environment.tesseract || "否"}</b>（语言：{d.environment.ocr_languages.join(", ") || "无"}）。
                未经验证的格式不会标为支持。
              </div>
              <div className="row wrap gap4 mb8">{Object.entries(d.legend).map(([k, v]) => <span key={k} className={`badge ${cls(k)}`}>{lab(k)}：{v as string}</span>)}</div>
              <div className="card" style={{ overflow: "auto" }}>
                <table className="tbl small" style={{ minWidth: 1700, tableLayout: "fixed" }}>
                  <colgroup><col style={{ width: 150 }} />{COLS.map(([k]) => <col key={k} style={{ width: 190 }} />)}<col style={{ width: 230 }} /><col style={{ width: 130 }} /><col style={{ width: 130 }} /></colgroup>
                  <thead><tr><th>格式</th>{COLS.map(([k, v]) => <th key={k}>{v}</th>)}<th>格式保留 / 损失</th><th>处理位置</th><th>批量 / 重试 / 校验</th></tr></thead>
                  <tbody>
                    {d.rows.map((r: any) => (
                      <tr key={r.format}>
                        <td><b>{r.format}</b></td>
                        {COLS.map(([k]) => (
                          <td key={k}><span className={`badge ${cls(r[k][0])}`}>{lab(r[k][0])}</span><div className="tiny" style={{ marginTop: 3 }}>{r[k][1]}</div></td>
                        ))}
                        <td className="tiny">{r.fidelity}</td>
                        <td className="tiny">{r.processing}</td>
                        <td className="tiny">{r.batch}</td>
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
const lab = (k: string) => ({ verified: "已验证", conversion: "转换", partial: "部分", unavailable: "缺依赖", no: "不支持" } as any)[k] || k;
