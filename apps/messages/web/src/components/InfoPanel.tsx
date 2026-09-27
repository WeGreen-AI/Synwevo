/** Conversation info and group settings: the per-person preferences (pin, mute for good or on a
 *  timer, notifications, mark as unread), the object this conversation is about when it is one
 *  (§8.1), and for a group the name, description, announcement, the members with their roles and
 *  presence, and the management actions. Destructive actions (remove, hand over ownership, leave)
 *  confirm through the shared alert dialog with focus on 取消; archiving is reversible and does not.
 *
 *  Rendered three ways by the page: a third column on wide screens, the shared Drawer on medium
 *  ones, a full-width panel with a back control on phones. The body is the same. */
import { useEffect, useState, type ReactNode } from "react";
import { api, get, post, put } from "@synwevo/auth-client";
import { Drawer, Menu, useDialog, useI18n } from "@synwevo/ui";
import { M, objectOf } from "../api";
import type { Conversation, Member, ObjectInfo, Presence } from "../types";
import { fullDate } from "../lib/time";
import { FlowError } from "../flow";
import { Avatar } from "./MessageItem";
import { IcBack, IcClose, IcLink, IcPlus } from "./icons";

export type InfoMode = "column" | "drawer" | "page";

export function InfoPanel({ conv, uid, mode, presence, onClose, onChanged, onLeft }: { conv: Conversation; uid: string; mode: InfoMode; presence: Record<string, Presence>; onClose: () => void; onChanged: () => void; onLeft: () => void }) {
  const { t } = useI18n();
  const title = conv.kind === "group" ? t("群信息") : t("会话设置");
  const body = <InfoBody conv={conv} uid={uid} presence={presence} onChanged={onChanged} onLeft={onLeft} />;
  if (mode === "drawer") return <Drawer title={title} onClose={onClose} side="end">{body}</Drawer>;
  return <aside className={`msg-info ${mode}`} aria-label={title}>
    <div className="msg-info-h">
      {mode === "page" && <button type="button" className="btn ghost sm icon" onClick={onClose} aria-label={t("返回会话")}><IcBack /></button>}
      <h2 className="msg-info-title">{title}</h2>
      {mode === "column" && <button type="button" className="btn ghost sm icon" onClick={onClose} aria-label={t("关闭")}><IcClose size={16} /></button>}
    </div>
    <div className="msg-info-b">{body}</div>
  </aside>;
}

function Switch({ checked, onChange, disabled, label, hint }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string; hint?: string }) {
  return <label className="msg-switch">
    <span className="msg-switch-text"><span>{label}</span>{hint && <span className="tiny muted">{hint}</span>}</span>
    <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
    <span className="msg-switch-track" aria-hidden="true"><span className="msg-switch-knob" /></span>
  </label>;
}

function Section({ title, children }: { title: ReactNode; children: ReactNode }) {
  return <section className="msg-info-sec"><h3 className="msg-info-sec-title">{title}</h3>{children}</section>;
}

const MUTE_CHOICES: { key: string; hours: number }[] = [{ key: "1 小时", hours: 1 }, { key: "8 小时", hours: 8 }, { key: "24 小时", hours: 24 }, { key: "7 天", hours: 24 * 7 }];

