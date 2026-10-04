import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useDialog, useI18n } from "@synwevo/ui";
import { Loading } from "../../shared/states";
import { useRead, useMutation, TableFailure, valueText, roleKeys, errorKey } from "./common";
import { relationLabel } from "./display";
import { COMPUTED } from "./types";
import type { Workspace, Table, Field, RecordRow, QueryResult } from "./types";
import { History } from "./transfer";

export function RecordEditor({ws, table}: {ws: Workspace; table: Table}) {
  const {rid} = useParams();
  const [tick, setTick] = useState(0);
  const {data, error} = useRead<RecordRow>("tables.record.get", {table_id: table.id, record_id: rid}, ws.id, tick, !!rid);
  if (error) return <TableFailure error={error} retry={() => setTick(n => n+1)} />;
  if (rid && !data) return <Loading />;
  return <RecordForm key={`${rid ?? "new"}:${tick}`} ws={ws} table={table} row={data} reload={() => setTick(n => n+1)} />;
}

function RecordForm({ws, table, row, reload}: {ws: Workspace; table: Table; row?: RecordRow; reload: () => void}) {
  const {t, locale} = useI18n();
  const dialog = useDialog();
  const navigate = useNavigate();
  const initial = row?.cells ?? Object.fromEntries(table.fields.map(f => [f.id, f.default]));
  const [cells, setCells] = useState<Record<string, any>>(initial);
  const [readRole, setReadRole] = useState(row?.read_role ?? "viewer");
  const [writeRole, setWriteRole] = useState(row?.write_role ?? "editor");
  const [saved, setSaved] = useState(false);
  const mutation = useMutation(ws.id);
  const writable = table.can_write && (!row || row.can_write) && !row?.deleted_at;
  const dirty = JSON.stringify(initial) !== JSON.stringify(cells) || readRole !== (row?.read_role ?? "viewer") || writeRole !== (row?.write_role ?? "editor");
  const base = `/tables/w/${ws.id}/t/${table.id}`;
  useEffect(() => {
    function leave(e: BeforeUnloadEvent) {if (dirty) {e.preventDefault(); e.returnValue = "";}}
    window.addEventListener("beforeunload", leave); return () => window.removeEventListener("beforeunload", leave);
  }, [dirty]);
  async function close() {if (!dirty || await dialog.confirm(t("撤销未保存修改"), t("放弃未保存的修改？"), t("确定"), true)) navigate(base);}
  async function save() {
    const patch = Object.fromEntries(Object.entries(cells).filter(([fid, value]) => table.fields.some(f => f.id === fid && f.can_write) && (!row || JSON.stringify(value) !== JSON.stringify(initial[fid]))));
    try {
      const result = await mutation.run(row ? "tables.record.update" : "tables.record.create", {table_id: table.id, cells: patch,
        ...(row ? {record_id: row.id, rev: row.rev} : {}), ...(table.can_manage ? {read_role: readRole, write_role: writeRole} : {})});
      setSaved(true);
      if (!row) navigate(`${base}/r/${result.id}`, {replace: true});
      reload();
    } catch {}
  }
  return <section className="tables-record">
    <div className="tables-toolbar"><button className="btn ghost" onClick={close}>{t("返回网格")}</button><h2>{row ? t("记录 {n}", {n: row.seq}) : t("新建记录")}</h2>{row && <span className="badge">{t("修订 {n}", {n: row.rev})}</span>}</div>
    <form className="tables-form" onSubmit={e => {e.preventDefault(); void save();}} onKeyDown={e => {if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && writable && !mutation.busy) {e.preventDefault(); e.currentTarget.requestSubmit();}}}>
      <fieldset disabled={mutation.busy}><legend>{t("记录内容")}</legend>
        {table.fields.map(f => <div className="tables-record-field" key={f.id}>
          <label htmlFor={`cell-${f.id}`}>{f.name}{f.required && <span> *</span>}</label>
          {f.description && <p className="muted small">{f.description}</p>}
          {writable && f.can_write && !COMPUTED.has(f.type) ? <CellInput ws={ws} field={f} value={cells[f.id]} titles={Object.fromEntries((row?.relations[f.id] ?? []).map(x => [x.id, x.title]))} onChange={v => {setCells({...cells, [f.id]: v}); setSaved(false);}} /> : <div className="tables-value" id={`cell-${f.id}`}>{row?.formula_errors[f.id] ? t(errorKey(row.formula_errors[f.id])) : f.type === "link" && row ? row.relations[f.id]?.map(link => <Link key={link.id} to={`/tables/w/${ws.id}/t/${link.table_id}/r/${link.id}`}>{link.title}</Link>) : f.type === "boolean" && cells[f.id] != null ? t(cells[f.id] ? "是" : "否") : valueText(f, cells[f.id], locale, row)}</div>}
        </div>)}
        {table.can_manage && writable && <details><summary>{t("记录权限")}</summary>
          <label>{t("可查看")}<select className="input" value={readRole} onChange={e => setReadRole(e.target.value)}>{Object.keys(roleKeys).map(role => <option key={role} value={role}>{t(roleKeys[role])}</option>)}</select></label>
          <label>{t("可编辑")}<select className="input" value={writeRole} onChange={e => setWriteRole(e.target.value)}>{["editor", "admin"].map(role => <option key={role} value={role}>{t(roleKeys[role])}</option>)}</select></label>
        </details>}
        {writable && <div className="tables-toolbar"><button className="btn primary" disabled={mutation.busy}>{mutation.busy ? t("保存中…") : t("保存记录")}</button><button className="btn" type="button" disabled={!dirty} onClick={() => {setCells(initial); setReadRole(row?.read_role ?? "viewer"); setWriteRole(row?.write_role ?? "editor");}}>{t("撤销未保存修改")}</button><span className="muted small" aria-live="polite">{dirty ? t("有未保存的修改") : saved ? t("已保存") : t("按 Ctrl 或 Command + Enter 保存")}</span></div>}
      </fieldset>
    </form>
    <TableFailure error={mutation.error} />
    {!!mutation.error && row && <button className="btn" onClick={async () => {if (!dirty || await dialog.confirm(t("撤销未保存修改"), t("放弃未保存的修改？"), t("确定"), true)) reload();}}>{t("放弃我的修改，载入最新")}</button>}
    {row && table.can_write && row.can_write && <button className="btn" disabled={mutation.busy} onClick={async () => {if (await dialog.confirm(t(row.deleted_at ? "恢复记录" : "归档记录"), <><p>{table.name} · {t("记录 {n}", {n: row.seq})}</p><p>{t("对记录执行归档或恢复？关联引用会保留。")}</p></>, t(row.deleted_at ? "恢复记录" : "归档记录"), !row.deleted_at)) void mutation.run("tables.record.state", {table_id: table.id, record_id: row.id, rev: row.rev, deleted: !row.deleted_at}).then(reload).catch(() => {});}}>{t(row.deleted_at ? "恢复记录" : "归档记录")}</button>}
    {row && <History key={row.rev} ws={ws} table={table} record={row} onRestore={reload} />}
  </section>;
}

