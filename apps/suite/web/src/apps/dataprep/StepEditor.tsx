import { useI18n } from "@synwevo/ui";
import { Field } from "./common";
import { typeNames, type Step } from "./model";

export function StepEditor({ step: s, columns, sources, sourceColumns, change }: {
  step: Step; columns: string[]; sources: string[]; sourceColumns: Record<string, string[]>; change: (s: Step) => void;
}) {
  const { t } = useI18n();
  const patch = (data: Record<string, any>) => change({ ...s, ...data });
  const text = (key: string, label: string) => <Field label={t(label)}><input className="input" maxLength={120} value={s[key] ?? ""} onChange={e => patch({ [key]: e.target.value })} /></Field>;
  const select = (key: string, label: string, choices: Record<string, string>) => <Field label={t(label)}><select className="input" value={s[key] ?? ""} onChange={e => patch({ [key]: e.target.value })}>{Object.entries(choices).map(([v, label]) => <option key={v} value={v}>{t(label)}</option>)}</select></Field>;
  const column = (key: string, label: string, available = columns) => <Field label={t(label)}><select className="input" value={s[key] ?? ""} onChange={e => patch({ [key]: e.target.value })}><option value="">{t("请选择列")}</option>{available.map(c => <option key={c}>{c}</option>)}</select></Field>;
  const multi = (key: string, label: string, available = columns) => <Field label={t(label)}><select multiple className="input dp-multiselect" value={s[key] ?? []} onChange={e => patch({ [key]: [...e.target.selectedOptions].map(o => o.value) })}>{available.map(c => <option key={c}>{c}</option>)}</select><small className="muted">{t("可选择多列；顺序按列列表")}</small></Field>;
  const check = (key: string, label: string) => <label className="dp-check"><input type="checkbox" checked={!!s[key]} onChange={e => patch({ [key]: e.target.checked })} />{t(label)}</label>;
  const source = <Field label={t("追加或关联来源")}><input className="input" value={s.source ?? ""} pattern="[a-z][a-z0-9_]{0,31}" list={`sources-${s.id}`} onChange={e => patch({ source: e.target.value })} /><datalist id={`sources-${s.id}`}>{sources.map(k => <option key={k}>{k}</option>)}</datalist></Field>;
  const condition = s.condition && <>
    <Field label={t("列")}><select className="input" value={s.condition.column} onChange={e => patch({ condition: { ...s.condition, column: e.target.value } })}><option value="">{t("请选择列")}</option>{columns.map(c => <option key={c}>{c}</option>)}</select></Field>
    <Field label={t("条件")}><select className="input" value={s.condition.op} onChange={e => patch({ condition: { ...s.condition, op: e.target.value } })}>
      {Object.entries({ eq: "等于", ne: "不等于", gt: "大于", ge: "大于或等于", lt: "小于", le: "小于或等于", contains: "包含", is_null: "是空值", is_missing: "是缺失", is_empty: "是空字符串" }).map(([v, label]) => <option key={v} value={v}>{t(label)}</option>)}
    </select></Field>
    {!s.condition.op.startsWith("is_") && <Field label={t("比较值（$名称引用参数）")}><input className="input" maxLength={4096} value={s.condition.value ?? ""} onChange={e => patch({ condition: { ...s.condition, value: e.target.value } })} /></Field>}
    <label className="dp-check"><input type="checkbox" checked={s.condition.numeric} onChange={e => patch({ condition: { ...s.condition, numeric: e.target.checked } })} />{t("按精确数值比较")}</label>
  </>;
  return <div className="dp-fields">
    {["trim", "merge", "sort", "dedupe"].includes(s.type) && multi("columns", "选择列")}
    {["cast", "split"].includes(s.type) && column("column", "列")}
    {s.type === "cast" && <>{select("to", "目标类型", typeNames)}{["integer", "decimal"].includes(s.to) && <>
      {s.to === "decimal" && <Field label={t("小数位数")}><input className="input" type="number" min={0} max={18} value={s.scale} onChange={e => patch({ scale: Number(e.target.value) })} /></Field>}
      {select("rounding", "精度处理", { reject: "拒绝精度损失", half_even: "银行家舍入" })}</>}
      {s.to === "datetime" && <Field label={t("无时区时间的固定偏移")}><input className="input" placeholder="+08:00" value={s.offset ?? ""} onChange={e => patch({ offset: e.target.value || null })} /><small>{t("留空则拒绝没有时区的日期时间")}</small></Field>}</>}
    {s.type === "split" && <>{text("separator", "分隔文本")}<Field label={t("输出列（每行一个）")}><textarea className="input" value={s.into.join("\n")} onChange={e => patch({ into: e.target.value.split("\n") })} /></Field></>}
    {s.type === "merge" && <>{text("target", "输出列名")}{text("separator", "分隔文本")}{select("nulls", "空值处理", { propagate: "有空值则结果为空", empty: "空值按空字符串处理" })}</>}
    {s.type === "sort" && <>{check("descending", "降序")}{check("numeric", "按精确数值排序")}</>}
    {s.type === "dedupe" && select("keep", "保留规则", { first: "保留第一行", last: "保留最后一行" })}
    {["filter", "validate"].includes(s.type) && condition}
    {s.type === "validate" && <>{text("message", "异常说明")}{select("on_error", "异常行处理", { keep: "保留并标记异常", drop: "排除异常行" })}</>}
    {s.type === "append" && <>{source}{select("alignment", "列对齐", { strict: "要求列完全一致", union: "合并全部列，缺少的保留缺失" })}</>}
    {s.type === "join" && <>{source}{multi("left", "本表关联键")}{multi("right", "来源关联键", sourceColumns[s.source] || [])}
      {select("how", "关联方式", { left: "保留本表全部行", inner: "仅保留匹配行" })}{select("matches", "重复匹配", { one: "要求唯一匹配", first: "取第一个匹配", all: "全部匹配（多对多）" })}
      {text("prefix", "来源列前缀")}{check("nulls_equal", "允许同类空值与缺失键匹配")}</>}
    {s.type === "aggregate" && <>{multi("group_by", "分组列（可留空）")}<div className="dp-measures"><h4>{t("汇总指标")}</h4>{s.measures.map((m: any, i: number) => <div className="dp-fields" key={i}>
      <Field label={t("汇总方式")}><select className="input" value={m.op} onChange={e => patch({ measures: s.measures.map((x: any, j: number) => j === i ? { ...x, op: e.target.value, column: e.target.value === "count" ? null : x.column || columns[0] || "" } : x) })}>
        {Object.entries({ count: "行数", sum: "合计", min: "最小值", max: "最大值" }).map(([v, l]) => <option value={v} key={v}>{t(l)}</option>)}</select></Field>
      {m.op !== "count" && <Field label={t("数值列")}><select className="input" value={m.column || ""} onChange={e => patch({ measures: s.measures.map((x: any, j: number) => j === i ? { ...x, column: e.target.value } : x) })}><option value="">{t("请选择列")}</option>{columns.map(c => <option key={c}>{c}</option>)}</select></Field>}
      <Field label={t("输出列名")}><input className="input" value={m.target} onChange={e => patch({ measures: s.measures.map((x: any, j: number) => j === i ? { ...x, target: e.target.value } : x) })} /></Field>
      <button type="button" className="btn sm" disabled={s.measures.length < 2} onClick={() => patch({ measures: s.measures.filter((_: any, j: number) => i !== j) })}>{t("删除指标")}</button>
    </div>)}<button type="button" className="btn sm" disabled={s.measures.length >= 16} onClick={() => patch({ measures: [...s.measures, { op: "count", column: null, target: `count_${s.measures.length + 1}` }] })}>{t("添加指标")}</button></div></>}
  </div>;
}
