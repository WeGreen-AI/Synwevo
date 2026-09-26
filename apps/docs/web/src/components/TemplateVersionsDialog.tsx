/** A template's version history, with restore: an earlier version becomes current *as a new
 *  version* (the history keeps every step, undo stays linear — the way Google Docs and OneDrive
 *  restore). Before restoring, the dialog says what follows: how many projects use this
 *  template and follow its head, and how many are pinned to a version and will not move. */
import { useEffect, useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { fmtTime, formatNumber, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { errorText } from "../lib/perm";
import { ChangeExcerpt, ago, changeText } from "../lib/changes";
import { peerClass } from "./ProjectPresence";
import type { TemplateDetail } from "../lib/types";

interface Usage { projects: { id: string; name: string; pinned_version: number | null }[]; following: number; pinned: number }
/** The editing room's state (GET …/collab/changes): edits not yet saved as a version, and who is there. */
interface LiveItem { id: string; user?: { id: string; name: string; color?: number } | null; part: string; kind: string; block_no?: number; node_type?: string; excerpt: string; before?: string | null; count: number; updated_at: number }
interface Live { dirty: boolean; online: { id: string; name: string; readonly?: boolean }[]; items: LiveItem[] }

const liveText = (online: string[], lastBy: string | null | undefined) =>
  online.length ? tr("编辑器里有尚未保存为版本的实时修改（在线：{v1}）。恢复会丢弃这些修改。", { v1: online.join("、") })
    : lastBy ? tr("编辑器里有尚未保存为版本的实时修改（最后由 {v1} 修改）。恢复会丢弃这些修改。", { v1: lastBy })
    : tr("编辑器里有尚未保存为版本的实时修改。恢复会丢弃这些修改。");

export default function TemplateVersionsDialog({ tpl, canWrite, onChanged, onClose }: { tpl: TemplateDetail; canWrite: boolean; onChanged: () => void; onClose: () => void }) {
  useI18n();
  const toast = useToast();
  const [usage, setUsage] = useState<Usage | null>(null);
  const [usageErr, setUsageErr] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  // What the restore would throw away besides moving the head: unsaved live edits in the editing
  // room. Read when the dialog opens; the server re-checks at the moment of the restore.
  const [live, setLive] = useState<{ dirty: boolean; online: string[]; lastBy: string | null; items: LiveItem[] } | null>(null);
  useEffect(() => { get<Usage>(`/api/templates/${tpl.id}/usage`).then(setUsage).catch((e) => setUsageErr(errorText(e))); }, [tpl.id]);
  useEffect(() => {
    if (tpl.kind !== "builtin_doc") { setLive({ dirty: false, online: [], lastBy: null, items: [] }); return; }
    get<Live>(`/api/templates/${tpl.id}/collab/changes?limit=20`)
      .then((l) => setLive({ dirty: !!l.dirty, online: (l.online || []).filter((p) => !p.readonly).map((p) => p.name), lastBy: l.items?.[0]?.user?.name || null, items: l.items || [] }))
      .catch(() => setLive({ dirty: false, online: [], lastBy: null, items: [] }));   // unknown here; the server still guards
  }, [tpl.id, tpl.kind]);

  const restore = async (no: number) => {
    setBusy(true);
    try {
      // `discard_live` is said only when the dialog has shown what it discards (below, or after a
      // 409 `live_edits` — someone typed after the dialog opened — which re-asks with the names).
      const d = await post<TemplateDetail>(`/api/templates/${tpl.id}/restore`, { version: no, base_head: tpl.head, discard_live: !!live?.dirty });
      toast(tr("已把第 {v1} 版的内容恢复为第 {v2} 版（当前）", { v1: no, v2: d.head }), "ok");
      onChanged();
      onClose();
    } catch (e: any) {
      if (e?.code === "live_edits") {
        const det = (e.detail || {}) as { online?: string[]; last_by?: string | null };
        setLive((prev) => ({ dirty: true, online: det.online || [], lastBy: det.last_by || null, items: prev?.items || [] }));
        toast(tr("有人刚刚修改了编辑器内容，这次没有恢复；请看清将丢弃的内容后再确认。"), "info");
        setBusy(false);
        return;                                                // stay on the confirmation, now with the names
      }
      toast(errorText(e), "err");
      setBusy(false);
      setConfirming(null);
    }
  };

  const versions = [...(tpl.versions || [])].sort((a, b) => b.version_no - a.version_no);
  return (
    <Modal title={tr("模板版本")} onClose={onClose}>
      <div className="col gap8">
        <p className="tiny muted">{tr("每次修改字段或保存编辑器都会产生一个版本。恢复某个版本不会删除之后的版本：它的内容会成为一个新的当前版本，随时可以撤销。")}</p>
        {usageErr && <div className="notice err small">{usageErr}</div>}
        {usage && <div className="notice info small">
          {tr("使用此模板的项目：{v1} 个跟随当前版本，{v2} 个固定了版本（恢复后不受影响）", { v1: formatNumber(usage.following), v2: formatNumber(usage.pinned) })}
          {usage.projects.length > 0 && <div className="tiny muted mt8">{usage.projects.slice(0, 8).map((p) => p.pinned_version ? `${p.name}（v${p.pinned_version}）` : p.name).join("、")}{usage.projects.length > 8 ? "…" : ""}</div>}
        </div>}
        <table className="tbl" aria-label={tr("模板版本")}>
          <thead><tr><th>{tr("版本")}</th><th>{tr("说明")}</th><th>{tr("时间")}</th><th><span className="sr-only">{tr("操作")}</span></th></tr></thead>
          <tbody>
            {versions.map((v) => (
              <tr key={v.version_no} data-version={v.version_no}>
                <td>v{v.version_no}{v.version_no === tpl.head && <span className="badge info" style={{ marginLeft: 6 }}>{tr("当前")}</span>}</td>
                <td className="small">{v.note || "—"}</td>
                <td className="small muted">{fmtTime(v.created_at)}</td>
                <td className="nowrap">
                  {canWrite && v.version_no !== tpl.head && (confirming === v.version_no
                    ? <span className="row gap4">
                        <button type="button" className={`btn sm ${live?.dirty ? "danger" : "primary"}`} disabled={busy || live === null} onClick={() => void restore(v.version_no)}>{live?.dirty ? tr("确认恢复并丢弃实时修改") : tr("确认恢复")}</button>
                        <button type="button" className="btn sm" disabled={busy} onClick={() => setConfirming(null)}>{tr("取消")}</button>
                      </span>
                    : <button type="button" className="btn sm" disabled={busy} onClick={() => setConfirming(v.version_no)}>{tr("恢复此版本")}</button>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {live?.dirty && live.items.length > 0 && (
          <details className="collab-live-changes" data-testid="live-changes">
            <summary className="small">{tr("尚未保存为版本的实时修改（{v1} 条）", { v1: live.items.length })}</summary>
            <ul className="collab-log compact">
              {live.items.filter((it) => it.kind !== "restore" && it.kind !== "replace").map((it) => (
                <li key={it.id}>
                  <span className={`collab-avatar big ${peerClass(it.user?.color)}`} aria-hidden="true">{(it.user?.name || "—").trim().slice(0, 1).toUpperCase()}</span>
                  <div>
                    <div className="what"><b>{it.user?.name || tr("系统")}</b> {changeText(it)}{it.count > 1 ? tr("（{v1} 次）", { v1: it.count }) : ""}</div>
                    {it.excerpt && <ChangeExcerpt item={it} />}
                    <div className="when">{ago(it.updated_at)}</div>
                  </div>
                </li>
              ))}
            </ul>
          </details>
        )}
        {confirming !== null && live?.dirty && <div className="notice warn small" role="alert" data-testid="restore-live-warning">{liveText(live.online, live.lastBy)}</div>}
        {confirming !== null && <p className="small" role="status">{tr("将以第 {v1} 版的内容新建第 {v2} 版并成为当前版本；跟随当前版本的项目会随之变化，固定了版本的项目不变。", { v1: confirming, v2: (tpl.versions?.length ? Math.max(...tpl.versions.map((x: { version_no: number }) => x.version_no)) : tpl.head) + 1 })}</p>}
      </div>
    </Modal>
  );
}
