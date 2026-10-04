/** The settings of one field: what it asks, what counts as a valid answer, and when it shows. */
import { useI18n } from "@synwevo/ui";
import { blankSubField, type Condition, type Definition, type FieldDef, type FieldType } from "./logic";
import { typeLabel } from "./text";

const ACCEPT = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".heic", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".zip"];
const CURRENCIES = ["CNY", "HKD", "USD", "EUR", "GBP", "JPY", "SGD", "TWD", "MOP", "AUD", "CAD"];

export function FieldProps({ field: f, defn, readOnly, onChange, onStep }: {
  field: FieldDef; defn: Definition; readOnly: boolean; onChange: (patch: Partial<FieldDef>) => void;
  onStep: (step: number) => void;
}) {
  const { t } = useI18n();
  const before = defn.fields.slice(0, defn.fields.findIndex((x) => x.id === f.id)).filter((x) => x.type !== "section" && x.type !== "repeat" && x.type !== "file");
  const steps = defn.steps ?? [];
  const inStep = steps.findIndex((s) => s.fields.includes(f.id));
  return <div className="fm-props-body">
    <h2 className="fm-pane-title">{typeLabel(t, f.type)}</h2>
    <p className="muted small">{t("字段编号 {id}（固定不变，映射与接口按它读取答案）", { id: f.id })}</p>
    <Text label={f.type === "section" ? t("标题") : t("问题")} value={f.label} max={200} disabled={readOnly} onChange={(v) => onChange({ label: v })} />
    <Text label={f.type === "section" ? t("说明文字") : t("填写说明")} value={f.help ?? ""} max={2000} multiline disabled={readOnly} onChange={(v) => onChange({ help: v })} />
    {f.type !== "section" && <>
      {["text", "textarea", "number", "money"].includes(f.type) &&
        <Text label={t("占位提示")} value={f.placeholder ?? ""} max={200} disabled={readOnly} onChange={(v) => onChange({ placeholder: v })} />}
      <Check label={f.type === "boolean" ? t("必须勾选（例如同意条款）") : t("必填")} checked={!!f.required} disabled={readOnly}
             onChange={(v) => onChange({ required: v })} />
      <TypeSettings f={f} readOnly={readOnly} onChange={onChange} />
      <ConditionEditor label={t("仅在满足条件时显示")} value={f.visible_if ?? null} candidates={before} readOnly={readOnly}
                       onChange={(c) => onChange({ visible_if: c })} />
      {!f.required && <ConditionEditor label={t("满足条件时必填")} value={f.required_if ?? null} candidates={before} readOnly={readOnly}
                                       onChange={(c) => onChange({ required_if: c })} />}
      <p className="muted small">{t("隐藏的字段在提交时会被清空，不会进入提交结果。")}</p>
    </>}
    {steps.length > 0 && <label className="fm-prop">
      <span className="fm-label small">{t("所在步骤")}</span>
      <select className="input sm" value={inStep} disabled={readOnly} onChange={(e) => onStep(Number(e.target.value))}>
        {steps.map((s, i) => <option key={s.id} value={i}>{s.title}</option>)}
      </select>
    </label>}
  </div>;
}

