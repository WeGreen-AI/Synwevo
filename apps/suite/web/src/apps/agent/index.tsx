import { lazy, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, useLocation, useMatch, useNavigate } from "react-router-dom";
import { PlatformNav, RequireSession } from "@synwevo/auth-client";
import { Brand, useDialog, useI18n } from "@synwevo/ui";
import { useApp } from "../../shared/catalog";
import { Empty, Loading } from "../../shared/states";
import { AgentFailure as Failure } from "./errors";
import { newKey, op, read } from "../../shared/ops";
import Chat from "./Chat";
import { activeTurn, type Conversation, type Workspace } from "./types";
import { useConversation } from "./useConversation";
import "./messages";
import "./agent.css";

const Settings = lazy(() => import("./Settings"));

function AgentApp() {
  const { t } = useI18n();
  const dialog = useDialog();
  const { app, error: catalogError, reload } = useApp("agent");
  const id = useMatch("/agent/c/:cid")?.params.cid;
  const settings = useLocation().pathname === "/agent/settings";
  const navigate = useNavigate();
  const { value: conversation, error, refresh, connection } = useConversation(id);
  const [list, setList] = useState<Conversation[]>();
  const [listError, setListError] = useState<unknown>();
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [before, setBefore] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const [sidebar, setSidebar] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>();
  const [configured, setConfigured] = useState<boolean>();
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const intents = useRef(new Map<string, string>());
  const retryCreate = useRef<string | undefined>(undefined);
  const generation = useRef(0);

  const write = useCallback(async (operation: string, input: Record<string, unknown>) => {
    const signature = JSON.stringify([operation, input]);
    const key = intents.current.get(signature) ?? newKey();
    intents.current.set(signature, key);
    const result = await op(operation, input, { key });
    intents.current.delete(signature);
    return result.output;
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    const run = ++generation.current;
    setList(undefined); setListError(undefined);
    const timer = setTimeout(() => {
      read<{ conversations: Conversation[]; next_before: number | null }>("agent.conversation.list", { status: archived ? "archived" : "active", q }, null, abort.signal)
        .then((out) => { if (generation.current === run) { setList(out.conversations); setBefore(out.next_before); } })
        .catch((e) => { if (!abort.signal.aborted) setListError(e); });
    }, q ? 200 : 0);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [q, archived, tick]);

  useEffect(() => {
    const abort = new AbortController();
    Promise.all([
      read<{ active: unknown }>("agent.settings.get", {}, null, abort.signal),
      read<{ workspaces: Workspace[] }>("agent.workspace_policy.list", {}, null, abort.signal),
    ]).then(([model, policies]) => { setConfigured(!!model.active); setSpaces(policies.workspaces); })
      .catch((e) => { if (!abort.signal.aborted) setActionError(e); });
    return () => abort.abort();
  }, [settings]);

  async function act(operation: string, input: Record<string, unknown>) {
    const result = await write(operation, input);
    await refresh(); setTick((n) => n + 1);
    return result;
  }
  async function send(text: string) {
    let cid = id ?? retryCreate.current;
    if (!cid) { cid = (await write("agent.conversation.create", {})).id; retryCreate.current = cid; }
    await write("agent.turn.start", { conversation_id: cid, text });
    retryCreate.current = undefined;
    navigate(`/agent/c/${cid}`); setTick((n) => n + 1); await refresh();
  }
  async function newConversation() {
    setBusy(true); setActionError(undefined);
    try {
      const out = await write("agent.conversation.create", {});
      retryCreate.current = undefined; setArchived(false); setQ("");
      navigate(`/agent/c/${out.id}`); setSidebar(false); setTick((n) => n + 1);
    } catch (e) { setActionError(e); } finally { setBusy(false); }
  }
  async function manage(kind: "rename" | "archive" | "delete") {
    if (!conversation) return;
    let input: Record<string, unknown> = { conversation_id: conversation.id };
    let operation = "agent.conversation.update";
    if (kind === "rename") {
      const title = await dialog.prompt(t("重命名会话"), t("会话名称"), conversation.title, t("保存"));
      if (!title?.trim()) return;
      input.title = title;
    } else if (kind === "archive") input.status = conversation.status === "archived" ? "active" : "archived";
    else {
      if (!await dialog.confirm(t("删除会话"), t("永久删除此会话及其消息？已创建的业务记录仍保留在各自的工作空间。"), t("删除"), true)) return;
      operation = "agent.conversation.delete";
    }
    setBusy(true); setActionError(undefined);
    try {
      await act(operation, input);
      if (kind === "delete") navigate("/agent");
    } catch (e) { setActionError(e); } finally { setBusy(false); }
  }
  async function more() {
    const run = generation.current;
    setBusy(true);
    try {
      const out = await read<{ conversations: Conversation[]; next_before: number | null }>("agent.conversation.list", { status: archived ? "archived" : "active", q, before }, null);
      if (run === generation.current) { setList((old) => [...old ?? [], ...out.conversations]); setBefore(out.next_before); }
    } catch (e) { setListError(e); } finally { setBusy(false); }
  }
  if (catalogError) return <Failure error={catalogError} onRetry={reload} />;
  if (app === undefined) return <Loading />;
  if (!app) return <Empty title={t("个人 Agent 尚未开放")} />;
  const running = conversation?.messages?.some(activeTurn);
  const title = conversation?.title === "新会话" ? t("新会话") : conversation?.title;
  return <div className="ag-app">
    <header className="topbar">
      <button className="btn ghost ag-mobile-menu" aria-label={t(sidebar ? "关闭会话列表" : "打开会话列表")} aria-expanded={sidebar} aria-controls="agent-sidebar" onClick={() => setSidebar(!sidebar)}>☰</button>
      <Brand href="/agent" product="Agent" label="Synwevo Agent" markBelow={720} />
      <span className="badge">{t("个人 Agent")}</span><span className="spacer" /><PlatformNav />
    </header>
    <div className="ag-workspace">
      <aside id="agent-sidebar" className={`ag-sidebar${sidebar ? " open" : ""}`} aria-label={t("会话历史")}>
        <button className="btn primary" disabled={busy} onClick={() => void newConversation()}>＋ {t("新会话")}</button>
        <label><span className="sr-only">{t("搜索会话")}</span><input type="search" className="input" placeholder={t("搜索会话")} value={q} onChange={(e) => setQ(e.target.value)} /></label>
        <select className="input sm" aria-label={t("会话历史")} value={archived ? "archived" : "active"} onChange={(e) => setArchived(e.target.value === "archived")}>
          <option value="active">{t("当前会话")}</option><option value="archived">{t("已归档会话")}</option>
        </select>
        <nav className="ag-history" aria-label={t("会话历史")}>
          {listError != null ? <Failure error={listError} onRetry={() => setTick((n) => n + 1)} /> : !list ? <Loading /> : !list.length ? <p className="muted">{t("尚无会话")}</p> : list.map((c) => <NavLink key={c.id} to={`/agent/c/${c.id}`} className={({ isActive }) => `ag-history-link${isActive ? " selected" : ""}`} onClick={() => setSidebar(false)}>{c.title === "新会话" ? t("新会话") : c.title}</NavLink>)}
          {before != null && <button className="btn ghost sm" disabled={busy} onClick={() => void more()}>{t("加载更多会话")}</button>}
        </nav>
        <NavLink className="btn ghost" to="/agent/settings" onClick={() => setSidebar(false)}>{t("Agent 设置")}</NavLink>
        <Link className="btn ghost" to="/workspace">{t("返回工作空间")}</Link>
      </aside>
      <main className="ag-main">
        {settings ? <Settings /> : <>
          <div className="ag-toolbar"><h1>{title ?? t("新会话")}</h1>
            {conversation && <div className="ag-toolbar-actions">
              <button className="btn ghost sm" disabled={busy} onClick={() => void manage("rename")}>{t("重命名会话")}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("archive")}>{t(conversation.status === "archived" ? "恢复" : "归档")}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("delete")}>{t("删除会话")}</button>
            </div>}
          </div>
          {actionError != null && <Failure error={actionError} />}
          <Chat key={id ?? "new"} conversation={conversation} loading={!!id && !conversation && !error} error={error} connection={connection}
            spaces={spaces} configured={configured} act={act} send={send} refresh={refresh} />
        </>}
      </main>
    </div>
  </div>;
}

export function agentRoutes() {
  return [<Route key="agent" path="/agent/*" element={<RequireSession><AgentApp /></RequireSession>} />];
}
