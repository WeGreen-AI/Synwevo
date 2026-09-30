import { useState } from "react";
import { useI18n } from "@synwevo/ui";
import { useMutation, useRead, typeKeys, roleKeys, TableFailure } from "./common";
import type { Field, Table, Workspace } from "./types";

export function FieldsEditor({ws, table, reload}: {ws: Workspace; table: Table; reload: () => void}) {
  const {t} = useI18n();
  const [selected, setSelected] = useState<string>("new");
  const field = table.fields.find(f => f.id === selected);
  const mutation = useMutation(ws.id);
  const [deletion, setDeletion] = useState<any>();
  async function previewDelete(field: Field, deleted: boolean) {
    try {
      const args = {table_id: table.id, schema_rev: table.schema_rev, field_id: field.id, deleted};
      const result = await mutation.run("tables.field.state", {...args, preview: true});
      setDeletion({...result, args, name: field.name});
    } catch {}
  }
  return <div className="tables-schema">
    <aside><h2>{t("字段列表")}</h2><p className="muted">{t("字段编号保持不变，改名不会破坏映射。")}</p>
      <ul>{table.fields.map(f => <li key={f.id}><button className={`tables-field-nav${selected === f.id ? " on" : ""}`} onClick={() => {setSelected(f.id); setDeletion(undefined);}}><strong>{f.name}</strong><span>{t(typeKeys[f.type])}</span></button></li>)}</ul>
      {table.can_manage && table.status === "active" && <button className="btn" onClick={() => {setSelected("new"); setDeletion(undefined);}}>{t("添加字段")}</button>}
      {!!table.deleted_fields.length && <details><summary>{t("已删除字段")}</summary>{table.deleted_fields.map(f => <div className="tables-toolbar" key={f.id}><span>{f.name}</span><button className="btn sm" disabled={mutation.busy || table.status !== "active"} onClick={() => void previewDelete(f, false)}>{t("预览恢复")}</button></div>)}</details>}
    </aside>
    <div>
      {selected !== "new" && !field ? null : <FieldForm key={`${selected}:${table.schema_rev}`} ws={ws} table={table} field={field} reload={reload} />}
      {field && table.can_manage && table.status === "active" && <button className="btn" disabled={mutation.busy} onClick={() => void previewDelete(field, true)}>{t("预览删除")}</button>}
      {deletion && <section className="tables-preview" aria-live="polite"><h3>{t("变更影响预览")}</h3><p>{deletion.name}</p><p>{t("涉及 {n} 条记录；已有值会保留。", {n: deletion.records})}</p><p>{t("依赖：{formulas} 个公式，{views} 个视图", {formulas: deletion.dependencies.formulas.length, views: deletion.dependencies.views.length})}</p>
        <button className="btn primary" disabled={mutation.busy || (deletion.args.deleted && (deletion.dependencies.formulas.length > 0 || deletion.dependencies.views.length > 0))} onClick={() => void mutation.run("tables.field.state", {...deletion.args, preview_token: deletion.preview_token}).then(reload).catch(() => {})}>{t("确认应用变更")}</button><button className="btn" onClick={() => setDeletion(undefined)}>{t("取消")}</button>
      </section>}
      <TableFailure error={mutation.error} retry={reload} />
    </div>
  </div>;
}

