/** Filling in a form through its link — for anyone holding the link, signed in or not.
 *
 *  What the page promises and keeps: answers are saved as a draft a moment after each change
 *  (and kept in this browser when the connection drops, then saved when it returns); a draft is
 *  not a submission; submitting twice (a double click, a retry after a dropped connection) sends
 *  one submission, because the attempt's key survives a reload; the server checks everything
 *  again and its errors land on the fields they are about. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError, signInUrl, useSession } from "@synwevo/auth-client";
import { Brand, LanguageMenu, useI18n, formatDate } from "@synwevo/ui";
import { checkAnswers, visibleFields, type Definition, type FieldError } from "./logic";
import { Renderer, type UploadedFile } from "./Renderer";
import { Failure, Loading } from "../../shared/states";
import { failureText } from "./text";

interface Loaded {
  request: { id: string | null; kind: "personal" | "public"; title: string; status: string; due_at: number | null;
             submission_policy: "once" | "multiple"; require_sign_in: boolean };
  form: { title: string; description: string };
  closed?: { code: string; message: string };
  version?: number; definition?: Definition; prefill?: Record<string, any>;
  draft?: { answers: Record<string, any>; step: number; rev: number; updated_at: number } | null;
  review?: { submission_id: string; note: string | null; items: { field_id: string | null; note: string }[];
             previous_answers: Record<string, any> } | null;
  files?: UploadedFile[]; submissions?: { id: string; revision: number; status: string; created_at: number }[];
  submitted?: boolean;
}

const FILLER_KEY = "synwevo.forms.filler";

function fillerKey(): string {
  try {
    let k = localStorage.getItem(FILLER_KEY);
    if (!k || !/^[A-Za-z0-9_-]{24,100}$/.test(k)) {
      const b = new Uint8Array(24);
      crypto.getRandomValues(b);
      k = Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
      localStorage.setItem(FILLER_KEY, k);
    }
    return k;
  } catch {
    return "session-" + Array.from(crypto.getRandomValues(new Uint8Array(16)), (x) => x.toString(16).padStart(2, "0")).join("");
  }
}

type SaveState = "idle" | "saving" | "saved" | "offline" | "conflict" | "error";

export default function Fill() {
  const { t } = useI18n();
  const { token = "" } = useParams();
  const { state } = useSession();
  const filler = useMemo(fillerKey, []);
  const headers = useMemo(() => ({ "X-Synwevo-Filler": filler }), [filler]);
  const base = `/api/suite/public/forms/r/${encodeURIComponent(token)}`;
  const localKey = `synwevo.forms.draft.${token.slice(0, 12)}`;
  const attemptKey = `synwevo.forms.attempt.${token.slice(0, 12)}`;

  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [answers, setAnswers] = useState<Record<string, any>>({});
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [step, setStep] = useState(0);
  const [save, setSave] = useState<SaveState>("idle");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<{ submission_id: string; revision: number; confirmation: string } | null>(null);
  const rev = useRef(0);
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    setError(null);
    try {
      const out = await api<Loaded>(base, { headers });
      setData(out);
      rev.current = out.draft?.rev ?? 0;
      let start = { ...(out.prefill ?? {}), ...(out.review?.previous_answers ?? {}), ...(out.draft?.answers ?? {}) };
      try {
        // A draft kept in this browser while offline wins over the server's older copy.
        const local = JSON.parse(localStorage.getItem(localKey) || "null");
        if (local && local.answers && (!out.draft || local.at > out.draft.updated_at * 1000)) {
          start = { ...start, ...local.answers };
          dirty.current = true;
        }
      } catch { /* storage unavailable: the server draft is the draft */ }
      setAnswers(start);
      setFiles(out.files ?? []);
      setStep(out.draft?.step ?? 0);
    } catch (e) {
      setError(e);
    }
  }, [base, headers, localKey]);

  useEffect(() => { void load(); }, [load]);

  const persist = useCallback(async (next: Record<string, any>, nextStep: number) => {
    setSave("saving");
    try {
      const out = await api<{ rev: number; saved_at: number }>(base + "/draft", {
        method: "PUT", headers, body: { answers: next, step: nextStep, rev: rev.current } });
      rev.current = out.rev;
      dirty.current = false;
      setSave("saved");
      setSavedAt(out.saved_at * 1000);
      try { localStorage.removeItem(localKey); } catch { /* nothing to clear */ }
    } catch (e) {
      if (e instanceof ApiError && e.code === "draft_conflict") setSave("conflict");
      else if (e instanceof ApiError && e.status === 0) {
        setSave("offline");
        try { localStorage.setItem(localKey, JSON.stringify({ answers: next, at: Date.now() })); } catch { /* best effort */ }
      } else setSave("error");
    }
  }, [base, headers, localKey]);

  function change(id: string, value: any) {
    setAnswers((prev) => {
      const next = { ...prev, [id]: value };
      dirty.current = true;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void persist(next, step), 1200);
      return next;
    });
    setErrors((prev) => prev.filter((e) => e.field !== id && e.parent !== id));
  }

  // Retry an offline save when the connection returns.
  useEffect(() => {
    const online = () => { if (dirty.current) void persist(answers, step); };
    window.addEventListener("online", online);
    return () => window.removeEventListener("online", online);
  }, [answers, step, persist]);

  // Leaving with unsaved answers: say so.
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty.current && !receipt) { e.preventDefault(); } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [receipt]);

  async function upload(fieldId: string, file: File) {
    const fd = new FormData();
    fd.append("field_id", fieldId);
    fd.append("file", file);
    try {
      const res = await fetch(base + "/files", { method: "POST", body: fd, headers: { ...headers, ...csrfHeader() } });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new ApiError(res.status, body.error || "upload_failed", body.message || "", body.detail);
      setFiles((prev) => [...prev, body]);
      const cur = Array.isArray(answers[fieldId]) ? answers[fieldId] : [];
      change(fieldId, [...cur, body.id]);
    } catch (e) {
      setErrors((prev) => [...prev.filter((x) => x.field !== fieldId),
                           { field: fieldId, code: uploadCode(e), params: e instanceof ApiError ? e.detail ?? undefined : undefined }]);
    }
  }

  async function removeFile(fileId: string) {
    await api(base + `/files/${encodeURIComponent(fileId)}`, { method: "DELETE", headers }).catch(() => undefined);
    setFiles((prev) => prev.filter((f) => f.id !== fileId));
  }

  const defn = data?.definition;
  const steps = defn?.steps?.length ? defn.steps : null;
  const visible = defn ? visibleFields(defn, answers) : new Set<string>();
  const stepFields = steps ? steps[Math.min(step, steps.length - 1)].fields.filter((f) => visible.has(f)) : undefined;
  const fileMap = Object.fromEntries(files.map((f) => [f.id, f]));
  const notes: Record<string, string[]> = {};
  for (const it of data?.review?.items ?? []) if (it.field_id) (notes[it.field_id] ??= []).push(it.note);

  function focusFirstError(errs: FieldError[]) {
    const first = errs[0];
    if (!first) return;
    const el = document.querySelector(`[data-field="${first.parent ?? first.field}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center" });
    (el?.querySelector("input,textarea,select,button") as HTMLElement | null)?.focus();
  }

  function goto(next: number) {
    if (!steps || !defn) return;
    if (next > step) {
      const errs = checkAnswers(defn, answers, fileMap).filter((e) => stepFields?.includes(e.parent ?? e.field));
      setErrors(errs);
      if (errs.length) { focusFirstError(errs); return; }
    }
    setStep(next);
    void persist(answers, next);
    window.scrollTo({ top: 0 });
  }

  async function submit() {
    if (!defn || !data?.version) return;
    const errs = checkAnswers(defn, answers, fileMap);
    setErrors(errs);
    if (errs.length) {
      if (steps) {
        const where = steps.findIndex((s) => s.fields.includes(errs[0].parent ?? errs[0].field));
        if (where >= 0 && where !== step) setStep(where);
      }
      setTimeout(() => focusFirstError(errs), 0);
      return;
    }
    let key = "";
    try { key = sessionStorage.getItem(attemptKey) || ""; } catch { /* no storage */ }
    if (!key) {
      key = `sub-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
      try { sessionStorage.setItem(attemptKey, key); } catch { /* one attempt per page then */ }
    }
    setSubmitting(true);
    window.clearTimeout(timer.current);
    try {
      const out = await api<{ submission_id: string; revision: number; confirmation: string }>(base + "/submit", {
        method: "POST", headers, body: { answers, version: data.version, idempotency_key: key } });
      dirty.current = false;
      try { sessionStorage.removeItem(attemptKey); localStorage.removeItem(localKey); } catch { /* done anyway */ }
      setReceipt(out);
      window.scrollTo({ top: 0 });
    } catch (e) {
      if (e instanceof ApiError && e.code === "answers_invalid" && Array.isArray(e.detail?.errors)) {
        setErrors(e.detail.errors);
        focusFirstError(e.detail.errors);
      } else {
        setError(e);
      }
      if (e instanceof ApiError && e.status !== 0 && e.code !== "answers_invalid") {
        try { sessionStorage.removeItem(attemptKey); } catch { /* next attempt gets a new key */ }
      }
    } finally {
      setSubmitting(false);
    }
  }

  const due = data?.request.due_at ? formatDate(data.request.due_at * 1000, { dateStyle: "medium", timeStyle: "short" }) : null;

  return <div className="fm-fill-page">
    <header className="fm-fill-top">
      <Brand href="/" product="Forms" markBelow={480} />
      <span className="spacer" />
      <LanguageMenu />
    </header>
    <main className="fm-fill" id="main">
      {error && !data ? <FillError error={error} state={state} onRetry={load} /> :
       !data ? <Loading /> :
       data.closed ? <div className="fm-fill-card"><h1>{data.request.title}</h1>
         <p className="fm-closed" role="status">{closedText(t, data.closed.code)}</p></div> :
       receipt ? <div className="fm-fill-card" role="status">
         <h1>{t("已提交")}</h1>
         <p>{receipt.confirmation || t("你的回答已经送达。")}</p>
         <p className="muted small">{t("回执编号：{id}", { id: receipt.submission_id })}{receipt.revision > 1 ? ` · ${t("第 {n} 次修订", { n: receipt.revision })}` : ""}</p>
         {data.request.submission_policy === "multiple" && <button type="button" className="btn" onClick={() => { setReceipt(null); setAnswers({}); setFiles([]); setStep(0); void load(); }}>{t("再填一份")}</button>}
       </div> :
       data.submitted ? <div className="fm-fill-card" role="status">
         <h1>{data.request.title}</h1><p>{t("这份表单你已经提交过了。发出请求的人确认或要求补充后，你会看到相应状态。")}</p>
       </div> :
       <form className="fm-fill-card" noValidate onSubmit={(e) => { e.preventDefault(); if (steps && step < steps.length - 1) goto(step + 1); else void submit(); }}>
         <h1>{data.request.title || data.form.title}</h1>
         {data.form.description && <p className="fm-desc">{data.form.description}</p>}
         <p className="muted small">{due ? t("截止：{due}", { due }) : null}</p>
         {data.review && <div className="notice warn" role="note">
           <strong>{t("需要补充或修改")}</strong>
           {data.review.note && <p>{data.review.note}</p>}
           {data.review.items.filter((i) => !i.field_id).map((i, n) => <p key={n}>{i.note}</p>)}
           <p className="small">{t("已带入你上次提交的内容，修改后重新提交。")}</p>
         </div>}
         {steps && <ol className="fm-steps" aria-label={t("填写步骤")}>
           {steps.map((s, i) => <li key={s.id} className={i === step ? "cur" : i < step ? "done" : ""} aria-current={i === step ? "step" : undefined}>
             <span className="n">{i + 1}</span><span className="t">{s.title}</span></li>)}
         </ol>}
         {defn && <Renderer definition={defn} answers={answers} onChange={change} errors={errors} files={files}
                            onUpload={upload} onRemoveFile={removeFile} only={stepFields} notes={notes} />}
         {errors.length > 0 && <p className="fm-err" role="alert">{t("还有 {n} 处需要填写或更正。", { n: errors.length })}</p>}
         {error ? <Failure error={error} text={failureText(t, error)} /> : null}
         <div className="fm-actions">
           <SaveIndicator state={save} at={savedAt} />
           <span className="spacer" />
           {steps && step > 0 && <button type="button" className="btn" onClick={() => goto(step - 1)}>{t("上一步")}</button>}
           {steps && step < steps.length - 1
             ? <button type="submit" className="btn primary">{t("下一步")}</button>
             : <button type="submit" className="btn primary" disabled={submitting}>{submitting ? t("提交中…") : t("提交")}</button>}
         </div>
       </form>}
    </main>
  </div>;
}

