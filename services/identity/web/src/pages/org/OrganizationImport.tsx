/** Bulk member import: upload → read → map → preview → confirm → results.
 *  Every decision is the server's (the plan comes back per row with a reason); this component
 *  only shows it and collects the operator's explicit mapping. A row in a file is never a member:
 *  new people get an ordinary invitation and accept it themselves. */
import { useEffect, useRef, useState } from "react";
import { get, post, put, upload } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading, resultText } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, type CustomRole, type ImportAction, type ImportCapabilities, type ImportMapping, type ImportReview, type MemberImport, type MemberImportRow, type Organization, type OrganizationWorkspace } from "./types";

const ACTION_LABEL: Record<ImportAction, string> = { invite: "邀请", update: "更新", unchanged: "不变", skip: "跳过", blocked: "阻断" };
const REASON_LABEL: Record<string, string> = {
  new_person: "新成员：发送邀请", existing_account: "已有账户：发送邀请", org_member_new_workspace: "组织成员：邀请加入此工作空间", already_member: "已是此工作空间成员，无变化",
  duplicate_in_file: "文件内重复", self: "这是你自己", bad_email: "邮箱格式不正确", unknown_workspace_role: "工作空间角色未映射", unknown_org_role: "组织角色未映射",
  role_not_importable: "所有者/管理员基础角色不能通过导入授予", role_not_delegable: "你没有分配这个角色所需的权限", account_unavailable: "账户不可用",
  already_invited: "已有待接受的邀请", over_limit: "超出单次导入上限", forbidden: "你不能邀请到这个工作空间", role_too_high: "不能邀请比自己权限更高的角色",
  workspace_frozen: "工作空间已冻结", cancelled: "已取消", ocr_unchecked: "文字识别读出，核对后才会导入",
};
/** Below this lowest word confidence the machine was unsure (a hint for the person checking; every OCR row is checked). */
const LOW_CONFIDENCE = 85;

/** One OCR row the operator checks: the line as the machine read it, how sure it was, the
 *  address and name (editable) and 「核对无误」. Saving re-plans with the review kept in the
 *  mapping (CI-100). A checked row can be opened again to change it. */
function RowReview({ row, busy, onSave, where }: { row: MemberImportRow; busy: boolean; where: string; onSave: (key: string, review: ImportReview) => void }) {
  const { t } = useI18n();
  const key = String(row.source.review_key);
  const [email, setEmail] = useState(row.fields.email);
  const [name, setName] = useState(row.fields.name);
  const [open, setOpen] = useState(row.source.checked !== true);
  const conf = typeof row.source.conf === "number" ? Math.round(row.source.conf as number) : null;
  const read = row.source.read_email ? String(row.source.read_email) : null;
  return <div className="col gap4 import-review">
    <span className="tiny muted">
      {row.source.text ? <>{t("识别原文：{v}", { v: String(row.source.text) })} · </> : null}
      {conf !== null ? t("识别置信度 {n}/100", { n: conf }) : row.source.layer === "scan" ? t("扫描件的识别文字层") : null}
      {conf !== null && conf < LOW_CONFIDENCE && <> · <strong className="import-unsure">{t("机器不确定")}</strong></>}
      {read ? <> · {t("识别结果：{v}", { v: read })}</> : null}
    </span>
    {row.source.checked === true && !open ? <div className="row wrap gap4"><span className="tiny">{t("已核对")}</span>
      <button type="button" className="btn small" disabled={busy} onClick={() => setOpen(true)} aria-label={`${t("修改")} · ${where}`}>{t("修改")}</button></div>
      : <div className="row wrap gap4">
        <input className="input import-review-email" aria-label={`${t("邮箱")} · ${where}`} value={email} onChange={e => setEmail(e.target.value)} autoComplete="off" spellCheck={false} inputMode="email" />
        <input className="input import-review-name" aria-label={`${t("姓名")} · ${where}`} value={name} onChange={e => setName(e.target.value)} autoComplete="off" />
        <button type="button" className="btn" disabled={busy || !email.trim()} aria-label={`${t("核对无误")} · ${where}`}
          onClick={() => { onSave(key, { email: email.trim(), name: name.trim(), checked: true }); setOpen(false); }}>{t("核对无误")}</button>
      </div>}
  </div>;
}
const WS_ROLE_LABEL: Record<string, string> = { viewer: "查看者", editor: "编辑者", admin: "工作空间管理员" };

