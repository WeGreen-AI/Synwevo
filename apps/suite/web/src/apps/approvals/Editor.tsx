import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useI18n } from "@synwevo/ui";
import { Loading } from "../../shared/states";
import { base, ErrorBox, fieldTypes, joined, modes, operators, problems, stamp, Status, useAction, useRead, warnings } from "./common";
import { ApproverSelector } from "./Selector";
import { Fields } from "./Fields";
import type { Definition, Field, Stage, Template, Workspace } from "./types";

const id = (prefix: string) => `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
function move<T>(xs: T[], index: number, delta: number): T[] {
  const out = [...xs]; const target = index + delta;
  if (target < 0 || target >= out.length) return out;
  [out[index], out[target]] = [out[target], out[index]]; return out;
}

export default function Editor({ ws }: { ws: Workspace }) {
  const { tid } = useParams(); const result = useRead<Template>("template.get", { template_id: tid }, ws.id);
  if (result.error) return <ErrorBox error={result.error} retry={result.reload} />;
  if (!result.data) return <Loading />;
  return <Edit key={`${ws.id}:${tid}:${result.data.draft_rev}:${result.data.published_version}:${result.data.status}`} ws={ws} template={result.data} reload={result.reload} />;
}
export function Edit({ ws, template, reload }: { ws: Workspace; template: Template; reload: () => void }) {
  const { t } = useI18n(); const action = useAction(ws.id);
  const [title, setTitle] = useState(template.title); const [description, setDescription] = useState(template.description);
  const [history, setHistory] = useState<Definition[]>([template.draft]); const [position, setPosition] = useState(0);
  const [preview, setPreview] = useState(false); const [previewValues, setPreviewValues] = useState<Record<string, string>>({});
  const [version, setVersion] = useState<number | null>(null);
  const d = history[position];
  const allowed = ["owner", "admin"].includes(ws.role); const editable = allowed && template.status === "active" && !action.busy;
  const dirty = title !== template.title || description !== template.description || JSON.stringify(d) !== JSON.stringify(template.draft);
  const update = (next: Definition) => { setHistory([...history.slice(0, position + 1), next]); setPosition(position + 1); };
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", guard); return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        if (editable && dirty) void action.run("template.update", { template_id: template.id, draft_rev: template.draft_rev, title, description, definition: d }, reload);
      }
    };
    window.addEventListener("keydown", shortcut); return () => window.removeEventListener("keydown", shortcut);
  }, [editable, dirty, title, description, d]);
  function field(index: number, patch: Partial<Field>) { update({ ...d, fields: d.fields.map((f, n) => n === index ? { ...f, ...patch } : f) }); }
  function stage(index: number, patch: Partial<Stage>) { update({ ...d, stages: d.stages.map((s, n) => n === index ? { ...s, ...patch } : s) }); }
  return <div className="ap-editor">
    <Link to={`${base(ws.id)}/templates`} onClick={e => { if (dirty && !confirm(t("离开会丢失未保存的修改，确定离开？"))) e.preventDefault(); }}>{t("审批模板")}</Link>
    <header className="suite-head"><div><h1>{template.title}</h1><p className="lede">{t("已发布版本保持不变；新申请使用选定版本。")}</p></div>
      <div className="actions"><button className="btn" onClick={() => { if (!dirty || confirm(t("离开会丢失未保存的修改，确定离开？"))) reload(); }}>{t("刷新")}</button><button className="btn" onClick={() => setPreview(!preview)}>{t(preview ? "编辑" : "预览")}</button>
        {allowed && <><button className="btn" disabled={!editable || !dirty} onClick={() => action.run("template.update", { template_id: template.id, draft_rev: template.draft_rev, title, description, definition: d }, reload)}>{t("保存草稿")}</button>
        <button className="btn primary" disabled={!editable || dirty} onClick={() => action.run("template.publish", { template_id: template.id, draft_rev: template.draft_rev }, reload)}>{t("发布新版本")}</button></>}
      </div></header>
    <ErrorBox error={action.error} />
    <p role="status" className="muted">{t(action.busy ? "保存中…" : dirty ? "有未保存的修改" : "草稿已保存")}</p>
    {!!template.problems.length && !dirty && <div className="notice warn"><strong>{t("发布前需要修正")}</strong><ul>{template.problems.map((p, n) => <li key={n}>{joined(d.fields.find(f => f.id === p.field)?.label ?? d.stages.find(s => s.id === p.stage)?.name, t(problems[p.code] ?? "请检查字段、条件和审批人设置。"))}</li>)}</ul></div>}
    {!!template.warnings.length && !dirty && <div className="notice warn"><strong>{t("部分成员发起的申请会被拒绝")}</strong>
      <p>{t("发起人不能审批自己的申请。要让这些成员也能发起，请调整下列阶段的通过规则或审批人。")}</p>
      <ul>{template.warnings.map((w, n) => <li key={n}>{joined(d.stages.find(s => s.id === w.stage)?.name, t(warnings[w.code] ?? "请检查字段、条件和审批人设置。"))}</li>)}</ul></div>}
    {preview ? <section className="ap-panel"><h2>{title}</h2><p>{description}</p><p className="notice">{t("草稿预览不会创建申请或保存填写内容。")}</p><Fields fields={d.fields} values={previewValues} setValues={setPreviewValues} /></section> : <>
      <section className="ap-panel ap-fields"><label>{t("模板名称")}<input className="input" maxLength={200} value={title} disabled={!editable} onChange={e => setTitle(e.target.value)} /></label>
        <label>{t("模板说明")}<textarea className="input" maxLength={2000} value={description} disabled={!editable} onChange={e => setDescription(e.target.value)} /></label>
        <label className="ap-choice"><input type="checkbox" disabled={!editable} checked={d.rules.distinct_deciders} onChange={e => update({ ...d, rules: { distinct_deciders: e.target.checked } })} />{t("不同阶段必须由不同的人决定")}</label>
        <p className="muted">{t("发起人始终不能审批自己的申请。没有有效审批人时等待修正，不会自动通过。")}</p>
        <div className="ap-toolbar"><button className="btn sm" disabled={!editable || !position} onClick={() => setPosition(position - 1)}>{t("撤销编辑")}</button>
          <button className="btn sm" disabled={!editable || position === history.length - 1} onClick={() => setPosition(position + 1)}>{t("重做编辑")}</button></div>
      </section>
      <section className="ap-panel"><div className="ap-toolbar"><h2>{t("申请字段")}</h2><button className="btn" disabled={!editable || d.fields.length >= 50}
        onClick={() => update({ ...d, fields: [...d.fields, { id: id("f"), label: t("新字段"), type: "text", required: false, options: [] }] })}>{t("添加字段")}</button></div>
        {d.fields.map((f, n) => <fieldset className="ap-edit-block" key={f.id} disabled={!editable}><legend>{f.label}</legend>
          <div className="ap-grid"><label>{t("字段名称")}<input className="input" value={f.label} maxLength={200} onChange={e => field(n, { label: e.target.value })} /></label>
            <label>{t("字段类型")}<select className="input" value={f.type} onChange={e => field(n, { type: e.target.value })}>{Object.entries(fieldTypes).map(([v, key]) => <option key={v} value={v}>{t(key)}</option>)}</select></label>
            <label>{t("字段提示")}<input className="input" value={f.help ?? ""} maxLength={500} onChange={e => field(n, { help: e.target.value })} /></label>
            <label className="ap-choice"><input type="checkbox" checked={f.required} onChange={e => field(n, { required: e.target.checked })} />{t("必填")}</label>
            {f.type === "money" && <label>{t("币种代码")}<input className="input" value={f.currency ?? ""} maxLength={3} pattern="[A-Z]{3}" onChange={e => field(n, { currency: e.target.value.toUpperCase() })} /></label>}
            {f.type === "number" && <label>{t("最多小数位")}<input className="input" type="number" min={0} max={6} value={f.decimals ?? 6} onChange={e => field(n, { decimals: Number(e.target.value) })} /></label>}
            {["number", "money"].includes(f.type) && <>{["min", "max"].map(k => <label key={k}>{t(k === "min" ? "最小值" : "最大值")}<input className="input" inputMode="decimal" value={f[k as "min" | "max"] ?? ""} onChange={e => field(n, { [k]: e.target.value || null })} /></label>)}</>}
            {["text", "textarea"].includes(f.type) && <label>{t("最长字符数")}<input className="input" type="number" min={1} max={f.type === "text" ? 2000 : 10000} value={f.max_length ?? (f.type === "text" ? 2000 : 10000)} onChange={e => field(n, { max_length: Number(e.target.value) })} /></label>}
          </div>
          {f.type === "select" && <div className="ap-fields">{f.options.map((o, x) => <div className="ap-toolbar" key={x}>
            <label>{t("选项值")}<input className="input" value={o.value} maxLength={100} onChange={e => field(n, { options: f.options.map((v, j) => j === x ? { ...v, value: e.target.value } : v) })} /></label>
            <label>{t("选项名称")}<input className="input" value={o.label} maxLength={200} onChange={e => field(n, { options: f.options.map((v, j) => j === x ? { ...v, label: e.target.value } : v) })} /></label>
            <button className="btn sm" onClick={() => field(n, { options: f.options.filter((_, j) => j !== x) })}>{t("删除选项")}</button></div>)}
            <button className="btn" disabled={f.options.length >= 100} onClick={() => field(n, { options: [...f.options, { value: `option_${f.options.length + 1}`, label: t("新选项") }] })}>{t("添加选项")}</button></div>}
          <div className="ap-toolbar"><button className="btn sm" disabled={!n} onClick={() => update({ ...d, fields: move(d.fields, n, -1) })}>{t("上移")}</button>
            <button className="btn sm" disabled={n === d.fields.length - 1} onClick={() => update({ ...d, fields: move(d.fields, n, 1) })}>{t("下移")}</button>
            <button className="btn ghost sm" onClick={() => update({ ...d, fields: d.fields.filter((_, j) => j !== n) })}>{t("删除字段")}</button></div>
        </fieldset>)}
      </section>
      <section className="ap-panel"><div className="ap-toolbar"><h2>{t("按顺序执行的审批阶段")}</h2><button className="btn" disabled={!editable || d.stages.length >= 20}
        onClick={() => update({ ...d, stages: [...d.stages, { id: id("s"), name: t("新审批阶段"), mode: "all", approvers: { users: [] } }] })}>{t("添加阶段")}</button></div>
        <p className="muted">{t("每个阶段通过后才进入下一个阶段。会签需要所有指定人同意，或签只需一人；任何驳回都会终止申请。")}</p>
        {d.stages.map((s, n) => <fieldset className="ap-edit-block" key={s.id} disabled={!editable}><legend>{n + 1}. {s.name}</legend>
          <div className="ap-grid"><label>{t("阶段名称")}<input className="input" maxLength={100} value={s.name} onChange={e => stage(n, { name: e.target.value })} /></label>
            <label>{t("通过规则")}<select className="input" value={s.mode} onChange={e => stage(n, { mode: e.target.value, required: e.target.value === "n_of_m" ? s.required ?? 1 : null })}>{Object.entries(modes).map(([v, key]) => <option key={v} value={v}>{t(key)}</option>)}</select></label>
            {s.mode === "n_of_m" && <label>{t("需要同意的人数")}<input className="input" type="number" min={1} max={50} value={s.required ?? 1} onChange={e => stage(n, { required: Number(e.target.value) })} /></label>}
          </div>
          <ApproverSelector ws={ws.id} value={s.approvers} onChange={v => stage(n, { approvers: v })} disabled={!editable} />
          <label className="ap-choice"><input type="checkbox" checked={!!s.condition} onChange={e => stage(n, { condition: e.target.checked ? { match: "all", rules: [{ field: d.fields[0]?.id ?? "", op: "filled" }] } : null })} />{t("仅在条件满足时执行")}</label>
          {s.condition && <div className="ap-condition"><label>{t("条件组合")}<select className="input" value={s.condition.match} onChange={e => stage(n, { condition: { ...s.condition!, match: e.target.value } })}><option value="all">{t("满足全部条件")}</option><option value="any">{t("满足任一条件")}</option></select></label>
            {s.condition.rules.map((rule, x) => {
              function patch(v: object) { stage(n, { condition: { ...s.condition!, rules: s.condition!.rules.map((r, j) => j === x ? { ...r, ...v } : r) } }); }
              return <div className="ap-grid" key={x}><label>{t("条件字段")}<select className="input" value={rule.field} onChange={e => patch({ field: e.target.value })}><option value="">{t("请选择")}</option>{d.fields.map(f => <option value={f.id} key={f.id}>{f.label}</option>)}</select></label>
                <label>{t("比较方式")}<select className="input" value={rule.op} onChange={e => patch({ op: e.target.value, value: null })}>{Object.entries(operators).map(([v, key]) => <option value={v} key={v}>{t(key)}</option>)}</select></label>
                {!["filled", "empty"].includes(rule.op) && <label>{t(["in", "not_in"].includes(rule.op) ? "比较值（每行一个）" : "比较值")}<textarea className="input" value={Array.isArray(rule.value) ? rule.value.join("\n") : rule.value ?? ""} onChange={e => patch({ value: ["in", "not_in"].includes(rule.op) ? e.target.value.split("\n") : e.target.value })} /></label>}
                <button className="btn sm" onClick={() => stage(n, { condition: { ...s.condition!, rules: s.condition!.rules.filter((_, j) => j !== x) } })}>{t("删除条件")}</button></div>;
            })}
            <button className="btn sm" disabled={s.condition.rules.length >= 10} onClick={() => stage(n, { condition: { ...s.condition!, rules: [...s.condition!.rules, { field: d.fields[0]?.id ?? "", op: "filled" }] } })}>{t("添加条件")}</button>
          </div>}
          <label className="ap-choice"><input type="checkbox" checked={!!s.due} onChange={e => stage(n, { due: e.target.checked ? { hours: 24, remind: true } : null })} />{t("设置超时提醒")}</label>
          {s.due && <div className="ap-condition"><div className="ap-grid"><label>{t("阶段时限（小时）")}<input className="input" type="number" min={1} max={2160} value={s.due.hours} onChange={e => stage(n, { due: { ...s.due!, hours: Number(e.target.value) } })} /></label>
            <label className="ap-choice"><input type="checkbox" checked={s.due.remind} onChange={e => stage(n, { due: { ...s.due!, remind: e.target.checked } })} />{t("到期提醒发起人与审批人")}</label></div>
            <label className="ap-choice"><input type="checkbox" checked={!!s.due.escalation} onChange={e => stage(n, { due: { ...s.due!, escalation: e.target.checked ? { after_hours: 24, action: "notify_requester", approvers: { users: [] } } : null } })} />{t("超时升级")}</label>
            {s.due.escalation && <><div className="ap-grid"><label>{t("到期后等待（小时）")}<input className="input" type="number" min={0} max={720} value={s.due.escalation.after_hours} onChange={e => stage(n, { due: { ...s.due!, escalation: { ...s.due!.escalation!, after_hours: Number(e.target.value) } } })} /></label>
              <label>{t("升级方式")}<select className="input" value={s.due.escalation.action} onChange={e => stage(n, { due: { ...s.due!, escalation: { ...s.due!.escalation!, action: e.target.value } } })}><option value="notify_requester">{t("提醒发起人跟进")}</option><option value="add_approvers">{t("增加可处理的审批人")}</option></select></label></div>
              {s.due.escalation.action === "add_approvers" && <ApproverSelector ws={ws.id} value={s.due.escalation.approvers} disabled={!editable} onChange={v => stage(n, { due: { ...s.due!, escalation: { ...s.due!.escalation!, approvers: v } } })} />}</>}
            <p className="muted">{t("提醒会保存在审批中心。升级不会减少所需票数，也不会自动批准。")}</p>
          </div>}
          <div className="ap-toolbar"><button className="btn sm" disabled={!n} onClick={() => update({ ...d, stages: move(d.stages, n, -1) })}>{t("上移")}</button><button className="btn sm" disabled={n === d.stages.length - 1} onClick={() => update({ ...d, stages: move(d.stages, n, 1) })}>{t("下移")}</button>
            <button className="btn ghost sm" onClick={() => update({ ...d, stages: d.stages.filter((_, j) => j !== n) })}>{t("删除阶段")}</button></div>
        </fieldset>)}
      </section>
    </>}
    <section className="ap-panel"><h2>{t("版本记录")}</h2>{template.versions.map(v => <div className="ap-toolbar" key={v.version}><button className="btn ghost" onClick={() => setVersion(v.version)}>{t("版本 {v}", { v: v.version })}</button><Status value={v.status} /><time>{stamp(v.published_at)}</time><span>{v.note}</span>
      {allowed && v.status === "published" && <button className="btn sm" disabled={action.busy} onClick={() => { if (confirm(t("停用后不能发起新申请，已有申请继续使用该版本。确定停用？"))) void action.run("template.retire", { template_id: template.id, version: v.version }, reload); }}>{t("停用")}</button>}</div>)}
      {version !== null && <VersionPreview ws={ws.id} tid={template.id} version={version} />}
      {allowed && <button className="btn" disabled={action.busy || dirty} onClick={() => action.run("template.archive", { template_id: template.id, archived: template.status !== "archived" }, reload)}>{t(template.status === "archived" ? "恢复模板" : "归档模板")}</button>}
    </section>
  </div>;
}
function VersionPreview({ ws, tid, version }: { ws: string; tid: string; version: number }) {
  const { t } = useI18n(); const result = useRead<{ definition: Definition; title: string }>("template.version", { template_id: tid, version }, ws);
  if (result.error) return <ErrorBox error={result.error} />; if (!result.data) return <Loading />;
  return <div className="ap-condition"><h3>{result.data.title} · {t("版本 {v}", { v: version })}</h3><Fields fields={result.data.definition.fields} values={{}} setValues={() => {}} disabled />
    <ol>{result.data.definition.stages.map(s => <li key={s.id}>{s.name} · {t(modes[s.mode])} · {t("指定 {count} 人", { count: s.approvers.users.length })}{s.condition && ` · ${t("条件路由")}`}</li>)}</ol></div>;
}
