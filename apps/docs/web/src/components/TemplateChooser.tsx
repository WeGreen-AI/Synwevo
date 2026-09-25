import { formatNumber } from "@synwevo/ui";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, patch, post, upload } from "@synwevo/auth-client";
import { fmtTime, Modal, useToast } from "@synwevo/ui";
import { errorText } from "../lib/perm";

export default function TemplateChooser({ projectId, settingsRev, current, onDone, onClose }: { settingsRev?: number; projectId: string; current?: string | null; onDone: () => void; onClose: () => void }) {
  useI18n();
  const [tab, setTab] = useState<"upload" | "new" | "library">("upload");
  const [lib, setLib] = useState<any[] | null>(null);
  const [libErr, setLibErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [hot, setHot] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const nav = useNavigate();
  const toast = useToast();

  // A library that failed to load is not an empty library: say which it is.
  const loadLib = () => { setLibErr(null); get("/api/templates").then(setLib).catch((e) => setLibErr(errorText(e))); };
  useEffect(loadLib, []);

  const doUpload = async (f: File) => {
    setBusy(true);
    setErr(null);
    const fd = new FormData();
    fd.append("file", f);
    fd.append("project_id", projectId);
    try {
      const t = await upload("/api/templates/upload", fd, setProg);
      toast(tr("已导入模板「{v1}」", { v1: t.name }), "ok");
      (t.info?.notes || []).forEach((n: string) => toast(diagnosticText(n)));
      onDone();
      onClose();
    } catch (e: any) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const pick = async (tid: string, copy: boolean) => {
    setBusy(true);
    try {
      let id = tid;
      if (copy) id = (await post(`/api/templates/${tid}/duplicate`, {})).id;
      await patch(`/api/projects/${projectId}`, { template_id: id, base_settings_rev: settingsRev });
      onDone();
      onClose();
    } catch (e: any) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={tr("设置目标模板")} size="wide" onClose={onClose}>
      <div className="tabs" style={{ marginTop: -8 }}>
        <button type="button" aria-pressed={tab === "upload"} className={tab === "upload" ? "on" : ""} onClick={() => setTab("upload")}>{tr("上传已有文件作为模板")}</button>
        <button type="button" aria-pressed={tab === "new"} className={tab === "new" ? "on" : ""} onClick={() => setTab("new")}>{tr("在软件内新建模板")}</button>
        <button type="button" aria-pressed={tab === "library"} className={tab === "library" ? "on" : ""} onClick={() => setTab("library")}>{tr("从模板库选择")}{lib ? `（${lib.length}）` : ""}</button>
      </div>
      {err && <div className="notice err mt12">{serverText(err)}</div>}
      {tab === "upload" && (
        <div className="mt12 col">
          {/* A real button (Tab, Enter, Space); dropping a file on it still works. */}
          <button type="button" className={`dropzone${hot ? " hot" : ""}`} style={{ padding: 36 }} disabled={busy} onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setHot(true); }} onDragLeave={() => setHot(false)}
            onDrop={(e) => { e.preventDefault(); setHot(false); const f = e.dataTransfer.files[0]; if (f) doUpload(f); }}>
            {busy ? tr("上传中 {v1}%…", { v1: Math.round(prog * 100) }) : <>{tr("拖入模板文件，或 ")}<b>{tr("点击选择")}</b><div className="small mt8">{tr("支持 DOCX、XLSX、PDF、PPTX，以及 PNG / JPEG 图片（直接输出同尺寸图片）；DOC/XLS/PPT/ODT/ODS/ODP/RTF 会先经格式转换（会提示可能的版式变化），BMP/TIFF/WEBP 输出为 PNG")}</div></>}
          </button>
          <input ref={fileRef} type="file" hidden accept=".docx,.docm,.xlsx,.xlsm,.pdf,.pptx,.pptm,.doc,.xls,.ppt,.odt,.ods,.odp,.rtf,.png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp" onChange={(e) => e.target.files?.[0] && doUpload(e.target.files[0])} />
          <div className="notice small">{tr("上传后在右侧预览中选中文字、单元格、表格行，或在 PDF / 图片上框选区域，点击「设为待填区域」即可创建字段。原始模板保持不变，所有修改保存在工作副本中，可撤销/重做。")}</div>
        </div>
      )}
      {tab === "new" && (
        <div className="grid2 mt12">
          <div className="opt" style={{ flexDirection: "column" }}>
            <div className="t">{tr("📄 文档型模板（生成 DOCX）")}</div>
            <div className="small muted">{tr("段落、标题、字体格式、表格（合并单元格、重复明细行）、图片、纸张与页边距、分页、页眉页脚；在任意位置插入填充字段。")}</div>
            <button type="button" className="btn primary sm" style={{ alignSelf: "flex-start" }} onClick={() => nav(`/templates/new/doc?project=${projectId}`)}>{tr("新建文档模板")}</button>
          </div>
          <div className="opt" style={{ flexDirection: "column" }}>
            <div className="t">{tr("▦ 表格型模板（生成 XLSX / CSV）")}</div>
            <div className="small muted">{tr("单元格、行列尺寸、合并单元格、字体/填充/边框/对齐/数字格式；定义待填单元格、固定区域和重复明细行。")}</div>
            <button type="button" className="btn primary sm" style={{ alignSelf: "flex-start" }} onClick={() => nav(`/templates/new/sheet?project=${projectId}`)}>{tr("新建表格模板")}</button>
          </div>
        </div>
      )}
      {tab === "library" && (
        <div className="mt12" style={{ maxHeight: 420, overflow: "auto" }}>
          <table className="tbl">
            <thead><tr><th>{tr("名称")}</th><th>{tr("格式")}</th><th>{tr("字段")}</th><th>{tr("更新时间")}</th><th /></tr></thead>
            <tbody>
              {(lib || []).map((t) => (
                <tr key={t.id}>
                  <td>{t.name}{t.id === current && <span className="badge info" style={{ marginLeft: 6 }}>{tr("当前")}</span>}<div className="tiny muted">{t.kind === "uploaded" ? tr("上传：{v1}", { v1: t.original_name }) : t.kind === "builtin_doc" ? tr("内置文档编辑器") : tr("内置表格编辑器")}</div></td>
                  <td>{t.format.toUpperCase()}</td>
                  <td>{formatNumber(t.slot_count)}</td>
                  <td className="small muted">{fmtTime(t.updated_at)}</td>
                  <td className="nowrap">
                    <button type="button" className="btn sm" disabled={busy} onClick={() => pick(t.id, false)}>{tr("使用")}</button>{" "}
                    <button type="button" className="btn sm" disabled={busy} onClick={() => pick(t.id, true)} title={tr("复制一份后使用，避免影响其他项目")}>{tr("复制后使用")}</button>
                  </td>
                </tr>
              ))}
              {libErr && <tr><td colSpan={5}><div className="notice err row"><span className="grow">{tr("模板库加载失败：")}{serverText(libErr)}</span><button type="button" className="btn sm" onClick={loadLib}>{tr("重试")}</button></div></td></tr>}
              {!libErr && !lib && <tr><td colSpan={5} className="muted" style={{ textAlign: "center" }}>{tr("加载模板库…")}</td></tr>}
              {!libErr && lib && !lib.length && <tr><td colSpan={5} className="muted" style={{ textAlign: "center" }}>{tr("模板库为空")}</td></tr>}
            </tbody>
          </table>
          <div className="tiny muted mt8">{tr("模板可被多个项目使用；修改会产生新版本。若不希望影响其他项目，请选择「复制后使用」。")}</div>
        </div>
      )}
    </Modal>
  );
}