function FieldForm({ws, table, field, reload}: {ws: Workspace; table: Table; field?: Field; reload: () => void}) {
  const {t} = useI18n();
  const [name, setName] = useState(field?.name ?? "");
  const [description, setDescription] = useState(field?.description ?? "");
  const [type, setType] = useState(field?.type ?? "text");
  const [required, setRequired] = useState(field?.required ?? false);
  const [unique, setUnique] = useState(field?.is_unique ?? false);
  const [readRole, setReadRole] = useState(field?.read_role ?? "viewer");
  const [writeRole, setWriteRole] = useState(field?.write_role ?? "editor");
  const [decimals, setDecimals] = useState(field?.config.decimals ?? 2);
  const [minimum, setMinimum] = useState(field?.config.min ?? "");
  const [maximum, setMaximum] = useState(field?.config.max ?? "");
  const [maxLength, setMaxLength] = useState(field?.config.max_length ?? "");
  const [currency, setCurrency] = useState(field?.config.currency ?? "CNY");
  const [choices, setChoices] = useState<string>((field?.config.choices ?? []).map((c: any) => c.label).join("\n"));
  const [targetSearch, setTargetSearch] = useState("");
  const [target, setTarget] = useState(field?.config.table_id ?? "");
  const [multiple, setMultiple] = useState(field?.config.multiple ?? false);
  const [expression, setExpression] = useState(field?.config.expression ?? "");
  const [defaultValue, setDefault] = useState(field?.default == null ? "" : typeof field.default === "object" ? String((field.default as any).amount ?? "") : String(field.default));
  const [preview, setPreview] = useState<any>();
  const mutation = useMutation(ws.id);
  const tables = useRead<{tables: Table[]; total: number}>("tables.table.list", {limit: 200, q: targetSearch}, ws.id, 0, type === "link");
  const computed = ["formula", "created_time", "updated_time", "created_by", "updated_by"].includes(type);
  const uniqueAllowed = ["text", "email", "url", "phone", "number", "date", "datetime", "single_select"].includes(type);
  const defaultAllowed = ["text", "long_text", "number", "money", "date", "boolean", "email", "url", "phone"].includes(type);
  function definition() {
    const config: Record<string, unknown> = {};
    if (["number", "money", "formula"].includes(type)) config.decimals = decimals;
    if (type === "money") config.currency = currency;
    if (["number", "money", "date"].includes(type)) {if (minimum !== "") config.min = minimum; if (maximum !== "") config.max = maximum;}
    if (["text", "long_text"].includes(type) && maxLength !== "") config.max_length = Number(maxLength);
    if (type === "formula") config.expression = expression;
    if (type === "link") {config.table_id = target; config.multiple = multiple;}
    if (["single_select", "multi_select"].includes(type)) config.choices = choices.split("\n").filter(s => s.trim()).map((label, n) => ({label: label.trim(), ...(field?.config.choices?.[n] ? {id: field.config.choices[n].id} : {})}));
    return {name, description, type, config, required: !computed && required, is_unique: uniqueAllowed && unique,
      default: defaultAllowed && defaultValue !== "" ? type === "boolean" ? defaultValue === "true" : defaultValue : null,
      read_role: readRole, write_role: writeRole};
  }
  return <form className="tables-form" onSubmit={e => {e.preventDefault(); const args = {table_id: table.id, schema_rev: table.schema_rev, field_id: field?.id ?? null, field: definition()}; void mutation.run("tables.field.save", {...args, preview: true}).then(result => setPreview({...result, args})).catch(() => {});}} onChange={() => setPreview(undefined)}>
    <fieldset disabled={!table.can_manage || table.status !== "active" || mutation.busy}><legend>{t(field ? "字段设置" : "添加字段")}</legend>
      {field && <p className="muted small">{t("映射编号")} <code>{field.id}</code></p>}
      <label>{t("名称")}<input autoFocus className="input" required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>{t("说明")}<textarea className="input" maxLength={1000} value={description} onChange={e => setDescription(e.target.value)} /></label>
      <label>{t("字段类型")}<select className="input" value={type} onChange={e => setType(e.target.value)}>{Object.entries(typeKeys).map(([key, label]) => <option key={key} value={key}>{t(label)}</option>)}</select></label>
      {["number", "money", "formula"].includes(type) && <label>{t("小数位数")}<input className="input" type="number" min={0} max={type === "money" ? 4 : 10} value={decimals} onChange={e => setDecimals(Number(e.target.value))} /></label>}
      {["number", "money", "date"].includes(type) && <div className="tables-filter"><label>{t("最小值（可选）")}<input className="input" type={type === "date" ? "date" : "text"} value={minimum} onChange={e => setMinimum(e.target.value)} /></label><label>{t("最大值（可选）")}<input className="input" type={type === "date" ? "date" : "text"} value={maximum} onChange={e => setMaximum(e.target.value)} /></label></div>}
      {["text", "long_text"].includes(type) && <label>{t("最长字符数（可选）")}<input className="input" type="number" min={1} max={type === "text" ? 1000 : 20000} value={maxLength} onChange={e => setMaxLength(e.target.value)} /></label>}
      {type === "money" && <label>{t("币种")}<input className="input" required pattern="[A-Za-z]{3}" maxLength={3} value={currency} onChange={e => setCurrency(e.target.value.toUpperCase())} /></label>}
      {["single_select", "multi_select"].includes(type) && <label>{t("选项（每行一个）")}<textarea className="input" value={choices} onChange={e => setChoices(e.target.value)} /><small>{t("同一行改名保留选项编号；移除选项会在预览中列出影响。")}</small></label>}
      {type === "link" && <><label>{t("搜索关联数据表")}<input className="input" type="search" value={targetSearch} onChange={e => setTargetSearch(e.target.value)} /></label>{tables.data && tables.data.total > 200 && <p className="muted">{t("仅显示前 200 个结果，请缩小搜索范围。")}</p>}<label>{t("关联数据表")}<select className="input" required value={target} onChange={e => setTarget(e.target.value)}><option value="">{t("请选择")}</option>{tables.data?.tables.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label><TableFailure error={tables.error} /><label className="tables-check"><input type="checkbox" checked={multiple} onChange={e => setMultiple(e.target.checked)} />{t("允许多条关联")}</label></>}
      {type === "formula" && <><label>{t("计算公式")}<textarea className="input tables-formula" required maxLength={2000} value={expression} onChange={e => setExpression(e.target.value)} /></label><p className="muted">{t("用花括号引用字段名称或编号。支持算术、比较与 IF、ROUND、CONCAT、AND、OR、NOT、ABS、ISBLANK。")}</p><div className="tables-checks">{table.fields.filter(f => f.id !== field?.id).map(f => <button className="btn sm" type="button" key={f.id} onClick={() => {setExpression(expression+`{${f.id}}`); setPreview(undefined);}}>{f.name}</button>)}</div></>}
      {!computed && <label className="tables-check"><input type="checkbox" checked={required} onChange={e => setRequired(e.target.checked)} />{t("必填")}</label>}
      {uniqueAllowed && <label className="tables-check"><input type="checkbox" checked={unique} onChange={e => setUnique(e.target.checked)} />{t("唯一值（忽略大小写与首尾空格）")}</label>}
      {defaultAllowed && <label>{t("默认值（可选）")}{type === "boolean" ? <select className="input" value={defaultValue} onChange={e => setDefault(e.target.value)}><option value="">{t("留空")}</option><option value="true">{t("是")}</option><option value="false">{t("否")}</option></select> : <input className="input" value={defaultValue} onChange={e => setDefault(e.target.value)} />}</label>}
      <label>{t("可查看")}<select className="input" value={readRole} onChange={e => setReadRole(e.target.value)}>{Object.keys(roleKeys).map(role => <option key={role} value={role}>{t(roleKeys[role])}</option>)}</select></label>
      <label>{t("可编辑")}<select className="input" value={writeRole} onChange={e => setWriteRole(e.target.value)}>{["editor", "admin"].map(role => <option key={role} value={role}>{t(roleKeys[role])}</option>)}</select></label>
      <button className="btn primary" disabled={!name.trim()}>{mutation.busy ? t("检查中…") : t("预览变更")}</button>
    </fieldset>
    <TableFailure error={mutation.error} retry={reload} />
    {preview && <section className="tables-preview" aria-live="polite"><h3>{t("变更影响预览")}</h3><p>{t("扫描 {n} 条记录", {n: preview.records})}</p><dl className="tables-counts">{Object.entries({unchanged: "保持原值", ok: "无损转换", lossy: "转换有损", failed: "无法转换"}).map(([key, label]) => <div key={key}><dt>{t(label)}</dt><dd>{preview.counts[key]}</dd></div>)}</dl>
      <p>{t("转换前的原值保留在变更记录中；后续编辑仍会检查当前约束。")}</p>
      {!!preview.issue_count && <p role="alert">{t("有 {n} 项约束冲突，无法应用。", {n: preview.issue_count})}</p>}
      <button className="btn primary" type="button" disabled={mutation.busy || preview.issue_count > 0} onClick={() => void mutation.run("tables.field.save", {...preview.args, field: preview.field, preview_token: preview.preview_token}).then(reload).catch(() => {})}>{t("确认应用变更")}</button><button className="btn" type="button" onClick={() => setPreview(undefined)}>{t("取消")}</button>
    </section>}
  </form>;
}
