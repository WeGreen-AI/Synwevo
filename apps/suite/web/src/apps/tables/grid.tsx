import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useDialog, useI18n } from "@synwevo/ui";
import { Loading, Empty, RunBadge } from "../../shared/states";
import { useRead, useMutation, TableFailure, valueText, errorKey, issueText } from "./common";
import { viewSaved } from "./display";
import type { Workspace, Table, Query, QueryResult } from "./types";

export function Grid({ws, table, reload, notice, onNotice}: {ws: Workspace; table: Table; reload: () => void; notice?: string; onNotice: (text?: string) => void}) {
  const {t, locale} = useI18n();
  const dialog = useDialog();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [filters, setFilters] = useState<NonNullable<Query["filters"]>>([]);
  const [sort, setSort] = useState<NonNullable<Query["sort"]>>([]);
  const [offset, setOffset] = useState(0);
  const [previousPages, setPreviousPages] = useState<number[]>([]);
  const [editingView, setEditingView] = useState<{id: string; rev: number} | null>(null);
  const [deleted, setDeleted] = useState(false);
  const [view, setView] = useState(params.get("view") ?? "");
  const [visible, setVisible] = useState(table.fields.map(f => f.id));
  const [selected, setSelected] = useState<string[]>([]);
  const [viewName, setViewName] = useState("");
  const [outcome, setOutcome] = useState<any>();
  const [ticket, setTicket] = useState<{url: string; expires_at: number}>();
  const [tick, setTick] = useState(0);
  const query: Query = {table_id: table.id, q, filters, sort, fields: visible, deleted, offset, limit: 50, view_id: view || null};
  const {data, error} = useRead<QueryResult>("tables.record.query", {...query}, ws.id, tick);
  const mutation = useMutation(ws.id);
  const base = `/tables/w/${ws.id}/t/${table.id}`;
  function changed() {setOffset(0); setPreviousPages([]); setSelected([]); setTicket(undefined); onNotice(undefined);}
  const viewRow = table.views.find(v => v.id === view);
  const writable = data?.records.filter(r => selected.includes(r.id) && r.can_write) ?? [];
  return <div>
    <div className="tables-toolbar">
      <label>{t("保存的视图")}<select className="input" value={view} onChange={e => {setView(e.target.value); setEditingView(null); setParams(e.target.value ? {view: e.target.value} : {}); changed();}}><option value="">{t("自定义查询")}</option>{table.views.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
      {viewRow && <button className="btn" onClick={() => {setQ(viewRow.config.q ?? ""); setFilters(viewRow.config.filters ?? []); setSort(viewRow.config.sort ?? []); setVisible(viewRow.config.fields ?? table.fields.map(f => f.id)); setDeleted(viewRow.config.deleted ?? false); setViewName(viewRow.name); setEditingView({id: viewRow.id, rev: viewRow.rev}); setView(""); setParams({}); changed();}}>{t("编辑此视图")}</button>}
      {!view && <label>{t("搜索可见内容")}<input className="input" type="search" value={q} onChange={e => {setQ(e.target.value); setParams(e.target.value ? {q: e.target.value} : {}); changed();}} /></label>}
      <button className="btn" onClick={() => {setTick(n => n+1); setSelected([]);}}>{t("刷新")}</button>
      {table.can_write && <Link className="btn primary" to={`${base}/new`}>{t("新建记录")}</Link>}
      <details className="tables-menu"><summary>{t("导出")}</summary><div className="tables-popover">
        <p>{t("导出当前查询的全部授权记录。下载时重新检查权限。")}</p>
        {["csv", "xlsx"].map(format => <button className="btn sm" key={format} disabled={mutation.busy} onClick={() => void mutation.run("tables.export.create", {...query, format}).then(setTicket).catch(() => {})}>{format.toUpperCase()}</button>)}
        {ticket && <p><a className="btn primary" href={ticket.url}>{t("下载文件")}</a><small>{t("下载链接 10 分钟后失效。")}</small></p>}
      </div></details>
    </div>
    {!view && <details className="tables-query"><summary>{t("筛选、排序与显示字段")}</summary>
      <div className="tables-query-body">
        <label className="tables-check"><input type="checkbox" checked={deleted} onChange={e => {setDeleted(e.target.checked); changed();}} />{t("显示已归档记录")}</label>
        {filters.map((f, index) => <div className="tables-filter" key={index}>
          <select className="input" aria-label={t("筛选字段")} value={f.field_id} onChange={e => {setFilters(filters.map((x, j) => j === index ? {...x, field_id: e.target.value} : x)); changed();}}>{table.fields.map(field => <option key={field.id} value={field.id}>{field.name}</option>)}</select>
          <select className="input" aria-label={t("筛选条件")} value={f.op} onChange={e => {setFilters(filters.map((x, j) => j === index ? {...x, op: e.target.value} : x)); changed();}}>{Object.entries({eq: "等于", ne: "不等于", contains: "包含", gt: "大于", gte: "大于或等于", lt: "小于", lte: "小于或等于", empty: "为空", not_empty: "不为空"}).map(([op, label]) => <option key={op} value={op}>{t(label)}</option>)}</select>
          {!["empty", "not_empty"].includes(f.op) && (table.fields.find(x => x.id === f.field_id)?.type === "boolean" && f.op !== "contains" ? <select className="input" aria-label={t("筛选值")} value={String(f.value ?? "")} onChange={e => {setFilters(filters.map((x, j) => j === index ? {...x, value: e.target.value === "" ? null : e.target.value === "true"} : x)); changed();}}><option value="">{t("留空")}</option><option value="true">{t("是")}</option><option value="false">{t("否")}</option></select> : <input className="input" aria-label={t("筛选值")} value={String(f.value ?? "")} onChange={e => {setFilters(filters.map((x, j) => j === index ? {...x, value: e.target.value} : x)); changed();}} />)}
          <button className="btn sm" onClick={() => {setFilters(filters.filter((_, j) => j !== index)); changed();}}>{t("移除筛选")}</button>
        </div>)}
        <button className="btn sm" disabled={!table.fields.length || filters.length >= 20} onClick={() => {setFilters([...filters, {field_id: table.fields[0].id, op: "contains", value: ""}]); changed();}}>{t("添加筛选")}</button>
        {sort.map((s, index) => <div className="tables-filter" key={index}>
          <select className="input" aria-label={t("排序字段")} value={s.field_id} onChange={e => {setSort(sort.map((x, j) => j === index ? {...x, field_id: e.target.value} : x)); changed();}}>{table.fields.map(f => <option value={f.id} key={f.id}>{f.name}</option>)}</select>
          <select className="input" aria-label={t("排序方向")} value={s.direction} onChange={e => {setSort(sort.map((x, j) => j === index ? {...x, direction: e.target.value} : x)); changed();}}><option value="asc">{t("升序")}</option><option value="desc">{t("降序")}</option></select>
          <button className="btn sm" onClick={() => {setSort(sort.filter((_, j) => j !== index)); changed();}}>{t("移除排序")}</button>
        </div>)}
        <button className="btn sm" disabled={!table.fields.length || sort.length >= 5} onClick={() => {setSort([...sort, {field_id: table.fields[0].id, direction: "asc"}]); changed();}}>{t("添加排序")}</button>
        <fieldset><legend>{t("显示字段")}</legend><div className="tables-checks">{table.fields.map(f => <label className="tables-check" key={f.id}><input type="checkbox" checked={visible.includes(f.id)} onChange={e => {setVisible(e.target.checked ? [...visible, f.id] : visible.filter(x => x !== f.id)); changed();}} />{f.name}</label>)}</div></fieldset>
      </div>
    </details>}
    {table.can_write && <form className="tables-toolbar" onSubmit={e => {e.preventDefault(); void mutation.run("tables.view.save", {table_id: table.id, name: viewName, ...(editingView ? {view_id: editingView.id, rev: editingView.rev} : {}), query: viewRow ? {...viewRow.config, table_id: table.id, view_id: null} : {...query, view_id: null}}).then(result => {const saved = viewSaved(t, result, viewName); setParams(saved.params); onNotice(saved.notice); reload();}).catch(() => {});}}>
      <label>{t("视图名称")}<input className="input sm" required maxLength={100} value={viewName} onChange={e => setViewName(e.target.value)} /></label><button className="btn sm" disabled={mutation.busy}>{t(editingView ? "更新此视图" : "保存为新视图")}</button>
      {viewRow && <button className="btn sm" type="button" disabled={mutation.busy} onClick={async () => {if (await dialog.confirm(viewRow.name, t("删除此保存的视图？记录不会删除。"), t("删除视图"), true)) void mutation.run("tables.view.delete", {table_id: table.id, view_id: viewRow.id, rev: viewRow.rev}).then(() => {setParams({}); reload();}).catch(() => {});}}>{t("删除视图")}</button>}
    </form>}
    {notice && <p className="notice ok" role="status">{notice}</p>}
    <TableFailure error={error || mutation.error} fields={table.fields} retry={() => setTick(n => n+1)} />
    {!data && !error && <Loading />}
    {data && <>
      <div className="tables-toolbar"><span>{t("共 {n} 条记录", {n: data.total})}</span>{!!selected.length && <><span>{t("已选择本页 {n} 条", {n: selected.length})}</span><button className="btn sm" disabled={mutation.busy || !writable.length || !table.can_write} onClick={async () => {
        if (await dialog.confirm(t("归档或恢复所选记录"), <><p>{table.name} · {t("已选择本页 {n} 条", {n: writable.length})}</p><p>{t("对所选记录执行归档或恢复？")}</p></>, t("确定"), true)) void mutation.run("tables.record.batch", {table_id: table.id, atomic: false, items: writable.map(r => ({action: r.deleted_at ? "restore" : "archive", record_id: r.id, rev: r.rev}))}).then(r => {setOutcome(r); setSelected([]); setTick(n => n+1);}).catch(() => {});
      }}>{t("归档或恢复所选记录")}</button></>}</div>
      {!data.records.length ? <Empty title={t("没有匹配的记录")} action={table.can_write && <Link className="btn primary" to={`${base}/new`}>{t("新建记录")}</Link>} /> : <div className="tables-grid-wrap" tabIndex={0} role="region" aria-label={t("数据网格")}>
        <table className="tables-grid"><thead><tr><th><input type="checkbox" aria-label={t("选择本页")} checked={data.records.length > 0 && selected.length === data.records.length} onChange={e => setSelected(e.target.checked ? data.records.map(r => r.id) : [])} /></th><th>{t("记录")}</th>{data.fields.map(f => <th key={f.id}>{f.name}{f.required && <span aria-label={t("必填")}> *</span>}</th>)}</tr></thead>
        <tbody>{data.records.map(row => <tr key={row.id}><td><input type="checkbox" aria-label={t("选择记录 {n}", {n: row.seq})} checked={selected.includes(row.id)} onChange={e => setSelected(e.target.checked ? [...selected, row.id] : selected.filter(x => x !== row.id))} /></td><th scope="row"><Link to={`${base}/r/${row.id}`}>#{row.seq}</Link></th>{data.fields.map(f => <td key={f.id} title={row.formula_errors[f.id] ? t(errorKey(row.formula_errors[f.id])) : valueText(f, row.cells[f.id], locale, row)}>{row.formula_errors[f.id] ? <span className="tables-cell-error">{t(errorKey(row.formula_errors[f.id]))}</span> : f.type === "boolean" ? row.cells[f.id] == null ? "—" : t(row.cells[f.id] ? "是" : "否") : f.type === "link" ? row.relations[f.id]?.map(r => <Link key={r.id} to={`/tables/w/${ws.id}/t/${r.table_id}/r/${r.id}`}>{r.title}</Link>) : valueText(f, row.cells[f.id], locale, row)}</td>)}</tr>)}</tbody></table>
      </div>}
      <div className="tables-pagination"><button className="btn sm" disabled={!offset} onClick={() => {setOffset(previousPages.at(-1) ?? 0); setPreviousPages(previousPages.slice(0, -1)); setSelected([]);}}>{t("上一页")}</button><span>{t("从第 {n} 条开始", {n: data.total ? offset+1 : 0})}</span><button className="btn sm" disabled={data.next_offset === null} onClick={() => {setPreviousPages([...previousPages, offset]); setOffset(data.next_offset!); setSelected([]);}}>{t("下一页")}</button></div>
    </>}
    {outcome && <section className="notice" role="status"><RunBadge status={outcome.failed ? "partial" : "succeeded"} /><p>{t("成功 {ok} 项，失败 {failed} 项", {ok: outcome.succeeded, failed: outcome.failed})}</p>{outcome.items.filter((x: any) => !x.ok).map((x: any) => <p key={x.index}>{t("第 {n} 项", {n: x.index+1})}: {issueText(t, x.error, x.detail, table.fields)}</p>)}</section>}
  </div>;
}
