/** The dialogs of the page: starting a conversation (a private one or a group, with the
 *  organization's directory — preselected from the list's organization scope, or, for a group made
 *  from a direct message, with that organization and the other person already chosen and a plain
 *  note that the private history does not come along), reporting a message (what the
 *  organization's messaging managers will see is said before anything is sent), the two mutual
 *  privacy switches (§7.2) with the people this person blocked (§7.18), and forwarding a message to
 *  up to the server's number of conversations (§7.7). */
import { useCallback, useEffect, useId, useState } from "react";
import { ApiError, api, get, post, put } from "@synwevo/auth-client";
import { Modal, useI18n, useToast } from "@synwevo/ui";
import { M, groupAvatarUrl } from "../api";
import { newClientId } from "../lib/thread";
import type { BlockEntry, Conversation, Message, Org, Settings } from "../types";
import { FlowError } from "../flow";
import { Avatar } from "./MessageItem";
import { IcClose } from "./icons";

export const REPORT_REASONS: Record<string, string> = { harassment: "骚扰或辱骂", spam: "垃圾信息", privacy: "泄露隐私或索要凭据", other: "其他" };

export type Person = { user_id: string; name: string };

export function NewConversation({ kind, orgs, initialOrg, initialChosen, note, onClose, onCreated }: {
  kind: "dm" | "group"; orgs: Org[]; onClose: () => void; onCreated: (c: Conversation) => void;
  /** the organization to start in: the list's scope, or the direct message's organization */
  initialOrg?: string;
  /** people already chosen (a group made from a direct message starts with the other person) */
  initialChosen?: Person[];
  /** a sentence shown above the form (what does and does not come along) */
  note?: string;
}) {
  const { t } = useI18n();
  const [oid, setOid] = useState(() => (initialOrg && orgs.some(o => o.id === initialOrg) ? initialOrg : orgs[0]?.id || "")); const [q, setQ] = useState(""); const [people, setPeople] = useState<Person[]>([]);
  const [chosen, setChosen] = useState<Person[]>(initialChosen || []); const [name, setName] = useState(""); const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
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
      {note && <p className="notice msg-note" role="note">{note}</p>}
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

function SwitchRow({ id, checked, disabled, onChange, label, hint }: { id: string; checked: boolean; disabled?: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  return <label className="msg-switch" htmlFor={id}>
    <span className="msg-switch-text"><span>{label}</span><span className="tiny muted">{hint}</span></span>
    <input id={id} type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
    <span className="msg-switch-track" aria-hidden="true"><span className="msg-switch-knob" /></span>
  </label>;
}

/** The two privacy switches (§7.2). Both are mutual: what you hide, you also stop seeing. */
export function SettingsDialog({ settings, onClose, onSaved, blocks, onBlocksChanged }: { settings: Settings; onClose: () => void; onSaved: (s: Settings) => void;
  /** the server offers blocking: list the people this person blocked */
  blocks?: boolean; onBlocksChanged?: () => void }) {
  const { t } = useI18n(); const id = useId();
  const [draft, setDraft] = useState<Settings>(settings); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null);
  const save = async () => {
    setBusy(true); setError(null);
    try { onSaved(await put<Settings>(`${M()}/settings`, draft)); onClose(); } catch (e) { setError(e); setBusy(false); }
  };
  const changed = draft.show_presence !== settings.show_presence || draft.read_receipts !== settings.read_receipts;
  return <Modal title={t("消息设置")} onClose={onClose} size="narrow" footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose} disabled={busy}>{t("取消")}</button><button type="button" className="btn primary" onClick={() => void save()} disabled={busy || !changed}>{t("保存")}</button></>}>
    <div className="col gap8" aria-busy={busy}>
      <FlowError error={error} />
      <SwitchRow id={`${id}-presence`} checked={draft.show_presence} disabled={busy} onChange={v => setDraft({ ...draft, show_presence: v })} label={t("显示在线状态")}
        hint={t("互相可见：关闭后别人看不到你在线或正在输入，你也看不到别人的。在线 = 最近 1 分钟内有打开的页面；不保存任何时间记录。")} />
      <SwitchRow id={`${id}-receipts`} checked={draft.read_receipts} disabled={busy} onChange={v => setDraft({ ...draft, read_receipts: v })} label={t("已读回执")}
        hint={t("互相可见：开启后，同样开启了回执的成员能看到你读到哪里，你也能看到他们的。默认关闭。")} />
      {blocks && <BlockedList onChanged={onBlocksChanged} />}
    </div>
  </Modal>;
}

/** The people this person blocked (§7.18), each with 解除屏蔽. Unblocking takes effect at once
 *  and is said in a toast; the other person is not told either way. */
