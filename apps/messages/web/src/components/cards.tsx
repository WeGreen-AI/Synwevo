/** The cards a message can carry under its text.
 *
 *  - LinkCard: a Docs project or job the reader may open (server answers per reader; a 404 leaves
 *    the plain link as it is).
 *  - AppCardView: a card sent by an application (§8.3), rendered as the server rendered it for
 *    this reader: the full card with its state chip and action links (same tab), or the degraded
 *    one — neutral title, no subtitle, no actions — for a reader who may not open the object.
 *  - AttachmentCard: an image opens the viewer, a voice message plays inline with its declared
 *    duration (none when the sender declared none), a file shows its name, size and a download
 *    through the authorised address. Inline preview only for what the server marks `inline`. */
import { useEffect, useState } from "react";
import { useI18n } from "@synwevo/ui";
import { attachmentUrl, cardFor, type LinkCardData } from "../api";
import { fmtSize } from "../lib/thread";
import type { AppCard, Attachment } from "../types";
import { IcDownload, IcFile } from "./icons";

const JOB_STATE: Record<string, string> = { queued: "排队中", running: "生成中", cancelling: "取消中", completed: "已完成", completed_with_errors: "部分失败", failed: "失败", cancelled: "已取消" };

/** The state labels of §8.3, keyed by object kind; an unknown state is shown as the server sent it. */
const CARD_STATE: Record<string, Record<string, string>> = {
  job: { completed: "已完成", completed_with_errors: "部分失败", failed: "失败", cancelled: "已取消" },
  comment: { open: "待处理", resolved: "已解决" },
  copy_request: { approved: "已批准", rejected: "被拒绝", done: "已复制", failed: "失败", stale: "需重新申请" },
};
const STATE_TONE: Record<string, string> = { completed: "ok", done: "ok", approved: "ok", resolved: "ok", failed: "err", rejected: "err", completed_with_errors: "warn", stale: "warn", cancelled: "" };

export function LinkCard({ url }: { url: string }) {
  const { t } = useI18n();
  const [card, setCard] = useState<LinkCardData | null | undefined>(undefined);
  useEffect(() => { let live = true; void cardFor(url).then(c => { if (live) setCard(c); }); return () => { live = false; }; }, [url]);
  if (!card) return null;
  return <a className="msg-card" href={url} target="_blank" rel="noopener noreferrer nofollow">
    <span className="msg-card-kind">{card.kind === "job" ? t("生成任务") : t("项目")}</span>
    <span className="msg-card-title">{card.title}</span>
    <span className="msg-card-meta">{card.workspace}{card.kind === "job" && card.status && <> · {t(JOB_STATE[card.status] || card.status)}{card.status !== "queued" && card.status !== "running" && <> · {t("{done} 份成功，{failed} 份失败", { done: String(card.done ?? 0), failed: String(card.failed ?? 0) })}</>}</>}{card.archived && <> · {t("已归档")}</>}</span>
  </a>;
}

export function AppCardView({ card }: { card: AppCard }) {
  const { t } = useI18n();
  const kind = card.object?.kind || "";
  const label = card.state ? (CARD_STATE[kind]?.[card.state] ? t(CARD_STATE[kind][card.state]) : card.state) : null;
  return <div className={`msg-card msg-appcard ${card.degraded ? "degraded" : ""}`} data-app={card.app} data-object-kind={kind} data-object-id={card.object?.id}>
    <span className="msg-card-kind">{card.app_name}</span>
    <span className="msg-card-title">{card.title}</span>
    {card.subtitle && !card.degraded && <span className="msg-card-meta">{card.subtitle}</span>}
    {label && <span className={`badge ${STATE_TONE[card.state || ""] || "info"} msg-card-state`}>{label}</span>}
    {!card.degraded && card.actions && card.actions.length > 0 && <span className="msg-card-actions">
      {card.actions.map((a, i) => <a key={i} className="btn sm" href={a.href}>{a.label}</a>)}
    </span>}
    <span className="sr-only">{t("来自应用 {app} 的卡片", { app: card.app_name })}</span>
  </div>;
}

export function fmtDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function AttachmentCard({ attachment, pending, onOpenImage }: { attachment: Attachment; pending?: boolean; onOpenImage: (a: Attachment) => void }) {
  const { t } = useI18n();
  if (attachment.rejected) {
    // A rescan found these bytes infected (CI-90): the name stays so the conversation reads the same;
    // nothing is linked, played or shown inline, and the server refuses the download with 410.
    return <div className="msg-attach-file" aria-disabled="true">
      <span className="msg-attach-ic" aria-hidden="true"><IcFile size={20} /></span>
      <span className="msg-attach-main"><span className="msg-attach-name">{attachment.filename}</span><span className="msg-attach-meta">{t("该附件未通过安全检查，已被拒绝并隔离")}</span></span>
    </div>;
  }
  if (attachment.voice && !pending) {
    return <div className="msg-voice" data-duration-ms={attachment.duration_ms ?? undefined}>
      <audio controls preload="none" src={attachmentUrl(attachment.id, true)} aria-label={t("语音消息")} />
      <span className="tiny muted">{typeof attachment.duration_ms === "number" ? fmtDuration(attachment.duration_ms) : fmtSize(attachment.size)}</span>
    </div>;
  }
  if (attachment.inline && !pending && attachment.content_type.startsWith("image/")) {
    return <button type="button" className="msg-attach-img" onClick={() => onOpenImage(attachment)} aria-label={t("查看图片：{name}", { name: attachment.filename })}>
      <img src={attachmentUrl(attachment.id, true)} alt={attachment.filename} loading="lazy" decoding="async" />
    </button>;
  }
  const href = pending ? undefined : attachmentUrl(attachment.id);
  return <a className="msg-attach-file" href={href} download={pending ? undefined : attachment.filename} aria-disabled={pending || undefined}>
    <span className="msg-attach-ic" aria-hidden="true"><IcFile size={20} /></span>
    <span className="msg-attach-main"><span className="msg-attach-name">{attachment.filename}</span><span className="msg-attach-meta">{fmtSize(attachment.size)}{attachment.content_type ? ` · ${attachment.content_type}` : ""}</span></span>
    {!pending && <span className="msg-attach-dl" aria-hidden="true"><IcDownload size={16} /></span>}
    <span className="sr-only">{pending ? t("附件上传后可下载") : t("下载")}</span>
  </a>;
}
