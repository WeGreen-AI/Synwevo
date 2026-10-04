/** The form builder: fields, conditions, steps, checks — saved as a draft, published as versions.
 *
 *  Saving is automatic and says what actually happened: "已保存" only after the server accepted
 *  the draft revision; a revision someone else saved first is shown as a conflict with a choice,
 *  never overwritten silently; a save refused as invalid says what to change and where, and keeps
 *  the edit on the canvas. Publishing publishes what the canvas shows or nothing: it saves first,
 *  stops when that save is refused, and sends what it shows so the server can refuse a draft
 *  that differs (`draft_unsaved`). Undo/redo work on this editor's own changes (Ctrl/⌘+Z, Shift for
 *  redo). Fields move with buttons as well as the keyboard — no drag-only operation. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError } from "@synwevo/auth-client";
import { Drawer, Menu, Modal, useDialog, useI18n, useToast, formatDate } from "@synwevo/ui";
import { op, newKey } from "../../shared/ops";
import { Failure, Loading } from "../../shared/states";
import { blankField, draftRefusals, newFieldId, owningField, type Condition, type Definition, type DraftRefusal,
         type FieldDef, type FieldType } from "./logic";
import { Renderer } from "./Renderer";
import { FieldProps } from "./FieldProps";
import { problemText, refusalText, typeLabel } from "./text";
import { ShareDrawer } from "./Share";

interface FormData_ {
  id: string; workspace_id: string; title: string; description: string; status: "active" | "archived";
  draft: Definition; draft_rev: number; published_version: number | null;
  public_link: { enabled: boolean; url: string | null } | null;
  versions: { version: number; status: string; title: string; note: string; published_by: string; published_at: number; retired_at: number | null }[];
}

type Save = "saved" | "pending" | "saving" | "conflict" | "error";

const PALETTE: FieldType[] = ["text", "textarea", "number", "money", "date", "datetime", "select", "multiselect",
  "boolean", "file", "repeat", "section"];

export default function Editor({ ws, canWrite }: { ws: { id: string; name: string }; canWrite: boolean }) {
  const { t } = useI18n();
  const toast = useToast();
  const dialog = useDialog();
  const navigate = useNavigate();
  const { fid = "" } = useParams();
  const [form, setForm] = useState<FormData_ | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [defn, setDefn] = useState<Definition>({ fields: [] });
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [save, setSave] = useState<Save>("saved");
  const [tab, setTab] = useState<"build" | "preview">("build");
  const [previewWidth, setPreviewWidth] = useState<"desktop" | "phone">("desktop");
  const [previewAnswers, setPreviewAnswers] = useState<Record<string, any>>({});
  const [problems, setProblems] = useState<{ code: string; field?: string }[] | null>(null);
  const [panel, setPanel] = useState<"versions" | "share" | null>(null);
  const [conflict, setConflict] = useState<FormData_ | null>(null);
  // A save the server refused as invalid: where, in the definition that was sent.
  const [refused, setRefused] = useState<{ defn: Definition; items: DraftRefusal[] } | null>(null);
  const rev = useRef(1);
  const undo = useRef<{ defn: Definition; title: string; description: string }[]>([]);
  const redo = useRef<{ defn: Definition; title: string; description: string }[]>([]);
  const timer = useRef<number | undefined>(undefined);
  const latest = useRef({ defn, title, description });
  latest.current = { defn, title, description };
  // The canvas as the server last stored it (this page's own snapshot), and the save in flight:
  // saves run one at a time, so a publish never races an autosave into a false conflict.
  const stored = useRef("");
  const saving = useRef<Promise<boolean> | null>(null);
  const snapshot = (cur: typeof latest.current) => JSON.stringify([savedTitle(cur.title), cur.description, cur.defn]);
  const savedTitle = (s: string) => s.trim() || t("未命名表单");

  const load = useCallback(async () => {
    setError(null);
    try {
      const out = await op<FormData_>("forms.form.get", { form_id: fid }, { workspace: ws.id });
      const next = { defn: out.output.draft?.fields ? out.output.draft : { fields: [] }, title: out.output.title,
                     description: out.output.description };
      setForm(out.output);
      setDefn(next.defn);
      setTitle(next.title);
      setDescription(next.description);
      rev.current = out.output.draft_rev;
      stored.current = JSON.stringify([next.title, next.description, next.defn]);
      setRefused(null);
      setSave("saved");
    } catch (e) { setError(e); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fid, ws.id]);
  useEffect(() => { void load(); }, [load]);

  /** Save what the canvas shows now. True only when the server stored exactly that. */
  const persist = useCallback((): Promise<boolean> => {
    const run = async (): Promise<boolean> => {
      const cur = latest.current;
      setSave("saving");
      try {
        const out = await op<FormData_>("forms.form.update_draft", {
          form_id: fid, draft_rev: rev.current, title: savedTitle(cur.title), description: cur.description,
          definition: cur.defn }, { workspace: ws.id });
        rev.current = out.output.draft_rev;
        stored.current = snapshot(cur);
        setForm(out.output);
        setRefused(null);
        setSave((s) => s === "saving" ? "saved" : s);
        return true;
      } catch (e) {
        if (e instanceof ApiError && e.code === "draft_conflict") { setConflict(e.detail?.current ?? null); setSave("conflict"); }
        else if (e instanceof ApiError && e.code === "invalid_input") {
          // Said as what to change and where; the canvas keeps the edit.
          const items = draftRefusals(cur.defn, e.detail);
          setRefused({ defn: cur.defn, items: items.length ? items : [{ part: "other" }] });
          setSave("error");
        } else { setSave("error"); toast(e instanceof ApiError ? e.message : String(e), "err"); }
        return false;
      }
    };
    const next = (saving.current ?? Promise.resolve(true)).then(run, run);
    saving.current = next;
    void next.finally(() => { if (saving.current === next) saving.current = null; });
    return next;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fid, ws.id, t, toast]);

  function schedule() {
    setSave("pending");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void persist(), 800);
  }

  function commit(next: Definition, nextTitle = title, nextDescription = description) {
    undo.current.push({ defn, title, description });
    if (undo.current.length > 100) undo.current.shift();
    redo.current = [];
    setDefn(next); setTitle(nextTitle); setDescription(nextDescription);
    schedule();
  }

  function step(from: typeof undo, to: typeof redo) {
    const snap = from.current.pop();
    if (!snap) return;
    to.current.push({ defn, title, description });
    setDefn(snap.defn); setTitle(snap.title); setDescription(snap.description);
    schedule();
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "z" || !canWrite) return;
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;   // the field's own undo
      e.preventDefault();
      if (e.shiftKey) step(redo, undo); else step(undo, redo);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (save === "pending" || save === "saving" || save === "error") e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [save]);

  const index = useMemo(() => defn.fields.findIndex((f) => f.id === selected), [defn, selected]);
  const current = index >= 0 ? defn.fields[index] : null;

  function add(type: FieldType) {
    const f = blankField(type, type === "section" ? t("新的分节") : typeLabel(t, type), t("明细项 {n}", { n: 1 }));
    const at = index >= 0 ? index + 1 : defn.fields.length;
    const fields = [...defn.fields.slice(0, at), f, ...defn.fields.slice(at)];
    let steps = defn.steps;
    if (steps?.length) {
      const target = steps.findIndex((s) => current && s.fields.includes(current.id));
      const k = target >= 0 ? target : steps.length - 1;
      steps = steps.map((s, i) => i === k ? { ...s, fields: [...s.fields, f.id] } : s);
    }
    commit({ ...defn, fields, steps });
    setSelected(f.id);
  }

  function update(id: string, patch: Partial<FieldDef>) {
    commit({ ...defn, fields: defn.fields.map((f) => f.id === id ? { ...f, ...patch } : f) });
  }

  function move(id: string, by: -1 | 1) {
    const i = defn.fields.findIndex((f) => f.id === id);
    const j = i + by;
    if (i < 0 || j < 0 || j >= defn.fields.length) return;
    const fields = [...defn.fields];
    [fields[i], fields[j]] = [fields[j], fields[i]];
    commit({ ...defn, fields });
  }

  async function remove(id: string) {
    const used = defn.fields.some((f) => refersTo(f.visible_if, id) || refersTo(f.required_if, id))
      || (defn.rules ?? []).some((r) => r.field === id || r.other === id);
    if (used && !(await dialog.confirm(t("删除字段"), t("有其他字段的条件或校验规则引用了它。删除后这些条件需要重新设置。确定删除？"), t("删除"), true))) return;
    commit({ ...defn, fields: defn.fields.filter((f) => f.id !== id),
             steps: defn.steps?.map((s) => ({ ...s, fields: s.fields.filter((x) => x !== id) })),
             rules: (defn.rules ?? []).filter((r) => r.field !== id && r.other !== id) });
    setSelected(null);
  }

  function duplicateField(id: string) {
    const f = defn.fields.find((x) => x.id === id);
    if (!f) return;
    const copy: FieldDef = { ...structuredClone(f), id: newFieldId(), label: `${f.label} ${t("（副本）")}` };
    if (copy.fields) copy.fields = copy.fields.map((s) => ({ ...s, id: newFieldId() }));
    const i = defn.fields.findIndex((x) => x.id === id);
    commit({ ...defn, fields: [...defn.fields.slice(0, i + 1), copy, ...defn.fields.slice(i + 1)],
             steps: defn.steps?.map((s) => s.fields.includes(id) ? { ...s, fields: [...s.fields, copy.id] } : s) });
    setSelected(copy.id);
  }

  /** Publish exactly what the canvas shows: save it first, and publish nothing when that save is
   *  refused (the server also refuses a publish whose title, description or definition differ
   *  from the stored draft). The canvas is never reloaded over the person's edits. */
  async function publish() {
    window.clearTimeout(timer.current);
    if (snapshot(latest.current) !== stored.current || save !== "saved") {
      if (!(await persist())) { toast(t("草稿没有保存，所以没有发布。请先处理提示的问题。"), "err"); return; }
    }
    const seen = latest.current;
    try {
      const out = await op<{ version: number }>("forms.form.publish", {
        form_id: fid, draft_rev: rev.current, title: savedTitle(seen.title), description: seen.description,
        definition: seen.defn }, { workspace: ws.id, key: newKey() });
      toast(t("已发布版本 {v}", { v: out.output.version }), "ok");
      setProblems(null);
      await refreshVersions();
    } catch (e) {
      if (e instanceof ApiError && e.code === "form_not_publishable") setProblems(e.detail?.problems ?? []);
      else if (e instanceof ApiError && e.code === "draft_unsaved") {
        toast(t("有修改还没有保存，没有发布。保存后再发布。"), "err");
        schedule();
      } else toast(e instanceof ApiError ? e.message : String(e), "err");
    }
  }

  /** The versions and links after a publish, without touching the canvas. */
  async function refreshVersions() {
    try {
      const out = await op<FormData_>("forms.form.get", { form_id: fid }, { workspace: ws.id });
      setForm(out.output);
    } catch { /* the page shows the new version on its next load */ }
  }

  async function archive(archived: boolean) {
    await op("forms.form.archive", { form_id: fid, archived }, { workspace: ws.id });
    await load();
  }

  async function duplicateForm() {
    const name = await dialog.prompt(t("复制表单"), t("新表单的名称"), `${title} ${t("（副本）")}`, t("复制"));
    if (!name) return;
    const out = await op<{ form_id: string }>("forms.form.duplicate", { form_id: fid, title: name.slice(0, 200) }, { workspace: ws.id, key: newKey() });
    navigate(`/forms/w/${ws.id}/f/${out.output.form_id}`);
  }

  if (error) return <Failure error={error} onRetry={load} />;
  if (!form) return <Loading />;
  const readOnly = !canWrite || form.status === "archived";

  return <div className="fm-editor">
    <div className="fm-editor-head">
      <Link to={`/forms/w/${ws.id}`} className="btn ghost sm">← {t("表单库")}</Link>
      <input className="input fm-title-input" value={title} aria-label={t("表单名称")} disabled={readOnly} maxLength={200}
             onChange={(e) => commit(defn, e.target.value, description)} />
      <SaveBadge state={save} onRetry={() => void persist()} />
      <span className="spacer" />
      <div className="seg" role="tablist" aria-label={t("视图")}>
        <button type="button" role="tab" aria-selected={tab === "build"} className={tab === "build" ? "on" : ""} onClick={() => setTab("build")}>{t("编辑")}</button>
        <button type="button" role="tab" aria-selected={tab === "preview"} className={tab === "preview" ? "on" : ""} onClick={() => setTab("preview")}>{t("预览")}</button>
      </div>
      {!readOnly && <button type="button" className="btn primary" onClick={() => void publish()}>{t("发布")}</button>}
      <Menu label={t("更多")} items={[
        { label: t("版本记录"), onSelect: () => setPanel("versions") },
        { label: t("分享与填写请求"), onSelect: () => setPanel("share"), disabled: !form.published_version },
        ...(canWrite ? [{ label: t("复制表单"), onSelect: () => void duplicateForm() },
                        form.status === "active"
                          ? { label: t("归档"), onSelect: () => void archive(true) }
                          : { label: t("恢复"), onSelect: () => void archive(false) }] : []),
        ...(canWrite ? [{ label: t("撤销"), onSelect: () => step(undo, redo), disabled: !undo.current.length },
                        { label: t("重做"), onSelect: () => step(redo, undo), disabled: !redo.current.length }] : []),
      ]} />
    </div>
    <p className="muted small fm-version-line">
      {form.published_version ? t("最新发布：版本 {v}", { v: form.published_version }) : t("尚未发布：发布后才能发出填写请求或开放公开链接。")}
      {form.status === "archived" && ` · ${t("已归档")}`}
    </p>

    {refused && <div className="notice err fm-problems" role="alert">
      <strong>{t("草稿没有保存，请先处理：")}</strong>
      <ul>{refused.items.map((r, i) => <li key={i}>
        {refusalText(t, r, refused.defn)}
        {(r.parent ?? r.field) && owningField(defn, r.parent ?? r.field!) && <> — <button type="button" className="linkish"
          onClick={() => { setSelected(r.parent ?? r.field!); setTab("build"); }}>{t("查看字段")}</button></>}
      </li>)}</ul>
    </div>}
    {problems && <div className="notice err fm-problems" role="alert">
      <strong>{t("还不能发布：")}</strong>
      <ul>{problems.map((p, i) => {
        // A problem of a repeating group's sub-field opens the group that holds it.
        const owner = p.field ? owningField(defn, p.field) : undefined;
        const sub = owner && owner.id !== p.field ? owner.fields?.find((s) => s.id === p.field) : undefined;
        return <li key={i}>
          {problemText(t, p.code)}
          {owner && <> — <button type="button" className="linkish" onClick={() => { setSelected(owner.id); setTab("build"); }}>
            {(sub ? [owner.label, sub.label].filter(Boolean).join(" › ") : owner.label) || t("查看字段")}</button></>}
        </li>;
      })}</ul>
    </div>}

    {tab === "preview" ? <div className="fm-preview-wrap">
      <div className="seg" role="group" aria-label={t("预览宽度")}>
        <button type="button" className={previewWidth === "desktop" ? "on" : ""} onClick={() => setPreviewWidth("desktop")}>{t("电脑")}</button>
        <button type="button" className={previewWidth === "phone" ? "on" : ""} onClick={() => setPreviewWidth("phone")}>{t("手机")}</button>
      </div>
      <div className={`fm-preview ${previewWidth}`}>
        <h1>{title}</h1>
        {description && <p className="fm-desc">{description}</p>}
        <Renderer definition={defn} answers={previewAnswers} onChange={(id, v) => setPreviewAnswers((a) => ({ ...a, [id]: v }))} />
        <p className="muted small">{t("这是草稿的预览：可以试填，但不会保存或提交。")}</p>
      </div>
    </div> :
    <div className="fm-builder">
      <aside className="fm-palette" aria-label={t("添加字段")}>
        <h2 className="fm-pane-title">{t("添加字段")}</h2>
        {PALETTE.map((type) => <button key={type} type="button" className="fm-palette-item" disabled={readOnly}
                                        onClick={() => add(type)}>{typeLabel(t, type)}</button>)}
      </aside>
      <section className="fm-canvas" aria-label={t("字段列表")}>
        <label className="fm-label" htmlFor="fm-desc">{t("说明（显示在表单顶部）")}</label>
        <textarea id="fm-desc" className="input" rows={2} value={description} disabled={readOnly} maxLength={2000}
                  onChange={(e) => commit(defn, title, e.target.value)} />
        {defn.fields.length === 0 && <div className="suite-empty fm-canvas-empty">
          <p className="suite-empty-title">{t("还没有字段")}</p>
          <p className="muted">{t("从左侧添加字段。每个字段都有固定的编号，改名不会影响已有的数据映射。")}</p>
        </div>}
        <ol className="fm-field-list">
          {defn.fields.map((f, i) => <li key={f.id} className={`fm-card${selected === f.id ? " selected" : ""}`}>
            <button type="button" className="fm-card-main" aria-pressed={selected === f.id} onClick={() => setSelected(f.id)}
                    aria-label={`${f.label || t("（未命名）")} · ${typeLabel(t, f.type)}${f.required ? ` · ${t("必填")}` : ""}`}>
              <span className="fm-card-label">{f.label || t("（未命名）")}{f.required && <span className="fm-req"> *</span>}</span>
              <span className="fm-card-meta">
                <span className="badge">{typeLabel(t, f.type)}</span>
                {f.visible_if && <span className="badge info">{t("有显示条件")}</span>}
                {f.required_if && <span className="badge info">{t("条件必填")}</span>}
                <code className="fm-id">{f.id}</code>
              </span>
            </button>
            {!readOnly && <div className="fm-card-tools">
              <button type="button" className="btn ghost sm" aria-label={t("上移「{name}」", { name: f.label })} disabled={i === 0} onClick={() => move(f.id, -1)}>↑</button>
              <button type="button" className="btn ghost sm" aria-label={t("下移「{name}」", { name: f.label })} disabled={i === defn.fields.length - 1} onClick={() => move(f.id, 1)}>↓</button>
              <button type="button" className="btn ghost sm" onClick={() => duplicateField(f.id)}>{t("复制")}</button>
              <button type="button" className="btn ghost sm" onClick={() => void remove(f.id)}>{t("删除")}</button>
            </div>}
          </li>)}
        </ol>
        <StepsAndRules defn={defn} readOnly={readOnly} onChange={(d) => commit(d)} />
      </section>
      <aside className="fm-props" aria-label={t("字段设置")}>
        {current ? <FieldProps key={current.id} field={current} defn={defn} readOnly={readOnly}
                               onChange={(patch) => update(current.id, patch)}
                               onStep={(k) => commit({ ...defn, steps: (defn.steps ?? []).map((s, i) => ({ ...s,
                                 fields: i === k ? [...s.fields.filter((x) => x !== current.id), current.id] : s.fields.filter((x) => x !== current.id) })) })} />
                 : <p className="muted">{t("选择一个字段查看和修改它的设置。")}</p>}
      </aside>
    </div>}

    {panel === "versions" && <Drawer title={t("版本记录")} onClose={() => setPanel(null)}>
      {form.versions.length === 0 ? <p className="muted">{t("还没有发布过版本。")}</p> :
       <ul className="fm-versions">{form.versions.map((v) => <li key={v.version}>
         <div><strong>{t("版本 {v}", { v: v.version })}</strong> <span className={`badge ${v.status === "published" ? "ok" : ""}`}>{v.status === "published" ? t("可用") : t("已停用")}</span></div>
         <div className="muted small">{formatDate(v.published_at * 1000, { dateStyle: "medium", timeStyle: "short" })}{v.note ? ` · ${v.note}` : ""}</div>
         {canWrite && v.status === "published" && <button type="button" className="btn ghost sm" onClick={async () => {
           if (!(await dialog.confirm(t("停用版本 {v}", { v: v.version }), t("停用后不能再用这个版本发出新的填写请求；已发出、仍未结束的请求不受影响。"), t("停用")))) return;
           await op("forms.form.retire_version", { form_id: fid, version: v.version }, { workspace: ws.id });
           await load();
         }}>{t("停用")}</button>}
       </li>)}</ul>}
    </Drawer>}
    {panel === "share" && <ShareDrawer form={form} ws={ws} canWrite={canWrite} onClose={() => setPanel(null)} onChanged={load} />}
    {conflict && <Modal title={t("草稿已被其他人修改")} onClose={() => setConflict(null)} footer={<>
      <button type="button" className="btn" onClick={() => { setConflict(null); void load(); }}>{t("放弃我的修改，载入最新")}</button>
      <button type="button" className="btn primary" onClick={() => { rev.current = conflict.draft_rev; setConflict(null); void persist(); }}>{t("用我的修改覆盖")}</button>
    </>}>
      <p>{t("在你编辑期间，有人保存了这份表单的草稿。你可以载入最新的内容，或用你的修改覆盖它。")}</p>
    </Modal>}
  </div>;
}

