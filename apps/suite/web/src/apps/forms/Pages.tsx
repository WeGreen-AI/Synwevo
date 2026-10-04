/** Synwevo Forms' list and detail pages: the form library, fill-in requests and submissions. */
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Modal, useDialog, useI18n, useToast, formatDate } from "@synwevo/ui";
import { op, newKey, read } from "../../shared/ops";
import { Empty, Failure, Loading } from "../../shared/states";
import { failureText, requestHeading, requestStatus, submissionStatus } from "./text";
import { NewRequest, CopyField } from "./Share";
import { fmtBytes } from "./Renderer";
import { formatAnswer } from "./answers";

type WS = { id: string; name: string; role: string };

function when(ts: number | null | undefined) {
  return ts ? formatDate(ts * 1000, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

function useOp<T>(fn: (signal: AbortSignal) => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    fn(abort.signal).then(setData).catch((e) => { if ((e as any)?.name !== "AbortError") setError(e); });
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: useCallback(() => setTick((n) => n + 1), []) };
}

// ------------------------------------------------------------------------ library
interface FormRow { id: string; title: string; description: string; status: string; published_version: number | null;
                    public: boolean; requests: number; submissions: number; updated_at: number }

export function Library({ ws, canWrite }: { ws: WS; canWrite: boolean }) {
  const { t } = useI18n();
  const dialog = useDialog();
  const toast = useToast();
  const navigate = useNavigate();
  const [status, setStatus] = useState<"active" | "archived">("active");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => { const h = window.setTimeout(() => setQuery(q.trim()), 250); return () => window.clearTimeout(h); }, [q]);
  const { data, error, reload } = useOp((s) => read<{ forms: FormRow[] }>("forms.form.list", { status, q: query || undefined }, ws.id, s), [ws.id, status, query]);

  async function create() {
    const title = await dialog.prompt(t("新建表单"), t("表单名称"), "", t("创建"));
    if (!title?.trim()) return;
    try {
      const out = await op<{ id: string }>("forms.form.create", { title: title.trim().slice(0, 200) }, { workspace: ws.id, key: newKey() });
      navigate(`/forms/w/${ws.id}/f/${out.output.id}`);
    } catch (e) { toast(failureText(t, e), "err"); }
  }

  return <>
    <div className="suite-head">
      <div><h1>{t("表单")}</h1><p className="lede">{t("设计表单并发布版本，向指定的人发出填写请求，或开放公开链接收集资料。")}</p></div>
      <div className="actions">{canWrite && <button type="button" className="btn primary" onClick={() => void create()}>{t("新建表单")}</button>}</div>
    </div>
    <div className="suite-toolbar">
      <div className="seg" role="tablist" aria-label={t("表单状态")}>
        <button type="button" role="tab" aria-selected={status === "active"} className={status === "active" ? "on" : ""} onClick={() => setStatus("active")}>{t("使用中")}</button>
        <button type="button" role="tab" aria-selected={status === "archived"} className={status === "archived" ? "on" : ""} onClick={() => setStatus("archived")}>{t("已归档")}</button>
      </div>
      <input className="input sm suite-search" type="search" value={q} placeholder={t("搜索表单名称")} aria-label={t("搜索表单名称")} onChange={(e) => setQ(e.target.value)} />
    </div>
    {error ? <Failure error={error} onRetry={reload} text={failureText(t, error)} /> : !data ? <Loading /> :
     data.forms.length === 0 ? <Empty title={query ? t("没有找到匹配的表单") : status === "archived" ? t("没有归档的表单") : t("还没有表单")}
                                     action={canWrite && !query && status === "active" ? <button type="button" className="btn primary" onClick={() => void create()}>{t("新建表单")}</button> : undefined}>
       {!query && status === "active" && <p>{t("表单可以用在多个流程里：每一次填写请求都有独立的编号、数据与结果。")}</p>}
     </Empty> :
     <div className="suite-list">{data.forms.map((f) => <Link key={f.id} className="suite-row" to={`/forms/w/${ws.id}/f/${f.id}`}>
       <div className="grow">
         <div className="title">{f.title}</div>
         <div className="sub">{f.description || t("更新于 {time}", { time: when(f.updated_at) })}</div>
       </div>
       {f.public && <span className="badge info">{t("公开链接")}</span>}
       <span className={`badge ${f.published_version ? "ok" : ""}`}>{f.published_version ? t("版本 {v}", { v: f.published_version }) : t("草稿")}</span>
       <span className="muted small nowrap">{t("{r} 个请求 · {s} 份提交", { r: f.requests, s: f.submissions })}</span>
     </Link>)}</div>}
  </>;
}

