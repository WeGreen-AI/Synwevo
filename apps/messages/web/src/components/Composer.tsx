/** The composer: a growing text field, Enter/Shift+Enter with the person's remembered choice
 *  (never sending while an input method is composing), an emoji picker, attachments by button,
 *  drop or paste — each with its own real upload progress, a cancel while it goes up and a retry
 *  when it failed — @-mentions in a group, the reply and edit banners, and the server's limits
 *  shown before a refusal rather than after. What it does not know it does not show: there is no
 *  "typing" indicator because the server has no such fact. */
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { ApiError } from "@synwevo/auth-client";
import { Menu, isImeKey, useI18n } from "@synwevo/ui";
import { M, uploadWithCancel } from "../api";
import { ENTER_KEY, fmtSize } from "../lib/thread";
import type { Attachment, Capabilities, Conversation, Member, Message } from "../types";
import { EmojiPicker } from "./EmojiPicker";
import { Popover } from "./layers";
import { IcAt, IcAttach, IcClose, IcEmoji, IcImage, IcSend, IcSettings } from "./icons";

export interface PendingUpload {
  key: string; file: File; progress: number; status: "uploading" | "done" | "failed"; result?: Attachment; error?: string; cancel?: () => void;
}

export interface ComposerHandle { addFiles: (files: File[]) => void; focus: () => void }

export interface ComposerProps {
  conv: Conversation; uid: string; caps: Capabilities | null;
  draft: string; setDraft: (v: string) => void;
  replyTo: Message | null; onCancelReply: () => void;
  editing: Message | null; onCancelEdit: () => void; onSaveEdit: (body: string) => Promise<void>;
  onSend: (body: string, attachments: Attachment[], mentionIds: string[]) => void;
  busy: boolean;
}

const TEXT_MAX_PX = 200;

