/** The personal Agent page. Send-and-go (owner decision 2026-10-23, design D-1…D-7): a person
 *  types a request, presses Send, and the answer starts at once — one `agent.turn.start` per
 *  send with the payer named inline, never a quote, a consent or a dialog in between. The server
 *  issues and binds the run's authorization inside its own admission transaction and enforces
 *  every money bound there; this page only shows the facts it reports (the maximum an answer can
 *  cost, the balance, a price notice) and words its refusals. The one-time "Start using Synwevo
 *  Agent" step (AgentSetup) appears inline when a send needs something the person has not done
 *  yet; the draft stays in the composer until it is sent. */
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, useLocation, useMatch, useNavigate } from "react-router-dom";
import { ApiError, PlatformNav, RequireSession, get, useSession } from "@synwevo/auth-client";
import { Brand, useDialog, useI18n } from "@synwevo/ui";
import { useApp } from "../../shared/catalog";
import { Empty, Loading } from "../../shared/states";
import { AgentFailure as Failure } from "./errors";
import { newKey, op, read } from "../../shared/ops";
import Chat, { type Draft } from "./Chat";
import { activeTurn, branchMessages, type Workspace } from "./types";
import { createFileClient, fileErrorKey } from './files';
import { contextErrorKey } from "./context";
import { useConversation } from "./useConversation";
import { useHistory } from "./useHistory";
import { validateOffer, type Pricing } from "./budget";
import { chatCopy } from "./chat.messages";
import { RefusalNotice, TurnMaximum } from "./ChatParts";
import { ComposerStatus, type ModelOffer } from "./ComposerStatus";
import { accountHref, composerMaximum, seenPrice, walletHref, type BillingStatus, type PriceNoticeFacts } from "./composer";
import { conversationMarkdown, downloadText, exportFileName } from "./exportChat";
import { choosePayer, parsePayers, payerInput, payerKey, personal, recallPayer, rememberPayer, type Payer } from "./payers";
import { errorCode, refusalOf } from "./refusals";
import AgentSetup, { completedSetup, setupNeeded, validateSetup, type SetupState } from "./AgentSetup";
import PriceNotice from "./PriceNotice";
import "./messages";
import { automationCopy } from "./automations.messages";
import "./agent.css";

const Settings = lazy(() => import("./Settings"));
const Automations = lazy(() => import("./Automations"));