// ------------------------------------------------------------------------ requests
interface RequestRow { id: string; kind: "personal" | "public"; form_id: string; form_title: string; title: string; status: string; due_at: number | null;
                       recipient: { email: string | null; name: string | null } | null; created_at: number;
                       url: string | null; submission_policy: string; require_sign_in: boolean; form_version: number;
                       prefill: Record<string, any>; source: any; closed_reason: string | null }

export function Requests({ ws, canWrite }: { ws: WS; canWrite: boolean }) {
  const { t } = useI18n();
  const [status, setStatus] = useState<string>("");
  const [creating, setCreating] = useState(false);
  const { data, error, reload } = useOp((s) => read<{ requests: RequestRow[]; next_before: number | null }>(
    "forms.request.list", { status: status || undefined, limit: 100 }, ws.id, s), [ws.id, status]);
  return <>
    <div className="suite-head">
      <div><h1>{t("填写请求")}</h1><p className="lede">{t("每一份请求发给一个人，固定使用一个表单版本，有自己的状态与提交记录。")}</p></div>
      <div className="actions">{canWrite && <button type="button" className="btn primary" onClick={() => setCreating(true)}>{t("发出填写请求")}</button>}</div>
    </div>
    <div className="suite-toolbar">
      <label className="suite-filter"><span className="sr-only">{t("按状态筛选")}</span>
        <select className="input sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("按状态筛选")}>
          <option value="">{t("全部状态")}</option>
          {["open", "submitted", "changes_requested", "accepted", "expired", "closed", "revoked"].map((s) => <option key={s} value={s}>{requestStatus(t, s)[0]}</option>)}
        </select></label>
    </div>
    {error ? <Failure error={error} onRetry={reload} text={failureText(t, error)} /> : !data ? <Loading /> :
     data.requests.length === 0 ? <Empty title={t("还没有填写请求")}>{t("在表单的“分享与填写请求”里，或点击右上角发出请求。")}</Empty> :
     <div className="suite-list">{data.requests.map((r) => {
       const [label, tone] = requestStatus(t, r.status);
       return <Link key={r.id} className="suite-row" to={`/forms/w/${ws.id}/r/${r.id}`}>
         <div className="grow">
           <div className="title">{r.title || r.form_title}</div>
           <div className="sub">{[r.recipient?.name, r.recipient?.email].filter(Boolean).join(" · ") || t("未指定收件人")} · {t("创建于 {time}", { time: when(r.created_at) })}</div>
         </div>
         {r.due_at && <span className="muted small nowrap">{t("截止 {time}", { time: when(r.due_at) })}</span>}
         <span className={`badge ${tone}`}>{label}</span>
       </Link>;
     })}</div>}
    {creating && <PickFormToRequest ws={ws} onClose={() => { setCreating(false); reload(); }} />}
  </>;
}

function PickFormToRequest({ ws, onClose }: { ws: WS; onClose: () => void }) {
  const { t } = useI18n();
  const [formId, setFormId] = useState("");
  const { data } = useOp((s) => read<{ forms: FormRow[] }>("forms.form.list", { status: "active" }, ws.id, s), [ws.id]);
  const published = (data?.forms ?? []).filter((f) => f.published_version);
  return <Modal title={t("发出填写请求")} onClose={onClose} size="wide">
    <label className="fm-prop"><span className="fm-label small">{t("表单")}</span>
      <select className="input" value={formId} onChange={(e) => setFormId(e.target.value)}>
        <option value="">{t("选择已发布的表单…")}</option>
        {published.map((f) => <option key={f.id} value={f.id}>{f.title}</option>)}</select></label>
    {data && !published.length && <p className="muted">{t("还没有已发布的表单。先在表单库里发布一个表单。")}</p>}
    {formId && <NewRequest key={formId} formId={formId} ws={ws} />}
  </Modal>;
}

