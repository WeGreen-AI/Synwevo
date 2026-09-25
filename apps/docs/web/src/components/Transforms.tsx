import { formatNumber } from "@synwevo/ui";
import { t as tr, useI18n } from "@synwevo/ui";
const OPS: Record<string, string> = {
  trim: "去除首尾空格",
  collapse_spaces: "合并连续空白",
  date_format: "日期显示格式",
  decimals: "数字小数位",
  thousands: "千位分隔符",
  prefix: "添加前缀",
  suffix: "添加后缀",
  default: "为空时使用默认值",
  map: "值对照替换",
  pad_left: "左侧补齐位数",
  upper: "转大写",
  lower: "转小写",
};
import { useEffect, useState } from "react";
import { formatValueMap, parseValueMap, sameTable } from "../lib/valuemap";

const DATES = ["yyyy-mm-dd", "yyyy/m/d", "yyyy/mm/dd", "yyyy年m月d日", "yyyy年mm月dd日", "yyyy年m月", "mm/dd/yyyy", "dd.mm.yyyy", "yyyymmdd", "yyyy-mm-dd hh:mm", "yyyy-mm-dd hh:mm:ss", "hh:mm"];

export default function Transforms({ value, onChange }: { value: any[]; onChange: (v: any[]) => void }) {
  useI18n();
  const ops = value || [];
  const set = (i: number, patch: any) => onChange(ops.map((o, k) => (k === i ? { ...o, ...patch } : o)));
  return (
    <div className="col gap4">
      {ops.map((o, i) => (
        <div key={i} className="row wrap" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", padding: "4px 6px", borderRadius: 6 }}>
          <span className="small" style={{ minWidth: 96 }}>{formatNumber(i + 1)}. {tr(OPS[o.op] || o.op)}</span>
          {o.op === "date_format" && (
            <select className="input sm" value={o.pattern} onChange={(e) => set(i, { pattern: e.target.value })}>
              {DATES.map((d) => <option key={d}>{d}</option>)}
            </select>
          )}
          {o.op === "decimals" && (
            <>
              <input className="input sm" type="number" min={0} max={10} style={{ width: 60 }} value={o.n ?? 2} onChange={(e) => set(i, { n: Number(e.target.value) })} />{tr(" 位")}<label className="check small"><input type="checkbox" checked={!!o.thousands} onChange={(e) => set(i, { thousands: e.target.checked })} />{tr("千位分隔")}</label>
            </>
          )}
          {(o.op === "prefix" || o.op === "suffix" || o.op === "default") && (
            <input className="input sm" style={{ width: 140 }} value={o.text ?? ""} placeholder={tr("文字")} onChange={(e) => set(i, { text: e.target.value })} />
          )}
          {o.op === "pad_left" && (
            <>{tr("宽度 ")}<input className="input sm" type="number" min={1} max={40} style={{ width: 56 }} value={o.width ?? 6} onChange={(e) => set(i, { width: Number(e.target.value) })} />{tr("补 ")}<input className="input sm" style={{ width: 36 }} maxLength={1} value={o.char ?? "0"} onChange={(e) => set(i, { char: e.target.value })} />
            </>
          )}
          {o.op === "map" && <ValueMap table={o.table} onChange={(table) => set(i, { table })} />}
          <span className="spacer" />
          <button type="button" className="btn xs ghost" disabled={i === 0} onClick={() => { const n = [...ops]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; onChange(n); }} title={tr("上移")} aria-label={tr("上移第 {v1} 条转换规则", { v1: i + 1 })}>↑</button>
          <button type="button" className="btn xs ghost" onClick={() => onChange(ops.filter((_, k) => k !== i))} title={tr("移除")} aria-label={tr("移除第 {v1} 条转换规则", { v1: i + 1 })}>✕</button>
        </div>
      ))}
      <select className="input sm" value="" aria-label={tr("添加转换规则")} onChange={(e) => {
        const op = e.target.value;
        if (!op) return;
        const init: any = { op };
        if (op === "date_format") init.pattern = "yyyy年m月d日";
        if (op === "decimals") init.n = 2;
        if (op === "pad_left") { init.width = 6; init.char = "0"; }
        if (op === "map") init.table = { TRUE: "是", FALSE: "否" };
        onChange([...ops, init]);
      }} style={{ width: 200 }}>
        <option value="">{tr("＋ 添加转换规则…")}</option>
        {Object.entries(OPS).map(([k, v]) => <option key={k} value={k}>{tr(v)}</option>)}
      </select>
      <div className="tiny muted">{tr("转换只使用以上受控选项，按顺序执行；不会执行任何自定义代码。")}</div>
    </div>
  );
}

/** The 值对照替换 editor keeps the text exactly as typed and hands the parsed table up.
 *  It used to redraw the textarea from the parsed table on every keystroke, which deleted any
 *  line that was not `原值=新值` yet — a new line could not be typed at all. */
function ValueMap({ table, onChange }: { table: Record<string, string> | undefined; onChange: (t: Record<string, string>) => void }) {
  useI18n();
  const [text, setText] = useState(() => formatValueMap(table));
  // Follow changes made elsewhere (undo, another row reordering) but never overwrite what is
  // being typed when the parent simply echoes our own table back.
  useEffect(() => {
    if (!sameTable(parseValueMap(text).table, table)) setText(formatValueMap(table));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(table || {})]);
  const { bad } = parseValueMap(text);
  return (
    <div className="col gap4">
      <textarea className="input" rows={3} style={{ width: 220, fontSize: 12 }} placeholder={tr("每行一条：原值=新值\n例如 TRUE=是")} aria-label={tr("值对照表，每行一条：原值=新值")}
        value={text} onChange={(e) => { setText(e.target.value); onChange(parseValueMap(e.target.value).table); }} />
      {bad.length > 0 && <div className="tiny" style={{ color: "var(--warn)" }}>{tr("第 {lines} 行还不是「原值=新值」，暂不生效", { lines: bad.join(", ") })}</div>}
    </div>
  );
}