function SaveIndicator({ state, at }: { state: SaveState; at: number | null }) {
  const { t } = useI18n();
  const text = state === "saving" ? t("正在保存草稿…")
    : state === "saved" && at ? t("草稿已保存 {time}", { time: formatDate(at, { timeStyle: "short" }) })
    : state === "offline" ? t("网络已断开，草稿暂存在本机，恢复连接后自动保存")
    : state === "conflict" ? t("草稿已在其他页面更新，请刷新后继续")
    : state === "error" ? t("草稿保存失败，稍后会再试")
    : "";
  return <span className={`fm-save ${state}`} role="status" aria-live="polite">{text}</span>;
}

function FillError({ error, state, onRetry }: { error: unknown; state: any; onRetry: () => void }) {
  const { t } = useI18n();
  if (error instanceof ApiError && error.code === "sign_in_required") {
    return <div className="fm-fill-card"><h1>{t("需要登录")}</h1>
      <p>{t("这份表单要求登录后填写。请用收到这份请求的邮箱对应的账户登录。")}</p>
      <a className="btn primary" href={signInUrl(state, window.location.pathname)}>{t("登录")}</a></div>;
  }
  if (error instanceof ApiError && (error.code === "link_not_found" || error.status === 404)) {
    return <div className="fm-fill-card"><h1>{t("链接无效")}</h1><p>{t("这个填写链接不存在或已失效。请向发出请求的人索取新的链接。")}</p></div>;
  }
  return <Failure error={error} onRetry={onRetry} text={failureText(t, error)} />;
}

function closedText(t: (k: string, p?: any) => string, code: string): string {
  switch (code) {
    case "link_expired": return t("这份表单已过截止时间，不能再填写。如需延期请联系发出请求的人。");
    case "link_revoked": return t("这个填写链接已被撤销。");
    case "request_closed": return t("这份表单已停止接收填写。");
    default: return t("这份表单当前不能填写。");
  }
}

/** The server's refusal of an upload, as a field error the page can word. */
function uploadCode(e: unknown): string {
  if (!(e instanceof ApiError)) return "upload_failed";
  const map: Record<string, string> = {
    file_too_large: "file_too_large", scan_limit: "file_too_large", file_type_not_accepted: "file_type",
    too_many_files: "too_many", empty_file: "file_empty", malware_detected: "file_malware", file_quarantined: "file_malware",
    encrypted_file: "file_encrypted", unchecked_file: "file_unchecked", scan_unavailable: "scan_unavailable",
    rate_limited: "rate_limited",
  };
  return map[e.code] ?? "upload_failed";
}

function csrfHeader(): Record<string, string> {
  const m = document.cookie.match(/(?:^|;\s*)dw_csrf=([^;]+)/);
  return m ? { "X-Synwevo-CSRF": decodeURIComponent(m[1]) } : {};
}
