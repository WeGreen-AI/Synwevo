/** Reports: messages members reported inside this organization's conversations, for holders of
 *  org.messaging.manage. The reviewer sees the reported message as it was when reported (the
 *  snapshot survives later edits), its sender, the reporter and the reason — never the rest of
 *  the conversation — and decides under re-authentication: remove the message for everyone, or
 *  dismiss. Every open report of the same message is settled by that one decision and the
 *  reporter is told through the inbox. */
import { useEffect, useRef, useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import type { Organization } from "./types";

interface Report {
  id: string; organization_id: string; conversation_id: string; message_id: string;
  conversation: { kind: "dm" | "group" | null; name: string | null }; reporter: { user_id: string; name: string }; sender: { user_id: string; name: string };
  reason: string; note: string | null; body_snapshot: string; attachment_name: string | null;
  status: "open" | "removed" | "dismissed"; reviewed_by: { user_id: string; name: string } | null; reviewed_at: number | null; decision_note: string | null; created_at: number;
}
const M = "/api/auth/messaging";
const REASON: Record<string, string> = { harassment: "骚扰或辱骂", spam: "垃圾信息", privacy: "泄露隐私或索要凭据", other: "其他" };
const STATUS: Record<string, string> = { open: "待处理", removed: "已删除消息", dismissed: "已驳回" };

export default function OrganizationReports({ organization, mine }: { organization: Organization; mine: string[] }) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<Report[] | null>(null); const [error, setError] = useState<unknown>(null); const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState<"open" | "all">("open"); const [focus] = useState<string | null>(() => new URLSearchParams(window.location.search).get("report"));
  const [action, setAction] = useState<{ item: Report; kind: "removed" | "dismissed" } | null>(null);
  const can = mine.includes("org.messaging.manage");
  useEffect(() => {
    if (!can) return;
    const abort = new AbortController(); setError(null);
    get<{ items: Report[] }>(`${M}/reports?organization_id=${encodeURIComponent(organization.id)}&status=${status}`, abort.signal).then(x => setItems(x.items)).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, status, revision, can]);
  // a deep link to a report that is no longer open moves to 全部 once, so the item is in view; after that the tabs are the reader's
  const widened = useRef(false);
  useEffect(() => { if (focus && items && status === "open" && !widened.current && !items.some(i => i.id === focus)) { widened.current = true; setStatus("all"); } }, [focus, items, status]);
  useEffect(() => { if (focus && items?.some(i => i.id === focus)) document.getElementById(`report-${focus}`)?.scrollIntoView({ block: "center" }); }, [focus, items]);
  if (!can) return null;
  const when = (ts: number | null) => ts ? new Date(ts * 1000).toLocaleString(locale) : "—";
  return <section className="org-section" id="org-reports"><h2>{t("举报处理")}</h2>
    <p className="tiny muted">{t("成员举报的消息。你看到的是举报时的消息内容、发送者、举报人和原因，不是整段会话。删除会让这条消息对所有人显示为「消息已删除」；无论删除还是驳回，举报人都会收到结果。")}</p>
    <FlowError error={error} />
    <div className="row wrap gap4" role="tablist">
      <button type="button" role="tab" aria-selected={status === "open"} className={`btn ${status === "open" ? "primary" : ""}`} onClick={() => setStatus("open")}>{t("待处理")}</button>
      <button type="button" role="tab" aria-selected={status === "all"} className={`btn ${status === "all" ? "primary" : ""}`} onClick={() => setStatus("all")}>{t("全部")}</button>
    </div>
    {!items ? <Loading /> : !items.length ? <p className="muted">{t(status === "open" ? "没有待处理的举报。" : "还没有举报。")}</p>
      : <ul className="org-list">{items.map(r => <li key={r.id} id={`report-${r.id}`} className={focus === r.id ? "org-focus" : undefined}>
        <div><strong>{r.conversation.kind === "group" ? t("群聊「{name}」", { name: r.conversation.name || "—" }) : t("私聊")}</strong> <span className={`badge ${r.status === "open" ? "warn" : r.status === "removed" ? "err" : ""}`}>{t(STATUS[r.status] || r.status)}</span>
          <div className="tiny muted">{t("举报人：{who} · {when}", { who: r.reporter.name, when: when(r.created_at) })} · {t("原因：{reason}", { reason: t(REASON[r.reason] || r.reason) })}{r.note && <> · {r.note}</>}</div>
          <blockquote className="msg-quote"><strong>{r.sender.name}</strong> {r.body_snapshot || (r.attachment_name ? `📎 ${r.attachment_name}` : "")}{r.body_snapshot && r.attachment_name && <span className="tiny muted"> · 📎 {r.attachment_name}</span>}</blockquote>
          {r.reviewed_by && <div className="tiny muted">{t("由 {who} 处理 · {when}", { who: r.reviewed_by.name, when: when(r.reviewed_at) })}{r.decision_note && <>：{r.decision_note}</>}</div>}
        </div>
        {r.status === "open" && <div className="org-actions">
          <button type="button" className="btn danger" onClick={() => setAction({ item: r, kind: "removed" })}>{t("删除消息")}</button>
          <button type="button" className="btn" onClick={() => setAction({ item: r, kind: "dismissed" })}>{t("驳回举报")}</button>
        </div>}
      </li>)}</ul>}
    {action && <OrganizationAction key={action.item.id + action.kind} spec={{
      title: t(action.kind === "removed" ? "删除被举报的消息" : "驳回举报"),
      impact: t(action.kind === "removed"
        ? "这条消息将对所有人显示为「消息已删除」，同一条消息的其它举报一并处理；举报人会收到结果和你的说明。"
        : "消息保留；同一条消息的其它举报一并驳回；举报人会收到结果和你的说明。"),
      danger: action.kind === "removed",
      run: reason => post(`${M}/reports/${action.item.id}/review`, { decision: action.kind, note: reason }),
    }} onClose={() => setAction(null)} onDone={() => { setAction(null); setRevision(v => v + 1); }} />}
  </section>;
}
