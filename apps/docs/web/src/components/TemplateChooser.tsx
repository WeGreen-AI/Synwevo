import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, patch, post, upload } from "@synwevo/auth-client";
import { fmtTime, Modal, useToast } from "@synwevo/ui";

export default function TemplateChooser({ projectId, current, onDone, onClose }: { projectId: string; current?: string | null; onDone: () => void; onClose: () => void }) {
  const [tab, setTab] = useState<"upload" | "new" | "library">("upload");
  const [lib, setLib] = useState<any[]>([]);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [hot, setHot] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  const toast = useToast();

  useEffect(() => {
    get("/api/templates").then(setLib).catch(() => {});
  }, []);

  const doUpload = async (f: File) => {
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("project_id", projectId);
    try {
      const t = await upload("/api/templates/upload", fd, setProg);
      toast(`已导入模板「${t.name}」`, "ok");
      (t.info?.notes || []).forEach((n: string) => toast(n));
      onDone();
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const pick = async (tid: string, copy: boolean) => {
    setBusy(true);
    try {
      let id = tid;
      if (copy) id = (await post(`/api/templates/${tid}/duplicate`, {})).id;
      await patch(`/api/projects/${projectId}`, { template_id: id });
      onDone();
      onClose();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title="设置目标模板" size="wide" onClose={onClose}>
      <div className="tabs" style={{ marginTop: -8 }}>
        <button className={tab === "upload" ? "on" : ""} onClick={() => setTab("upload")}>上传已有文件作为模板</button>
        <button className={tab === "new" ? "on" : ""} onClick={() => setTab("new")}>在软件内新建模板</button>
        <button className={tab === "library" ? "on" : ""} onClick={() => setTab("library")}>从模板库选择（{lib.length}）</button>
      </div>
      {err && <div className="notice err mt12">{err}</div>}
      {tab === "upload" && (
        <div className="mt12 col">
          <div className={`dropzone${hot ? " hot" : ""}`} style={{ padding: 36 }} onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setHot(true); }} onDragLeave={() => setHot(false)}
            onDrop={(e) => { e.preventDefault(); setHot(false); const f = e.dataTransfer.files[0]; if (f) doUpload(f); }}>
            {busy ? `上传中 ${Math.round(prog * 100)}%…` : <>拖入模板文件，或 <b>点击选择</b><div className="small mt8">支持 DOCX、XLSX、PDF、PPTX，以及 PNG / JPEG 图片（直接输出同尺寸图片）；DOC/XLS/PPT/ODT/ODS/ODP/RTF 会经 LibreOffice 转换（会提示可能的版式变化），BMP/TIFF/WEBP 输出为 PNG</div></>}
          </div>
          <input ref={fileRef} type="file" hidden accept=".docx,.docm,.xlsx,.xlsm,.pdf,.pptx,.pptm,.doc,.xls,.ppt,.odt,.ods,.odp,.rtf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp" onChange={(e) => e.target.files?.[0] && doUpload(e.target.files[0])} />
          <div className="notice small">
            上传后在右侧预览中选中文字、单元格、表格行，或在 PDF / 图片上框选区域，点击「设为待填区域」即可创建字段。原始模板保持不变，所有修改保存在工作副本中，可撤销/重做。
          </div>
        </div>
      )}
      {tab === "new" && (
        <div className="grid2 mt12">
          <div className="opt" style={{ flexDirection: "column" }} onClick={() => nav(`/templates/new/doc?project=${projectId}`)}>
            <div className="t">📄 文档型模板（生成 DOCX）</div>
            <div className="small muted">段落、标题、字体格式、表格（合并单元格、重复明细行）、图片、纸张与页边距、分页、页眉页脚；在任意位置插入填充字段。</div>
            <button className="btn primary sm" style={{ alignSelf: "flex-start" }}>新建文档模板</button>
          </div>
          <div className="opt" style={{ flexDirection: "column" }} onClick={() => nav(`/templates/new/sheet?project=${projectId}`)}>
            <div className="t">▦ 表格型模板（生成 XLSX / CSV）</div>
            <div className="small muted">单元格、行列尺寸、合并单元格、字体/填充/边框/对齐/数字格式；定义待填单元格、固定区域和重复明细行。</div>
            <button className="btn primary sm" style={{ alignSelf: "flex-start" }}>新建表格模板</button>
          </div>
        </div>
      )}
      {tab === "library" && (
        <div className="mt12" style={{ maxHeight: 420, overflow: "auto" }}>
          <table className="tbl">
            <thead><tr><th>名称</th><th>格式</th><th>字段</th><th>更新时间</th><th /></tr></thead>
            <tbody>
              {lib.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}{t.id === current && <span className="badge info" style={{ marginLeft: 6 }}>当前</span>}<div className="tiny muted">{t.kind === "uploaded" ? `上传：${t.original_name}` : t.kind === "builtin_doc" ? "内置文档编辑器" : "内置表格编辑器"}</div></td>
                  <td>{t.format.toUpperCase()}</td>
                  <td>{t.slot_count}</td>
                  <td className="small muted">{fmtTime(t.updated_at)}</td>
                  <td className="nowrap">
                    <button className="btn sm" disabled={busy} onClick={() => pick(t.id, false)}>使用</button>{" "}
                    <button className="btn sm" disabled={busy} onClick={() => pick(t.id, true)} title="复制一份后使用，避免影响其他项目">复制后使用</button>
                  </td>
                </tr>
              ))}
              {!lib.length && <tr><td colSpan={5} className="muted" style={{ textAlign: "center" }}>模板库为空</td></tr>}
            </tbody>
          </table>
          <div className="tiny muted mt8">模板可被多个项目使用；修改会产生新版本。若不希望影响其他项目，请选择「复制后使用」。</div>
        </div>
      )}
    </Modal>
  );
}
