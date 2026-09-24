import { useEffect, useRef, useState } from "react";
import { del, post, put, upload } from "@synwevo/auth-client";
import type { SourceFile } from "../lib/types";
import { errorText, NO_EXPORT_TEXT, READ_ONLY_TEXT } from "../lib/perm";
import { FAMILY_ICON, fmtSize, isImeKey, STATUS_TEXT, useDialog, useToast } from "@synwevo/ui";

interface Props {
  projectId: string;
  sources: SourceFile[];
  active: string | null;
  onActive: (id: string) => void;
  /** Reload the project; may return the reload's promise, which act() waits for. */
  onChanged: () => unknown;
  collapsed: boolean;
  onCollapse: (v: boolean) => void;
  /** Viewer role: no upload, reorder, representative or remove. */
  readOnly?: boolean;
  /** Without export permission the 原件 download is not offered. */
  canExport?: boolean;
  /** Incremented by the command palette's 上传来源文件…: opens the file chooser. */
  uploadRequest?: number;
}

export default function FilePanel({ projectId, sources, active, onActive, onChanged, collapsed, onCollapse, readOnly = false, canExport = true, uploadRequest = 0 }: Props) {
  const [hot, setHot] = useState(false);
  const [uploads, setUploads] = useState<{ id: number; name: string; p: number; err?: string }[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [said, setSaid] = useState("");                    // polite announcement after a move
  const uploadSeq = useRef(0);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const toast = useToast();
  const { confirm } = useDialog();

  useEffect(() => {
    if (uploadRequest && !readOnly) input.current?.click();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadRequest]);

  const doUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length || readOnly) return;
    const batch = list.map((file) => ({ file, id: ++uploadSeq.current }));
    const ids = new Set(batch.map((x) => x.id));
    setUploads((u) => [...u, ...batch.map(({ file, id }) => ({ id, name: file.name, p: 0 }))]);
    // one request per file so a single failure never hides the others
    await Promise.all(batch.map(async ({ file: f, id }) => {
      const fd = new FormData();
      fd.append("files", f);
      try {
        await upload(`/api/projects/${projectId}/sources`, fd, (p) => setUploads((u) => u.map((x) => (x.id === id ? { ...x, p } : x))));
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, p: 1 } : x)));
      } catch (e: any) {
        const msg = errorText(e);
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, err: msg } : x)));
        toast(`${f.name}：${msg}`, "err");
      }
    }));
    onChanged();
    setTimeout(() => setUploads((u) => u.filter((x) => !ids.has(x.id) || x.err)), 1500);
  };

  /** Every change here waits for the server: the list is redrawn from its answer, never ahead
   *  of it, and a refusal is said out loud instead of being an unhandled rejection. Resolves
   *  once the reload has come back too. */
  const act = async (what: string, fn: () => Promise<unknown>) => {
    let ok = true;
    try {
      await fn();
    } catch (e: any) {
      ok = false;
      toast(`${what}失败：${errorText(e)}`, "err");
    }
    await onChanged();
    return ok;
  };

  // Keyboard and button moves keep focus on what was used. The moved row is re-inserted by React
  // (a keyed move is an insertBefore), and Chrome drops focus from a node that is moved; the new
  // order only arrives with the reload, so focus is put back once the list is drawn in it — not
  // on a timer that fired before the reload had finished.
  type Refocus = { id: string; which: "name" | "up" | "down"; to: number };
  const pendingFocus = useRef<Refocus | null>(null);
  const restoreFocus = (p: Refocus) => {
    const list = listRef.current;
    const a = document.activeElement;
    // Someone who has moved on to another control keeps it.
    if (!list || (a && a !== document.body && !list.contains(a))) return;
    const id = CSS.escape(p.id);
    const btn = p.which === "name" ? null : list.querySelector<HTMLButtonElement>(`[data-move="${p.which}"][data-for="${id}"]`);
    (btn && !btn.disabled ? btn : list.querySelector<HTMLElement>(`[data-file="${id}"]`))?.focus();
  };
  useEffect(() => {
    const p = pendingFocus.current;
    // A parse poll can redraw the list before the reorder's reload: wait for the new order.
    if (!p || sources[p.to]?.id !== p.id) return;
    pendingFocus.current = null;
    restoreFocus(p);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sources]);

  const moveTo = async (id: string, to: number, which?: Refocus["which"]) => {
    const ids = sources.map((s) => s.id);
    const from = ids.indexOf(id);
    if (from < 0 || to < 0 || to >= ids.length || from === to) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    const name = sources[from].filename;
    pendingFocus.current = which ? { id, which, to } : null;
    if (await act("调整顺序", () => put(`/api/projects/${projectId}/sources/order`, { ids }))) {
      setSaid(`「${name}」已移到第 ${to + 1} 位，共 ${ids.length} 个文件`);
    } else if (pendingFocus.current?.id === id) {
      // Refused: the list keeps its old order, so the effect above will not see the move.
      const p = pendingFocus.current;
      pendingFocus.current = null;
      restoreFocus({ ...p, which: "name" });
    }
  };
  const reorder = (fromId: string, toId: string) => moveTo(fromId, sources.findIndex((s) => s.id === toId));

  if (collapsed) {
    return (
      <div className="side collapsed">
        <button type="button" className="btn ghost icon" title="展开文件列表" aria-label="展开来源文件列表" onClick={() => onCollapse(false)}>»</button>
        <div className="tiny muted" style={{ writingMode: "vertical-rl", margin: "8px auto" }}>来源文件 {sources.length}</div>
      </div>
    );
  }
  return (
    <div className="side" onDragOver={(e) => { if (!readOnly && e.dataTransfer.types.includes("Files")) { e.preventDefault(); setHot(true); } }}
      onDragLeave={() => setHot(false)} onDrop={(e) => { if (!readOnly && e.dataTransfer.files.length) { e.preventDefault(); setHot(false); doUpload(e.dataTransfer.files); } }}>
      <div className="pane-h">
        <span className="title grow">来源文件 <span className="muted">{sources.length}</span></span>
        <button type="button" className="btn ghost sm icon" title="收起" aria-label="收起来源文件列表" onClick={() => onCollapse(true)}>«</button>
      </div>
      {readOnly ? (
        <div className="notice small" style={{ margin: 6 }}>{READ_ONLY_TEXT}</div>
      ) : (
        // A real button: reachable with Tab and activated with Enter / Space. The drop target
        // for dragged files is the whole panel, so dragging stays an extra, not the only way.
        <button type="button" className={`dropzone${hot ? " hot" : ""}`} onClick={() => input.current?.click()}>
          ＋ 拖入或点击上传<div className="tiny">可多选：XLSX/CSV/DOCX/PDF/图片/PPTX 等</div>
        </button>
      )}
      <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ""; }} />
      {uploads.map((u) => (
        <div key={u.id} className="small" style={{ padding: "2px 10px" }}>
          <div className="ellipsis">{u.name}</div>
          {u.err ? <div className="tiny" style={{ color: "var(--err)" }}>{u.err}</div>
            : <div className="progress" style={{ height: 4 }} role="progressbar" aria-label={`上传 ${u.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(u.p * 100)}><div style={{ width: `${u.p * 100}%` }} /></div>}
        </div>
      ))}
      <div className="sr-only" role="status" aria-live="polite">{said}</div>
      <div className="filelist" ref={listRef}>
        {sources.map((s, idx) => (
          <div key={s.id} className={`fileitem${s.id === active ? " on" : ""}${overId === s.id ? " dragover" : ""}`} onClick={() => onActive(s.id)}
            draggable={!readOnly} onDragStart={() => setDragId(s.id)} onDragEnd={() => { setDragId(null); setOverId(null); }}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverId(s.id); } }}
            onDrop={(e) => { if (dragId) { e.preventDefault(); e.stopPropagation(); reorder(dragId, s.id); setDragId(null); setOverId(null); } }}>
            {!readOnly && <span className="handle" aria-hidden="true" title="拖动调整顺序（也可用下方的上移 / 下移，或 Alt+↑ / ↓）">⋮⋮</span>}
            <span style={{ width: 16, textAlign: "center" }} title={s.format} aria-hidden="true">{FAMILY_ICON[s.family] || "?"}</span>
            <div className="grow">
              {/* The name is the keyboard handle for the row: Enter/Space opens the file,
                  Alt+↑/↓ moves it (Linear's reorder keys). */}
              <button type="button" data-file={s.id} className="fname" aria-pressed={s.id === active}
                aria-label={`${s.filename}，第 ${idx + 1} 个，共 ${sources.length} 个${s.representative ? "，代表文件" : ""}`}
                onClick={(e) => { e.stopPropagation(); onActive(s.id); }}
                onKeyDown={async (e) => {
                  if (isImeKey(e) || readOnly || !e.altKey) return;
                  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
                    e.preventDefault();
                    await moveTo(s.id, idx + (e.key === "ArrowUp" ? -1 : 1), "name");
                  }
                }}>{s.filename}</button>
              <div className="fmeta row gap4 wrap">
                <span>{(s.format || "?").toUpperCase()}</span><span>{fmtSize(s.size)}</span>
                <span className={`badge ${s.status === "ready" ? "ok" : s.status === "parsing" ? "info" : s.status === "needs_ocr" ? "warn" : "err"}`}>{STATUS_TEXT[s.status] || s.status}</span>
                {s.representative && <span className="badge info" title="用于建立规则的代表文件">代表</span>}
              </div>
              {s.error && <div className="tiny" style={{ color: "var(--err)" }}>{s.error}</div>}
              {s.id === active && (
                <div className="row gap4 mt8 wrap" onClick={(e) => e.stopPropagation()}>
                  {!readOnly && sources.length > 1 && (
                    // Single-pointer alternative to dragging (WCAG 2.5.7).
                    <span className="moves">
                      <button type="button" className="btn xs" disabled={idx === 0} aria-label={`上移「${s.filename}」`} title="上移（Alt+↑）"
                        data-move="up" data-for={s.id} onClick={() => moveTo(s.id, idx - 1, "up")}>↑ 上移</button>
                      <button type="button" className="btn xs" disabled={idx === sources.length - 1} aria-label={`下移「${s.filename}」`} title="下移（Alt+↓）"
                        data-move="down" data-for={s.id} onClick={() => moveTo(s.id, idx + 1, "down")}>↓ 下移</button>
                    </span>
                  )}
                  {!readOnly && !s.representative && s.status === "ready" && (
                    <button type="button" className="btn xs" onClick={() => act("设为代表文件", () => post(`/api/sources/${s.id}/representative`))}>设为代表</button>
                  )}
                  {canExport
                    ? <a className="btn xs" href={`/api/sources/${s.id}/download`} title="下载原文件（未修改）">原件</a>
                    : <span className="tiny muted" title={NO_EXPORT_TEXT}>无下载权限</span>}
                  {!readOnly && (
                    <button type="button" className="btn xs danger" onClick={async () => {
                      if (await confirm("移除来源文件", `从项目中移除「${s.filename}」？原文件本身不受影响；已生成的历史结果仍保留。`, "移除", true)) {
                        await act("移除来源文件", () => del(`/api/sources/${s.id}`));
                      }
                    }}>移除</button>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
        {!sources.length && <div className="empty small">还没有来源文件</div>}
      </div>
    </div>
  );
}
