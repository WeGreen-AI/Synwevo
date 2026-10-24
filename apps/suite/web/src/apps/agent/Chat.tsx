import { useEffect, useId, useRef, useState, type ClipboardEvent, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Modal, useI18n } from "@synwevo/ui";
import { Loading, RunBadge } from "../../shared/states";
import { withAiLabel } from "./aiLabel";
import { AiLabel, RefusalNotice, ThinkingStatus, TurnCostLine, useChatText } from "./ChatParts";
import { shouldSubmit } from "./composer";
import FilePicker, { FileChips } from './FilePicker';
import { addFile, createFileClient, fileReferences, type FileClient } from './files';
import ContextPicker, { ContextChips, ContextSources, useContextText } from "./ContextPicker";
import { addContext, contextKey, contextReferences, contextSpaces } from "./context";
import { AgentFailure as Failure, agentErrorText } from "./errors";
import { Markdown } from "./Markdown";
import { validTurnCost } from "./cost";
import { refusalOf } from "./refusals";
import ReportDialog, { useReportText } from "./ReportDialog";
import { activeTurn, branchMessages, type ContextReference, type SelectedContext, type FileReference, type SelectedFile, type Conversation, type Message, type ToolCall, type Workspace } from "./types";

export type Act = (operation: string, input: Record<string, unknown>) => Promise<unknown>;
/** The message in the composer, as one send would carry it to `agent.turn.start`. */
export interface Draft { text: string; context_refs?: ContextReference[]; file_refs?: FileReference[] }
/** The composer as it stands (text and the chosen sources and files), kept by the page while the
 *  person is in Agent settings and given back when they return (addendum-2 W10). */
export interface ComposerDraft { text: string; contexts: SelectedContext[]; files: SelectedFile[] }

function Status({ status }: { status: string }) {
  const { t } = useI18n();
  if (["waiting_confirmation", "awaiting_confirmation"].includes(status)) return <span className="badge warn">{t("等待确认")}</span>;
  if (status === "declined") return <span className="badge">{t("已拒绝")}</span>;
  if (status === "proposed") return <span className="badge">{t("准备执行")}</span>;
  return <RunBadge status={status} />;
}

function Values({ value }: { value: unknown }) {
  if (value == null) return <span>—</span>;
  if (typeof value !== "object") return <span className="ag-value">{String(value)}</span>;
  if (Array.isArray(value)) return <ol className="ag-values">{value.map((v, i) => <li key={i}><Values value={v} /></li>)}</ol>;
  return <dl className="ag-values">{Object.entries(value).map(([key, v]) => <div key={key}>
    <dt>{key}</dt><dd><Values value={v} /></dd>
  </div>)}</dl>;
}

function ActionCard({ call, act, busy }: { call: ToolCall; act: Act; busy: boolean }) {
  const { t, locale } = useI18n();
  const pending = call.status === "awaiting_confirmation" && !call.hidden;
  const toolTitles: Record<string, string> = {
    list_workspaces: t("查看可用工作空间"),
    find_operations: t("查找应用操作"),
    run_operation: t("执行应用操作"),
  };
  const title = call.operation_title?.[locale] ?? call.operation_title?.en ?? toolTitles[call.tool] ?? t("应用操作");
  // A server-produced application link must remain on this origin.
  const safeLink = call.link?.startsWith("/") && !call.link.startsWith("//") ? call.link : null;
  return <section className={`ag-action${pending ? " pending" : ""}`} aria-label={title}>
    <div className="ag-action-heading"><strong>{title}</strong><Status status={call.status} /></div>
    <p className="muted small">{call.app_name} {call.workspace_name ? `· ${call.workspace_name}` : `· ${t("个人 Agent")}`}</p>
    {call.target_workspace_name && call.target_workspace_id !== call.workspace_id &&
      <p className="small">{t("目标工作空间")} · <strong>{call.target_workspace_name}</strong></p>}
    {!call.hidden && !!call.transfer_from?.length && <div className="small ag-transfer">
      <p>{t("会把以下工作空间的内容带入{target}，其成员将能看到：", { target: call.target_workspace_name ?? t("目标工作空间") })}</p>
      <ul>{call.transfer_from.map((source) => <li key={source.workspace_id}><strong>{source.workspace_name ?? source.workspace_id}</strong></li>)}</ul>
    </div>}
    {call.hidden ? <p>{t("内容因权限变化已隐藏")}</p> : <>
      <details open={pending ? true : undefined}>
        <summary>{pending ? t("拟执行的变更") : t("执行详情")}</summary>
        <div className="ag-expand"><Values value={call.input} />
          {call.result != null && <><h4>{t("执行结果")}</h4><Values value={call.result} /></>}</div>
      </details>
      {call.error && <p className="err" role="alert">{agentErrorText(call.error, t)}</p>}
      {pending && <div className="ag-action-buttons">
        <button className="btn primary" disabled={busy} onClick={() => void act("agent.tool_call.confirm", { tool_call_id: call.id, confirmation: call.confirmation })}>{t("确认执行")}</button>
        <button className="btn" disabled={busy} onClick={() => void act("agent.tool_call.decline", { tool_call_id: call.id })}>{t("拒绝执行")}</button>
      </div>}
      {safeLink && <Link className="btn ghost sm" to={safeLink}>{t("打开产物")}</Link>}
    </>}
  </section>;
}

