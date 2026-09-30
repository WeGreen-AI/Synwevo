import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Modal, useI18n } from "@synwevo/ui";
import { Loading, RunBadge } from "../../shared/states";
import { AgentFailure as Failure, agentErrorText } from "./errors";
import { Markdown } from "./Markdown";
import { activeTurn, branchMessages, type Conversation, type Message, type ToolCall, type Workspace } from "./types";

export type Act = (operation: string, input: Record<string, unknown>) => Promise<unknown>;

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
  const title = call.operation_title?.[locale] ?? call.operation_title?.en ?? call.tool;
  // A server-produced application link must remain on this origin.
  const safeLink = call.link?.startsWith("/") && !call.link.startsWith("//") ? call.link : null;
  return <section className={`ag-action${pending ? " pending" : ""}`} aria-label={title}>
    <div className="ag-action-heading"><strong>{title}</strong><Status status={call.status} /></div>
    <p className="muted small">{call.app_name} {call.workspace_name ? `· ${call.workspace_name}` : `· ${t("个人 Agent")}`}</p>
    {call.target_workspace_name && call.target_workspace_id !== call.workspace_id &&
      <p className="small">{t("目标工作空间")} · <strong>{call.target_workspace_name}</strong></p>}
    {call.hidden ? <p>{t("内容因权限变化已隐藏")}</p> : <>
      <details open={pending ? true : undefined}>
        <summary>{pending ? t("拟执行的变更") : t("执行详情")}</summary>
        <Values value={call.input} />
        {call.result != null && <><h4>{t("执行结果")}</h4><Values value={call.result} /></>}
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

export default function Chat({ conversation, loading, error, connection, configured, spaces, act, send, refresh }: {
  conversation?: Conversation; loading: boolean; error: unknown; connection: string; configured: boolean | undefined;
  spaces: Workspace[]; act: Act; send: (text: string) => Promise<unknown>; refresh: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>();
  const [scopeOpen, setScopeOpen] = useState(false);
  const [newContent, setNewContent] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const composing = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const branch = branchMessages(conversation?.messages ?? [], conversation?.head_id ?? null);
  const running = (conversation?.messages ?? []).find(activeTurn);
  const blocked = busy || !!running || !!conversation?.sealed || conversation?.status === "archived" || configured === false;
  useEffect(() => { setFailure(undefined); stick.current = true; setNewContent(false); }, [conversation?.id]);
  useEffect(() => {
    const box = scroller.current;
    if (!box) return;
    if (stick.current) box.scrollTop = box.scrollHeight;
    else setNewContent(true);
  }, [conversation?.event_seq, conversation?.head_id]);
  async function action(operation: string, body: Record<string, unknown>) {
    setBusy(true); setFailure(undefined);
    try { return await act(operation, body); } catch (e) { setFailure(e); } finally { setBusy(false); }
  }
  async function submit(e?: FormEvent) {
    e?.preventDefault();
    if (blocked || !text.trim()) return;
    setBusy(true); setFailure(undefined);
    try { await send(text); setText(""); stick.current = true; input.current?.focus(); }
    catch (e) { setFailure(e); } finally { setBusy(false); }
  }
  function answer(m: Message) {
    const siblings = (conversation?.messages ?? []).filter((s) => s.parent_id === m.parent_id && s.role === m.role);
    return <article key={m.id} className={`ag-message ${m.role}`}>
      <div className="ag-message-label">{m.role === "user" ? t("你") : "Synwevo Agent"}</div>
      {m.hidden ? <p className="notice">{t("内容因权限变化已隐藏")}</p> : <div className="ag-markdown"><Markdown text={m.body} /></div>}
      {m.tool_calls.map((call) => <ActionCard key={call.id} call={call} act={action} busy={busy} />)}
      {m.turn && activeTurn(m) && <p role="status"><Status status={m.turn.status} /></p>}
      {m.error && <p className="err" role="alert">{agentErrorText(m.error, t)}</p>}
      {m.turn?.status === "cancelled" && <p className="muted">{t("已取消，不再启动后续操作。已完成的结果保留。")}</p>}
      {m.role === "assistant" && !m.hidden && <div className="ag-message-tools">
        <button className="btn ghost sm" disabled={blocked} onClick={() => void action("agent.turn.start", { conversation_id: conversation!.id, regenerate_of: m.id })}>{t("重新生成回答")}</button>
        {siblings.length > 1 && <select className="input sm" aria-label={t("查看回答分支")} value={m.id} disabled={busy || !!running}
          onChange={(e) => void action("agent.conversation.update", { conversation_id: conversation!.id, head_id: e.target.value })}>
          {siblings.map((s, n) => <option key={s.id} value={s.id}>{t("分支 {n}", { n: n + 1 })}</option>)}
        </select>}
      </div>}
    </article>;
  }
  return <div className="ag-chat">
    <div className="ag-transcript" ref={scroller} tabIndex={0} aria-label={t("当前会话")}
      onScroll={() => { const el = scroller.current!; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; if (stick.current) setNewContent(false); }}>
      <div className="ag-content">
        {loading ? <Loading /> : error != null ? <Failure error={error} onRetry={() => void refresh()} /> : <>
          {!branch.length && <div className="ag-welcome"><h2>{t("今天想完成什么？")}</h2><p>{t("在一次会话中使用你获授权的工作空间。每次写入都展示实际目标和变更。")}</p></div>}
          {branch.map(answer)}
        </>}
      </div>
    </div>
    {newContent && <button className="btn ag-new-content" onClick={() => { stick.current = true; const el = scroller.current; if (el) el.scrollTop = el.scrollHeight; setNewContent(false); }}>{t("查看新内容")}</button>}
    <div className="ag-composer-area">
      {conversation?.sealed && <p className="notice" role="alert">{t("访问权限已改变，相关内容已隐藏。请新建会话继续。")}</p>}
      {conversation?.status === "archived" && <p className="notice">{t("这个会话已归档。恢复后可继续。")}</p>}
      {configured === false && <p className="notice"><Link to="/agent/settings">{t("模型服务未配置。请先打开 Agent 设置。")}</Link></p>}
      {conversation && connection !== "connected" && <p className="muted small" role="status">{t(connection === "offline" ? "连接中断，正在恢复；已完成的操作不会重复执行。" : "正在连接实时更新…")}</p>}
      {failure != null && <Failure error={failure} />}
      <form className="ag-composer" onSubmit={(e) => void submit(e)}>
        <label className="sr-only" htmlFor="agent-message">{t("输入消息")}</label>
        <textarea id="agent-message" ref={input} rows={3} maxLength={20000} value={text} placeholder={t("输入消息")} disabled={!!conversation?.sealed}
          onChange={(e) => setText(e.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !composing.current && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void submit(); } }} />
        <div className="ag-compose-tools">
          {conversation && <button type="button" className="btn ghost sm" disabled={busy || !!running} onClick={() => setScopeOpen(true)}>
            {conversation.scope === null ? t("全部获授权空间") : t("选择 {n} 个空间", { n: conversation.scope.length })}
          </button>}
          <span className="spacer" />
          {running?.turn ? <button className="btn" type="button" disabled={busy} onClick={() => void action("agent.turn.cancel", { turn_id: running.turn!.id })}>{t("停止回答")}</button>
            : <button className="btn primary" type="submit" disabled={blocked || !text.trim()}>{t("发送消息")}</button>}
        </div>
      </form>
      <p className="ag-composer-hint">{t("Enter 发送 · Shift+Enter 换行")}</p>
    </div>
    {scopeOpen && conversation && <Scope scope={conversation.scope} spaces={spaces} close={() => setScopeOpen(false)}
      change={(scope) => act("agent.conversation.update", { conversation_id: conversation.id, ...(scope === null ? { clear_scope: true } : { scope }) })} />}
  </div>;
}