export function RequestDetail({ ws, canWrite }: { ws: WS; canWrite: boolean }) {
  const { t } = useI18n();
  const dialog = useDialog();
  const toast = useToast();
  const { rid = "" } = useParams();
  const { data, error, reload } = useOp((s) => read<RequestRow & { submissions: any[] }>("forms.request.get", { request_id: rid }, ws.id, s), [ws.id, rid]);
  if (error) return <Failure error={error} onRetry={reload} text={failureText(t, error)} />;
  if (!data) return <Loading />;
  const [label, tone] = requestStatus(t, data.status);
  const active = !["closed", "revoked", "accepted"].includes(data.status);
  // The public link's request is the form's public fill-in: it changes in the share settings.
  const isPublic = data.kind === "public";

  async function act(name: string, input: Record<string, unknown>, confirm?: [string, string, string, boolean?]) {
    if (confirm && !(await dialog.confirm(confirm[0], confirm[1], confirm[2], confirm[3]))) return;
    try { await op(name, input, { workspace: ws.id }); reload(); }
    catch (e) { toast(failureText(t, e), "err"); }
  }
  async function extend() {
    const v = await dialog.prompt(t("修改截止时间"), t("新的截止时间（YYYY-MM-DD HH:MM，留空表示不限）"),
                                  data!.due_at ? new Date(data!.due_at * 1000).toISOString().slice(0, 16).replace("T", " ") : "", t("保存"));
    if (v === null) return;
    const due = v.trim() ? new Date(v.trim().replace(" ", "T")).getTime() / 1000 : null;
    if (due !== null && Number.isNaN(due)) { toast(t("时间格式不正确"), "err"); return; }
    await act("forms.request.extend", { request_id: rid, due_at: due });
  }

  return <>
    <div className="suite-head">
      <div>
        <p className="muted small"><Link to={`/forms/w/${ws.id}/requests`}>← {t("填写请求")}</Link></p>
        <h1>{requestHeading(t, data)}</h1>
        <p className="lede"><span className={`badge ${tone}`}>{label}</span> · {t("表单版本 {v}", { v: data.form_version })}
          {data.source?.via && data.source.via !== "ui" ? ` · ${viaText(t, data.source.via)}` : ""}</p>
      </div>
      {canWrite && isPublic && <div className="actions">
        <Link className="btn sm" to={`/forms/w/${ws.id}/f/${data.form_id}?panel=share`}>{t("在分享设置中管理")}</Link>
      </div>}
      {canWrite && active && !isPublic && <div className="actions">
        <button type="button" className="btn sm" onClick={() => void extend()}>{t("修改截止时间")}</button>
        <button type="button" className="btn sm" onClick={() => void act("forms.request.reissue_link", { request_id: rid },
          [t("重新生成链接"), t("旧链接会立即失效。确定生成新链接？"), t("生成")])}>{t("重新生成链接")}</button>
        <button type="button" className="btn sm" onClick={() => void act("forms.request.close", { request_id: rid, mode: "close" },
          [t("关闭请求"), t("关闭后不再接收填写，已提交的内容保留。"), t("关闭请求")])}>{t("关闭")}</button>
        <button type="button" className="btn sm danger" onClick={() => void act("forms.request.close", { request_id: rid, mode: "revoke" },
          [t("撤销请求"), t("撤销后链接立即失效，打开链接的人会看到“已撤销”。已提交的内容保留。"), t("撤销"), true])}>{t("撤销")}</button>
      </div>}
    </div>
    <div className="suite-cols">
      <section className="card"><div className="card-b">
        <dl className="suite-kv">
          <dt>{t("收件人")}</dt><dd>{isPublic ? t("任何持有公开链接的人") : [data.recipient?.name, data.recipient?.email].filter(Boolean).join(" · ") || t("未指定")}</dd>
          <dt>{t("截止时间")}</dt><dd>{data.due_at ? when(data.due_at) : t("不限")}</dd>
          <dt>{t("提交次数")}</dt><dd>{data.submission_policy === "once" ? t("只能提交一次") : t("可以提交多次")}</dd>
          <dt>{t("登录要求")}</dt><dd>{data.require_sign_in ? t("仅收件人登录后可填写") : t("持有链接即可填写")}</dd>
          <dt>{t("创建时间")}</dt><dd>{when(data.created_at)}</dd>
          {data.closed_reason && <><dt>{t("结束原因")}</dt><dd>{data.closed_reason}</dd></>}
        </dl>
        {data.url && active && <><p className="fm-label small">{t("填写链接")}</p><CopyField value={data.url} label={t("填写链接")} /></>}
        {isPublic && <p className="muted small">{t("公开链接在表单的“分享与填写请求”里管理：在那里复制、更换或关闭。")}</p>}
        {Object.keys(data.prefill || {}).length > 0 && <p className="muted small">{t("包含 {n} 项预填内容", { n: Object.keys(data.prefill).length })}</p>}
      </div></section>
      <section>
        <h2 className="suite-h2">{t("提交记录")}</h2>
        {data.submissions.length === 0 ? <Empty title={t("还没有提交")} /> :
         <div className="suite-list">{data.submissions.map((s) => {
           const [sl, st] = submissionStatus(t, s.status);
           return <Link key={s.id} className="suite-row" to={`/forms/w/${ws.id}/s/${s.id}`}>
             <div className="grow"><div className="title">{s.submitter_label || t("匿名填写人")}</div>
               <div className="sub">{when(s.created_at)}{s.revision > 1 ? ` · ${t("第 {n} 次修订", { n: s.revision })}` : ""}</div></div>
             <span className={`badge ${st}`}>{sl}</span>
           </Link>;
         })}</div>}
      </section>
    </div>
  </>;
}