function Scope({ scope, spaces, change, close }: { scope: string[] | null; spaces: Workspace[]; change: (scope: string[] | null) => Promise<unknown>; close: () => void }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState(scope);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>();
  async function save() {
    setSaving(true); setError(undefined);
    try { await change(selected); close(); } catch (e) { setError(e); } finally { setSaving(false); }
  }
  return <Modal title={t("工作空间范围")} onClose={close} footer={<button className="btn primary" disabled={saving} onClick={() => void save()}>{t("保存")}</button>}>
    <label className="ag-check"><input type="checkbox" checked={selected === null} onChange={(e) => setSelected(e.target.checked ? null : [])} />{t("全部获授权空间")}</label>
    {spaces.map((ws) => <label className="ag-check" key={ws.workspace_id}>
      <input type="checkbox" checked={selected === null || selected.includes(ws.workspace_id)} disabled={selected === null || saving}
        onChange={(e) => setSelected((old) => e.target.checked ? [...old ?? [], ws.workspace_id] : (old ?? []).filter((id) => id !== ws.workspace_id))} />
      <span>{ws.name}<small className="muted">{ws.processing === "none" ? t("业务内容不允许发送给模型") : ws.role}</small></span>
    </label>)}
    {selected?.length === 0 && <p className="muted">{t("仅个人对话")}</p>}
    {error != null && <Failure error={error} />}
  </Modal>;
}

/** Copy feedback for one answer: "copied", "failed", or nothing. */
type CopyState = { id: string; ok: boolean } | null;

