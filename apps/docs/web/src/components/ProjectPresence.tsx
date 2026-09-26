/** Who has this project open and what they are on, and what happened to it — the project page's
 *  share of the live room (see lib/collab.ts). Colours are the shared per-person slots; names and
 *  objects come from the server-checked identity and from this page's own data, never from a
 *  label another client sent. */
import { useEffect, useState } from "react";
import { Drawer, t as tr, useI18n } from "@synwevo/ui";
import { get } from "@synwevo/auth-client";
import type { Focus, PeerInfo, RoomState } from "../lib/collab";
import { errorText } from "../lib/perm";
import type { Slot, SourceFile } from "../lib/types";

export const peerClass = (slot: unknown) => `peer-c${Math.min(8, Math.max(1, Number(slot) || 1))}`;
const initialOf = (name: string) => (name || "—").trim().slice(0, 1).toUpperCase() || "—";

export function focusLabel(focus: Focus | null | undefined, slots: Slot[], mappings: { id: string; no: number }[], sources: SourceFile[]): string | null {
  if (!focus) return null;
  if (focus.kind === "slot") { const s = slots.find((x) => x.id === focus.id); return s ? tr("字段「{v1}」", { v1: s.name }) : null; }
  if (focus.kind === "mapping") { const m = mappings.find((x) => x.id === focus.id); return m ? tr("映射 #{v1}", { v1: m.no }) : null; }
  const f = sources.find((x) => x.id === focus.id);
  return f ? tr("文件「{v1}」", { v1: f.filename }) : null;
}

/** The presence bar shared by the project page and the table editor: status, me, the others
 *  (with where they are, as a label this page computes from its own data), and an optional
 *  activity button. */
