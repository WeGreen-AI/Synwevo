import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { useDialog, useI18n } from "@synwevo/ui";
import { Empty, Loading } from "../../shared/states";
import { useRead, useMutation, TableFailure, roleKeys } from "./common";
import type { Workspace, Table } from "./types";
import { Grid } from "./grid";
import { FieldsEditor } from "./schema";
import { RecordEditor } from "./record";
import { History, ImportPanel } from "./transfer";

export function Library({ws}: {ws: Workspace}) {
  const {t} = useI18n();
  const navigate = useNavigate();
  const [status, setStatus] = useState("active");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [name, setName] = useState("");
  const [tick, setTick] = useState(0);
  const {data, error} = useRead<{tables: Table[]; total: number}>("tables.table.list", {status, q, offset}, ws.id, tick);
  const mutation = useMutation(ws.id);
  return <section className="tables-app">
    <header className="suite-head"><div><h1>{t("数据表目录")}</h1><p className="lede">{t("让业务数据有结构、有关联、可追溯。")}</p></div></header>
    <div className="tables-toolbar">
      <label>{t("搜索数据表")}<input className="input" type="search" value={q} onChange={e => {setQ(e.target.value); setOffset(0);}} /></label>
      <label>{t("状态")}<select className="input" value={status} onChange={e => {setStatus(e.target.value); setOffset(0);}}>
        <option value="active">{t("使用中")}</option><option value="archived">{t("已归档")}</option>
      </select></label>
    </div>
    {["admin", "owner"].includes(ws.role) && <form className="tables-create" onSubmit={e => {e.preventDefault(); void mutation.run("tables.table.create", {name}).then(r => navigate(`t/${r.id}`)).catch(() => {});}}>
      <label>{t("新数据表名称")}<input className="input" required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></label>
      <button className="btn primary" disabled={mutation.busy || !name.trim()}>{t("新建数据表")}</button>
    </form>}
    <TableFailure error={mutation.error || error} retry={() => setTick(tick+1)} />
    {!data && !error && <Loading />}
    {data && !data.tables.length && <Empty title={t("还没有数据表")}><p>{t("新建数据表，然后配置字段并添加或导入记录。")}</p></Empty>}
    {!!data?.tables.length && <div className="suite-list">{data.tables.map(table => <Link className="suite-row" key={table.id} to={`t/${table.id}`}>
      <div className="grow"><div className="title">{table.name}</div><p className="sub">{table.description || t("结构化业务数据")}</p></div><span aria-hidden="true">→</span>
    </Link>)}</div>}
    {data && <div className="tables-pagination"><span>{t("共 {n} 项", {n: data.total})}</span><button className="btn sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset-50))}>{t("上一页")}</button><button className="btn sm" disabled={offset+50 >= data.total} onClick={() => setOffset(offset+50)}>{t("下一页")}</button></div>}
  </section>;
}

export function RecordLink({ws}: {ws: Workspace}) {
  const {rid = ""} = useParams();
  const {data, error} = useRead<{table_id: string; record_id: string}>("tables.record.locate", {record_id: rid}, ws.id);
  if (error) return <TableFailure error={error} />;
  if (!data) return <Loading />;
  return <Navigate replace to={`/tables/w/${ws.id}/t/${data.table_id}/r/${data.record_id}`} />;
}

export function TablePage({ws}: {ws: Workspace}) {
  const {tid = ""} = useParams();
  const {t} = useI18n();
  const [tick, setTick] = useState(0);
  const {data: table, error} = useRead<Table>("tables.table.get", {table_id: tid}, ws.id, tick);
  const reload = () => setTick(n => n+1);
  // A confirmation kept here outlives the reload that remounts the page below; it ends when the
  // person moves to another part of the table.
  const [notice, setNotice] = useState<string>();
  const {pathname} = useLocation();
  useEffect(() => setNotice(undefined), [pathname]);
  if (error) return <TableFailure error={error} retry={reload} />;
  if (!table) return <Loading />;
  const base = `/tables/w/${ws.id}/t/${tid}`;
  return <section className="tables-app" key={tid}>
    <Link to={`/tables/w/${ws.id}`}>{t("所有数据表")}</Link>
    <header className="suite-head"><div><h1>{table.name}</h1><p className="lede">{table.description}</p></div><span className="badge">{table.status === "archived" ? t("已归档") : t("使用中")}</span></header>
    {table.status === "archived" && <p className="notice">{t("数据表已归档，恢复后才能编辑。")}</p>}
    <nav className="tables-tabs" aria-label={t("数据表导航")}>
      {[["", "数据网格"], ["fields", "字段设置"], ["history", "变更记录"], ["import", "导入数据"], ["settings", "数据表设置"]].filter(([path]) => path !== "import" || table.can_write).map(([path, label]) => <NavLink key={path} end={path === ""} to={`${base}/${path}`} className={({isActive}) => `suite-section${isActive ? " on" : ""}`}>{t(label)}</NavLink>)}
    </nav>
    <Routes>
      <Route index element={<Grid key={tick} ws={ws} table={table} reload={reload} notice={notice} onNotice={setNotice} />} />
      <Route path="fields" element={<FieldsEditor ws={ws} table={table} reload={reload} />} />
      <Route path="history" element={<History ws={ws} table={table} />} />
      <Route path="import" element={<ImportPanel ws={ws} table={table} reload={reload} />} />
      <Route path="settings" element={<Settings ws={ws} table={table} reload={reload} />} />
      <Route path="new" element={<RecordEditor ws={ws} table={table} />} />
      <Route path="r/:rid" element={<RecordEditor ws={ws} table={table} />} />
    </Routes>
  </section>;
}

function Settings({ws, table, reload}: {ws: Workspace; table: Table; reload: () => void}) {
  const {t} = useI18n();
  const dialog = useDialog();
  const [name, setName] = useState(table.name);
  const [description, setDescription] = useState(table.description);
  const [readRole, setReadRole] = useState(table.read_role);
  const [writeRole, setWriteRole] = useState(table.write_role);
  const mutation = useMutation(ws.id);
  return <form className="tables-form" onSubmit={e => {e.preventDefault(); void mutation.run("tables.table.update", {table_id: table.id, schema_rev: table.schema_rev, name, description, read_role: readRole, write_role: writeRole}).then(reload).catch(() => {});}}>
    <fieldset disabled={!table.can_manage || mutation.busy}><legend>{t("数据表设置")}</legend>
      <label>{t("名称")}<input className="input" required maxLength={100} value={name} onChange={e => setName(e.target.value)} /></label>
      <label>{t("说明")}<textarea className="input" maxLength={2000} value={description} onChange={e => setDescription(e.target.value)} /></label>
      <label>{t("可查看")}<select className="input" value={readRole} onChange={e => setReadRole(e.target.value)}>{Object.keys(roleKeys).map(role => <option value={role} key={role}>{t(roleKeys[role])}</option>)}</select></label>
      <label>{t("可编辑")}<select className="input" value={writeRole} onChange={e => setWriteRole(e.target.value)}>{["editor", "admin"].map(role => <option value={role} key={role}>{t(roleKeys[role])}</option>)}</select></label>
      <p className="muted">{t("权限按工作空间、数据表、记录和字段共同限制。保存的视图不会授予权限。")}</p>
      <button className="btn primary">{t("保存设置")}</button>
      <button className="btn" type="button" onClick={async () => {
        if (await dialog.confirm(table.name, t(table.status === "archived" ? "恢复此数据表？" : "归档此数据表？记录与关联会保留。"), t(table.status === "archived" ? "恢复数据表" : "归档数据表"), table.status !== "archived")) void mutation.run("tables.table.update", {table_id: table.id, schema_rev: table.schema_rev, archived: table.status !== "archived"}).then(reload).catch(() => {});
      }}>{t(table.status === "archived" ? "恢复数据表" : "归档数据表")}</button>
    </fieldset>
    <TableFailure error={mutation.error} retry={reload} />
  </form>;
}