export default function Chat({ conversation, loading, error, connection, configured, spaces, act, send, refresh, sentDraft = 0, aside,
  status, maximum, hint, walletUrl, organizationPayer = false, onPersonal, payerName, fileClient, onSetup, onDraft, restore, onRestored, onLeave }: {
  conversation?: Conversation; loading: boolean; error: unknown; connection: string; configured: boolean | undefined;
  spaces: Workspace[]; act: Act; send: (text: string, context_refs?: ContextReference[], file_refs?: FileReference[]) => Promise<unknown>; refresh: () => Promise<void>; sentDraft?: number;
  fileClient?: FileClient;
  /** Payment state for this conversation, shown above the composer. */
  aside?: ReactNode;
  /** Payer, balance, limit and model, shown just above the message box. */
  status?: ReactNode;
  /** The most this turn can cost, shown beside the send button. */
  maximum?: ReactNode;
  /** An extra line under the composer (how funds are held). */
  hint?: string;
  /** The account service's wallet page, for a top-up next step. */
  walletUrl?: string;
  /** The selected payer is an organization (a fresh refusal is about its wallet or allowance). */
  organizationPayer?: boolean;
  /** Switch the payer to the personal wallet, after an organization payer was refused. */
  onPersonal?: () => void;
  /** An organization's name for an organization-paid answer. */
  payerName?: (payer: { kind: "organization"; id: string }) => string | null | undefined;
  /** Open the one-time "Start using Synwevo Agent" step, keeping the message (a refusal's next step). */
  onSetup?: (draft?: Draft) => void;
  /** While that step is open: the message as it now stands (null when it cannot be sent as is),
   *  so the step's button sends what the composer shows. */
  onDraft?: (draft: Draft | null) => void;
  /** A message the page kept while the person was away (in Agent settings): the composer starts
   *  from it and says so with `onRestored`. */
  restore?: ComposerDraft;
  onRestored?: () => void;
  /** The composer as it stands when this chat goes away (the page keeps it only for Agent settings). */
  onLeave?: (draft: ComposerDraft) => void;
}) {
  const { t } = useI18n();
  const { c } = useChatText();
  const contextText = useContextText();
  const id = useId();
  const [text, setText] = useState(() => restore?.text ?? "");
  const [selectedContexts, setSelectedContexts] = useState<SelectedContext[]>(() => restore?.contexts ?? []);
  const [selectedFiles, setSelectedFiles] = useState<SelectedFile[]>(() => restore?.files ?? []);
  const [localFileClient] = useState(createFileClient);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>();
  // What a refusal stopped, to repeat on "send again": the message in the composer as it stands at
  // the click, or that regenerate. Kept as what to repeat, not as a function of the render that failed, so the click
  // goes through this render's send/act and carries the payer and price the page shows now (AD-1).
  const retryFailed = useRef<"message" | { operation: string; body: Record<string, unknown> } | undefined>(undefined);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [newContent, setNewContent] = useState(false);
  const [copied, setCopied] = useState<CopyState>(null);
  /** The answer being reported, and the answers reported from this page (the server refuses a second report). */
  const [reporting, setReporting] = useState<string | null>(null);
  const [reported, setReported] = useState<ReadonlySet<string>>(() => new Set());
  const r = useReportText();
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const composing = useRef(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const input = useRef<HTMLTextAreaElement>(null);
  const branch = branchMessages(conversation?.messages ?? [], conversation?.head_id ?? null);
  const running = (conversation?.messages ?? []).find(activeTurn);
  const blocked = loading || error != null || configured !== true
    || busy || !!running || !!conversation?.sealed || conversation?.status === "archived";
  const pickerSpaces = contextSpaces(spaces, conversation?.scope ?? null);
  const pickerScope = JSON.stringify([conversation?.id, conversation?.scope, pickerSpaces.map((ws) => ws.workspace_id)]);
  const outsideScope = conversation?.scope != null && selectedContexts.some((item) => !conversation.scope!.includes(item.reference.workspace_id));
  useEffect(() => { if (sentDraft) { setText(""); setSelectedContexts([]); setSelectedFiles([]); setFailure(undefined); stick.current = true; input.current?.focus(); } }, [sentDraft]);
  // A kept message is taken once; the composer as it stands goes back to the page when this chat
  // goes away (W10: a refusal whose next step is Agent settings must not lose the message).
  const composer = useRef<{ draft: ComposerDraft; leave?: (draft: ComposerDraft) => void }>({ draft: { text, contexts: selectedContexts, files: selectedFiles } });
  composer.current = { draft: { text, contexts: selectedContexts, files: selectedFiles }, leave: onLeave };
  useEffect(() => {
    if (restore) onRestored?.();
    return () => { const { draft, leave } = composer.current; leave?.(draft); };
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  /** The composer's message as one send carries it; throws when an attached file is not ready. */
  function draftOf(): Draft {
    const contexts = contextReferences(selectedContexts), files = fileReferences(selectedFiles, selectedContexts.length);
    return { text, ...(contexts ? { context_refs: contexts } : {}), ...(files ? { file_refs: files } : {}) };
  }
  useEffect(() => {
    if (!onDraft) return;
    let next: Draft | null;
    try { next = text.trim() ? draftOf() : null; } catch { next = null; }
    onDraft(next);
  }, [onDraft, text, selectedContexts, selectedFiles]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setFailure(undefined); stick.current = true; setNewContent(false); setReporting(null); }, [conversation?.id]);
  useEffect(() => () => clearTimeout(copyTimer.current), []);
  useEffect(() => {
    // Follow the stream only while the reader is at the bottom; never pull them back from reading.
    const box = scroller.current;
    if (!box) return;
    if (stick.current) box.scrollTop = box.scrollHeight;
    else setNewContent(true);
  }, [conversation?.event_seq, conversation?.head_id]);
  async function action(operation: string, body: Record<string, unknown>) {
    setBusy(true); setFailure(undefined);
    try { return await act(operation, body); }
    catch (e) { retryFailed.current = operation === "agent.turn.start" ? { operation, body } : undefined; setFailure(e); }
    finally { setBusy(false); }
  }
  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (blocked || !text.trim()) return;
    setBusy(true); setFailure(undefined);
    try {
      const refs = fileReferences(selectedFiles, selectedContexts.length);
      const result = refs ? await send(text, contextReferences(selectedContexts), refs) : await send(text, contextReferences(selectedContexts));
      if (result !== false) { setText(""); setSelectedContexts([]); setSelectedFiles([]); stick.current = true; input.current?.focus(); } }
    catch (e) { retryFailed.current = "message"; setFailure(e); } finally { setBusy(false); }
  }
  function retry() {
    const again = retryFailed.current;
    if (again === "message") void submit();
    else if (again) void action(again.operation, again.body);
  }
  function flash(state: CopyState) {
    setCopied(state);
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(null), 2500);
  }
  async function copyAnswer(m: Message) {
    try { await navigator.clipboard.writeText(withAiLabel(m.body, c("aiLabel"))); flash({ id: m.id, ok: true }); }
    catch { flash({ id: m.id, ok: false }); }
  }
  /** A selection that touches any answer leaves the page with the AI label (DeepSeek terms 8.1). */
  function copySelection(event: ClipboardEvent<HTMLDivElement>) {
    const selection = typeof window !== "undefined" ? window.getSelection() : null;
    const selected = selection?.toString() ?? "";
    if (!selection || !selected.trim()) return;
    const answers = scroller.current?.querySelectorAll(".ag-message.assistant .ag-markdown") ?? [];
    if (![...answers].some((node) => selection.containsNode(node, true))) return;
    event.clipboardData.setData("text/plain", withAiLabel(selected, c("aiLabel")));
    event.preventDefault();
  }
  function answer(m: Message) {
    const siblings = (conversation?.messages ?? []).filter((s) => s.parent_id === m.parent_id && s.role === m.role);
    const position = siblings.findIndex((s) => s.id === m.id);
    const assistant = m.role === "assistant";
    const active = !!m.turn && activeTurn(m);
    const thinking = assistant && active && m.turn!.status === "running" && !m.body;
    // A refusal in the transcript is about the payer the ledger charged for that run.
    const refusal = m.error ? refusalOf(m.error, { organization: validTurnCost(m.turn?.cost)?.payer?.kind === "organization" }) : null;
    const failed = m.turn?.status === "failed";
    return <article key={m.id} className={`ag-message ${m.role}`}>
      <div className="ag-message-label">{m.role === "user" ? t("你") : "Synwevo Agent"}</div>
      {m.hidden ? <p className="notice">{t("内容因权限变化已隐藏")}</p> : !thinking && (m.body || !assistant) &&
        <div className={`ag-markdown${assistant && active && m.body ? " streaming" : ""}`}><Markdown text={m.body} /></div>}
      {thinking && <ThinkingStatus />}
      <ContextSources contexts={m.contexts} hidden={m.hidden} />
      {assistant && active && !!m.body && m.turn!.status === "running" && <span className="sr-only" role="status">{c("writing")}</span>}
      {m.tool_calls.map((call) => <ActionCard key={call.id} call={call} act={action} busy={busy} />)}
      {m.turn && active && m.turn.status !== "running" && <p role="status"><Status status={m.turn.status} /></p>}
      {/* "Send again" regenerates this answer in one request (a changed price applies to it); the
          one-time setup step opens inline. Neither is a confirmation. */}
      {m.error && (refusal ? <RefusalNotice refusal={refusal} walletUrl={walletUrl} busy={blocked} onPersonal={organizationPayer ? onPersonal : undefined}
        onRetry={refusal.next === "retry" ? () => void action("agent.turn.start", { conversation_id: conversation!.id, regenerate_of: m.id }) : undefined}
        onSetup={refusal.next === "setup" && onSetup ? () => onSetup() : undefined} />
        : <p className="err">{agentErrorText(m.error, t)}</p>)}
      {m.turn?.status === "cancelled" && <p className="muted">{t("已取消，不再启动后续操作。已完成的结果保留。")}</p>}
      {assistant && !m.hidden && <footer className="ag-answer-foot">
        {!!m.body && <AiLabel />}
        <TurnCostLine cost={m.turn?.cost} payerName={payerName} />
        {/* While this answer is still being written there is nothing final to copy, retry or switch. */}
        {!active && <div className="ag-message-tools">
          {!!m.body && <button type="button" className="btn ghost sm" onClick={() => void copyAnswer(m)}>
            {copied?.id === m.id && copied.ok ? <span className="ag-done">{c("copied")}</span> : c("copy")}
          </button>}
          {copied?.id === m.id && !copied.ok && <span className="err small" role="alert">{c("copyFailed")}</span>}
          <span className="sr-only" role="status">{copied?.id === m.id && copied.ok ? c("copied") : ""}</span>
          <button type="button" className="btn ghost sm" disabled={blocked}
            onClick={() => void action("agent.turn.start", { conversation_id: conversation!.id, regenerate_of: m.id })}>
            {failed ? c("retry") : t("重新生成回答")}
          </button>
          {!!m.body && (reported.has(m.id)
            ? <button type="button" className="btn ghost sm" aria-disabled="true" aria-label={r("reportedLabel")}>{r("reported")}</button>
            : <button type="button" className="btn ghost sm" aria-haspopup="dialog" aria-label={r("openLabel")}
              onClick={() => setReporting(m.id)}>{r("open")}</button>)}
          {siblings.length > 1 && <div className="ag-branch" role="group" aria-label={c("branchGroup")}>
            <button type="button" className="btn ghost sm" aria-label={c("branchPrev")} disabled={busy || !!running || position <= 0}
              onClick={() => void action("agent.conversation.update", { conversation_id: conversation!.id, head_id: siblings[position - 1].id })}>
              <span aria-hidden="true">‹</span></button>
            <span className="ag-branch-pos">{c("branchPosition", { n: position + 1, total: siblings.length })}</span>
            <button type="button" className="btn ghost sm" aria-label={c("branchNext")} disabled={busy || !!running || position >= siblings.length - 1}
              onClick={() => void action("agent.conversation.update", { conversation_id: conversation!.id, head_id: siblings[position + 1].id })}>
              <span aria-hidden="true">›</span></button>
          </div>}
        </div>}
      </footer>}
    </article>;
  }
  const failureRefusal = failure != null ? refusalOf(failure, { organization: organizationPayer }) : null;
  return <div className="ag-chat">
    <div className="ag-transcript-wrap">
      <div className="ag-transcript" ref={scroller} tabIndex={0} aria-label={t("当前会话")} onCopy={copySelection}
        onScroll={() => { const el = scroller.current!; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; if (stick.current) setNewContent(false); }}>
        <div className="ag-content">
          {loading ? <Loading /> : error != null ? <Failure error={error} onRetry={() => void refresh()} /> : <>
            {!branch.length && <div className="ag-welcome"><h2>{t("今天想完成什么？")}</h2><p>{t("在一次会话中使用你获授权的工作空间。每次写入都展示实际目标和变更。")}</p></div>}
            {branch.map(answer)}
          </>}
        </div>
      </div>
      {newContent && <button type="button" className="btn ag-new-content" onClick={() => {
        stick.current = true; const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; setNewContent(false);
      }}>{c("newContent")} <span aria-hidden="true">↓</span></button>}
    </div>
    <div className="ag-composer-area">
      {aside}
      {conversation?.sealed && <p className="notice" role="alert">{t("访问权限已改变，相关内容已隐藏。请新建会话继续。")}</p>}
      {conversation?.status === "archived" && <p className="notice">{t("这个会话已归档。恢复后可继续。")}</p>}
      {configured === false && <p className="notice"><Link to="/agent/settings">{t("模型服务未配置。请先打开 Agent 设置。")}</Link></p>}
      {conversation && connection !== "connected" && <p className="muted small" role="status">{t(connection === "offline" ? "连接中断，正在恢复；已完成的操作不会重复执行。" : "正在连接实时更新…")}</p>}
      {/* A refused send keeps the message: "send again" sends it as it now stands, "finish setup"
          opens the one-time step with it. */}
      {failure != null && (failureRefusal ? <RefusalNotice refusal={failureRefusal} walletUrl={walletUrl} live busy={blocked} onPersonal={onPersonal}
        onRetry={failureRefusal.next === "retry" && retryFailed.current ? retry : undefined}
        onSetup={failureRefusal.next === "setup" && onSetup ? () => {
          let draft: Draft | undefined;
          try { draft = text.trim() ? draftOf() : undefined; } catch { draft = undefined; }
          setFailure(undefined); onSetup(draft);   // the step now says what is needed
        } : undefined} />
        : <Failure error={failure} />)}
      {status}
      <ContextPicker key={pickerScope} spaces={pickerSpaces} selected={selectedContexts} otherCount={selectedFiles.length} disabled={blocked}
        onChoose={(item) => setSelectedContexts((old) => addContext(old, item, selectedFiles.length))}
        onRemove={(key) => setSelectedContexts((old) => old.filter((item) => contextKey(item.reference) !== key))} />
      <FilePicker client={fileClient ?? localFileClient} selected={selectedFiles} businessCount={selectedContexts.length}
        disabled={loading || error != null || busy} onChoose={(file) => setSelectedFiles((old) => addFile(old, file, selectedContexts.length))}
        onRemove={(fid) => setSelectedFiles((old) => old.filter((file) => file.reference.file_id !== fid))}
        onRecord={(file) => setSelectedFiles((old) => old.filter((selected) => selected.reference.file_id !== file.id
          || (file.status === 'ready' && selected.reference.snapshot === file.reference?.snapshot)))}
        onDeleted={refresh} />
      <FileChips selected={selectedFiles} disabled={blocked} onRemove={(fid) => setSelectedFiles((old) => old.filter((file) => file.reference.file_id !== fid))} />
      <ContextChips selected={selectedContexts} disabled={blocked} onRemove={(key) => setSelectedContexts((old) => old.filter((item) => contextKey(item.reference) !== key))} />
      {outsideScope && <p className="notice err" role="alert">{contextText("outsideScope")}</p>}
      <form className="ag-composer" onSubmit={(e) => void submit(e)}>
        <label className="sr-only" htmlFor="agent-message">{t("输入消息")}</label>
        <textarea id="agent-message" ref={input} rows={3} maxLength={20000} value={text} placeholder={t("输入消息")} disabled={!!conversation?.sealed}
          enterKeyHint="send" aria-describedby={`${id}-hint`}
          onChange={(e) => setText(e.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={(e) => { if (shouldSubmit({ key: e.key, shiftKey: e.shiftKey, altKey: e.altKey, isComposing: e.nativeEvent.isComposing, keyCode: e.keyCode }, composing.current)) { e.preventDefault(); void submit(); } }} />
        <div className="ag-compose-tools">
          {conversation && <button type="button" className="btn ghost sm" disabled={busy || !!running} onClick={() => setScopeOpen(true)}>
            {conversation.scope === null ? t("全部获授权空间") : t("选择 {n} 个空间", { n: conversation.scope.length })}
          </button>}
          <span className="spacer" />
          {!running && maximum}
          {running?.turn ? <button className="btn" type="button" disabled={busy || !!running.turn.stopping} aria-describedby={running.turn.stopping ? `${id}-stopping` : undefined}
            onClick={() => void action("agent.turn.cancel", { turn_id: running.turn!.id })}>{t("停止回答")}</button>
            : <button className="btn primary" type="submit" disabled={blocked || !text.trim()}>{t("发送消息")}</button>}
        </div>
      </form>
      <p className="ag-composer-hint" id={`${id}-hint`}>{t("Enter 发送 · Shift+Enter 换行")}{hint ? ` · ${hint}` : ""}</p>
      {/* The stop was accepted; the stream ends at its next stop point, and nothing further starts. */}
      {running?.turn?.stopping && <p className="muted small" id={`${id}-stopping`} role="status" aria-live="polite">{c("stopping")}</p>}
    </div>
    {reporting && conversation && <ReportDialog conversationId={conversation.id} messageId={reporting}
      send={(input) => act("agent.message.report", input)} onClose={() => setReporting(null)}
      onReported={(messageId) => setReported((old) => new Set(old).add(messageId))} />}
    {scopeOpen && conversation && <Scope scope={conversation.scope} spaces={spaces} close={() => setScopeOpen(false)}
      change={(scope) => act("agent.conversation.update", { conversation_id: conversation.id, ...(scope === null ? { clear_scope: true } : { scope }) })} />}
  </div>;
}