export function PresenceBar({ room, labelFor, onLocate, onActivity, testId = "project-presence" }: {
  room: RoomState; labelFor: (f: Focus) => string | null; onLocate: (f: Focus) => void; onActivity?: () => void; testId?: string;
}) {
  useI18n();
  const status = room.closed ? "disconnected" : room.status;
  const label = room.closed ? tr("实时更新不可用") : status === "connected" ? tr("实时更新中") : status === "connecting" ? tr("连接中…") : tr("连接已断开，重试中");
  return (
    <div className="collab-bar" data-testid={testId}>
      <span className={`collab-status tiny ${status}`} role="status" aria-live="polite" title={room.closed ? tr("此页面收不到其他人的实时修改；刷新页面重试。") : undefined}>{label}</span>
      {!room.closed && (
        <ul className="collab-peers" aria-label={tr("在线成员")}>
          {room.me && <li className={`collab-peer me ${peerClass(room.me.color)}`} title={room.me.name}><span className="collab-avatar" aria-hidden="true">{initialOf(room.me.name)}</span><span className="name">{tr("我")}</span></li>}
          {room.peers.map((p: PeerInfo) => {
            const where = p.focus ? labelFor(p.focus) : null;
            const title = where ? tr("{v1} 正在看{v2}（点击定位）", { v1: p.name, v2: where }) : p.name;
            return (
              <li key={p.id} className={`collab-peer ${peerClass(p.color)}`} data-testid="project-peer" data-focus={where || ""}>
                <button type="button" className="collab-peer-btn" title={title} aria-label={title} disabled={!p.focus || !where} onClick={() => p.focus && onLocate(p.focus)}>
                  <span className="collab-avatar" aria-hidden="true">{initialOf(p.name)}</span>
                  <span className="name">{p.name}{p.readonly ? tr("（查看）") : ""}</span>
                  {where && <span className="where tiny">· {where}</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {onActivity && <button type="button" className="btn ghost sm" onClick={onActivity} aria-haspopup="dialog">{tr("活动记录")}</button>}
    </div>
  );
}

export default function ProjectPresence({ room, slots, mappings, sources, onLocate, onActivity }: {
  room: RoomState; slots: Slot[]; mappings: { id: string; no: number }[]; sources: SourceFile[];
  onLocate: (f: Focus) => void; onActivity: () => void;
}) {
  return <PresenceBar room={room} labelFor={(f) => focusLabel(f, slots, mappings, sources)} onLocate={onLocate} onActivity={onActivity} />;
}

// ---------------------------------------------------------------- what happened, in words
const KIND_TEXT: Record<string, (d: any, slotName: (b: string) => string) => string> = {
  "mappings.save": (d, slotName) => {
    const parts: string[] = [];
    if (d.added?.length) parts.push(tr("新增映射 {v1}", { v1: d.added.map((m: any) => `#${m.no}→${slotName(m.target)}`).join("、") }));
    if (d.removed?.length) parts.push(tr("删除映射 {v1}", { v1: d.removed.map((m: any) => `#${m.no}`).join("、") }));
    if (d.changed?.length) parts.push(tr("修改映射 {v1}", { v1: d.changed.map((c: any) => `#${c.after.no}（${c.before.sources.join("+") || "—"} → ${c.after.sources.join("+") || "—"}${c.before.enabled !== c.after.enabled ? (c.after.enabled ? tr("，启用") : tr("，禁用")) : ""}）`).join("、") }));
    const head = parts.length ? parts.join("；") : tr("保存了映射");
    return d.merged_with ? tr("{v1}（第 {v2} 版，与第 {v3} 版自动合并）", { v1: head, v2: d.rev, v3: d.merged_with }) : tr("{v1}（第 {v2} 版）", { v1: head, v2: d.rev });
  },
  "mappings.undo": (d) => tr("撤销了映射修改（回到第 {v1} 版）", { v1: d.rev }),
  "mappings.redo": (d) => tr("重做了映射修改（第 {v1} 版）", { v1: d.rev }),
  "source.add": (d) => tr("添加了文件「{v1}」", { v1: d.name }),
  "source.remove": (d) => tr("移除了文件「{v1}」", { v1: d.name }),
  "source.order": () => tr("调整了文件顺序"),
  "source.representative": () => tr("更换了代表文件"),
  "source.options": () => tr("修改了文件读取选项"),
  "project.update": (d) => tr("修改了项目设置（{v1}）", { v1: (d.fields || []).map((f: string) => ({ name: tr("名称"), record_rule: tr("样本规则"), template_id: tr("模板"), template_version: tr("模板版本"), output: tr("输出设置") } as Record<string, string>)[f] || f).join("、") || "—" }),
  "template.save": (d) => tr("更新了模板（第 {v1} 版）", { v1: d.head }),
  "template.restore": (d) => tr("恢复了模板版本（现为第 {v1} 版）", { v1: d.head }),
  "template.undo": (d) => tr("撤销了模板修改（现为第 {v1} 版）", { v1: d.head }),
  "template.redo": (d) => tr("重做了模板修改（现为第 {v1} 版）", { v1: d.head }),
};

export function changeText(kind: string, detail: any, slots: Slot[]): string {
  const slotName = (binding: string) => slots.find((s) => s.binding === binding)?.name || binding || "—";
  const f = KIND_TEXT[kind];
  return f ? f(detail || {}, slotName) : tr("修改了项目");
}

function ago(ts: number): string {
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return tr("刚刚");
  if (s < 3600) return tr("{v1} 分钟前", { v1: Math.floor(s / 60) });
  if (s < 86400) return tr("{v1} 小时前", { v1: Math.floor(s / 3600) });
  return new Date(ts * 1000).toLocaleString();
}

export function ActivityDrawer({ pid, slots, seq, onClose }: { pid: string; slots: Slot[]; seq: number; onClose: () => void }) {
  useI18n();
  const [data, setData] = useState<{ items: any[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    get(`/api/projects/${pid}/activity?limit=200`).then((r) => { if (alive) { setData(r); setErr(null); } }).catch((e) => { if (alive) setErr(errorText(e)); });
    return () => { alive = false; };
  }, [pid, seq]);
  return (
    <Drawer title={tr("活动记录")} onClose={onClose}>
      <div className="col gap8" data-testid="project-activity">
        <div className="tiny muted">{tr("谁在什么时候改了什么：映射按条记录，文件、样本规则、模板版本的变化也在这里。")}</div>
        {err ? <div className="notice err">{err}</div>
          : data === null ? <div className="tiny muted" role="status">{tr("加载中…")}</div>
          : data.items.length === 0 ? <div className="tiny muted">{tr("还没有记录。")}</div>
          : <ul className="collab-log">
            {data.items.map((it) => (
              <li key={it.id}>
                <span className={`collab-avatar big ${peerClass(it.by?.color)}`} aria-hidden="true">{initialOf(it.by?.name || "—")}</span>
                <div>
                  <div className="what"><b>{it.by?.name || tr("系统")}</b> {changeText(it.kind, it.detail, slots)}</div>
                  <div className="when">{ago(it.at)}</div>
                </div>
              </li>
            ))}
          </ul>}
      </div>
    </Drawer>
  );
}