function BlockedList({ onChanged }: { onChanged?: () => void }) {
  const { t } = useI18n(); const toast = useToast();
  const [items, setItems] = useState<BlockEntry[] | null>(null); const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => get<{ items: BlockEntry[] }>(`${M()}/blocks`).then(x => setItems(x.items), e => setError(e)), []);
  useEffect(() => { void load(); }, [load]);
  const unblock = async (b: BlockEntry) => {
    setBusy(b.user_id); setError(null);
    try {
      await api(`${M()}/blocks/${encodeURIComponent(b.user_id)}`, { method: "DELETE" });
      setItems(cur => (cur || []).filter(x => x.user_id !== b.user_id));
      toast(t("已解除对 {who} 的屏蔽", { who: b.name }), "ok");
      onChanged?.();
    } catch (e) { setError(e); } finally { setBusy(null); }
  };
  return <section className="msg-blocked" aria-labelledby="msg-blocked-h">
    <h3 id="msg-blocked-h" className="msg-info-sec-title">{t("已屏蔽的人")}</h3>
    <p className="tiny muted">{t("你们不能互相发起私聊或在私聊里发消息，互相看不到在线状态、正在输入和已读；共同群里对方的消息仍可见，但不会通知你。对方不会收到通知。")}</p>
    <FlowError error={error} />
    {items === null ? <p className="tiny muted" role="status">{t("加载中…")}</p>
      : !items.length ? <p className="tiny muted">{t("你没有屏蔽任何人。")}</p>
      : <div className="msg-members">{items.map(b => <div key={b.user_id} className="msg-member">
        <Avatar id={b.user_id} name={b.name} size={28} /><span className="msg-member-name">{b.name}</span>
        <button type="button" className="btn sm" disabled={busy === b.user_id} onClick={() => void unblock(b)}>{t("解除屏蔽")}</button>
      </div>)}</div>}
  </section>;
}

/** Forward one message to other conversations (§7.7): a picker of the person's conversations,
 *  up to the server's limit, the archived ones and the source left out. */
export function ForwardDialog({ message, convs, max, onClose, onDone }: { message: Message; convs: Conversation[]; max: number; onClose: () => void; onDone: (n: number) => void }) {
  const { t } = useI18n(); const toast = useToast();
  const [chosen, setChosen] = useState<string[]>([]); const [q, setQ] = useState(""); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null);
  const targets = convs.filter(c => !c.archived && c.id !== message.conversation_id && (!q.trim() || c.name.toLowerCase().includes(q.trim().toLowerCase())));
  const toggle = (id: string) => setChosen(cur => (cur.includes(id) ? cur.filter(x => x !== id) : cur.length < max ? [...cur, id] : cur));
  const send = async () => {
    setBusy(true); setError(null);
    try {
      const out = await post<{ items: { conversation_id: string; created: boolean }[] }>(`${M()}/messages/${message.id}/forward`, { conversation_ids: chosen, client_id: newClientId() });
      toast(t("已转发到 {n} 个会话", { n: out.items.length }), "ok");
      onDone(out.items.length);
    } catch (e) { setError(e instanceof ApiError ? e : e); setBusy(false); }
  };
  return <Modal title={t("转发消息")} onClose={onClose} size="narrow" footer={<><span className="tiny muted">{t("已选 {n}/{max}", { n: chosen.length, max })}</span><span className="spacer" /><button type="button" className="btn" onClick={onClose} disabled={busy}>{t("取消")}</button><button type="button" className="btn primary" onClick={() => void send()} disabled={busy || !chosen.length}>{t("转发")}</button></>}>
    <div className="col gap8" aria-busy={busy}>
      <FlowError error={error} />
      <blockquote className="msg-quote"><strong>{message.sender}</strong> {message.body ? message.body.slice(0, 200) : message.attachment ? message.attachment.filename : ""}</blockquote>
      <input className="input" value={q} placeholder={t("筛选会话…")} aria-label={t("筛选会话…")} onChange={e => setQ(e.target.value)} data-autofocus />
      <div className="msg-members msg-picklist" role="group" aria-label={t("转发到")}>
        {targets.map(c => <label key={c.id} className={`msg-member msg-pick ${chosen.includes(c.id) ? "on" : ""}`}>
          <input type="checkbox" checked={chosen.includes(c.id)} disabled={busy || (!chosen.includes(c.id) && chosen.length >= max)} onChange={() => toggle(c.id)} />
          <Avatar id={c.kind === "dm" ? (c.peer?.user_id || c.id) : c.id} name={c.name} kind={c.kind} size={28} src={groupAvatarUrl(c)} /><span className="msg-member-name">{c.name}</span>
        </label>)}
        {!targets.length && <p className="tiny muted">{t("没有可转发到的会话。")}</p>}
      </div>
    </div>
  </Modal>;
}