function sourceText(src: Record<string, unknown>): string {
  const parts: string[] = [];
  if (src.sheet) parts.push(String(src.sheet)); else if (src.table) parts.push(String(src.table));
  if (src.page) parts.push(`p.${src.page}`);
  if (src.row) parts.push(`#${src.row}`); else if (src.line) parts.push(`L${src.line}`);
  if (src.ocr) parts.push("OCR");
  return parts.join(" ");
}

export default function OrganizationImport({ organization, workspaces, mine, onChanged }: {
  organization: Organization; workspaces: OrganizationWorkspace[]; mine: string[]; onChanged: () => void;
}) {
  const { t, locale } = useI18n();
  const base = `${orgPath(organization.id)}/imports`;
  const [caps, setCaps] = useState<ImportCapabilities | null>(null);
  const [roles, setRoles] = useState<CustomRole[]>([]);
  const [history, setHistory] = useState<MemberImport[]>([]);
  const [imp, setImp] = useState<MemberImport | null>(null);
  const [rows, setRows] = useState<MemberImportRow[] | null>(null);
  const [filter, setFilter] = useState<ImportAction | "">("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false); const [progress, setProgress] = useState<number | null>(null);
  const [workspaceId, setWorkspaceId] = useState(""); const [file, setFile] = useState<File | null>(null);
  const [mapping, setMapping] = useState<ImportMapping>({});
  const [onlyValid, setOnlyValid] = useState(false); const [confirming, setConfirming] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const canImport = mine.includes("org.members.import") && organization.status === "active";
  const targets = workspaces.filter(w => w.status === "active");

  const load = (id: string) => get<MemberImport>(`${base}/${encodeURIComponent(id)}`).then(x => { setImp(x); setMapping(x.mapping || {}); setWorkspaceId(w => x.workspace_id || w); return x; });
  const loadRows = (id: string, action: ImportAction | "") => get<{ items: MemberImportRow[] }>(`${base}/${encodeURIComponent(id)}/rows?limit=1000${action ? `&action=${action}` : ""}`).then(x => setRows(x.items));
  const refreshHistory = () => get<{ items: MemberImport[] }>(base).then(x => setHistory(x.items)).catch(() => undefined);
  useEffect(() => {
    if (!canImport) return;
    const abort = new AbortController();
    Promise.all([get<ImportCapabilities>(`${base}/capabilities`, abort.signal), get<{ items: CustomRole[] }>(`${orgPath(organization.id)}/roles`, abort.signal), get<{ items: MemberImport[] }>(base, abort.signal)])
      .then(([c, r, h]) => { setCaps(c); setRoles(r.items); setHistory(h.items); const wanted = new URLSearchParams(window.location.search).get("import"); if (wanted) void load(wanted).then(x => loadRows(x.id, "")).catch(e => setError(e)); })
      .catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, canImport]);   // eslint-disable-line react-hooks/exhaustive-deps
  // execution: poll while running, then stop and refresh the members list once
  useEffect(() => {
    if (!imp || imp.status !== "running") return;
    const timer = window.setInterval(() => { void load(imp.id).then(x => { void loadRows(x.id, filter); if (x.status !== "running") { onChanged(); void refreshHistory(); } }).catch(() => undefined); }, 2000);
    return () => window.clearInterval(timer);
  }, [imp?.id, imp?.status]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (!canImport) return null;

  const run = async (fn: () => Promise<unknown>) => { setBusy(true); setError(null); try { await fn(); } catch (e) { setError(e); } finally { setBusy(false); setProgress(null); } };
  const doUpload = () => run(async () => {
    if (!file) return;
    const form = new FormData(); form.append("file", file); if (workspaceId) form.append("workspace_id", workspaceId);
    const x = await upload<MemberImport>(base, form, p => setProgress(p));
    setImp(x); setMapping(x.mapping || {}); setRows(null); setFilter(""); void refreshHistory();
  });
  const doOcr = () => run(async () => { if (!imp) return; const x = await post<MemberImport>(`${base}/${imp.id}/ocr`, {}); setImp(x); setMapping(x.mapping || {}); });
  const doPlan = () => run(async () => {
    if (!imp) return;
    const x = await put<MemberImport>(`${base}/${imp.id}/mapping`, { workspace_id: workspaceId, mapping });
    setImp(x); setFilter(""); await loadRows(x.id, "");
  });
  const doCancel = () => run(async () => { if (!imp) return; setImp(await post<MemberImport>(`${base}/${imp.id}/cancel`, {})); });
  const doRetry = () => run(async () => { if (!imp) return; setImp(await post<MemberImport>(`${base}/${imp.id}/retry`, {})); });
  const setCol = (field: string, value: string) => setMapping(m => ({ ...m, columns: { ...(m.columns || {}), [field]: value === "" ? undefined : Number(value) } }));
  const setName = (kind: "workspace_role_names" | "org_role_names", text: string, value: string) => setMapping(m => ({ ...m, [kind]: { ...(m[kind] || {}), [text]: value } }));
  const saveReviews = (items: Record<string, ImportReview>) => run(async () => {
    if (!imp) return;
    const next = { ...mapping, reviews: { ...(mapping.reviews || {}), ...items } };
    setMapping(next);
    const x = await put<MemberImport>(`${base}/${imp.id}/mapping`, { workspace_id: workspaceId, mapping: next });
    setImp(x); setMapping(x.mapping || next); await loadRows(x.id, filter);
  });
  const saveReview = (key: string, review: ImportReview) => saveReviews({ [key]: review });

  const ex = imp?.extraction; const tables = ex?.tables || [];
  const table = mapping.mode === "lines" || mapping.table == null ? null : tables[mapping.table] || null;
  const headers = table ? (mapping.header_row ? table.preview[0] || [] : []) : [];
  const width = table ? Math.max(0, ...table.preview.map(r => r.length)) : 0;
  const colOptions = Array.from({ length: width }, (_, i) => ({ value: String(i), label: headers[i] ? `${i + 1}. ${headers[i]}` : t("第 {n} 列", { n: i + 1 }) }));
  const counts = imp?.summary?.counts; const outcome = imp?.summary?.outcome;
  const editable = imp && (imp.status === "extracted" || imp.status === "planned");
  const fmtList = caps ? caps.ingest.formats.map(f => f.toUpperCase()).join(" / ") : "";

  return <section className="org-section" id="org-import"><h2>{t("批量导入成员")}</h2>
    <p className="tiny muted">{t("按名单文件批量邀请。新人仍需通过邀请邮件自己接受；已有成员只会提升工作空间角色或增加组织角色，不会被降级或移除。")}</p>
    {caps && <p className="tiny muted">{t("可读取：{formats}。", { formats: fmtList })} {caps.ingest.ocr ? t("扫描件与图片通过文字识别读取。") : t("此部署没有文字识别，扫描件与图片无法读取。")} {t("单次最多 {n} 行。", { n: caps.limits.rows })}</p>}
    <FlowError error={error} />
    {/* ---- step 1: upload */}
    <div className="col gap8">
      <div className="field"><label htmlFor="import-workspace">{t("目标工作空间")}</label>
        <select id="import-workspace" className="input" value={workspaceId} onChange={e => setWorkspaceId(e.target.value)} disabled={busy || !!(imp && !editable)}>
          <option value="">{t("请选择…")}</option>{targets.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></div>
      <div className="field"><label htmlFor="import-file">{t("成员文件")}</label>
        <input id="import-file" ref={fileInput} className="input" type="file" disabled={busy} accept={caps ? caps.ingest.formats.map(f => "." + f).join(",") : undefined} onChange={e => setFile(e.target.files?.[0] || null)} /></div>
      <div className="org-actions"><button type="button" className="btn primary" disabled={busy || !file} onClick={() => void doUpload()}>{busy && progress != null ? t("上传中 {p}%", { p: Math.round(progress * 100) }) : t("上传并读取")}</button></div>
    </div>
    {/* ---- what was read */}
    {imp && ex && <div className="notice" role="status"><strong>{imp.filename}</strong> · {ex.format?.toUpperCase()} · {t(imp.status === "extracted" ? "已读取" : imp.status === "planned" ? "已生成预览" : imp.status === "running" ? "执行中" : imp.status === "finished" ? "已完成" : imp.status === "partial" ? "部分完成" : imp.status === "failed" ? "失败" : "已取消")}
      {imp.scan && <span className="tiny muted"> · {t(imp.scan.status === "clean" ? "安全检查：未检出已知威胁" : imp.scan.status === "not_configured" ? "安全检查：此部署未配置" : "安全检查：{status}", { status: imp.scan.status })}</span>}
      {ex.status === "error" && <p className="flow-error">{ex.error}</p>}
      {ex.status === "unsupported" && <p className="flow-error">{ex.error || t("暂不支持这种格式")}</p>}
      {ex.status === "needs_ocr" && <p>{t("这个文件没有可读文字（扫描件或图片）。")} {caps?.ingest.ocr ? <button type="button" className="btn" disabled={busy} onClick={() => void doOcr()}>{t("识别文字（OCR）")}</button> : t("此部署没有文字识别。")}</p>}
      {tables.length > 0 && <ul className="small">{tables.map((tb, i) => <li key={i}>{tb.name}：{t("{n} 行", { n: tb.rows })}{tb.truncated && t("（已截断）")}</li>)}</ul>}
      {ex.lines > 0 && <p className="small">{t("文本行：{n}", { n: ex.lines })}{ex.line_preview.length > 0 && <span className="tiny muted"> · {ex.line_preview.slice(0, 2).join(" / ")}</span>}</p>}
      {ex.notes.length > 0 && <ul className="tiny muted">{ex.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </div>}
    {/* ---- step 2: mapping */}
    {imp && editable && ex && ex.status === "ready" && <div className="col gap8" id="import-mapping">
      <h3 className="small">{t("字段映射")}</h3>
      {tables.length > 0 && <div className="field"><label htmlFor="import-table">{t("读取方式")}</label>
        <select id="import-table" className="input" value={mapping.mode === "lines" ? "lines" : String(mapping.table ?? 0)} onChange={e => setMapping(m => e.target.value === "lines" ? { ...m, mode: "lines", table: null } : { ...m, mode: "table", table: Number(e.target.value) })}>
          {tables.map((tb, i) => <option key={i} value={String(i)}>{t("表格：{name}", { name: tb.name })}</option>)}{ex.lines > 0 && <option value="lines">{t("按行识别邮箱（{n} 行）", { n: ex.lines })}</option>}</select></div>}
      {table && <>
        <label className="check"><input type="checkbox" checked={!!mapping.header_row} onChange={e => setMapping(m => ({ ...m, header_row: e.target.checked }))} /><span>{t("第一行是标题")}</span></label>
        {(["email", "name", "workspace_role", "org_roles"] as const).map(field => <div className="field" key={field}><label htmlFor={`import-col-${field}`}>{t(field === "email" ? "邮箱列（必填）" : field === "name" ? "姓名列" : field === "workspace_role" ? "工作空间角色列" : "组织角色列")}</label>
          <select id={`import-col-${field}`} className="input" value={mapping.columns?.[field] == null ? "" : String(mapping.columns[field])} onChange={e => setCol(field, e.target.value)}>
            <option value="">{t("不使用")}</option>{colOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></div>)}
        <div className="table-wrap"><table className="small"><tbody>{table.preview.slice(0, 5).map((r, i) => <tr key={i}>{Array.from({ length: width }, (_, c) => <td key={c}>{r[c] || ""}</td>)}</tr>)}</tbody></table></div>
      </>}
      <div className="field"><label htmlFor="import-default-role">{t("默认工作空间角色")}</label>
        <select id="import-default-role" className="input" value={mapping.defaults?.workspace_role || "viewer"} onChange={e => setMapping(m => ({ ...m, defaults: { ...(m.defaults || {}), workspace_role: e.target.value } }))}>
          {(caps?.workspace_roles || ["viewer", "editor", "admin"]).map(r => <option key={r} value={r}>{t(WS_ROLE_LABEL[r] || r)}</option>)}</select></div>
      {roles.length > 0 && <fieldset className="flow-options"><legend>{t("默认组织角色（每个人都会获得）")}</legend>
        {roles.map(r => <label key={r.id} className="check"><input type="checkbox" checked={(mapping.defaults?.org_role_ids || []).includes(r.id)} onChange={e => setMapping(m => { const cur = new Set(m.defaults?.org_role_ids || []); e.target.checked ? cur.add(r.id) : cur.delete(r.id); return { ...m, defaults: { ...(m.defaults || {}), org_role_ids: Array.from(cur) } }; })} /><span>{r.name}</span></label>)}</fieldset>}
      {imp.summary && imp.summary.unmapped_workspace_roles.length > 0 && <fieldset className="flow-options"><legend>{t("文件里的工作空间角色写法 → 实际角色")}</legend>
        {imp.summary.unmapped_workspace_roles.map(txt => <div className="field" key={txt}><label>{txt}</label><select className="input" value={mapping.workspace_role_names?.[txt] || ""} onChange={e => setName("workspace_role_names", txt, e.target.value)}><option value="">{t("请选择…")}</option>{(caps?.workspace_roles || []).map(r => <option key={r} value={r}>{t(WS_ROLE_LABEL[r] || r)}</option>)}</select></div>)}</fieldset>}
      {imp.summary && imp.summary.unmapped_org_roles.length > 0 && <fieldset className="flow-options"><legend>{t("文件里的组织角色写法 → 实际角色")}</legend>
        <p className="tiny muted">{t("角色不会按名字自动匹配。没有合适的角色时，先在「自定义角色」里创建，再回来映射。")} <a href="#org-roles">{t("去创建角色")}</a></p>
        {imp.summary.unmapped_org_roles.map(txt => <div className="field" key={txt}><label>{txt}</label><select className="input" value={mapping.org_role_names?.[txt] || ""} onChange={e => setName("org_role_names", txt, e.target.value)}><option value="">{t("请选择…")}</option>{roles.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div>)}</fieldset>}
      <div className="org-actions"><button type="button" className="btn primary" disabled={busy || !workspaceId || (mapping.mode !== "lines" && mapping.columns?.email == null)} onClick={() => void doPlan()}>{t("生成预览")}</button></div>
    </div>}
    {/* ---- step 3/4: preview and results */}
    {imp && counts && <div className="col gap8" id="import-preview">
      <h3 className="small">{t(imp.status === "planned" ? "预览" : "结果")}</h3>
      {outcome && <p className="notice" role="status">{t("已发出邀请 {invited}，更新成员 {updated}，跳过 {skipped}，失败 {failed}，待处理 {pending}。", outcome)}</p>}
      {imp.summary?.over_limit ? <p className="notice warn">{t("文件超过单次上限，后 {n} 行未处理；请分批导入。", { n: imp.summary.over_limit })}</p> : null}
      {imp.status === "planned" && imp.summary?.reasons?.ocr_unchecked ? <p className="notice warn">{t("{n} 行是文字识别读出的：请逐行对照识别原文核对邮箱（可以直接改），点「核对无误」后才会导入；不核对的行可以跳过。", { n: imp.summary.reasons.ocr_unchecked })}</p> : null}
      <div className="row wrap gap4" role="tablist">{(["", "invite", "update", "unchanged", "skip", "blocked"] as const).map(a => <button key={a} type="button" role="tab" aria-selected={filter === a} className={`btn ${filter === a ? "primary" : ""}`} onClick={() => { setFilter(a); void loadRows(imp.id, a); }}>{a ? `${t(ACTION_LABEL[a])} ${counts[a] ?? 0}` : `${t("全部")} ${imp.summary?.rows ?? 0}`}</button>)}</div>
      {!rows ? <Loading /> : !rows.length ? <p className="muted">{t("没有这类行。")}</p> : <ul className="org-list">{rows.slice(0, 300).map(r => <li key={r.id}>
        <div><strong>{r.fields.email}</strong>{r.fields.name && <span className="muted"> · {r.fields.name}</span>}<div className="tiny muted">{sourceText(r.source)} · {t(WS_ROLE_LABEL[r.fields.workspace_role] || r.fields.workspace_role)}{r.fields.org_roles.length > 0 && <> · {r.fields.org_roles.join("、")}</>}</div>
          {r.diff && <div className="tiny">{r.diff.workspace_role && <span>{t("工作空间角色 {from} → {to}", { from: t(WS_ROLE_LABEL[r.diff.workspace_role.from] || r.diff.workspace_role.from), to: t(WS_ROLE_LABEL[r.diff.workspace_role.to] || r.diff.workspace_role.to) })} </span>}{r.diff.org_roles && <span>{t("新增组织角色：{roles}", { roles: r.diff.org_roles.add.join("、") })}</span>}</div>}
          {r.result && (r.result.error || r.result.message) && <div className="tiny flow-error">{resultText(r.result)}</div>}
          {r.fields.would && <div className="tiny muted">{t("核对后：{v}", { v: t(ACTION_LABEL[r.fields.would.action as ImportAction] || r.fields.would.action) })}</div>}
          {r.source.review_key && (imp.status === "planned" ? <RowReview key={`review-${String(r.source.review_key)}`} row={r} busy={busy} onSave={saveReview} where={sourceText(r.source)} />
            : r.source.checked === true && <div className="tiny muted">{t("已核对")}{r.source.read_email ? <> · {t("识别结果：{v}", { v: String(r.source.read_email) })}</> : null}</div>)}</div>
        <div className="org-actions"><span className={`badge ${r.action === "blocked" ? "danger" : ""}`}>{t(ACTION_LABEL[r.action])}{r.status !== "planned" && ` · ${t(r.status === "done" ? "完成" : r.status === "failed" ? "失败" : r.status === "skipped" ? "已跳过" : r.status === "running" ? "处理中" : "待处理")}`}</span>{r.reason && <span className="tiny muted">{t(REASON_LABEL[r.reason] || r.reason)}</span>}</div>
      </li>)}</ul>}
      {rows && rows.length > 300 && <p className="tiny muted">{t("只显示前 300 行；完整结果见报告。")}</p>}
      {imp.status === "planned" && rows && (() => {
        const pending = rows.slice(0, 300).filter(r => r.reason === "ocr_unchecked" && r.source.review_key);
        return pending.length > 1 ? <div className="org-actions"><button type="button" className="btn" disabled={busy}
          onClick={() => saveReviews(Object.fromEntries(pending.map(r => [String(r.source.review_key), { email: r.fields.email, name: r.fields.name, checked: true }])))}>
          {t("以上 {n} 行都已对照识别原文核对无误", { n: pending.length })}</button></div> : null;
      })()}
      <div className="org-actions">
        {imp.status === "planned" && <>{counts.blocked > 0 && <label className="check"><input type="checkbox" checked={onlyValid} onChange={e => setOnlyValid(e.target.checked)} /><span>{t("跳过 {n} 行阻断项，只导入有效行", { n: counts.blocked })}</span></label>}
          <button type="button" className="btn primary" disabled={busy || (counts.blocked > 0 && !onlyValid) || !(counts.invite + counts.update)} onClick={() => setConfirming(true)}>{t("确认导入（邀请 {i} 人，更新 {u} 人）", { i: counts.invite, u: counts.update })}</button></>}
        {imp.status === "running" && <button type="button" className="btn danger" disabled={busy || imp.cancel_requested} onClick={() => void doCancel()}>{t(imp.cancel_requested ? "正在取消…" : "取消剩余行")}</button>}
        {(imp.status === "partial" || imp.status === "failed") && <button type="button" className="btn" disabled={busy} onClick={() => void doRetry()}>{t("重试失败行")}</button>}
        {imp.status !== "extracted" && imp.status !== "planned" && <a className="btn" href={`${base}/${imp.id}/report.csv`}>{t("下载报告（CSV）")}</a>}
      </div>
      <p className="tiny muted">{t("取消只影响尚未处理的行；已发出的邀请要在「成员与邀请」里撤回。新人接受邀请后才加入并获得角色。")}</p>
    </div>}
    {confirming && imp && counts && <OrganizationAction spec={{ title: t("确认导入"), impact: t("将发出 {i} 封邀请邮件并更新 {u} 位成员的角色。邀请只有接受后才生效；已有成员不会被降级或移除。", { i: counts.invite, u: counts.update }),
      run: reason => post(`${base}/${imp.id}/confirm`, { version: imp.version, only_valid: onlyValid, reason }) }}
      onClose={() => setConfirming(false)} onDone={() => { setConfirming(false); void load(imp.id).then(x => loadRows(x.id, filter)); }} />}
    {history.length > 0 && <details className="small"><summary>{t("最近的导入（{n}）", { n: history.length })}</summary><ul className="org-list">{history.map(h => <li key={h.id}><div><strong>{h.filename}</strong><div className="tiny muted">{new Date(h.created_at * 1000).toLocaleString(locale)} · {t(h.status === "finished" ? "已完成" : h.status === "partial" ? "部分完成" : h.status === "failed" ? "失败" : h.status === "cancelled" ? "已取消" : h.status === "running" ? "执行中" : h.status === "planned" ? "已生成预览" : "已读取")}</div></div><div className="org-actions"><button type="button" className="btn" disabled={busy} onClick={() => void load(h.id).then(x => loadRows(x.id, "")).catch(e => setError(e))}>{t("打开")}</button></div></li>)}</ul></details>}
  </section>;
}