export function InfoBody({ conv, uid, presence, onChanged, onLeft }: { conv: Conversation; uid: string; presence: Record<string, Presence>; onChanged: () => void; onLeft: () => void }) {
  const { t, locale } = useI18n();
  const { confirm } = useDialog();
  const [error, setError] = useState<unknown>(null); const [busy, setBusy] = useState(false);
  const [name, setName] = useState(conv.name); const [announcement, setAnnouncement] = useState(conv.announcement || ""); const [description, setDescription] = useState(conv.description || "");
  const [q, setQ] = useState(""); const [people, setPeople] = useState<{ user_id: string; name: string }[]>([]); const [adding, setAdding] = useState(false); const [allHistory, setAllHistory] = useState(false);
  const [object, setObject] = useState<ObjectInfo | null | undefined>(undefined);
  useEffect(() => { setName(conv.name); }, [conv.name]);
  useEffect(() => { setAnnouncement(conv.announcement || ""); }, [conv.announcement]);
  useEffect(() => { setDescription(conv.description || ""); }, [conv.description]);
  // what a group is about, when it is an object conversation (404 = an ordinary conversation)
  useEffect(() => { let live = true; if (conv.kind !== "group") { setObject(null); return; } objectOf(conv.id).then(o => { if (live) setObject(o); }, () => { if (live) setObject(null); }); return () => { live = false; }; }, [conv.id, conv.kind]);
  const manage = conv.kind === "group" && conv.me.role !== "member"; const owner = conv.kind === "group" && conv.me.role === "owner";
  const run = async (fn: () => Promise<unknown>, after?: () => void) => { setBusy(true); setError(null); try { await fn(); onChanged(); after?.(); } catch (e) { setError(e); } finally { setBusy(false); } };
  useEffect(() => {
    if (!manage || !adding || !q.trim()) { setPeople([]); return; }
    const timer = window.setTimeout(() => {
      get<{ items: { user_id: string; name: string }[] }>(`${M()}/directory?organization_id=${encodeURIComponent(conv.organization_id)}&q=${encodeURIComponent(q.trim())}`)
        .then(x => setPeople(x.items.filter(p => !conv.members.some(m => m.user_id === p.user_id)))).catch(() => setPeople([]));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [q, manage, adding, conv.organization_id, conv.members]);
  const pref = (body: Record<string, unknown>) => run(() => put(`${M()}/conversations/${conv.id}/preferences`, body));
  const roleName = (r: Member["role"]) => t(r === "owner" ? "群主" : r === "admin" ? "管理员" : "成员");
  const memberItems = (m: Member) => [
    ...(owner ? [{ label: t(m.role === "admin" ? "取消管理员" : "设为管理员"), onSelect: () => run(() => put(`${M()}/conversations/${conv.id}/members/${m.user_id}/role`, { role: m.role === "admin" ? "member" : "admin" })) }] : []),
    ...(owner ? [{ label: t("交接群主"), onSelect: async () => { if (await confirm(t("交接群主"), t("把群主交接给 {who}？你将成为管理员，之后不能自己收回。", { who: m.name }), t("交接"), true)) await run(() => post(`${M()}/conversations/${conv.id}/transfer`, { user_id: m.user_id })); } }] : []),
    ...((owner || m.role === "member") ? [{ label: t("移出群聊"), danger: true, onSelect: async () => { if (await confirm(t("移出群聊"), t("把 {who} 移出群聊？对方将立刻看不到这个群和它的消息。", { who: m.name }), t("移出"), true)) await run(() => api(`${M()}/conversations/${conv.id}/members/${m.user_id}`, { method: "DELETE" })); } }] : []),
  ];
  const timerOn = typeof conv.me.muted_until === "number" && conv.me.muted_until > Date.now() / 1000;
  const presenceOf = (id: string, fallback?: Presence): Presence => (presence[id] !== undefined ? presence[id] : (fallback ?? null));

  return <div className="msg-info-body">
    <div className="msg-info-id">
      <Avatar id={conv.kind === "dm" ? (conv.peer?.user_id || conv.id) : conv.id} name={conv.name} kind={conv.kind} size={56} presence={conv.kind === "dm" && conv.peer ? presenceOf(conv.peer.user_id, conv.peer.presence) : null} />
      <div className="msg-info-name">{conv.name}</div>
      <div className="tiny muted">{conv.kind === "group" ? t("群聊 · {n} 人", { n: conv.member_count }) : t("私聊")}{conv.archived && <> · {t("已归档")}</>}</div>
      {conv.kind === "group" && conv.description && !(manage && !conv.archived) && <p className="msg-info-desc">{conv.description}</p>}
    </div>
    <FlowError error={error} />
    {object && <Section title={t("关联对象")}>
      <div className="msg-object">
        <span className="msg-card-kind">{object.app_name}</span>
        <span className="msg-card-title">{object.title}</span>
        {!object.degraded && object.href && <a className="btn sm" href={object.href}><IcLink /> {t("在 {app} 中打开", { app: object.app_name })}</a>}
        {object.degraded && <span className="tiny muted">{t("你没有权限打开这个对象；会话仍然可用。")}</span>}
      </div>
    </Section>}
    <Section title={t("我的设置")}>
      <Switch label={t("置顶")} hint={t("置顶的会话排在列表最前。")} checked={conv.me.pinned} disabled={busy} onChange={v => void pref({ pinned: v })} />
      <Switch label={t("长期静音")} hint={t("不计未读、不通知；列表里以图标标记。")} checked={conv.me.muted} disabled={busy} onChange={v => void pref({ muted: v })} />
      <div className="msg-field"><label htmlFor="msg-mute-until">{t("暂时静音")}</label>
        <select id="msg-mute-until" className="input" value={timerOn ? "on" : ""} disabled={busy} onChange={e => { const c = MUTE_CHOICES.find(x => x.key === e.target.value); void pref({ muted_until: c ? Math.floor(Date.now() / 1000 + c.hours * 3600) : null }); }}>
          <option value="">{timerOn ? t("取消暂时静音") : t("不静音")}</option>
          {timerOn && <option value="on" disabled>{t("已静音至 {time}", { time: fullDate(conv.me.muted_until as number, locale) })}</option>}
          {MUTE_CHOICES.map(c => <option key={c.key} value={c.key}>{t(c.key)}</option>)}
        </select>
        {timerOn && <span className="tiny muted">{t("已静音至 {time}", { time: fullDate(conv.me.muted_until as number, locale) })}</span>}
      </div>
      <div className="msg-field"><label htmlFor="msg-notify">{t("通知")}</label>
        <select id="msg-notify" className="input" value={conv.me.notify} disabled={busy} onChange={e => void pref({ notify: e.target.value })}>
          <option value="all">{t("所有消息")}</option><option value="mentions">{t("仅提及我时")}</option><option value="none">{t("不通知")}</option>
        </select></div>
      <button type="button" className="btn sm" disabled={busy || conv.unread > 0 || !!conv.me.marked_unread} onClick={() => void run(() => post(`${M()}/conversations/${conv.id}/unread`, {}))}>{conv.me.marked_unread ? t("已标为未读") : t("标为未读")}</button>
    </Section>
    {conv.kind === "group" && <>
      {manage && !conv.archived ? <Section title={t("群名称")}>
        <form className="msg-inline-form" onSubmit={e => { e.preventDefault(); void run(() => api(`${M()}/conversations/${conv.id}`, { method: "PATCH", body: { name: name.trim() } })); }}>
          <input id="msg-name" className="input" value={name} maxLength={80} aria-label={t("群名称")} onChange={e => setName(e.target.value)} />
          <button className="btn sm" disabled={busy || !name.trim() || name.trim() === conv.name}>{t("保存")}</button>
        </form>
      </Section> : null}
      {manage && !conv.archived ? <Section title={t("群描述")}>
        <form className="msg-field" onSubmit={e => { e.preventDefault(); void run(() => api(`${M()}/conversations/${conv.id}`, { method: "PATCH", body: { description: description.trim() } })); }}>
          <textarea id="msg-desc" className="input msg-announce-input" rows={2} maxLength={500} value={description} aria-label={t("群描述")} placeholder={t("这个群是做什么的；成员在群信息里看到。")} onChange={e => setDescription(e.target.value)} />
          <div className="row gap4 wrap"><button className="btn sm" disabled={busy || description.trim() === (conv.description || "")}>{t("保存")}</button></div>
        </form>
      </Section> : null}
      <Section title={t("群公告")}>
        {manage && !conv.archived ? <form className="msg-field" onSubmit={e => { e.preventDefault(); void run(() => put(`${M()}/conversations/${conv.id}/announcement`, { text: announcement.trim() || null })); }}>
          <textarea id="msg-announce" className="input msg-announce-input" rows={3} maxLength={1000} value={announcement} aria-label={t("群公告")} placeholder={t("显示在会话顶部；保存后成员会收到一条通知。")} onChange={e => setAnnouncement(e.target.value)} />
          <div className="row gap4 wrap"><button className="btn sm" disabled={busy || announcement.trim() === (conv.announcement || "")}>{t("保存公告")}</button>
            {conv.announcement && <button type="button" className="btn ghost sm" disabled={busy} onClick={() => void run(() => put(`${M()}/conversations/${conv.id}/announcement`, { text: null }), () => setAnnouncement(""))}>{t("清除公告")}</button>}</div>
        </form>
          : conv.announcement ? <p className="msg-announce-full">{conv.announcement}</p> : <p className="tiny muted">{t("还没有公告。")}</p>}
      </Section>
      <Section title={<>{t("成员（{n}）", { n: conv.member_count })}{manage && !conv.archived && <button type="button" className="btn ghost sm" onClick={() => setAdding(a => !a)} aria-expanded={adding}><IcPlus size={14} /> {t("添加")}</button>}</>}>
        {adding && manage && !conv.archived && <div className="msg-field">
          <input id="msg-add" className="input" value={q} placeholder={t("按姓名搜索同组织成员…")} aria-label={t("添加成员（同组织）")} onChange={e => setQ(e.target.value)} autoFocus />
          <label className="check"><input type="checkbox" checked={allHistory} onChange={e => setAllHistory(e.target.checked)} /><span>{t("让新成员看到加入前的全部历史")}</span></label>
          {q.trim() && <div className="msg-members">{people.length ? people.map(p => <div key={p.user_id} className="msg-member"><Avatar id={p.user_id} name={p.name} size={28} /><span className="msg-member-name">{p.name}</span>
            <button type="button" className="btn sm" disabled={busy} onClick={() => void run(() => post(`${M()}/conversations/${conv.id}/members`, { user_ids: [p.user_id], ...(allHistory ? { history_from: "all" } : {}) }), () => setQ(""))}>{t("添加")}</button></div>)
            : <p className="tiny muted">{t("没有匹配的成员。只能添加同一组织的活跃成员。")}</p>}</div>}
        </div>}
        <div className="msg-members">{conv.members.map(m => <div key={m.user_id} className="msg-member">
          <Avatar id={m.user_id} name={m.name} size={28} presence={m.user_id === uid ? null : presenceOf(m.user_id, m.presence)} />
          <span className="msg-member-name">{m.name}{m.user_id === uid && <span className="tiny muted"> · {t("我")}</span>}</span>
          <span className={`badge ${m.role === "owner" ? "info" : ""}`}>{roleName(m.role)}</span>
          {manage && m.user_id !== uid && m.role !== "owner" && memberItems(m).length > 0 && <Menu ariaLabel={t("成员操作：{who}", { who: m.name })} buttonClass="btn ghost sm icon" trigger={<span aria-hidden="true">⋯</span>} items={memberItems(m)} />}
        </div>)}</div>
        <p className="tiny muted">{conv.me.history_from === "all" ? t("你能看到这个群的全部历史；退出或被移除后不再能看到这个群。") : t("新成员只能看到加入之后的消息；退出或被移除后不再能看到这个群。")}</p>
      </Section>
      <div className="msg-info-actions">
        {owner && <button type="button" className="btn" disabled={busy} onClick={() => void run(() => post(`${M()}/conversations/${conv.id}/archive`, { archived: !conv.archived }))}>{t(conv.archived ? "恢复群聊" : "归档群聊")}</button>}
        {owner && <p className="tiny muted">{conv.archived ? t("恢复后成员可以继续发消息。") : t("归档后所有人只能查看，不能再发消息；可以随时恢复。")}</p>}
        {!owner && <button type="button" className="btn danger" disabled={busy} onClick={async () => { if (await confirm(t("退出群聊"), t("退出这个群聊？你将不再看到它的消息，重新加入也不会恢复之前的历史。"), t("退出"), true)) await run(() => post(`${M()}/conversations/${conv.id}/leave`, {}), onLeft); }}>{t("退出群聊")}</button>}
        {owner && conv.member_count > 1 && <p className="tiny muted">{t("群主退出前需要先交接群主。")}</p>}
      </div>
    </>}
  </div>;
}