function TypeSettings({ f, readOnly, onChange }: { f: FieldDef; readOnly: boolean; onChange: (p: Partial<FieldDef>) => void }) {
  const { t } = useI18n();
  switch (f.type) {
    case "text":
      return <>
        <label className="fm-prop"><span className="fm-label small">{t("格式")}</span>
          <select className="input sm" value={f.format ?? "plain"} disabled={readOnly} onChange={(e) => onChange({ format: e.target.value as any })}>
            <option value="plain">{t("不限")}</option><option value="email">{t("邮箱")}</option><option value="phone">{t("电话")}</option>
            <option value="url">{t("网址")}</option><option value="pattern">{t("自定义格式")}</option>
          </select></label>
        {f.format === "pattern" && <>
          <Text label={t("格式规则（正则表达式）")} value={f.pattern ?? ""} max={100} disabled={readOnly} mono onChange={(v) => onChange({ pattern: v || null })} />
          <Text label={t("格式说明（填错时显示）")} value={f.pattern_hint ?? ""} max={200} disabled={readOnly} onChange={(v) => onChange({ pattern_hint: v })} />
        </>}
        <Lengths f={f} readOnly={readOnly} onChange={onChange} />
      </>;
    case "textarea":
      return <Lengths f={f} readOnly={readOnly} onChange={onChange} />;
    case "number": case "money":
      return <>
        {f.type === "money" && <label className="fm-prop"><span className="fm-label small">{t("币种")}</span>
          <select className="input sm" value={f.currency ?? "CNY"} disabled={readOnly} onChange={(e) => onChange({ currency: e.target.value })}>
            {CURRENCIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>}
        <div className="fm-prop-row">
          <Text label={t("最小值")} value={f.min ?? ""} max={40} disabled={readOnly} onChange={(v) => onChange({ min: v.trim() || null })} />
          <Text label={t("最大值")} value={f.max ?? ""} max={40} disabled={readOnly} onChange={(v) => onChange({ max: v.trim() || null })} />
        </div>
        <label className="fm-prop"><span className="fm-label small">{t("小数位数")}</span>
          <select className="input sm" value={f.decimals ?? (f.type === "money" ? 2 : 6)} disabled={readOnly}
                  onChange={(e) => onChange({ decimals: Number(e.target.value) })}>
            {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <p className="muted small">{t("数字按原样保存为精确的小数，不做浮点换算；多于允许位数的数字会被拒绝，不会被四舍五入。")}</p>
      </>;
    case "select": case "multiselect":
      return <>
        <Options f={f} readOnly={readOnly} onChange={onChange} />
        {f.type === "multiselect" && <div className="fm-prop-row">
          <Num label={t("至少选")} value={f.min_items} disabled={readOnly} onChange={(n) => onChange({ min_items: n })} />
          <Num label={t("最多选")} value={f.max_items} disabled={readOnly} onChange={(n) => onChange({ max_items: n })} />
        </div>}
      </>;
    case "file":
      return <>
        <fieldset className="fm-prop"><legend className="fm-label small">{t("接受的文件类型")}</legend>
          <div className="fm-accept">{ACCEPT.map((a) => <label key={a} className="fm-choice small">
            <input type="checkbox" checked={(f.accept ?? []).includes(a)} disabled={readOnly}
                   onChange={(e) => onChange({ accept: e.target.checked ? [...(f.accept ?? []), a] : (f.accept ?? []).filter((x) => x !== a) })} />
            <span>{a}</span></label>)}</div></fieldset>
        <div className="fm-prop-row">
          <Num label={t("最多文件数")} value={f.max_files ?? 1} min={1} max={10} disabled={readOnly} onChange={(n) => onChange({ max_files: n ?? 1 })} />
          <Num label={t("单个文件上限（MB）")} value={f.max_size_mb ?? 10} min={1} max={20} disabled={readOnly} onChange={(n) => onChange({ max_size_mb: n ?? 10 })} />
        </div>
        <p className="muted small">{t("上传的文件经过平台的安全检查；设有密码、无法检查的文件不会被接纳。")}</p>
      </>;
    case "repeat":
      return <RepeatFields f={f} readOnly={readOnly} onChange={onChange} />;
    default:
      return null;
  }
}

function Lengths({ f, readOnly, onChange }: { f: FieldDef; readOnly: boolean; onChange: (p: Partial<FieldDef>) => void }) {
  const { t } = useI18n();
  return <div className="fm-prop-row">
    <Num label={t("最少字数")} value={f.min_length} disabled={readOnly} onChange={(n) => onChange({ min_length: n })} />
    <Num label={t("最多字数")} value={f.max_length} disabled={readOnly} onChange={(n) => onChange({ max_length: n })} />
  </div>;
}

function Options({ f, readOnly, onChange }: { f: FieldDef; readOnly: boolean; onChange: (p: Partial<FieldDef>) => void }) {
  const { t } = useI18n();
  const opts = f.options ?? [];
  return <fieldset className="fm-prop"><legend className="fm-label small">{t("选项")}</legend>
    {opts.map((o, i) => <div key={i} className="fm-option">
      <input className="input sm grow" value={o.label} disabled={readOnly} aria-label={t("选项 {n} 的文字", { n: i + 1 })} maxLength={200}
             onChange={(e) => onChange({ options: opts.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })} />
      <input className="input sm fm-opt-value" value={o.value} disabled={readOnly} aria-label={t("选项 {n} 的值", { n: i + 1 })} maxLength={100}
             onChange={(e) => onChange({ options: opts.map((x, j) => j === i ? { ...x, value: e.target.value } : x) })} />
      {!readOnly && <button type="button" className="btn ghost sm" aria-label={t("删除选项 {n}", { n: i + 1 })}
                            onClick={() => onChange({ options: opts.filter((_, j) => j !== i) })}>×</button>}
    </div>)}
    {!readOnly && <button type="button" className="btn sm" onClick={() => {
      let n = opts.length + 1;
      while (opts.some((o) => o.value === `option_${n}`)) n++;
      onChange({ options: [...opts, { value: `option_${n}`, label: t("选项 {n}", { n }) }] });
    }}>{t("添加选项")}</button>}
    <p className="muted small">{t("“值”是保存和对接时使用的内容；修改文字不影响已有的答案。")}</p>
  </fieldset>;
}

function RepeatFields({ f, readOnly, onChange }: { f: FieldDef; readOnly: boolean; onChange: (p: Partial<FieldDef>) => void }) {
  const { t } = useI18n();
  const subs = f.fields ?? [];
  const TYPES: FieldType[] = ["text", "number", "money", "date", "select", "boolean"];
  return <fieldset className="fm-prop"><legend className="fm-label small">{t("每一项包含的字段")}</legend>
    {subs.map((s, i) => <div key={s.id} className="fm-sub">
      <input className="input sm grow" value={s.label} disabled={readOnly} maxLength={200} aria-label={t("字段 {n} 的问题", { n: i + 1 })}
             placeholder={t("问题")} onChange={(e) => onChange({ fields: subs.map((x, j) => j === i ? { ...x, label: e.target.value } : x) })} />
      <select className="input sm" value={s.type} disabled={readOnly} aria-label={t("字段 {n} 的类型", { n: i + 1 })}
              onChange={(e) => {
                const type = e.target.value as FieldType;
                const next: FieldDef = { id: s.id, type, label: s.label, required: s.required };
                if (type === "select") next.options = [{ value: "a", label: "A" }];
                if (type === "money") next.currency = "CNY";
                onChange({ fields: subs.map((x, j) => j === i ? next : x) });
              }}>
        {TYPES.map((ty) => <option key={ty} value={ty}>{typeLabel(t, ty)}</option>)}
      </select>
      <label className="fm-choice small"><input type="checkbox" checked={!!s.required} disabled={readOnly}
               onChange={(e) => onChange({ fields: subs.map((x, j) => j === i ? { ...x, required: e.target.checked } : x) })} /><span>{t("必填")}</span></label>
      {!readOnly && <button type="button" className="btn ghost sm" aria-label={t("删除字段 {n}", { n: i + 1 })}
                            onClick={() => onChange({ fields: subs.filter((_, j) => j !== i) })}>×</button>}
    </div>)}
    {!readOnly && <button type="button" className="btn sm" onClick={() => onChange({ fields: [...subs, blankSubField(t("明细项 {n}", { n: subs.length + 1 }))] })}>{t("添加字段")}</button>}
    <div className="fm-prop-row">
      <Num label={t("至少几项")} value={f.min_items} disabled={readOnly} onChange={(n) => onChange({ min_items: n })} />
      <Num label={t("最多几项")} value={f.max_items} disabled={readOnly} onChange={(n) => onChange({ max_items: n })} />
    </div>
  </fieldset>;
}

type Leaf = { field: string; op: string; value?: any };

function leaves(c: Condition | null): { mode: "all" | "any"; items: Leaf[] } {
  if (!c) return { mode: "all", items: [] };
  if ("all" in c) return { mode: "all", items: c.all.filter((x): x is any => "field" in x) };
  if ("any" in c) return { mode: "any", items: c.any.filter((x): x is any => "field" in x) };
  if ("field" in c) return { mode: "all", items: [c] };
  return { mode: "all", items: [] };
}

function build(mode: "all" | "any", items: Leaf[]): Condition | null {
  if (!items.length) return null;
  if (items.length === 1) return items[0] as Condition;
  return (mode === "all" ? { all: items } : { any: items }) as Condition;
}

/** A condition on earlier answers: one or more "field — comparison — value" lines, all or any. */
function ConditionEditor({ label, value, candidates, readOnly, onChange }: {
  label: string; value: Condition | null; candidates: FieldDef[]; readOnly: boolean; onChange: (c: Condition | null) => void;
}) {
  const { t } = useI18n();
  const { mode, items } = leaves(value);
  const set = (next: Leaf[], m = mode) => onChange(build(m, next));
  if (!candidates.length) return <div className="fm-prop"><span className="fm-label small">{label}</span>
    <p className="muted small">{t("条件只能引用排在前面的字段；这个字段前面还没有可用的字段。")}</p></div>;
  return <fieldset className="fm-prop fm-cond"><legend className="fm-label small">{label}</legend>
    {items.length > 1 && <select className="input sm" value={mode} disabled={readOnly} aria-label={t("条件组合方式")}
                                 onChange={(e) => set(items, e.target.value as any)}>
      <option value="all">{t("同时满足以下全部")}</option><option value="any">{t("满足以下任一")}</option></select>}
    {items.map((it, i) => {
      const src = candidates.find((c) => c.id === it.field) ?? candidates[0];
      const needsValue = !["filled", "empty"].includes(it.op);
      return <div key={i} className="fm-cond-line">
        <select className="input sm" value={it.field} disabled={readOnly} aria-label={t("条件字段")}
                onChange={(e) => set(items.map((x, j) => j === i ? { field: e.target.value, op: "eq", value: undefined } : x))}>
          {candidates.map((c) => <option key={c.id} value={c.id}>{c.label || c.id}</option>)}
        </select>
        <select className="input sm" value={it.op} disabled={readOnly} aria-label={t("条件")}
                onChange={(e) => set(items.map((x, j) => j === i ? { ...x, op: e.target.value } : x))}>
          <option value="eq">{t("等于")}</option><option value="neq">{t("不等于")}</option>
          {src.type === "multiselect" && <option value="contains">{t("包含")}</option>}
          {["number", "money", "date", "datetime"].includes(src.type) && <>
            <option value="gt">&gt;</option><option value="gte">≥</option><option value="lt">&lt;</option><option value="lte">≤</option></>}
          <option value="filled">{t("已填写")}</option><option value="empty">{t("未填写")}</option>
        </select>
        {needsValue && (src.type === "select" || src.type === "multiselect"
          ? <select className="input sm" value={it.value ?? ""} disabled={readOnly} aria-label={t("条件值")}
                    onChange={(e) => set(items.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}>
              <option value="">{t("请选择…")}</option>
              {(src.options ?? []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>
          : src.type === "boolean"
          ? <select className="input sm" value={String(it.value ?? true)} disabled={readOnly} aria-label={t("条件值")}
                    onChange={(e) => set(items.map((x, j) => j === i ? { ...x, value: e.target.value === "true" } : x))}>
              <option value="true">{t("已勾选")}</option><option value="false">{t("未勾选")}</option></select>
          : <input className="input sm" value={it.value ?? ""} disabled={readOnly} aria-label={t("条件值")} maxLength={200}
                   onChange={(e) => set(items.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />)}
        {!readOnly && <button type="button" className="btn ghost sm" aria-label={t("删除条件")} onClick={() => set(items.filter((_, j) => j !== i))}>×</button>}
      </div>;
    })}
    {!readOnly && <button type="button" className="btn ghost sm" onClick={() => set([...items, {
      field: candidates[candidates.length - 1].id, op: "eq",
      value: candidates[candidates.length - 1].type === "boolean" ? true : undefined }])}>{items.length ? t("再加一个条件") : t("添加条件")}</button>}
  </fieldset>;
}

function Text({ label, value, onChange, max, multiline, disabled, mono }: {
  label: string; value: string; onChange: (v: string) => void; max: number; multiline?: boolean; disabled?: boolean; mono?: boolean;
}) {
  return <label className="fm-prop"><span className="fm-label small">{label}</span>
    {multiline ? <textarea className="input" rows={2} value={value} maxLength={max} disabled={disabled} onChange={(e) => onChange(e.target.value)} />
               : <input className={`input sm${mono ? " mono" : ""}`} value={value} maxLength={max} disabled={disabled} onChange={(e) => onChange(e.target.value)} />}
  </label>;
}

function Num({ label, value, onChange, min = 0, max = 10000, disabled }: {
  label: string; value: number | null | undefined; onChange: (n: number | null) => void; min?: number; max?: number; disabled?: boolean;
}) {
  return <label className="fm-prop"><span className="fm-label small">{label}</span>
    <input className="input sm" type="number" min={min} max={max} value={value ?? ""} disabled={disabled}
           onChange={(e) => onChange(e.target.value === "" ? null : Math.max(min, Math.min(max, Number(e.target.value))))} /></label>;
}

function Check({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <label className="fm-choice"><input type="checkbox" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} /><span>{label}</span></label>;
}
