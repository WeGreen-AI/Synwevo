/** Renders a form definition as inputs — the fill-in page, the editor's preview and the prefill
 *  editor all use this one component, so what an editor previews is what a filler gets. */
import { useId, useRef, useState, type ReactNode } from "react";
import { useI18n } from "@synwevo/ui";
import { evaluate, type Definition, type FieldDef, type FieldError } from "./logic";
import { errorText } from "./text";

export interface UploadedFile { id: string; field_id: string; filename: string; size: number; scan?: string }

export interface RendererProps {
  definition: Definition;
  answers: Record<string, any>;
  onChange?: (id: string, value: any) => void;
  errors?: FieldError[];
  files?: UploadedFile[];
  onUpload?: (fieldId: string, file: File) => Promise<void>;
  onRemoveFile?: (fileId: string) => Promise<void>;
  readOnly?: boolean;
  /** Only these top-level fields (a step); all when absent. */
  only?: string[];
  /** Per-field notes from a reviewer (changes requested). */
  notes?: Record<string, string[]>;
}

export function Renderer(p: RendererProps) {
  const seen: Record<string, any> = {};
  const items: ReactNode[] = [];
  for (const f of p.definition.fields) {
    const visible = evaluate(f.visible_if, seen);
    if (f.type !== "section" && visible) seen[f.id] = f.type === "boolean" ? p.answers[f.id] === true : p.answers[f.id];
    if (!visible || (p.only && !p.only.includes(f.id))) continue;
    items.push(<FieldView key={f.id} field={f} {...p} requiredNow={!!(f.required || (f.required_if && evaluate(f.required_if, seen)))} />);
  }
  return <div className="fm-fields">{items}</div>;
}

function fieldErrors(errors: FieldError[] | undefined, id: string, row?: number) {
  return (errors ?? []).filter((e) => e.field === id && (row === undefined ? e.row === undefined : e.row === row));
}

function FieldView({ field: f, answers, onChange, errors, files, onUpload, onRemoveFile, readOnly, requiredNow, notes }:
                   RendererProps & { field: FieldDef; requiredNow: boolean }) {
  const { t } = useI18n();
  const id = useId();
  const errs = fieldErrors(errors, f.id);
  if (f.type === "section") {
    return <div className="fm-section"><h2>{f.label}</h2>{f.help && <p className="muted">{f.help}</p>}</div>;
  }
  const describedBy = [f.help ? `${id}-help` : "", errs.length ? `${id}-err` : ""].filter(Boolean).join(" ") || undefined;
  const set = (v: any) => onChange?.(f.id, v);
  const fieldNotes = notes?.[f.id] ?? [];
  return <div className={`fm-field${errs.length ? " invalid" : ""}${fieldNotes.length ? " noted" : ""}`} data-field={f.id}>
    {f.type !== "boolean" && <label className="fm-label" htmlFor={f.type === "multiselect" || f.type === "repeat" || f.type === "file" ? undefined : id} id={`${id}-label`}>
      {f.label}{requiredNow && <span className="fm-req" aria-hidden="true"> *</span>}
      {requiredNow && <span className="sr-only">{t("（必填）")}</span>}
    </label>}
    {fieldNotes.map((n, i) => <p key={i} className="fm-note" role="note">{t("需要修改：{note}", { note: n })}</p>)}
    <Control field={f} id={id} value={answers[f.id]} set={set} readOnly={readOnly} describedBy={describedBy}
             requiredNow={requiredNow} labelId={`${id}-label`} errors={errors} files={files} onUpload={onUpload} onRemoveFile={onRemoveFile} />
    {f.help && <p className="fm-help" id={`${id}-help`}>{f.help}</p>}
    {errs.length > 0 && <p className="fm-err" id={`${id}-err`} role="alert">{errs.map((e) => errorText(t, e)).join(" ")}</p>}
  </div>;
}

function localOffsetIso(local: string): string {
  // "2026-09-29T10:00" typed in this browser → the same wall time with this browser's offset.
  if (!local) return "";
  const d = new Date(local);
  if (Number.isNaN(d.getTime())) return local;
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? "+" : "-";
  const hh = String(Math.floor(Math.abs(off) / 60)).padStart(2, "0"), mm = String(Math.abs(off) % 60).padStart(2, "0");
  return `${local.length === 16 ? local : local.slice(0, 16)}:00${sign}${hh}:${mm}`;
}

