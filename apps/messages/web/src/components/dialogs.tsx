/** The two dialogs of the page: starting a conversation (a private one or a group, with the
 *  organization's directory) and reporting a message (what the organization's messaging
 *  managers will see is said before anything is sent). */
import { useEffect, useId, useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { Modal, useI18n } from "@synwevo/ui";
import { M } from "../api";
import type { Conversation, Message, Org } from "../types";
import { FlowError } from "../flow";
import { Avatar } from "./MessageItem";
import { IcClose } from "./icons";

export const REPORT_REASONS: Record<string, string> = { harassment: "骚扰或辱骂", spam: "垃圾信息", privacy: "泄露隐私或索要凭据", other: "其他" };

type Person = { user_id: string; name: string };

export function NewConversation({ kind, orgs, onClose, onCreated }: { kind: "dm" | "group"; orgs: Org[]; onClose: () => void; onCreated: (c: Conversation) => void }) {
  const { t } = useI18n();
  const [oid, setOid] = useState(orgs[0]?.id || ""); const [q, setQ] = useState(""); const [people, setPeople] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<Person[]>([]); const [name, setName] = useState(""); const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!oid) return;
    const timer = window.setTimeout(() => {
      get<{ items: Person[] }>(`${M()}/directory?organization_id=${encodeURIComponent(oid)}&q=${encodeURIComponent(q.trim())}`).then(x => setPeople(x.items)).catch(e => setError(e));
    }, 200);
    return () => window.clearTimeout(timer);
  }, [oid, q]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try {
      onCreated(kind === "dm" ? await post<Conversation>(`${M()}/dm`, { organization_id: oid, user_id: chosen[0]?.user_id })
        : await post<Conversation>(`${M()}/groups`, { organization_id: oid, name: name.trim(), member_ids: chosen.map(c => c.user_id) }));
    } catch (err) { setError(err); setBusy(false); }
  };
  const candidates = people.filter(p => !chosen.some(c => c.user_id === p.user_id));
  return <Modal title={t(kind === "dm" ? "新私聊" : "新群聊")} onClose={onClose}>
    <form onSubmit={submit} className="col gap8" aria-busy={busy}>
      <FlowError error={error} />
      {orgs.length > 1 && <div className="msg-field"><label htmlFor="msg-org">{t("组织")}</label>
        <select id="msg-org" className="input" value={oid} onChange={e => { setOid(e.target.value); setChosen([]); }}>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>}
      {kind === "group" && <div className="msg-field"><label htmlFor="msg-gname">{t("群名称")}</label><input id="msg-gname" className="input" value={name} maxLength={80} required onChange={e => setName(e.target.value)} /></div>}
      <div className="msg-field"><label htmlFor="msg-who">{t(kind === "dm" ? "和谁" : "成员")}</label><input id="msg-who" className="input" value={q} placeholder={t("按姓名搜索…")} onChange={e => setQ(e.target.value)} data-autofocus={kind === "dm" ? true : undefined} /></div>
      {chosen.length > 0 && <div className="msg-chips" aria-label={t("已选择")}>{chosen.map(c => <span key={c.user_id} className="msg-chip-person">{c.name}
        <button type="button" className="msg-chip-x" aria-label={t("移除 {who}", { who: c.name })} onClick={() => setChosen(chosen.filter(x => x.user_id !== c.user_id))}><IcClose size={12} /></button></span>)}</div>}
      <div className="msg-members msg-picklist">
        {candidates.map(p => <div key={p.user_id} className="msg-member"><Avatar id={p.user_id} name={p.name} size={28} /><span className="msg-member-name">{p.name}</span>
          <button type="button" className="btn sm" onClick={() => setChosen(kind === "dm" ? [p] : [...chosen, p])}>{t("选择")}</button></div>)}
        {!candidates.length && <p className="tiny muted">{t("没有匹配的成员。只能和同一组织的活跃成员对话。")}</p>}
      </div>
      <div className="org-actions"><button className="btn primary" disabled={busy || !oid || !chosen.length || (kind === "group" && !name.trim())}>{t(kind === "dm" ? "开始私聊" : "创建群聊")}</button><button type="button" className="btn" onClick={onClose}>{t("取消")}</button></div>
    </form>
  </Modal>;
}

export function ReportDialog({ message, onClose }: { message: Message; onClose: () => void }) {
  const { t } = useI18n(); const id = useId();
  const [reason, setReason] = useState("harassment"); const [note, setNote] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null); const [done, setDone] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setError(null);
    try { await post(`${M()}/messages/${message.id}/report`, { reason, note: note.trim() || undefined }); setDone(true); } catch (err) { setError(err); } finally { setBusy(false); }
  };
  return <Modal title={t("举报消息")} onClose={onClose} size="narrow">
    {done ? <div className="col gap8"><p className="notice ok" role="status">{t("已提交举报。组织的群聊管理员处理后，你会在通知里看到结果。")}</p><div className="org-actions"><button type="button" className="btn primary" onClick={onClose}>{t("知道了")}</button></div></div>
      : <form className="col gap8" onSubmit={submit} aria-busy={busy}>
        <p className="tiny muted">{t("组织的群聊管理员会看到这条消息的内容、发送者、你的姓名和说明，看不到会话的其它内容。")}</p>
        <blockquote className="msg-quote"><strong>{message.sender}</strong> {message.body ? message.body.slice(0, 200) : message.attachment ? message.attachment.filename : ""}</blockquote>
        <FlowError error={error} />
        <div className="msg-field"><label htmlFor={`${id}-reason`}>{t("举报原因")}</label><select id={`${id}-reason`} className="input" value={reason} disabled={busy} onChange={e => setReason(e.target.value)}>{Object.entries(REPORT_REASONS).map(([k, v]) => <option key={k} value={k}>{t(v)}</option>)}</select></div>
        <div className="msg-field"><label htmlFor={`${id}-note`}>{t("补充说明（可选）")}</label><textarea id={`${id}-note`} className="input" rows={3} maxLength={500} value={note} disabled={busy} onChange={e => setNote(e.target.value)} /></div>
        <div className="org-actions"><button type="submit" className="btn primary" disabled={busy}>{t("提交举报")}</button><button type="button" className="btn" disabled={busy} onClick={onClose}>{t("取消")}</button></div>
      </form>}
  </Modal>;
}
