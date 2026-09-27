/** The three cards a message can carry under its text.
 *
 *  - LinkCard: a Docs project or job the reader may open (server answers per reader; a 404 leaves
 *    the plain link as it is) — unchanged from before.
 *  - AppCardView: the `card` contract with the backend (CI-73): application, title, subtitle, a
 *    state chip and action links that open in the same tab. Rendered only when the message
 *    carries one; nothing is inferred from the text.
 *  - AttachmentCard: an image opens the viewer and a file shows its name, size and a download
 *    through the authorised address. Inline preview only for what the server marks `inline`. */
import { useEffect, useState } from "react";
import { useI18n } from "@synwevo/ui";
import { attachmentUrl, cardFor, type LinkCardData } from "../api";
import { fmtSize } from "../lib/thread";
import type { AppCard, Attachment } from "../types";
import { IcDownload, IcFile } from "./icons";

const JOB_STATE: Record<string, string> = { queued: "排队中", running: "生成中", cancelling: "取消中", completed: "已完成", completed_with_errors: "部分失败", failed: "失败", cancelled: "已取消" };

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
  return <div className="msg-card msg-appcard" data-app={card.app} data-object-kind={card.object?.kind} data-object-id={card.object?.id}>
    <span className="msg-card-kind">{card.app_name}</span>
    <span className="msg-card-title">{card.title}</span>
    {card.subtitle && <span className="msg-card-meta">{card.subtitle}</span>}
    {card.state && <span className="badge info msg-card-state">{card.state}</span>}
    {card.actions && card.actions.length > 0 && <span className="msg-card-actions">
      {card.actions.map((a, i) => <a key={i} className="btn sm" href={a.href}>{a.label}</a>)}
    </span>}
    <span className="sr-only">{t("来自应用 {app} 的卡片", { app: card.app_name })}</span>
  </div>;
}

export function AttachmentCard({ attachment, pending, onOpenImage }: { attachment: Attachment; pending?: boolean; onOpenImage: (a: Attachment) => void }) {
  const { t } = useI18n();
  if (attachment.inline && !pending) {
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