function CellInput({ws, field: f, value, titles, onChange}: {ws: Workspace; field: Field; value: any; titles: Record<string, string>; onChange: (v: any) => void}) {
  const {t} = useI18n();
  const props = {id: `cell-${f.id}`, className: "input", required: f.required};
  if (f.type === "link") return <RelationPicker ws={ws} field={f} value={value ?? []} titles={titles} onChange={onChange} />;
  if (f.type === "boolean") return <select {...props} value={value == null ? "" : String(value)} onChange={e => onChange(e.target.value === "" ? null : e.target.value === "true")}><option value="">{t("留空")}</option><option value="true">{t("是")}</option><option value="false">{t("否")}</option></select>;
  if (["single_select", "multi_select"].includes(f.type)) return <select {...props} multiple={f.type === "multi_select"} value={value ?? (f.type === "multi_select" ? [] : "")} onChange={e => onChange(f.type === "multi_select" ? [...e.target.selectedOptions].map(o => o.value) : e.target.value || null)}>
    {f.type === "single_select" && <option value="">{t("留空")}</option>}{f.config.choices.map((c: any) => <option key={c.id} value={c.id}>{c.label}</option>)}
  </select>;
  if (f.type === "long_text") return <textarea {...props} rows={4} maxLength={20000} value={value ?? ""} onChange={e => onChange(e.target.value || null)} />;
  if (f.type === "datetime") return <><input {...props} type="text" placeholder="2026-09-30T09:30:00+08:00" value={value ?? ""} onChange={e => onChange(e.target.value || null)} /><small>{t("请包含时区偏移，例如 +08:00。")}</small></>;
  return <div className="tables-input-unit"><input {...props} type={f.type === "date" ? "date" : f.type === "email" ? "email" : f.type === "url" ? "url" : "text"} inputMode={["number", "money"].includes(f.type) ? "decimal" : undefined} value={f.type === "money" ? value?.amount ?? value ?? "" : value ?? ""} onChange={e => onChange(e.target.value || null)} />{f.type === "money" && <span>{f.config.currency}</span>}</div>;
}

function RelationPicker({ws, field, value, titles, onChange}: {ws: Workspace; field: Field; value: string[]; titles: Record<string, string>; onChange: (v: string[]) => void}) {
  const {t, locale} = useI18n();
  // Names of the chosen records: those the record already had, and those picked from the list.
  const [labels, setLabels] = useState(titles);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const {data, error} = useRead<QueryResult>("tables.record.query", {table_id: field.config.table_id, q, offset, limit: 25}, ws.id);
  return <div className="tables-relations">
    <input className="input" id={`cell-${field.id}`} type="search" placeholder={t("搜索关联记录")} aria-label={t("搜索关联记录")} value={q} onChange={e => {setQ(e.target.value); setOffset(0);}} />
    {!!value.length && <div className="tables-checks">{value.map(id => <button className="btn sm" key={id} type="button" onClick={() => onChange(value.filter(x => x !== id))}>{t("移除关联")} {relationLabel(t, id, labels)}</button>)}</div>}
    <TableFailure error={error} />{!data && !error && <Loading />}
    {data && <><ul>{data.records.map(row => {const label = data.fields[0] ? valueText(data.fields[0], row.cells[data.fields[0].id], locale, row) : `#${row.seq}`; return <li key={row.id}><label className="tables-check"><input type="checkbox" checked={value.includes(row.id)} onChange={e => {setLabels({...labels, [row.id]: label}); onChange(e.target.checked ? field.config.multiple ? [...value, row.id] : [row.id] : value.filter(x => x !== row.id));}} />{label}</label></li>;})}</ul><div className="tables-pagination"><button className="btn sm" type="button" disabled={!offset} onClick={() => setOffset(Math.max(0, offset-25))}>{t("上一页")}</button><span>{t("共 {n} 条记录", {n: data.total})}</span><button className="btn sm" type="button" disabled={data.next_offset === null} onClick={() => setOffset(data.next_offset!)}>{t("下一页")}</button></div></>}
  </div>;
}