export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer({ conv, uid, caps, draft, setDraft, replyTo, onCancelReply, editing, onCancelEdit, onSaveEdit, onSend, busy }, ref) {
  const { t } = useI18n();
  const textRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const emojiBtn = useRef<HTMLButtonElement>(null);
  const atBtn = useRef<HTMLButtonElement>(null);
  const [emojiOpen, setEmojiOpen] = useState(false);
  const [uploads, setUploads] = useState<PendingUpload[]>([]);
  const [mentions, setMentions] = useState<Member[]>([]);
  const [mentionQuery, setMentionQuery] = useState<{ start: number; text: string } | null>(null);
  const [atMenu, setAtMenu] = useState(false);
  const [enterSends, setEnterSends] = useState(() => { try { return localStorage.getItem(ENTER_KEY) !== "newline"; } catch { return true; } });
  const composing = useRef(false);

  useImperativeHandle(ref, () => ({ addFiles: (files) => void addFiles(files), focus: () => textRef.current?.focus() }));

  // grow with the text, up to a cap, then scroll inside
  useEffect(() => {
    const el = textRef.current; if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, TEXT_MAX_PX)}px`;
    el.style.overflowY = el.scrollHeight > TEXT_MAX_PX ? "auto" : "hidden";
  }, [draft]);

  const setEnter = (sends: boolean) => { setEnterSends(sends); try { localStorage.setItem(ENTER_KEY, sends ? "send" : "newline"); } catch { /* not remembered */ } };

  // ---- attachments: one upload per file, real progress, cancel while in flight, retry after a failure
  const startUpload = useCallback((key: string, file: File) => {
    const max = caps?.max_attachment_bytes;
    if (max && file.size > max) {
      setUploads(us => us.map(u => (u.key === key ? { ...u, status: "failed", progress: 0, error: t("超过单个附件 {max} 的上限，请压缩或分开发送。", { max: fmtSize(max) }) } : u)));
      return;
    }
    const form = new FormData(); form.append("file", file);
    const h = uploadWithCancel<Attachment>(`${M()}/conversations/${conv.id}/attachments`, form, p => setUploads(us => us.map(u => (u.key === key ? { ...u, progress: p } : u))));
    setUploads(us => us.map(u => (u.key === key ? { ...u, status: "uploading", progress: 0, error: undefined, cancel: h.cancel } : u)));
    h.promise.then(result => setUploads(us => us.map(u => (u.key === key ? { ...u, status: "done", progress: 1, result, cancel: undefined } : u))),
      (e: unknown) => {
        if (e instanceof DOMException && e.name === "AbortError") { setUploads(us => us.filter(u => u.key !== key)); return; }
        const why = e instanceof ApiError ? e.message : String(e);
        setUploads(us => us.map(u => (u.key === key ? { ...u, status: "failed", error: why, cancel: undefined } : u)));
      });
  }, [caps, conv.id, t]);
  const addFiles = useCallback((files: File[]) => {
    if (!files.length || conv.archived || editing) return;
    const fresh = files.map(file => ({ key: `u${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`, file, progress: 0, status: "uploading" as const }));
    setUploads(us => [...us, ...fresh]);
    for (const f of fresh) startUpload(f.key, f.file);
  }, [conv.archived, editing, startUpload]);
  const removeUpload = (u: PendingUpload) => { u.cancel?.(); setUploads(us => us.filter(x => x.key !== u.key)); };
  const uploading = uploads.some(u => u.status === "uploading");
  const ready = uploads.filter(u => u.status === "done" && u.result).map(u => u.result as Attachment);

  // ---- mentions: `@` at the caret opens the member list filtered by what follows it
  const members = useMemo(() => conv.members.filter(m => m.user_id !== uid), [conv.members, uid]);
  const suggestions = useMemo(() => {
    if (!mentionQuery || conv.kind !== "group") return [];
    const q = mentionQuery.text.toLowerCase();
    return members.filter(m => !q || m.name.toLowerCase().includes(q)).slice(0, 8);
  }, [mentionQuery, members, conv.kind]);
  const [suggestIdx, setSuggestIdx] = useState(0);
  useEffect(() => { setSuggestIdx(0); }, [mentionQuery?.text]);
  const detectMention = (value: string, caret: number) => {
    if (conv.kind !== "group") { setMentionQuery(null); return; }
    const before = value.slice(0, caret);
    const at = before.lastIndexOf("@");
    if (at < 0 || (at > 0 && !/\s/.test(before[at - 1])) || /\s/.test(before.slice(at + 1))) { setMentionQuery(null); return; }
    setMentionQuery({ start: at, text: before.slice(at + 1) });
  };
  const insertMention = (m: Member, from: { start: number; end: number }) => {
    const value = draft.slice(0, from.start) + `@${m.name} ` + draft.slice(from.end);
    setDraft(value);
    if (!mentions.some(x => x.user_id === m.user_id)) setMentions([...mentions, m]);
    setMentionQuery(null); setAtMenu(false);
    requestAnimationFrame(() => { const el = textRef.current; if (el) { el.focus(); const pos = from.start + m.name.length + 2; el.setSelectionRange(pos, pos); } });
  };
  const mentionIdsFor = (body: string) => mentions.filter(m => body.includes(`@${m.name}`)).map(m => m.user_id);

  // ---- sending
  const bodyMax = caps?.max_body ?? null;
  const tooLong = bodyMax !== null && draft.length > bodyMax;
  const canSend = !busy && !uploading && !tooLong && (draft.trim().length > 0 || ready.length > 0);
  const submit = () => {
    if (editing) { if (draft.trim()) void onSaveEdit(draft); return; }
    if (!canSend) return;
    const body = draft;
    onSend(body, ready, mentionIdsFor(body));
    setDraft(""); setUploads(us => us.filter(u => u.status !== "done")); setMentions([]); setMentionQuery(null);
    requestAnimationFrame(() => textRef.current?.focus());
  };
  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (isImeKey(e) || composing.current) return;                      // Enter that confirms a candidate is not a send
    if (mentionQuery && suggestions.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); setSuggestIdx(i => (i + 1) % suggestions.length); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSuggestIdx(i => (i - 1 + suggestions.length) % suggestions.length); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(suggestions[suggestIdx], { start: mentionQuery.start, end: mentionQuery.start + 1 + mentionQuery.text.length }); return; }
      if (e.key === "Escape") { e.preventDefault(); setMentionQuery(null); return; }
    }
    if (e.key === "Escape" && (replyTo || editing)) { e.preventDefault(); editing ? onCancelEdit() : onCancelReply(); return; }
    if (e.key !== "Enter") return;
    if ((enterSends && !e.shiftKey && !e.ctrlKey && !e.metaKey) || (!enterSends && (e.ctrlKey || e.metaKey))) { e.preventDefault(); submit(); }
  };
  const insertAtCaret = (text: string) => {
    const el = textRef.current; const start = el?.selectionStart ?? draft.length; const end = el?.selectionEnd ?? draft.length;
    setDraft(draft.slice(0, start) + text + draft.slice(end));
    requestAnimationFrame(() => { if (el) { el.focus(); el.setSelectionRange(start + text.length, start + text.length); } });
  };
  const onPaste = (e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files || []);
    if (files.length) { e.preventDefault(); addFiles(files); }
  };

  if (conv.archived) return <div className="msg-composer msg-composer-off" role="note">{t("会话已归档，不能再发消息。")}</div>;

  const editingNow = !!editing;
  return <form className="msg-composer" onSubmit={e => { e.preventDefault(); submit(); }} aria-label={t("写消息")} aria-busy={busy}>
    {replyTo && !editingNow && <div className="msg-banner"><span className="msg-banner-text"><strong>{t("回复 {who}", { who: replyTo.sender })}</strong><span>{replyTo.deleted ? t("消息已删除") : replyTo.body || (replyTo.attachment ? replyTo.attachment.filename : "")}</span></span>
      <button type="button" className="btn ghost sm icon" onClick={onCancelReply} aria-label={t("取消回复")}><IcClose size={14} /></button></div>}
    {editingNow && <div className="msg-banner"><span className="msg-banner-text"><strong>{t("正在编辑")}</strong><span>{t("保存后其他人会看到「已编辑」标记。")}</span></span>
      <button type="button" className="btn ghost sm" onClick={onCancelEdit}>{t("取消")}</button></div>}
    {uploads.length > 0 && <ul className="msg-uploads" aria-label={t("待发送的附件")}>
      {uploads.map(u => <li key={u.key} className={`msg-upload ${u.status}`}>
        <span className="msg-upload-ic" aria-hidden="true">{u.file.type.startsWith("image/") ? <IcImage size={16} /> : <IcAttach size={16} />}</span>
        <span className="msg-upload-main">
          <span className="msg-upload-name">{u.file.name} <span className="tiny muted">({fmtSize(u.file.size)})</span></span>
          {u.status === "uploading" && <progress className="msg-progress" value={Math.round(u.progress * 100)} max={100} aria-label={t("上传进度：{name}", { name: u.file.name })}>{Math.round(u.progress * 100)}%</progress>}
          {u.status === "uploading" && <span className="tiny muted">{t("上传中 {pct}%", { pct: Math.round(u.progress * 100) })}</span>}
          {u.status === "done" && <span className="tiny muted">{t("已上传，随消息发送")}</span>}
          {u.status === "failed" && <span className="tiny msg-upload-err" role="alert">{u.error}</span>}
        </span>
        {u.status === "uploading" && <button type="button" className="btn ghost sm" onClick={() => removeUpload(u)}>{t("取消")}</button>}
        {u.status === "failed" && <button type="button" className="btn ghost sm" onClick={() => startUpload(u.key, u.file)}>{t("重试")}</button>}
        <button type="button" className="btn ghost sm icon" onClick={() => removeUpload(u)} aria-label={t("移除附件：{name}", { name: u.file.name })}><IcClose size={14} /></button>
      </li>)}
    </ul>}
    <div className="msg-composer-box">
      <textarea ref={textRef} className="msg-text" rows={1} value={draft} placeholder={editingNow ? t("修改消息…") : t("写消息…")} aria-label={editingNow ? t("修改消息") : t("写消息")}
        disabled={busy} maxLength={bodyMax ? bodyMax + 200 : undefined}
        onChange={e => { setDraft(e.target.value); detectMention(e.target.value, e.target.selectionStart ?? e.target.value.length); }}
        onKeyDown={onKey} onPaste={onPaste}
        onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
        aria-describedby="msg-composer-hint" />
      <div className="msg-tools">
        <button ref={emojiBtn} type="button" className="msg-tool" onClick={() => setEmojiOpen(o => !o)} aria-label={t("表情")} title={t("表情")} aria-expanded={emojiOpen} aria-haspopup="dialog"><IcEmoji /></button>
        {!editingNow && <button type="button" className="msg-tool" onClick={() => fileRef.current?.click()} aria-label={t("附件")} title={caps ? t("附件（单个最大 {max}）", { max: fmtSize(caps.max_attachment_bytes) }) : t("附件")}><IcAttach /></button>}
        {conv.kind === "group" && !editingNow && <button ref={atBtn} type="button" className="msg-tool" onClick={() => setAtMenu(o => !o)} aria-label={t("提及成员")} title={t("提及成员")} aria-expanded={atMenu} aria-haspopup="listbox"><IcAt /></button>}
        <Menu ariaLabel={t("输入设置")} buttonClass="msg-tool" trigger={<IcSettings size={18} />} align="start" items={[
          { label: <>{enterSends ? "✓ " : ""}{t("Enter 发送，Shift+Enter 换行")}</>, onSelect: () => setEnter(true) },
          { label: <>{!enterSends ? "✓ " : ""}{t("Enter 换行，Ctrl/⌘+Enter 发送")}</>, onSelect: () => setEnter(false) },
        ]} />
        <span className="spacer" />
        <span id="msg-composer-hint" className="msg-hint tiny muted">
          {tooLong ? <span className="msg-hint-err" role="alert">{t("超出 {n} 字，请缩短后再发送。", { n: draft.length - (bodyMax as number) })}</span>
            : bodyMax && draft.length > bodyMax * 0.8 ? t("{n}/{max} 字", { n: draft.length, max: bodyMax })
            : uploading ? t("附件上传完成后才能发送") : ""}
        </span>
        <button type="submit" className="btn primary sm msg-send" disabled={editingNow ? busy || !draft.trim() || tooLong : !canSend} aria-label={editingNow ? t("保存") : t("发送")}>
          <IcSend size={16} /><span className="msg-send-label">{editingNow ? t("保存") : t("发送")}</span>
        </button>
      </div>
    </div>
    <input ref={fileRef} type="file" multiple hidden tabIndex={-1} aria-hidden="true" onChange={e => { addFiles(Array.from(e.target.files || [])); e.target.value = ""; }} />
    <EmojiPicker open={emojiOpen} anchor={emojiBtn} onClose={refocus => { setEmojiOpen(false); if (refocus) emojiBtn.current?.focus(); }} onPick={e => { insertAtCaret(e); }} />
    <Popover open={atMenu && conv.kind === "group"} anchor={atBtn} onClose={refocus => { setAtMenu(false); if (refocus) atBtn.current?.focus(); }} label={t("提及成员")} className="msg-mentions" role="listbox">
      {members.length === 0 ? <div className="tiny muted" style={{ padding: 8 }}>{t("群里没有其他成员。")}</div>
        : members.map((m, i) => <button key={m.user_id} type="button" role="option" aria-selected={false} data-autofocus={i === 0 ? true : undefined} onClick={() => { const el = textRef.current; const pos = el?.selectionStart ?? draft.length; insertMention(m, { start: pos, end: pos }); }}>{m.name}</button>)}
    </Popover>
    <Popover open={!!mentionQuery && suggestions.length > 0} anchor={textRef} onClose={() => setMentionQuery(null)} label={t("提及成员")} className="msg-mentions" role="listbox" autoFocus={false}>
      {suggestions.map((m, i) => <button key={m.user_id} type="button" role="option" aria-selected={i === suggestIdx} className={i === suggestIdx ? "on" : ""} tabIndex={-1}
        onMouseDown={e => e.preventDefault()} onClick={() => insertMention(m, { start: mentionQuery!.start, end: mentionQuery!.start + 1 + mentionQuery!.text.length })}>{m.name}</button>)}
    </Popover>
  </form>;
});