function viaText(t: (k: string) => string, via: string) {
  return via === "agent" ? t("由个人 Agent 发出") : via === "flow" ? t("由自动流程发出") : via === "automation" ? t("由个人自动化发出") : t("通过接口发出");
}

// ------------------------------------------------------------------------ submissions
export function Submissions({ ws }: { ws: WS }) {
  const { t } = useI18n();
  const [status, setStatus] = useState("");
  const { data, error, reload } = useOp((s) => read<{ submissions: any[] }>("forms.submission.list",
    { status: status || undefined, limit: 100 }, ws.id, s), [ws.id, status]);
  return <>
    <div className="suite-head"><div><h1>{t("提交记录")}</h1><p className="lede">{t("所有表单收到的提交，最新的在前。")}</p></div></div>
    <div className="suite-toolbar">
      <select className="input sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("按状态筛选")}>
        <option value="">{t("全部状态")}</option>
        {["submitted", "changes_requested", "accepted"].map((s) => <option key={s} value={s}>{submissionStatus(t, s)[0]}</option>)}
      </select>
    </div>
    {error ? <Failure error={error} onRetry={reload} text={failureText(t, error)} /> : !data ? <Loading /> :
     data.submissions.length === 0 ? <Empty title={t("还没有提交")} /> :
     <div className="suite-list">{data.submissions.map((s) => {
       const [sl, st] = submissionStatus(t, s.status);
       return <Link key={s.id} className="suite-row" to={`/forms/w/${ws.id}/s/${s.id}`}>
         <div className="grow"><div className="title">{s.submitter_label || t("匿名填写人")}</div>
           <div className="sub">{when(s.created_at)} · {t("版本 {v}", { v: s.form_version })}{s.revision > 1 ? ` · ${t("第 {n} 次修订", { n: s.revision })}` : ""}</div></div>
         <span className={`badge ${st}`}>{sl}</span>
       </Link>;
     })}</div>}
  </>;
}

export function SubmissionDetail({ ws, canWrite }: { ws: WS; canWrite: boolean }) {
  const { t } = useI18n();
  const toast = useToast();
  const { sid = "" } = useParams();
  const [asking, setAsking] = useState(false);
  const { data, error, reload } = useOp((s) => read<any>("forms.submission.get", { submission_id: sid }, ws.id, s), [ws.id, sid]);
  if (error) return <Failure error={error} onRetry={reload} text={failureText(t, error)} />;
  if (!data) return <Loading />;
  const [sl, st] = submissionStatus(t, data.status);
  const byField: Record<string, any[]> = {};
  for (const f of data.files) (byField[f.field_id] ??= []).push(f);

  async function accept() {
    try { await op("forms.submission.accept", { submission_id: sid }, { workspace: ws.id, key: newKey() }); reload(); }
    catch (e) { toast(failureText(t, e), "err"); }
  }

  return <>
    <div className="suite-head">
      <div>
        <p className="muted small"><Link to={`/forms/w/${ws.id}/r/${data.request_id}`}>← {t("填写请求")}</Link></p>
        <h1>{data.submitter_label || t("匿名填写人")}</h1>
        <p className="lede"><span className={`badge ${st}`}>{sl}</span> · {when(data.created_at)} · {t("版本 {v}", { v: data.form_version })}
          {data.revision > 1 ? ` · ${t("第 {n} 次修订", { n: data.revision })}` : ""}</p>
      </div>
      {canWrite && data.status === "submitted" && <div className="actions">
        <button type="button" className="btn" onClick={() => setAsking(true)}>{t("要求补充或修改")}</button>
        <button type="button" className="btn primary" onClick={() => void accept()}>{t("确认")}</button>
      </div>}
    </div>
    <section className="card"><div className="card-b">
      <dl className="suite-kv fm-answers">
        {data.fields.filter((f: any) => f.type !== "section").map((f: any) => <FieldAnswer key={f.id} f={f} value={data.answers[f.id]} files={byField[f.id] ?? []} />)}
      </dl>
    </div></section>
    {(data.review_items.length > 0 || data.review_note) && <section className="card mt8"><div className="card-b">
      <h2 className="suite-h2">{t("补充要求")}</h2>
      {data.review_note && <p>{data.review_note}</p>}
      <ul>{data.review_items.map((it: any, i: number) => <li key={i}>{it.field_id ? `${data.fields.find((f: any) => f.id === it.field_id)?.label ?? it.field_id}：` : ""}{it.note}</li>)}</ul>
    </div></section>}
    {data.history.length > 1 && <section className="mt8"><h2 className="suite-h2">{t("修订历史")}</h2>
      <ol className="fm-history">{data.history.map((h: any) => <li key={h.id}>
        {h.id === data.id ? <strong>{t("第 {n} 次修订", { n: h.revision })}</strong> : <Link to={`/forms/w/${ws.id}/s/${h.id}`}>{t("第 {n} 次修订", { n: h.revision })}</Link>}
        {" · "}{submissionStatus(t, h.status)[0]} · {when(h.created_at)}</li>)}</ol></section>}
    {asking && <RequestChanges ws={ws} data={data} onClose={() => setAsking(false)} onDone={() => { setAsking(false); reload(); }} />}
  </>;
}

