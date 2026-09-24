import { useEffect, useRef, useState } from "react";
import { get, patch, post, qs } from "@synwevo/auth-client";
import { useLatest } from "../lib/latest";
import type { Project, TemplateDetail } from "../lib/types";
import { errorText } from "../lib/perm";
import { Modal } from "@synwevo/ui";

/** The request id of a batch the user is starting, kept per project and per settings for the
 *  browser tab. Closing the dialog while the server was still creating a large job and opening it
 *  again used to mint a new id, so the second 开始生成 queued a second full job for the same
 *  batch. The same settings now reuse the same id (the server answers with the job it already
 *  has); a successful start forgets it, so the next batch is a new one. */
const REQ_KEY = (pid: string) => `synwevo.docs.genreq.${pid}`;
function requestId(pid: string, settings: string): string {
  try {
    const raw = sessionStorage.getItem(REQ_KEY(pid));
    const hit = raw ? JSON.parse(raw) : null;
    if (hit && hit.settings === settings && typeof hit.id === "string") return hit.id;
  } catch { /* storage unavailable: fall through to a fresh id */ }
  const id = `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  try { sessionStorage.setItem(REQ_KEY(pid), JSON.stringify({ id, settings })); } catch { /* not remembered */ }
  return id;
}
function forgetRequest(pid: string) {
  try { sessionStorage.removeItem(REQ_KEY(pid)); } catch { /* nothing to forget */ }
}

export default function GenerateDialog({ project, tpl, onStarted, onClose }: { project: Project; tpl: TemplateDetail; onStarted: (jobId: string) => void; onClose: () => void }) {
  const native = tpl.exports.find((e) => e.native)?.format || tpl.format;
  const [formats, setFormats] = useState<string[]>(project.output?.formats?.length ? project.output.formats : [native]);
  const [naming, setNaming] = useState(project.output?.naming || "{序号}");
  const [policy, setPolicy] = useState(project.output?.error_policy || "block");
  const [layout, setLayout] = useState<string>(project.output?.layout || "off");
  const pageable = ["docx", "xlsx", "pptx"].includes(native);
  const hasShrink = tpl.version.slots.some((s: any) => s.shrink);
  const [sum, setSum] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const latest = useLatest();
  const summaryKey = JSON.stringify([project.id, formats, naming]);
  const [checkedKey, setCheckedKey] = useState("");
  const ready = checkedKey === summaryKey;

  useEffect(() => {
    let cancel = () => {};
    setCheckedKey("");
    setErr(null);
    const t = setTimeout(() => {
      // The counts and filename examples this fills in are what the user checks before starting
      // a batch, so a stale answer that outran a newer one is worse than no answer.
      cancel = latest(
        (signal) => get(`/api/projects/${project.id}/generation-summary${qs({ formats: formats.join(","), naming })}`, signal),
        (s) => { setSum(s); setCheckedKey(summaryKey); setErr(null); },
        (e) => { setCheckedKey(""); setErr(errorText(e)); },
      );
    }, 200);
    return () => { clearTimeout(t); cancel(); };
  }, [summaryKey, latest]);

  const start = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const body = { formats, naming, error_policy: policy, layout: pageable ? layout : "off" };
      const rid = requestId(project.id, JSON.stringify(body));
      await patch(`/api/projects/${project.id}`, { output: { naming, formats, error_policy: policy, layout } });
      const j = await post(`/api/projects/${project.id}/jobs`, { ...body, request_id: rid });
      forgetRequest(project.id);
      onStarted(j.id);
    } catch (e: any) {
      // 429 queue_full: the workspace already has as many active jobs as it may; say when to retry.
      const wait = e?.code === "queue_full" ? `（当前进行中的任务已达上限${e.detail?.retry_after ? `，约 ${e.detail.retry_after} 秒后再试` : "，请等已有任务结束后再试"}）` : "";
      setErr(errorText(e) + wait + (e.detail?.invalid ? `（例：${e.detail.invalid[0]?.record}：${e.detail.invalid[0]?.issues?.[0]?.message}）` : ""));
    } finally {
      setBusy(false);
    }
  };

  const insertToken = (t: string) => {
    const el = inputRef.current;
    const s = el?.selectionStart ?? naming.length, e = el?.selectionEnd ?? naming.length;
    setNaming(naming.slice(0, s) + `{${t}}` + naming.slice(e));
  };

  const blocked = sum && sum.invalid > 0 && policy === "block";
  const nFormats = formats.filter((f) => sum?.formats?.find((x: any) => x.format === f)?.available).length;

  return (
    // While the job is being created the dialog stays open: closing it would not stop the request.
    <Modal title="批量生成" size="wide" onClose={() => { if (!busy) onClose(); }}
      footer={<>
        {err && <div className="notice err grow" role="alert">{err}</div>}
        <span className="spacer" />
        <button type="button" className="btn" disabled={busy} onClick={onClose}>取消</button>
        <button type="button" className="btn primary" disabled={busy || !ready || !sum || blocked || !nFormats || (sum && sum.valid === 0)} onClick={start}>
          {busy ? "正在创建任务…" : ready && sum ? `开始生成 ${policy === "skip" ? sum.valid * nFormats : sum.records * nFormats} 个文件` : "等待样本检查"}
        </button>
      </>}>
      {!ready && !err && <div className="notice info" role="status">正在检查当前设置下的所有样本…</div>}
      {sum && (
        <fieldset className="col gap12" disabled={busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          {ready && <>
          <div className="stats">
            <div className="statbox"><div className="v">{sum.input_files}</div><div className="k">输入文件（可用 {sum.ready_files}）</div></div>
            <div className="statbox"><div className="v">{sum.records}</div><div className="k">识别样本</div></div>
            <div className="statbox"><div className="v" style={{ color: "var(--ok)" }}>{sum.valid}</div><div className="k">有效样本</div></div>
            <div className="statbox"><div className="v" style={{ color: sum.invalid ? "var(--err)" : undefined }}>{sum.invalid}</div><div className="k">有错误的样本</div></div>
            <div className="statbox"><div className="v" style={{ color: sum.warnings ? "var(--warn)" : undefined }}>{sum.warnings}</div><div className="k">警告</div></div>
            <div className="statbox"><div className="v">{policy === "skip" ? sum.valid * nFormats : sum.invalid ? 0 : sum.records * nFormats}</div><div className="k">预计输出文件</div></div>
          </div>
          {formats.length > 1 && <div className="notice info small">选择了 {nFormats} 种格式：{sum.valid} 个有效样本 × {nFormats} 种格式 = {sum.valid * nFormats} 个文件（每个样本各生成一份）。</div>}
          {sum.file_errors.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
          {sum.static_issues.map((e: any, i: number) => <div key={i} className="notice err small">{e.message}</div>)}
          {sum.record_warnings.map((w: string, i: number) => <div key={i} className="notice warn small">{w}</div>)}
          {sum.invalid > 0 && (
            <div className="notice err small">
              <b>{sum.invalid} 个样本存在错误</b>：
              {Object.entries(sum.issues_by_code).map(([k, v]) => <span key={k} className="badge err" style={{ margin: "0 4px" }}>{CODE[k] || k} × {v as number}</span>)}
              <ul style={{ margin: "6px 0 0", paddingLeft: 18, maxHeight: 120, overflow: "auto" }}>
                {sum.invalid_samples.slice(0, 20).map((x: any, i: number) => <li key={i}>{x.record}：{x.issues[0]?.message}</li>)}
              </ul>
            </div>
          )}
          </>}
          <div className="grid2">
            <div className="col">
              <h3>输出格式</h3>
              {tpl.exports.map((e) => (
                <label key={e.format} className={`opt${formats.includes(e.format) ? " on" : ""}`} style={{ opacity: e.available ? 1 : 0.7 }}>
                  <input type="checkbox" disabled={!e.available} checked={formats.includes(e.format)} onChange={(ev) => setFormats(ev.target.checked ? [...formats, e.format] : formats.filter((f) => f !== e.format))} />
                  <div>
                    <div className="t">{e.label} {!e.available && <span className="badge warn">不可用</span>}</div>
                    {e.reason && <div className="small muted">{e.reason}</div>}
                    {e.note && <div className="small muted">{e.note}</div>}
                  </div>
                </label>
              ))}
              {native === "xlsx" && (
                <div className="notice small">公式由内置公式引擎计算并写入结果（{">"}90 个常用函数）；少数不支持的函数会自动改用 LibreOffice 计算，结果清单中会注明。</div>
              )}
              {pageable && (
                <>
                  <h3 className="mt8">分页检查</h3>
                  <select className="input" value={layout} onChange={(e) => setLayout(e.target.value)} aria-label="分页检查">
                    <option value="off">不检查（最快）</option>
                    <option value="warn">逐份用 LibreOffice 排版，页数多于模板时提示</option>
                    <option value="fit">保持模板页数：先缩小允许缩小的字段，仍超出则该份失败</option>
                  </select>
                  <div className="tiny muted">
                    {layout === "off" ? "新内容更长时分页可能变化；可在预览中打开「真实分页」查看。" :
                      layout === "warn" ? "每份多一次 LibreOffice 排版（约 1 秒）；超出的样本会在结果清单中标出。" :
                        hasShrink ? "只缩小字段设置中勾选了「超长时允许缩小字号」的字段，每一步都重新排版确认，不会估算。" :
                          "模板中还没有字段勾选「超长时允许缩小字号」：超出页数的样本会直接失败。"}
                  </div>
                </>
              )}
            </div>
            <div className="col">
              <h3>文件命名</h3>
              <input ref={inputRef} className="input mono" value={naming} onChange={(e) => setNaming(e.target.value)} aria-label="文件命名规则" />
              <div className="row wrap gap4">{sum.tokens.map((t: string) => <button type="button" key={t} className="btn xs" onClick={() => insertToken(t)} aria-label={`插入 {${t}}`}>{`{${t}}`}</button>)}</div>
              <div className="small muted">示例：{ready && sum.naming_examples.length ? sum.naming_examples.map((n: string, i: number) => <code key={i} style={{ marginRight: 8 }}>{n}.{formats[0] || native}</code>) : "—"}</div>
              <div className="tiny muted">非法字符会被替换，过长会截断；重名自动加序号「(2)」，不会覆盖。</div>
              <h3 className="mt8">遇到错误时</h3>
              <label className={`opt${policy === "block" ? " on" : ""}`}><input type="radio" checked={policy === "block"} onChange={() => setPolicy("block")} /><div><div className="t">有错误则不开始（默认）</div><div className="small muted">先修正所有错误样本</div></div></label>
              <label className={`opt${policy === "skip" ? " on" : ""}`}><input type="radio" checked={policy === "skip"} onChange={() => setPolicy("skip")} /><div><div className="t">跳过错误样本，生成其余样本</div><div className="small muted">跳过的样本会逐条列在结果清单中</div></div></label>
            </div>
          </div>
        </fieldset>
      )}
    </Modal>
  );
}

export const CODE: Record<string, string> = {
  missing_column: "缺少列", ambiguous_column: "重名列", missing_sheet: "缺少工作表", ambiguous_sheet: "工作表歧义", anchor_missing: "找不到定位标签",
  anchor_ambiguous: "定位标签不唯一", layout_mismatch: "版式不符", required_empty: "必填为空", unmapped_required: "必填未映射", group_conflict: "组内取值冲突",
  region_size: "区域尺寸不符", target_missing: "目标字段失效", target_conflict: "多个映射写同一字段", needs_ocr: "需要 OCR", ocr_low_confidence: "OCR 待确认",
  path_missing: "结构不存在", mode_mismatch: "与样本模式不符", value_error: "值错误", no_detail: "无明细", type_mismatch: "类型不匹配", combine_missing: "缺少组合规则",
};
