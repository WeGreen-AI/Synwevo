import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useI18n } from "@synwevo/ui";
import { read } from "../../shared/ops";
import { Empty, Loading } from "../../shared/states";
import { useAction, useRead, Field, ErrorState, Grid, IssueList, when } from "./common";
import { StepEditor } from "./StepEditor";
import { newStep, stepNames, type Recipe, type Definition, type Dataset, type Page, type WS } from "./model";

type Draft = { name: string; description: string; definition: Definition };

export function Editor({ ws, write }: { ws: WS; write: boolean }) {
  const { t } = useI18n(), { id = "" } = useParams(), navigate = useNavigate();
  const result = useRead<Recipe>("dataprep.recipe.get", { recipe_id: id }, ws.id), action = useAction(ws.id);
  const [draft, setDraft] = useState<Draft | null>(null), [saved, setSaved] = useState(""), [rev, setRev] = useState(0);
  const [undo, setUndo] = useState<Draft[]>([]), [redo, setRedo] = useState<Draft[]>([]);
  const [inputs, setInputs] = useState<Record<string, string>>({}), [parameters, setParameters] = useState<Record<string, string | null>>({});
  const [datasets, setDatasets] = useState<Dataset[]>([]), [dataOffset, setDataOffset] = useState(0);
  const list = useRead<Page<Dataset>>("dataprep.dataset.list", { offset: dataOffset, limit: 200 }, ws.id);
  const [version, setVersion] = useState(0), [kind, setKind] = useState("trim"), [sampleOffset, setSampleOffset] = useState(0), [sampleSize, setSampleSize] = useState(50);
  const [preview, setPreview] = useState<any>(null), [previewError, setPreviewError] = useState<unknown>(null), [previewing, setPreviewing] = useState(false);
  const [parameterName, setParameterName] = useState("");
  const published = useRead<{ definition: Definition }>("dataprep.recipe.version", { recipe_id: id, version }, ws.id, false, version > 0);
  useEffect(() => { if (list.data) setDatasets(old => [...new Map([...old, ...list.data!.items].map(d => [d.id, d])).values()]); }, [list.data]);
  function adopt(r: Recipe) {
    const next = { name: r.name, description: r.description, definition: r.draft };
    setDraft(next); setSaved(JSON.stringify(next)); setRev(r.draft_rev); setUndo([]); setRedo([]);
    setVersion(v => v || r.published_version || 0);
  }
  useEffect(() => { if (result.data && !draft) adopt(result.data); }, [result.data]);
  const dirty = !!draft && JSON.stringify(draft) !== saved;
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  function edit(next: Draft) {
    if (draft) setUndo(u => [...u.slice(-49), draft]);
    setRedo([]); setDraft(next); setPreview(null);
  }
  function save() { if (draft) void action.run<Recipe>("recipe.save", { recipe_id: id, draft_rev: rev, ...draft }, r => { adopt(r); result.reload(); }); }
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); if (dirty && write && !action.busy) save(); } };
    window.addEventListener("keydown", key); return () => window.removeEventListener("keydown", key);
  });
  if (result.error) return <ErrorState error={result.error} retry={result.reload} />;
  if (!draft || !result.data) return <Loading />;
  const recipe = result.data, def = draft.definition, selectedVersion = recipe.versions.find(v => v.version === version);
  const runDef = published.data?.definition;
  const sourceNames = [...new Set([def.source, ...def.steps.filter(s => ["append", "join"].includes(s.type)).map(s => s.source),
    ...(runDef ? [runDef.source, ...runDef.steps.filter(s => ["append", "join"].includes(s.type)).map(s => s.source)] : [])])];
  const sourceColumns: Record<string, string[]> = Object.fromEntries(sourceNames.map(name => [name, datasets.find(d => d.id === inputs[name])?.columns || []]));
  let current = [...(sourceColumns[def.source] || [])];
  const beforeColumns = def.steps.map(step => {
    const before = [...current];
    if (step.type === "split") current.push(...step.into);
    if (step.type === "merge") current.push(step.target);
    if (step.type === "append") current = [...new Set([...current, ...(sourceColumns[step.source] || [])])];
    if (step.type === "join") current.push(...(sourceColumns[step.source] || []).map(c => step.prefix + c));
    if (step.type === "aggregate") current = [...step.group_by, ...step.measures.map((m: any) => m.target)];
    return before;
  });
  const editDef = (next: Definition) => edit({ ...draft, definition: next });
  const bind = (definition: Definition) => {
    const names = [...new Set([definition.source, ...definition.steps.filter(s => ["append", "join"].includes(s.type)).map(s => s.source)])];
    return { inputs: Object.fromEntries(names.map(k => [k, inputs[k] || ""])), parameters: Object.fromEntries(Object.entries(parameters).filter(([k]) => k in definition.parameters)) };
  };
  async function sample() {
    if (previewing) return;
    setPreviewing(true); setPreviewError(null);
    // Keep the previewed definition: undo/redo change the draft without clearing the preview,
    // and its issues must be labelled by the steps that produced them.
    try { setPreview({ ...await read("dataprep.recipe.preview", { definition: def, ...bind(def), offset: sampleOffset, limit: sampleSize }, ws.id), definition: def }); }
    catch (e) { setPreviewError(e); } finally { setPreviewing(false); }
  }
  return <div className="dp-editor" onClickCapture={e => {
    const link = (e.target as HTMLElement).closest("a");
    if (dirty && link && !window.confirm(t("草稿尚未保存，离开会丢失修改。继续？"))) { e.preventDefault(); e.stopPropagation(); }
  }}><div className="suite-head"><div><h1>{draft.name}</h1><p className="lede" role="status">{dirty ? t("有未保存修改") : t("草稿已保存")} · {t("草稿修订 {n}", { n: rev })}</p></div>
      <div className="actions"><button className="btn" disabled={!write || !undo.length || action.busy} onClick={() => { setRedo(r => [draft, ...r]); setDraft(undo.at(-1)!); setUndo(u => u.slice(0, -1)); }}>{t("撤销编辑")}</button>
        <button className="btn" disabled={!write || !redo.length || action.busy} onClick={() => { setUndo(u => [...u, draft]); setDraft(redo[0]); setRedo(r => r.slice(1)); }}>{t("重做")}</button>
        <button className="btn primary" disabled={!write || !dirty || action.busy || recipe.status !== "active"} onClick={save}>{action.busy ? t("处理中…") : t("保存草稿")}</button></div></div>
    {action.error && <><ErrorState error={action.error} /><button className="btn" onClick={() => { if (!dirty || window.confirm(t("重新载入会丢弃本地修改。继续？"))) { setDraft(null); setSaved(""); result.reload(); } }}>{t("重新载入")}</button></>}
    <div className="dp-editor-layout"><section>
      <fieldset className="dp-panel" disabled={!write || action.busy || recipe.status !== "active"}>
        <div className="dp-fields"><Field label={t("方案名称")}><input className="input" maxLength={120} value={draft.name} onChange={e => edit({ ...draft, name: e.target.value })} /></Field>
          <Field label={t("方案说明")}><input className="input" maxLength={2000} value={draft.description} onChange={e => edit({ ...draft, description: e.target.value })} /></Field>
          <Field label={t("主来源名称")}><input className="input" value={def.source} pattern="[a-z][a-z0-9_]{0,31}" onChange={e => editDef({ ...def, source: e.target.value })} /></Field></div>
        <h2>{t("处理步骤")}</h2>
        {!def.steps.length && <Empty title={t("添加步骤，或直接使用原始数据")} />}
        {def.steps.map((s, i) => <section className="dp-step" key={s.id}><div className="dp-step-head"><h3>{i + 1}. {t(stepNames[s.type])}</h3><div className="dp-actions">
          <button type="button" className="btn sm" disabled={i === 0} aria-label={t("上移步骤")} onClick={() => { const steps = [...def.steps]; [steps[i - 1], steps[i]] = [steps[i], steps[i - 1]]; editDef({ ...def, steps }); }}>↑</button>
          <button type="button" className="btn sm" disabled={i === def.steps.length - 1} aria-label={t("下移步骤")} onClick={() => { const steps = [...def.steps]; [steps[i + 1], steps[i]] = [steps[i], steps[i + 1]]; editDef({ ...def, steps }); }}>↓</button>
          <button type="button" className="btn sm" onClick={() => editDef({ ...def, steps: def.steps.filter((_, n) => n !== i) })}>{t("删除步骤")}</button></div></div>
          <StepEditor step={s} columns={beforeColumns[i]} sources={sourceNames} sourceColumns={sourceColumns} change={step => editDef({ ...def, steps: def.steps.map((x, n) => n === i ? step : x) })} /></section>)}
        <div className="dp-actions"><Field label={t("添加处理步骤")}><select className="input" value={kind} onChange={e => setKind(e.target.value)}>{Object.entries(stepNames).map(([v, label]) => <option value={v} key={v}>{t(label)}</option>)}</select></Field>
          <button type="button" className="btn" disabled={def.steps.length >= 30} onClick={() => editDef({ ...def, steps: [...def.steps, newStep(kind)] })}>{t("添加步骤")}</button></div>
        <details className="dp-parameters"><summary>{t("方案参数")}</summary>{Object.entries(def.parameters).map(([k, v]) => <div className="dp-actions" key={k}><Field label={k}><input className="input" value={v ?? ""} onChange={e => editDef({ ...def, parameters: { ...def.parameters, [k]: e.target.value } })} /></Field><button type="button" className="btn sm" onClick={() => { const parameters = { ...def.parameters }; delete parameters[k]; editDef({ ...def, parameters }); }}>{t("删除参数")}</button></div>)}
          <div className="dp-actions"><Field label={t("参数名称")}><input className="input" pattern="[a-z][a-z0-9_]{0,31}" value={parameterName} onChange={e => setParameterName(e.target.value)} /></Field><button type="button" className="btn" disabled={!/^[a-z][a-z0-9_]{0,31}$/.test(parameterName) || parameterName in def.parameters || Object.keys(def.parameters).length >= 20} onClick={() => { editDef({ ...def, parameters: { ...def.parameters, [parameterName]: "" } }); setParameterName(""); }}>{t("添加参数")}</button></div></details>
      </fieldset>
    </section><aside className="dp-panel dp-run-panel"><h2>{t("输入与运行")}</h2>
      <p className="muted">{t("绑定本次输入，方案本身不保存业务数据。所有来源合计受处理规模限制。")}</p>
      {sourceNames.map(name => <Field key={name} label={name || t("未命名来源")}><select className="input" value={inputs[name] || ""} onChange={e => { setInputs({ ...inputs, [name]: e.target.value }); setPreview(null); }}><option value="">{t("选择数据集")}</option>{datasets.map(d => <option key={d.id} value={d.id}>{d.name} · {d.row_count}</option>)}</select></Field>)}
      {!!list.error && <ErrorState error={list.error} retry={list.reload} />}{list.data?.next_offset != null && <button className="btn sm" onClick={() => setDataOffset(list.data!.next_offset!)}>{t("载入更多数据集")}</button>}
      <Link to={`/dataprep/w/${ws.id}/import`}>{t("导入数据")}</Link>
      {[...new Set([...Object.keys(def.parameters), ...Object.keys(runDef?.parameters || {})])].map(k => <Field key={k} label={`${t("运行参数")} · ${k}`}><input className="input" value={parameters[k] ?? def.parameters[k] ?? runDef?.parameters[k] ?? ""} onChange={e => setParameters({ ...parameters, [k]: e.target.value })} /></Field>)}
      <h3>{t("样本预览")}</h3><p className="muted">{t("每个来源使用同一行偏移和样本数量。关联与汇总只反映样本；正式运行处理整个输入范围。")}</p>
      <div className="dp-fields"><Field label={t("跳过数据行数")}><input className="input" type="number" min={0} max={50000} value={sampleOffset} onChange={e => setSampleOffset(Number(e.target.value))} /></Field>
        <Field label={t("样本行数")}><input className="input" type="number" min={1} max={200} value={sampleSize} onChange={e => setSampleSize(Number(e.target.value))} /></Field></div>
      <button className="btn" disabled={previewing} onClick={() => void sample()}>{previewing ? t("预览中…") : t("预览当前草稿")}</button>
      <h3>{t("发布与版本")}</h3>{!!published.error && <ErrorState error={published.error} retry={published.reload} />}<button className="btn" disabled={!write || dirty || action.busy || recipe.status !== "active"} onClick={() => void action.run<Recipe>("recipe.publish", { recipe_id: id, draft_rev: rev }, r => { setVersion(r.published_version || 0); result.reload(); })}>{t("发布保存的草稿")}</button>
      <Field label={t("运行版本")}><select className="input" value={version} onChange={e => setVersion(Number(e.target.value))}><option value={0}>{t("选择发布版本")}</option>{recipe.versions.map(v => <option value={v.version} key={v.version}>{t("版本 {v}", { v: v.version })} · {v.status === "retired" ? t("已停用") : when(v.published_at)}</option>)}</select></Field>
      {dirty && <p className="notice">{t("未保存草稿不会进入全量运行。全量运行只使用所选发布版本。")}</p>}
      <button className="btn primary" disabled={!write || !runDef || !selectedVersion || selectedVersion.status !== "published" || recipe.status !== "active" || action.busy} onClick={() => runDef && void action.run("run.start", { recipe_id: id, version, ...bind(runDef) }, r => navigate(`/dataprep/w/${ws.id}/runs/${r.id}`))}>{t("全量运行所选版本")}</button>
      {write && <details><summary>{t("生命周期管理")}</summary>
        <button className="btn sm" disabled={action.busy || !version || selectedVersion?.status !== "published"} onClick={() => { if (window.confirm(t("停用后不能新建此版本的运行，已有运行继续。确认？"))) void action.run("recipe.retire", { recipe_id: id, version }, result.reload); }}>{t("停用所选版本")}</button>
        <button className="btn sm" disabled={action.busy} onClick={() => { if (window.confirm(t("归档会阻止编辑和新建运行，已有结果保留。确认切换状态？"))) void action.run("recipe.archive", { recipe_id: id, archived: recipe.status !== "archived" }, result.reload); }}>{recipe.status === "archived" ? t("恢复方案") : t("归档方案")}</button></details>}
      <p className="muted small">{t("无效转换行被排除并记录；校验步骤可选择保留。日期时间统一转为 UTC；排序稳定；相同固定输入可重现。")}</p>
    </aside></div>
    {previewError && <ErrorState error={previewError} />}{previewing && <Loading />}
    {preview && <section className="dp-preview"><h2>{t("前后对比（仅所选样本）")}</h2><p className="notice">{Object.entries(preview.ranges).map(([k, r]: [string, any]) => `${k}: ${r.offset + 1}–${r.offset + r.selected} / ${r.total}`).join(" · ")}</p>
      <div className="dp-comparison"><Grid columns={sourceColumns[def.source] || []} rows={preview.before || []} caption={t("处理前")} /><Grid columns={preview.columns} rows={preview.rows} caption={t("处理后")} /></div>
      <p>{t("预览产出 {n} 行，最多显示 200 行", { n: preview.output_rows })}</p><IssueList items={preview.issues} steps={preview.definition.steps} /></section>}
  </div>;
}