function isoToLocal(iso: any): string {
  if (typeof iso !== "string" || !iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function Control({ field: f, id, value, set, readOnly, describedBy, requiredNow, labelId, errors, files, onUpload, onRemoveFile }: {
  field: FieldDef; id: string; value: any; set: (v: any) => void; readOnly?: boolean; describedBy?: string;
  requiredNow: boolean; labelId: string; errors?: FieldError[]; files?: UploadedFile[];
  onUpload?: (fieldId: string, file: File) => Promise<void>; onRemoveFile?: (fileId: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const common = { id, disabled: readOnly, "aria-describedby": describedBy, "aria-required": requiredNow || undefined,
                   "aria-invalid": fieldErrors(errors, f.id).length > 0 || undefined } as const;
  switch (f.type) {
    case "text":
      return <input className="input" type={f.format === "email" ? "email" : f.format === "url" ? "url" : f.format === "phone" ? "tel" : "text"}
                    value={value ?? ""} placeholder={f.placeholder} maxLength={f.max_length ?? 500}
                    autoComplete={f.format === "email" ? "email" : f.format === "phone" ? "tel" : "off"}
                    onChange={(e) => set(e.target.value)} {...common} />;
    case "textarea":
      return <textarea className="input fm-textarea" value={value ?? ""} placeholder={f.placeholder} maxLength={f.max_length ?? 10000}
                       rows={4} onChange={(e) => set(e.target.value)} {...common} />;
    case "number":
      return <input className="input fm-num" inputMode="decimal" value={value ?? ""} placeholder={f.placeholder}
                    onChange={(e) => set(e.target.value)} {...common} />;
    case "money": {
      const amount = value && typeof value === "object" ? value.amount : value;
      return <div className="fm-money">
        <input className="input fm-num" inputMode="decimal" value={amount ?? ""} placeholder={f.placeholder || "0.00"}
               onChange={(e) => set(e.target.value)} {...common} />
        <span className="fm-currency" aria-label={t("币种")}>{f.currency}</span>
      </div>;
    }
    case "date":
      return <input className="input fm-date" type="date" value={value ?? ""} onChange={(e) => set(e.target.value)} {...common} />;
    case "datetime":
      return <input className="input fm-date" type="datetime-local" value={isoToLocal(value)}
                    onChange={(e) => set(localOffsetIso(e.target.value))} {...common} />;
    case "select": {
      const opts = f.options ?? [];
      if (opts.length <= 5) {
        return <div className="fm-choices" role="radiogroup" aria-labelledby={labelId} aria-describedby={describedBy}>
          {opts.map((o) => <label key={o.value} className="fm-choice">
            <input type="radio" name={id} value={o.value} checked={value === o.value} disabled={readOnly}
                   onChange={() => set(o.value)} />
            <span>{o.label}</span>
          </label>)}
        </div>;
      }
      return <select className="input" value={value ?? ""} onChange={(e) => set(e.target.value || null)} {...common}>
        <option value="">{t("请选择…")}</option>
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>;
    }
    case "multiselect": {
      const picked: string[] = Array.isArray(value) ? value : [];
      return <div className="fm-choices" role="group" aria-labelledby={labelId} aria-describedby={describedBy}>
        {(f.options ?? []).map((o) => <label key={o.value} className="fm-choice">
          <input type="checkbox" checked={picked.includes(o.value)} disabled={readOnly}
                 onChange={(e) => set(e.target.checked ? [...picked, o.value] : picked.filter((x) => x !== o.value))} />
          <span>{o.label}</span>
        </label>)}
      </div>;
    }
    case "boolean":
      return <label className="fm-choice fm-bool">
        <input type="checkbox" checked={value === true} disabled={readOnly} onChange={(e) => set(e.target.checked)}
               aria-describedby={describedBy} aria-required={requiredNow || undefined} />
        <span>{f.label}{requiredNow && <span className="fm-req" aria-hidden="true"> *</span>}</span>
      </label>;
    case "file":
      return <FileControl field={f} value={value} set={set} readOnly={readOnly} files={files} onUpload={onUpload}
                          onRemoveFile={onRemoveFile} labelId={labelId} describedBy={describedBy} />;
    case "repeat":
      return <RepeatControl field={f} value={value} set={set} readOnly={readOnly} errors={errors} labelId={labelId} />;
    default:
      return null;
  }
}

function FileControl({ field: f, value, set, readOnly, files, onUpload, onRemoveFile, labelId, describedBy }: {
  field: FieldDef; value: any; set: (v: any) => void; readOnly?: boolean; files?: UploadedFile[];
  onUpload?: (fieldId: string, file: File) => Promise<void>; onRemoveFile?: (fileId: string) => Promise<void>;
  labelId: string; describedBy?: string;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const ids: string[] = Array.isArray(value) ? value : [];
  const mine = (files ?? []).filter((x) => ids.includes(x.id));
  const full = ids.length >= (f.max_files ?? 1);
  return <div className="fm-files" role="group" aria-labelledby={labelId} aria-describedby={describedBy}>
    {mine.map((x) => <div key={x.id} className="fm-file">
      <span className="fm-file-name">{x.filename}</span>
      <span className="muted small">{fmtBytes(x.size)}</span>
      {x.scan === "clean" && <span className="badge ok">{t("已通过安全检查")}</span>}
      {x.scan === "not_configured" && <span className="badge">{t("未进行病毒扫描")}</span>}
      {!readOnly && onRemoveFile && <button type="button" className="btn ghost sm" onClick={async () => {
        await onRemoveFile(x.id); set(ids.filter((i) => i !== x.id));
      }}>{t("移除")}</button>}
    </div>)}
    {!readOnly && onUpload && !full && <>
      <input ref={input} type="file" className="sr-only" tabIndex={-1} accept={(f.accept ?? []).join(",") || undefined}
             onChange={async (e) => {
               const file = e.target.files?.[0];
               e.target.value = "";
               if (!file) return;
               setBusy(true);
               try { await onUpload(f.id, file); } finally { setBusy(false); }
             }} />
      <button type="button" className="btn sm" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? t("上传中…") : t("选择文件")}
      </button>
      <span className="muted small">{t("最多 {n} 个，每个不超过 {mb} MB", { n: f.max_files ?? 1, mb: f.max_size_mb ?? 10 })}
        {f.accept?.length ? ` · ${f.accept.join(" ")}` : ""}</span>
    </>}
    {!onUpload && !mine.length && <span className="muted small">{t("预览中不能上传附件")}</span>}
  </div>;
}

function RepeatControl({ field: f, value, set, readOnly, errors, labelId }: {
  field: FieldDef; value: any; set: (v: any) => void; readOnly?: boolean; errors?: FieldError[]; labelId: string;
}) {
  const { t } = useI18n();
  const rows: Record<string, any>[] = Array.isArray(value) ? value : [];
  const max = f.max_items ?? 200;
  return <div className="fm-repeat" role="group" aria-labelledby={labelId}>
    {rows.map((row, i) => <fieldset key={i} className="fm-row">
      <legend className="fm-row-title">{t("第 {n} 项", { n: i + 1 })}</legend>
      <div className="fm-row-fields">
        <RowFields field={f} row={row} i={i} readOnly={readOnly} errors={errors}
                   onChange={(sid, v) => set(rows.map((r, j) => j === i ? { ...r, [sid]: v } : r))} />
      </div>
      {!readOnly && <button type="button" className="btn ghost sm fm-row-remove"
                            onClick={() => set(rows.filter((_, j) => j !== i))}>{t("删除这一项")}</button>}
    </fieldset>)}
    {!readOnly && rows.length < max && <button type="button" className="btn sm" onClick={() => set([...rows, {}])}>{t("添加一项")}</button>}
  </div>;
}

function RowFields({ field, row, i, readOnly, errors, onChange }: {
  field: FieldDef; row: Record<string, any>; i: number; readOnly?: boolean; errors?: FieldError[];
  onChange: (id: string, v: any) => void;
}) {
  const { t } = useI18n();
  const seen: Record<string, any> = {};
  const out: ReactNode[] = [];
  for (const s of field.fields ?? []) {
    if (!evaluate(s.visible_if, seen)) continue;
    seen[s.id] = s.type === "boolean" ? row[s.id] === true : row[s.id];
    const req = !!(s.required || (s.required_if && evaluate(s.required_if, seen)));
    const errs = fieldErrors(errors, s.id, i);
    const cid = `${field.id}-${i}-${s.id}`;
    out.push(<div key={s.id} className={`fm-cell${errs.length ? " invalid" : ""}`}>
      {s.type !== "boolean" && <label className="fm-label small" htmlFor={cid}>{s.label}{req && <span className="fm-req" aria-hidden="true"> *</span>}</label>}
      <Control field={s} id={cid} value={row[s.id]} set={(v) => onChange(s.id, v)} readOnly={readOnly} requiredNow={req}
               labelId={cid} errors={(errors ?? []).filter((e) => e.row === i)} describedBy={errs.length ? `${cid}-err` : undefined} />
      {errs.length > 0 && <p className="fm-err" id={`${cid}-err`}>{errs.map((e) => errorText(t, e)).join(" ")}</p>}
    </div>);
  }
  return <>{out}</>;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