function refersTo(cond: Condition | null | undefined, id: string): boolean {
  if (!cond) return false;
  if ("all" in cond) return cond.all.some((c) => refersTo(c, id));
  if ("any" in cond) return cond.any.some((c) => refersTo(c, id));
  if ("not" in cond) return refersTo(cond.not, id);
  return cond.field === id;
}

function SaveBadge({ state, onRetry }: { state: Save; onRetry: () => void }) {
  const { t } = useI18n();
  if (state === "error") return <button type="button" className="badge err" onClick={onRetry}>{t("保存失败，点此重试")}</button>;
  const text = state === "saved" ? t("草稿已保存") : state === "saving" ? t("保存中…") : state === "conflict" ? t("有冲突") : t("有未保存的修改");
  return <span className={`badge ${state === "saved" ? "ok" : state === "conflict" ? "warn" : ""}`} role="status" aria-live="polite">{text}</span>;
}

/** Steps (optional) and cross-field checks. */
function StepsAndRules({ defn, readOnly, onChange }: { defn: Definition; readOnly: boolean; onChange: (d: Definition) => void }) {
  const { t } = useI18n();
  const steps = defn.steps ?? [];
  const rules = defn.rules ?? [];
  const comparable = defn.fields.filter((f) => ["number", "money", "date", "datetime"].includes(f.type));
  const ids = defn.fields.map((f) => f.id);
  return <div className="fm-extra">
    <details open={steps.length > 0}>
      <summary>{t("分步填写")}{steps.length ? ` · ${t("{n} 步", { n: steps.length })}` : ""}</summary>
      {steps.length === 0 ? <button type="button" className="btn sm" disabled={readOnly || !ids.length}
                                   onClick={() => onChange({ ...defn, steps: [{ id: "s_1", title: t("第一步"), fields: ids }] })}>{t("分成多个步骤")}</button> :
       <>
         {steps.map((s, i) => <div key={s.id} className="fm-step-edit">
           <input className="input sm" value={s.title} disabled={readOnly} aria-label={t("步骤名称")} maxLength={200}
                  onChange={(e) => onChange({ ...defn, steps: steps.map((x, j) => j === i ? { ...x, title: e.target.value } : x) })} />
           <span className="muted small">{t("{n} 个字段", { n: s.fields.length })}</span>
           {i > 0 && !readOnly && <button type="button" className="btn ghost sm" onClick={() => {
             const merged = steps.map((x, j) => j === i - 1 ? { ...x, fields: [...x.fields, ...s.fields] } : x).filter((_, j) => j !== i);
             onChange({ ...defn, steps: merged });
           }}>{t("并入上一步")}</button>}
         </div>)}
         {!readOnly && <div className="row gap4">
           <button type="button" className="btn sm" onClick={() => {
             const last = steps[steps.length - 1];
             if (last.fields.length < 2) return;
             const half = Math.ceil(last.fields.length / 2);
             onChange({ ...defn, steps: [...steps.slice(0, -1), { ...last, fields: last.fields.slice(0, half) },
               { id: `s_${steps.length + 1}${Math.random().toString(36).slice(2, 6)}`, title: t("第 {n} 步", { n: steps.length + 1 }), fields: last.fields.slice(half) }] });
           }}>{t("拆分最后一步")}</button>
           <button type="button" className="btn ghost sm" onClick={() => onChange({ ...defn, steps: [] })}>{t("取消分步")}</button>
         </div>}
         <p className="muted small">{t("每一步显示的字段按表单顺序排列；在字段设置里可以把字段移到其他步骤。")}</p>
       </>}
    </details>
    <details open={rules.length > 0}>
      <summary>{t("跨字段校验")}{rules.length ? ` · ${rules.length}` : ""}</summary>
      {rules.map((r, i) => <div key={r.id} className="fm-rule">
        <select className="input sm" value={r.field} disabled={readOnly} aria-label={t("字段")}
                onChange={(e) => onChange({ ...defn, rules: rules.map((x, j) => j === i ? { ...x, field: e.target.value } : x) })}>
          {comparable.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </select>
        <select className="input sm" value={r.op} disabled={readOnly} aria-label={t("比较")}
                onChange={(e) => onChange({ ...defn, rules: rules.map((x, j) => j === i ? { ...x, op: e.target.value as any } : x) })}>
          <option value="gte">≥</option><option value="gt">&gt;</option><option value="lte">≤</option><option value="lt">&lt;</option>
          <option value="eq">=</option><option value="neq">≠</option>
        </select>
        <select className="input sm" value={r.other} disabled={readOnly} aria-label={t("另一个字段")}
                onChange={(e) => onChange({ ...defn, rules: rules.map((x, j) => j === i ? { ...x, other: e.target.value } : x) })}>
          {comparable.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
        </select>
        <input className="input sm grow" value={r.message} disabled={readOnly} maxLength={200} aria-label={t("不满足时的提示")}
               onChange={(e) => onChange({ ...defn, rules: rules.map((x, j) => j === i ? { ...x, message: e.target.value } : x) })} />
        {!readOnly && <button type="button" className="btn ghost sm" onClick={() => onChange({ ...defn, rules: rules.filter((_, j) => j !== i) })}>{t("删除")}</button>}
      </div>)}
      {!readOnly && comparable.length >= 2 && <button type="button" className="btn sm" onClick={() => onChange({ ...defn, rules: [...rules,
        { id: `r_${Math.random().toString(36).slice(2, 8)}`, field: comparable[1].id, op: "gte", other: comparable[0].id, message: t("两个答案的先后或大小不正确") }] })}>{t("添加校验规则")}</button>}
      {comparable.length < 2 && <p className="muted small">{t("需要至少两个数字、金额或日期字段才能添加比较规则。")}</p>}
    </details>
    <details>
      <summary>{t("提交后显示的文字")}</summary>
      <textarea className="input" rows={2} value={defn.confirmation ?? ""} disabled={readOnly} maxLength={2000}
                aria-label={t("提交后显示的文字")} onChange={(e) => onChange({ ...defn, confirmation: e.target.value })} />
    </details>
  </div>;
}
