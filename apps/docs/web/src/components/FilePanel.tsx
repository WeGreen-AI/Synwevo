import { useRef, useState } from "react";
import { del, post, put, upload } from "@synwevo/auth-client";
import type { SourceFile } from "../lib/types";
import { FAMILY_ICON, fmtSize, STATUS_TEXT, useDialog, useToast } from "@synwevo/ui";

interface Props {
  projectId: string;
  sources: SourceFile[];
  active: string | null;
  onActive: (id: string) => void;
  onChanged: () => void;
  collapsed: boolean;
  onCollapse: (v: boolean) => void;
}

export default function FilePanel({ projectId, sources, active, onActive, onChanged, collapsed, onCollapse }: Props) {
  const [hot, setHot] = useState(false);
  const [uploads, setUploads] = useState<{ name: string; p: number; err?: string }[]>([]);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const { confirm } = useDialog();

  const doUpload = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setUploads(list.map((f) => ({ name: f.name, p: 0 })));
    // one request per file so a single failure never hides the others
    await Promise.all(list.map(async (f, i) => {
      const fd = new FormData();
      fd.append("files", f);
      try {
        await upload(`/api/projects/${projectId}/sources`, fd, (p) => setUploads((u) => u.map((x, k) => (k === i ? { ...x, p } : x))));
        setUploads((u) => u.map((x, k) => (k === i ? { ...x, p: 1 } : x)));
      } catch (e: any) {
        setUploads((u) => u.map((x, k) => (k === i ? { ...x, err: e.message } : x)));
        toast(`${f.name}：${e.message}`, "err");
      }
    }));
    onChanged();
    setTimeout(() => setUploads((u) => u.filter((x) => x.err)), 1500);
  };

  const reorder = async (fromId: string, toId: string) => {
    const ids = sources.map((s) => s.id);
    const from = ids.indexOf(fromId), to = ids.indexOf(toId);
    if (from < 0 || to < 0 || from === to) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    await put(`/api/projects/${projectId}/sources/order`, { ids });
    onChanged();
  };

  if (collapsed) {
    return (
      <div className="side collapsed">
        <button className="btn ghost icon" title="展开文件列表" onClick={() => onCollapse(false)}>»</button>
        <div className="tiny muted" style={{ writingMode: "vertical-rl", margin: "8px auto" }}>来源文件 {sources.length}</div>
      </div>
    );
  }
  return (
    <div className="side" onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setHot(true); } }}
      onDragLeave={() => setHot(false)} onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); setHot(false); doUpload(e.dataTransfer.files); } }}>
      <div className="pane-h">
        <span className="title grow">来源文件 <span className="muted">{sources.length}</span></span>
        <button className="btn ghost sm icon" title="收起" onClick={() => onCollapse(true)}>«</button>
      </div>
      <div className={`dropzone${hot ? " hot" : ""}`} onClick={() => input.current?.click()}>
        ＋ 拖入或点击上传<div className="tiny">可多选：XLSX/CSV/DOCX/PDF/图片/PPTX 等</div>
      </div>
      <input ref={input} type="file" multiple hidden onChange={(e) => { if (e.target.files) doUpload(e.target.files); e.target.value = ""; }} />
      {uploads.map((u, i) => (
        <div key={i} className="small" style={{ padding: "2px 10px" }}>
          <div className="ellipsis">{u.name}</div>
          {u.err ? <div className="tiny" style={{ color: "var(--err)" }}>{u.err}</div> : <div className="progress" style={{ height: 4 }}><div style={{ width: `${u.p * 100}%` }} /></div>}
        </div>
      ))}
      <div className="filelist">
        {sources.map((s) => (
          <div key={s.id} className={`fileitem${s.id === active ? " on" : ""}${overId === s.id ? " dragover" : ""}`} onClick={() => onActive(s.id)}
            draggable onDragStart={() => setDragId(s.id)} onDragEnd={() => { setDragId(null); setOverId(null); }}
            onDragOver={(e) => { if (dragId) { e.preventDefault(); setOverId(s.id); } }}
            onDrop={(e) => { if (dragId) { e.preventDefault(); e.stopPropagation(); reorder(dragId, s.id); setDragId(null); setOverId(null); } }}>
            <span className="handle" title="拖动调整顺序">⋮⋮</span>
            <span style={{ width: 16, textAlign: "center" }} title={s.format}>{FAMILY_ICON[s.family] || "?"}</span>
            <div className="grow">
              <div className="fname">{s.filename}</div>
              <div className="fmeta row gap4 wrap">
                <span>{(s.format || "?").toUpperCase()}</span><span>{fmtSize(s.size)}</span>
                <span className={`badge ${s.status === "ready" ? "ok" : s.status === "parsing" ? "info" : s.status === "needs_ocr" ? "warn" : "err"}`}>{STATUS_TEXT[s.status] || s.status}</span>
                {s.representative && <span className="badge info" title="用于建立规则的代表文件">代表</span>}
              </div>
              {s.error && <div className="tiny" style={{ color: "var(--err)" }}>{s.error}</div>}
              {s.id === active && (
                <div className="row gap4 mt8" onClick={(e) => e.stopPropagation()}>
                  {!s.representative && s.status === "ready" && <button className="btn xs" onClick={async () => { await post(`/api/sources/${s.id}/representative`); onChanged(); }}>设为代表</button>}
                  <a className="btn xs" href={`/api/sources/${s.id}/download`} title="下载原文件（未修改）">原件</a>
                  <button className="btn xs danger" onClick={async () => {
                    if (await confirm("移除来源文件", `从项目中移除「${s.filename}」？原文件本身不受影响；已生成的历史结果仍保留。`, "移除", true)) {
                      await del(`/api/sources/${s.id}`);
                      onChanged();
                    }
                  }}>移除</button>
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