function AgentApp() {
  const { t } = useI18n();
  const dialog = useDialog();
  const { app, error: catalogError, reload } = useApp("agent");
  const id = useMatch("/agent/c/:cid")?.params.cid;
  const automations = !!useMatch("/agent/automations");
  const settings = useLocation().pathname === "/agent/settings";
  const navigate = useNavigate();
  const { value: conversation, error, refresh, connection } = useConversation(id);
  const [q, setQ] = useState("");
  const [archived, setArchived] = useState(false);
  const [tick, setTick] = useState(0);
  const history = useHistory(q, archived, tick);
  const list = history.rows;
  const [sidebar, setSidebar] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>();
  const [configured, setConfigured] = useState<boolean>();
  const [billingRequired, setBillingRequired] = useState(false);
  const [sentDraft, setSentDraft] = useState(0);
  const [fileClient] = useState(createFileClient);
  const currentConversation = useRef(id);
  currentConversation.current = id;
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const retryCreate = useRef<string | undefined>(undefined);
  function contextRefused(error: unknown, input: Record<string, unknown>) {
    if (!contextErrorKey(error) && !fileErrorKey(error)) return;
    const cid = input.conversation_id;
    if (cid !== currentConversation.current && !(currentConversation.current === undefined && cid === retryCreate.current)) return;
    // A refused source must be removed or reselected in the preserved draft, never silently
    // re-resolved: bring that draft and its actionable error back into view.
    setActionError(error);
  }
  const { me, state: auth } = useSession();
  const payerName = me?.display_name || me?.email || "";
  const userId = me?.id ?? "";
  useEffect(() => { fileClient.reset(); return () => fileClient.reset(); }, [fileClient, userId]);
  // Money facts beside the composer; read-only, refreshed when an answer finishes.
  const [status, setStatus] = useState<BillingStatus | null>();
  const [offer, setOffer] = useState<ModelOffer | null>();
  const [payerList, setPayerList] = useState<unknown>();
  const [payersError, setPayersError] = useState(false);
  const [payerRef, setPayerRef] = useState(() => (userId ? recallPayer(userId) : null));
  const payerFacts = parsePayers(payerList, { id: userId, name: payerName });
  const payers = payerFacts.payers;
  const payer: Payer = userId ? choosePayer(payers, payerRef) : personal(userId, payerName);
  const organizationPayer = payer.kind !== "user";
  // What the composer shows now, read when a turn is started: a send from a function an earlier
  // render handed out (a click handler, the setup step's button) still carries the price and payer
  // on screen, never ones the page has since replaced (addendum AD-1).
  const shown = useRef({ status, offer, payer });
  shown.current = { status, offer, payer };
  const walletUrl = walletHref(auth?.account_url, auth?.app_id);
  const accountUrl = accountHref(auth?.account_url, auth?.app_id);
  const organizationName = (ref: { kind: "organization"; id: string }) => payers.find((p) => payerKey(p) === payerKey(ref))?.name;
  // The one-time setup state (agent.setup.get): undefined while loading, null when unreadable.
  // An unreadable state never blocks a send; the server's refusal then names the missing step.
  const [setup, setSetup] = useState<SetupState | null>();
  const [setupTick, setSetupTick] = useState(0);
  const [setupOpen, setSetupOpen] = useState(false);
  const [focusTick, setFocusTick] = useState(0);
  // The message waiting in the composer while the setup step is open (sent by its button).
  const [draft, setDraft] = useState<Draft | null>(null);
  const answering = !!conversation?.messages?.some(activeTurn);
  // Addendum AD-1: the price the composer shows is the price a send runs at. The status is read
  // again on demand (a refused stale price), when the window regains focus or becomes visible,
  // and at most every 5 minutes while the page is visible, so a long-open page rarely shows an
  // out-of-date price; a send that still carries one is refused by the server, never run.
  const [statusTick, setStatusTick] = useState(0);
  const statusRead = useRef(0);
  // The pricing the page was showing when a send was refused for a newer price (a notice, not a gate).
  const [shownBefore, setShownBefore] = useState<Pricing | null>(null);
  useEffect(() => {
    if (!billingRequired) { setStatus(undefined); setOffer(undefined); return; }
    statusRead.current = Date.now();
    const abort = new AbortController();
    void Promise.allSettled([
      read<BillingStatus>("agent.billing.status", {}, null, abort.signal),
      read<ModelOffer>("agent.billing.offer", {}, null, abort.signal),
    ]).then(([money, terms]) => {
      if (abort.signal.aborted) return;
      setStatus(money.status === "fulfilled" && money.value && typeof money.value === "object" ? money.value : null);
      let valid: ModelOffer | null = null;
      if (terms.status === "fulfilled") { try { validateOffer(terms.value); valid = terms.value; } catch { valid = null; } }
      setOffer(valid);
    });
    return () => abort.abort();
  }, [billingRequired, tick, answering, statusTick]);
  // Back from Agent settings, where the maximum (W2) and the setup facts (consent, limits: W4) may
  // have changed: both are read again.
  const wasSettings = useRef(settings);
  useEffect(() => {
    if (wasSettings.current && !settings) { setStatusTick((n) => n + 1); setSetupTick((n) => n + 1); }
    wasSettings.current = settings;
  }, [settings]);
  useEffect(() => {
    if (!billingRequired || typeof window === "undefined") return;
    const visible = () => typeof document === "undefined" || document.visibilityState === "visible";
    // focus and visibilitychange usually arrive together: one read.
    const again = (after: number) => { if (visible() && Date.now() - statusRead.current >= after) setStatusTick((n) => n + 1); };
    const onFocus = () => again(2000);
    const timer = window.setInterval(() => again(5 * 60_000), 60_000);
    window.addEventListener("focus", onFocus);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onFocus);
    };
  }, [billingRequired]);
  useEffect(() => {
    if (!billingRequired || !userId) { setSetup(undefined); return; }
    const abort = new AbortController();
    read<unknown>("agent.setup.get", {}, null, abort.signal).then((value) => {
      if (abort.signal.aborted) return;
      try { setSetup(validateSetup(value)); } catch { setSetup(null); }
    }).catch(() => { if (!abort.signal.aborted) setSetup(null); });
    return () => abort.abort();
  }, [billingRequired, userId, setupTick]);
  useEffect(() => {
    if (!billingRequired || !userId) return;
    const abort = new AbortController();
    // Organization payment (shared contract "Payer") lists who may pay; before it exists the
    // endpoint is absent and only the personal wallet is offered.
    get<unknown>("/api/suite/agent/payers", abort.signal)
      .then((value) => { if (!abort.signal.aborted) { setPayerList(value); setPayersError(false); } })
      .catch((e) => { if (!abort.signal.aborted) { setPayerList(undefined); setPayersError(!(e instanceof ApiError && e.status === 404)); } });
    return () => abort.abort();
  }, [billingRequired, userId]);
  function choose(next: Payer) {
    setPayerRef({ kind: next.kind, id: next.id });
    if (userId) rememberPayer(userId, next);
  }

  /** One explicit action of the person (Send, Regenerate, "send again", the setup step's button)
   *  is one attempt with one fresh idempotency key (addendum-2 W1). The key is sent again only by
   *  the automatic retry of that same attempt when it got no answer from the server (the
   *  connection failed, or a gateway in front of Synwevo could not reach it), so a lost response
   *  is answered from the server's record of the attempt instead of running it twice. Any answer
   *  from the server ends the attempt: the next click is a new one, so a cause fixed in between
   *  (terms accepted, the maximum raised, the price read again, a top-up) is never met with the
   *  replay of the earlier refusal. */
  const write = useCallback(async (operation: string, input: Record<string, unknown>) => {
    const key = newKey();
    for (let attempt = 0; ; attempt += 1) {
      try { return (await op(operation, input, { key })).output; }
      catch (error) {
        if (attempt < NO_ANSWER_RETRIES.length && unanswered(error)) { await pause(NO_ANSWER_RETRIES[attempt]); continue; }
        contextRefused(error, input);
        throw error;
      }
    }
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    Promise.all([
      read<{ active: unknown; billing?: { required: boolean } }>("agent.settings.get", {}, null, abort.signal),
      read<{ workspaces: Workspace[] }>("agent.workspace_policy.list", {}, null, abort.signal),
    ]).then(([model, policies]) => { setConfigured(!!model.active); setBillingRequired(!!model.billing?.required); setSpaces(policies.workspaces); })
      .catch((e) => { if (!abort.signal.aborted) setActionError(e); });
    return () => abort.abort();
  }, [settings]);

  /** One request: the turn starts at once. The payer is named inline when billing applies; the
   *  server issues the run's authorization itself (design D-1) and refuses with a code the chat
   *  words when something is missing. */
  async function start(next: Draft) {
    if (billingRequired) shownPrice(shown.current.status);   // held before a conversation is made for it
    let cid = id ?? retryCreate.current;
    if (!cid) { cid = (await write("agent.conversation.create", {})).id; retryCreate.current = cid; }
    checkTurn(await startTurn({ conversation_id: cid, ...next }));
    retryCreate.current = undefined;
    navigate(`/agent/c/${cid}`); setTick((n) => n + 1); await refresh();
  }
  /** The price the composer shows, as a paid send repeats it (AD-1). Without one (the status is
   *  loading, unreadable or has no run) nothing is sent (addendum-2 W3): the status is read again
   *  and the held send is shown like a refusal, with the message kept and "send again". */
  function shownPrice(status: BillingStatus | null | undefined) {
    const seen = seenPrice(status);
    if ("seen_price_id" in seen) return seen;
    setStatusTick((n) => n + 1);
    throw Object.assign(new Error("price_not_shown"), { code: "price_not_shown" });
  }
  /** `agent.turn.start` with what billing needs beside the message: the payer, and the price the
   *  composer showed (AD-1, W3). A refusal for a newer price keeps the message, reads the status
   *  again and keeps what the page showed for the price notice. */
  async function startTurn(input: Record<string, unknown>) {
    const showing = shown.current;
    const body = billingRequired ? { ...input, ...payerInput(showing.payer, userId), ...shownPrice(showing.status) } : input;
    try {
      const out = await write("agent.turn.start", body);
      setShownBefore(null);
      return out;
    } catch (error) {
      if (errorCode(error) === "billing_price_changed") {
        const refused = showing.offer;
        if (refused) setShownBefore((before) => before ?? refused);
        setStatusTick((n) => n + 1);
      }
      throw error;
    }
  }
  /** Open the one-time setup step with the draft kept; focus moves to its heading. The state is
   *  read again and the step shows it loading until it arrives (addendum-2 W4), so a step opened
   *  from a refusal never asks from the state read when the page loaded. */
  function openSetup(next?: Draft) {
    if (next !== undefined) setDraft(next);
    setSetup(undefined); setSetupTick((n) => n + 1);
    setSetupOpen(true); setFocusTick((n) => n + 1);
  }
  const readSetupFact = useCallback((operation: string) => read<unknown>(operation, {}, null), []);
  const rereadStatus = useCallback(() => setStatusTick((n) => n + 1), []);
  const updateDraft = useCallback((next: Draft | null) => setDraft(next), []);
  /** Send the kept message in one request; on success the composer is cleared. A refusal leaves
   *  it in the composer and is shown above it with its next step. */
  async function sendDraft(next: Draft) {
    setActionError(undefined);
    try { await start(next); setDraft(null); setSentDraft((n) => n + 1); }
    catch (e) { setActionError(e); }
  }
  /** Everything the step needed is recorded: send the kept message, then read the state again. */
  async function completeSetup() {
    const next = draft;
    setSetup((current) => (current ? completedSetup(current, organizationPayer) : current));
    setSetupTick((n) => n + 1);
    setSetupOpen(false);
    if (next?.text.trim()) await sendDraft(next);
    else focusComposer();
  }
  /** Back to the message box (the step was closed without sending). */
  function focusComposer() {
    if (typeof document !== "undefined") document.getElementById("agent-message")?.focus();
  }
  async function act(operation: string, input: Record<string, unknown>) {
    const result = operation === "agent.turn.start" ? await startTurn(input) : await write(operation, input);
    await refresh(); setTick((n) => n + 1);
    return result;
  }
  async function send(text: string, context_refs?: Draft["context_refs"], file_refs?: Draft["file_refs"]) {
    setActionError(undefined);
    const next: Draft = { text, ...(context_refs?.length ? { context_refs } : {}), ...(file_refs?.length ? { file_refs } : {}) };
    if (billingRequired && setupNeeded(setup, organizationPayer)) { openSetup(next); return false; }
    await start(next);
    return true;
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
  if (catalogError) return <Failure error={catalogError} onRetry={reload} />;
  if (app === undefined) return <Loading />;
  if (!app) return <Empty title={t("个人 Agent 尚未开放")} />;
  const running = conversation?.messages?.some(activeTurn);
  const title = conversation?.title === "新会话" ? t("新会话") : conversation?.title;
  const money = billingRequired ? { status, offer, payers, payersError, payer, setPayer: choose, walletUrl, accountUrl,
    environment: payerFacts.environment, welcome: payerFacts.welcome } : null;
  // The most the next answer can cost: the run bound the server reports (status.run.maximum), or
  // that it is being read or cannot be shown (W3).
  const maximum = billingRequired ? composerMaximum(status) : null;
  // What changed since the person's last answer (server), or since the price this page showed
  // when a send was refused for a newer one (AD-1). Information only.
  const priceNotice: PriceNoticeFacts | null = status?.price_notice
    ?? (shownBefore && offer && (shownBefore.price_id !== offer.price_id || shownBefore.route_version !== offer.route_version)
      ? { previous: shownBefore, previous_run_at: Math.floor(Date.now() / 1000) } : null);
  // A refusal of the send the setup step made (the draft is still in the composer). While it waits,
  // the page follows the composer as it does while the step is open, so "send again" sends the
  // message as it now stands (onDraft below).
  const actionRefusal = actionError != null ? refusalOf(actionError, { organization: organizationPayer }) : null;
  function exportConversation() {
    if (!conversation) return;
    const name = title ?? t("新会话");
    const text = conversationMarkdown(name, branchMessages(conversation.messages, conversation.head_id),
      { you: t(chatCopy.exportYou[0]), agent: "Synwevo Agent", hidden: t("内容因权限变化已隐藏"), label: t(chatCopy.aiLabel[0]) });
    downloadText(text, exportFileName(name, new Date()));
  }
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
          {!list ? (history.error == null ? <Loading /> : null) : !list.length ? <p className="muted">{t("尚无会话")}</p> : list.map((c) => <NavLink key={c.id} to={`/agent/c/${c.id}`} className={({ isActive }) => `ag-history-link${isActive ? " selected" : ""}`} onClick={() => setSidebar(false)}>{c.title === "新会话" ? t("新会话") : c.title}</NavLink>)}
          {history.error != null && <Failure error={history.error} onRetry={() => setTick((n) => n + 1)} />}
          {history.cursor != null && <button className="btn ghost sm" disabled={history.loadingMore || busy} onClick={() => void history.more()}>{t("加载更多会话")}</button>}
        </nav>
        <NavLink className="btn ghost" to="/agent/automations" onClick={() => setSidebar(false)}>{t(automationCopy.title[0])}</NavLink>
        <NavLink className="btn ghost" to="/agent/settings" onClick={() => setSidebar(false)}>{t("Agent 设置")}</NavLink>
        <Link className="btn ghost" to="/workspace">{t("返回工作空间")}</Link>
      </aside>
      <main className="ag-main">
        {automations ? <Suspense fallback={<Loading />}><Automations /></Suspense> : settings ? <Suspense fallback={<Loading />}><Settings onBillingChanged={rereadStatus} /></Suspense> : <>
          <div className="ag-toolbar"><h1>{title ?? t("新会话")}</h1>
            {conversation && <div className="ag-toolbar-actions">
              <button className="btn ghost sm" disabled={busy} onClick={() => void manage("rename")}>{t("重命名会话")}</button>
              <button className="btn ghost sm" disabled={!branchMessages(conversation.messages, conversation.head_id).length}
                title={t(chatCopy.exportChatHint[0])} onClick={exportConversation}>{t(chatCopy.exportChat[0])}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("archive")}>{t(conversation.status === "archived" ? "恢复" : "归档")}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("delete")}>{t("删除会话")}</button>
            </div>}
          </div>
          {actionError != null && (actionRefusal
            ? <RefusalNotice refusal={actionRefusal} walletUrl={walletUrl} live busy={busy || !!running}
              onPersonal={organizationPayer && payers[0] ? () => choose(payers[0]) : undefined}
              onRetry={draft?.text.trim() ? () => void sendDraft(draft) : undefined} onSetup={() => openSetup()} />
            : <Failure error={actionError} />)}
          <Chat key={id ?? "new"} conversation={conversation} loading={!!id && !conversation && !error} error={error} connection={connection}
            spaces={spaces} fileClient={fileClient} configured={configured} act={act} send={send} refresh={refresh} sentDraft={sentDraft}
            walletUrl={walletUrl} organizationPayer={organizationPayer} payerName={organizationName}
            onPersonal={organizationPayer && payers[0] ? () => choose(payers[0]) : undefined}
            onSetup={billingRequired ? openSetup : undefined}
            onDraft={setupOpen || (actionRefusal != null && draft != null) ? updateDraft : undefined}
            status={money && <ComposerStatus money={money} disabled={running} />}
            maximum={money && <TurnMaximum maximum={maximum} />}
            hint={money ? t(chatCopy.turnMaximumHint[0]) : undefined}
            aside={money && <>
              {priceNotice != null && <PriceNotice notice={priceNotice} offer={offer} userId={userId} />}
              {setupOpen && <AgentSetup setup={setup} organizationPayer={organizationPayer} hasDraft={!!draft?.text}
                maximum={maximum && "amount" in maximum && maximum.key === "turnMaximum" ? { amount: maximum.amount, unit: maximum.unit } : null}
                siteUrl={auth?.site_url} read={readSetupFact} write={write}
                onComplete={completeSetup} onChanged={() => setSetupTick((n) => n + 1)} focus focusTick={focusTick}
                onDismiss={() => { setSetupOpen(false); focusComposer(); }} onRetryRead={() => setSetupTick((n) => n + 1)} />}
            </>} />
        </>}
      </main>
    </div>
  </div>;
}

/** The waits before the automatic retries of an attempt that got no answer (W1): two, then the
 *  attempt ends with the connection error and the message stays in the composer. */
const NO_ANSWER_RETRIES = [1000, 3000];
/** No answer from Synwevo: the request may or may not have arrived, so only the same key may ask again. */
function unanswered(error: unknown): boolean {
  return error instanceof ApiError && ((error.status === 0 && error.code === "network")
    || (error.upstream && [502, 503, 504].includes(error.status)));
}
const pause = (ms: number) => new Promise<void>((resolve) => { setTimeout(resolve, ms); });

function checkTurn(value: { turn_id?: unknown }) {
  if (!value || typeof value.turn_id !== "string" || !value.turn_id) throw new Error("invalid_turn_response");
}

export function agentRoutes() {
  return [<Route key="agent" path="/agent/*" element={<RequireSession><AgentApp /></RequireSession>} />];
}