function FieldAnswer({ f, value, files }: { f: any; value: any; files: any[] }) {
  const { t } = useI18n();
  let shown: ReactNode = <span className="muted">{t("（未填写或未显示）")}</span>;
  if (f.type === "file") {
    shown = files.length ? <ul className="fm-file-list">{files.map((x) => <li key={x.id}>
      <a href={x.url}>{x.filename}</a> <span className="muted small">{fmtBytes(x.size)}</span>{" "}
      {x.scan === "clean" ? <span className="badge ok">{t("已通过安全检查")}</span> : x.scan === "not_configured" ? <span className="badge">{t("未进行病毒扫描")}</span> : null}
    </li>)}</ul> : shown;
  } else if (value !== undefined && value !== null && value !== "") {
    shown = formatAnswer(t, f, value);
  }
  return <><dt>{f.label}</dt><dd>{shown}</dd></>;
}

function RequestChanges({ ws, data, onClose, onDone }: { ws: WS; data: any; onClose: () => void; onDone: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [items, setItems] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const fields = data.fields.filter((f: any) => f.type !== "section");
  async function send() {
    const perField = Object.entries(items).filter(([, v]) => v.trim()).map(([field_id, v]) => ({ field_id, note: v.trim() }));
    if (!perField.length && !note.trim()) { toast(t("请至少写一条需要补充或修改的内容"), "err"); return; }
    setBusy(true);
    try {
      // With no per-field notes the overall note is the one item (the operation needs at least one).
      await op("forms.submission.request_changes", perField.length
        ? { submission_id: data.id, note: note.trim(), items: perField }
        : { submission_id: data.id, note: "", items: [{ note: note.trim() }] }, { workspace: ws.id, key: newKey() });
      onDone();
    } catch (e) { toast(failureText(t, e), "err"); } finally { setBusy(false); }
  }
  return <Modal title={t("要求补充或修改")} onClose={onClose} size="wide" footer={<>
    <button type="button" className="btn" onClick={onClose}>{t("取消")}</button>
    <button type="button" className="btn primary" disabled={busy} onClick={() => void send()}>{t("发送")}</button></>}>
    <p className="muted small">{t("填写人会看到你的说明，链接重新开放，修改后作为新的修订提交；原来的提交会保留。")}</p>
    <label className="fm-prop"><span className="fm-label small">{t("总体说明")}</span>
      <textarea className="input" rows={2} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} /></label>
    <p className="fm-label small">{t("逐项说明（只填需要修改的字段）")}</p>
    {fields.map((f: any) => <label key={f.id} className="fm-prop"><span className="small">{f.label}</span>
      <input className="input sm" value={items[f.id] ?? ""} maxLength={1000} onChange={(e) => setItems((m) => ({ ...m, [f.id]: e.target.value }))} /></label>)}
  </Modal>;
}
