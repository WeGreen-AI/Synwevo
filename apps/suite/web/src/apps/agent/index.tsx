import { lazy, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, useLocation, useMatch, useNavigate } from "react-router-dom";
import { ApiError, PlatformNav, RequireSession, get, useSession } from "@synwevo/auth-client";
import { Brand, useDialog, useI18n } from "@synwevo/ui";
import { useApp } from "../../shared/catalog";
import { Empty, Loading } from "../../shared/states";
import { AgentFailure as Failure } from "./errors";
import { newKey, op, read } from "../../shared/ops";
import Chat from "./Chat";
import { activeTurn, branchMessages, type ContextReference, type FileReference, type Workspace } from "./types";
import { createFileClient, fileErrorKey } from './files';
import { contextErrorKey } from "./context";
import { useConversation } from "./useConversation";
import { useHistory } from "./useHistory";
import { ConsentPanel, ConsentRecovery, useConversationConsents } from "./ConsentControls";
import { ConsentStateError, PendingConsentOperation, createConsentWriter, effectiveConsentStatus, validateConsent,
  validateCreatedConsent, type Consent, type ConsentWriter } from "./consent";
import { validateOffer, type BudgetIntent } from "./budget";
import { chatCopy } from "./chat.messages";
import { ComposerStatus, TurnMaximum, type ModelOffer } from "./ComposerStatus";
import { accountHref, turnMaximum, walletHref, type BillingStatus } from "./composer";
import { conversationMarkdown, downloadText, exportFileName } from "./exportChat";
import { choosePayer, parsePayers, payerInput, payerKey, payerOf, personal, recallPayer, rememberPayer, samePayer, type Payer } from "./payers";
import "./messages";
import { automationCopy } from "./automations.messages";
import "./agent.css";

const Settings = lazy(() => import("./Settings"));
const Automations = lazy(() => import("./Automations"));
const BudgetDialog = lazy(() => import("./BudgetDialog"));

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
  const [budgetIntent, setBudgetIntent] = useState<BudgetIntent>();
  const [sentDraft, setSentDraft] = useState(0);
  const [fileClient] = useState(createFileClient);
  const currentConversation = useRef(id);
  currentConversation.current = id;
  const currentBudgetIntent = useRef(budgetIntent);
  currentBudgetIntent.current = budgetIntent;
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const intents = useRef(new Map<string, string>());
  const retryCreate = useRef<string | undefined>(undefined);
  function contextRefused(error: unknown, input: Record<string, unknown>) {
    if (!contextErrorKey(error) && !fileErrorKey(error)) return;
    const cid = input.conversation_id;
    if (cid !== currentConversation.current && !(currentConversation.current === undefined && cid === retryCreate.current)) return;
    // A refused source must be removed/reselected in the preserved draft, not silently re-resolved
    // under a previously accepted quote. Bring that draft and its actionable error back into view.
    setActionError(error);
    if (currentBudgetIntent.current?.conversation_id === cid) setBudgetIntent(undefined);
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
  const walletUrl = walletHref(auth?.account_url, auth?.app_id);
  const accountUrl = accountHref(auth?.account_url, auth?.app_id);
  /** A consent pays only for the payer it was issued for (shared contract "Payer"). */
  const paysFor = (terms: object) => samePayer(payerOf(terms, userId), payer);
  const organizationName = (ref: { kind: "organization"; id: string }) => payers.find((p) => payerKey(p) === payerKey(ref))?.name;
  // One payment writer per conversation: an uncertain outcome stays locked across navigation.
  const writers = useRef(new Map<string, ConsentWriter>());
  const writerFor = useCallback((cid: string) => {
    let writer = writers.current.get(cid);
    if (!writer) {
      writer = createConsentWriter(async (operation, input, key) => {
        try { return await op(operation, input, { key }); }
        catch (error) { contextRefused(error, input); throw error; }
      }, newKey);
      writers.current.set(cid, writer);
    }
    return writer;
  }, []);
  const consents = useConversationConsents(id, conversation?.scope, connection, billingRequired && !!id);
  const answering = !!conversation?.messages?.some(activeTurn);
  useEffect(() => {
    if (!billingRequired) { setStatus(undefined); setOffer(undefined); return; }
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
  }, [billingRequired, tick, answering]);
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
  const [single, setSingle] = useState(false);
  useEffect(() => setSingle(false), [id]);

  const write = useCallback(async (operation: string, input: Record<string, unknown>) => {
    const signature = JSON.stringify([operation, input]);
    const key = intents.current.get(signature) ?? newKey();
    intents.current.set(signature, key);
    let result;
    try { result = await op(operation, input, { key }); }
    catch (error) { contextRefused(error, input); throw error; }
    intents.current.delete(signature);
    return result.output;
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

  /** The active consent for a manual send, or null for a single-run quote. Never guessed from a stale list. */
  function consentFor(cid: string): Consent | null {
    if (writers.current.get(cid)?.pending()) throw new PendingConsentOperation();
    if (cid !== id) return null;  // A conversation created by this send has no consent yet.
    // Continuing consents are created for the personal wallet only; an organization's run goes
    // through a single-run quote that names it.
    if (organizationPayer) return null;
    if (!conversation || conversation.id !== cid || consents.error != null || !consents.data) throw new ConsentStateError("consent_state_unavailable");
    if (single) return null;
    return consents.data.find((c) => effectiveConsentStatus(c, conversation.scope) === "active" && paysFor(c)) ?? null;
  }
  /** The writer shows a refusal or an unresolved outcome itself; nothing is resent or switched to a quote. */
  async function consentSend(consent: Consent, input: Record<string, unknown>) {
    try {
      await writerFor(consent.conversation_id).start("agent.turn.start", { ...input, consent_id: consent.id }, checkTurn);
      return true;
    } catch { return false; } finally { consents.refresh(); setTick((n) => n + 1); await refresh(); }
  }
  async function revoke(consent: Consent) {
    try {
      await writerFor(consent.conversation_id).start("agent.consent.revoke", { consent_id: consent.id }, (value: Consent) => {
        validateConsent(value, consent.conversation_id);
        if (value.id !== consent.id || value.status !== "revoked") throw new Error("invalid_consent_response");
      });
    } finally { consents.refresh(); }
  }
  function finishDialogSend(acceptedIntent: BudgetIntent) {
    const sameDialog = currentBudgetIntent.current === acceptedIntent;
    const sameConversation = currentConversation.current === acceptedIntent.conversation_id
      || (currentConversation.current === undefined && retryCreate.current === acceptedIntent.conversation_id);
    // A closed dialog does not undo a submitted request. Late completion must not
    // dismiss another consent dialog, clear its text or steal a newly opened page.
    if (sameDialog && sameConversation && acceptedIntent.text !== undefined) setSentDraft((n) => n + 1);
    if (retryCreate.current === acceptedIntent.conversation_id) retryCreate.current = undefined;
    setBudgetIntent((current) => current === acceptedIntent ? undefined : current);
    if (sameDialog && sameConversation) navigate(`/agent/c/${acceptedIntent.conversation_id}`);
    setSingle(false);
    setTick((n) => n + 1);
  }

  async function act(operation: string, input: Record<string, unknown>) {
    if (operation === "agent.turn.start" && billingRequired) {
      const intent: BudgetIntent = { conversation_id: String(input.conversation_id),
        ...(typeof input.regenerate_of === "string" ? { regenerate_of: input.regenerate_of } : {}),
        ...(typeof input.text === "string" ? { text: input.text } : {}),
        ...(typeof input.parent_message_id === "string" ? { parent_message_id: input.parent_message_id } : {}),
        ...(input.context_refs !== undefined ? { context_refs: input.context_refs as ContextReference[] } : {}),
        ...(input.file_refs !== undefined ? { file_refs: input.file_refs as FileReference[] } : {}) };
      const consent = consentFor(intent.conversation_id);
      if (consent) await consentSend(consent, { ...intent });
      else setBudgetIntent(intent);
      return;
    }
    const result = await write(operation, input);
    await refresh(); setTick((n) => n + 1);
    return result;
  }
  async function send(text: string, context_refs?: ContextReference[], file_refs?: FileReference[]) {
    setActionError(undefined);
    let cid = id ?? retryCreate.current;
    if (!cid) { cid = (await write("agent.conversation.create", {})).id; retryCreate.current = cid; }
    const intent: BudgetIntent = { conversation_id: cid!, text, ...(context_refs?.length ? { context_refs } : {}), ...(file_refs?.length ? { file_refs } : {}) };
    if (billingRequired) {
      const consent = consentFor(cid!);
      if (consent) return consentSend(consent, { ...intent });
      setBudgetIntent(intent); return false;
    }
    checkTurn(await write("agent.turn.start", { ...intent }));
    retryCreate.current = undefined;
    navigate(`/agent/c/${cid}`); setTick((n) => n + 1); await refresh();
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
  const writer = billingRequired && id ? writerFor(id) : undefined;
  const blocking = consents.data?.some((c) => !["revoked", "expired"].includes(effectiveConsentStatus(c, conversation?.scope ?? null)));
  const dialogScope = budgetIntent?.conversation_id === id ? conversation?.scope ?? null : null;
  const title = conversation?.title === "新会话" ? t("新会话") : conversation?.title;
  const activeConsent = !organizationPayer && conversation ? consents.data?.find((c) => effectiveConsentStatus(c, conversation.scope) === "active" && paysFor(c)) ?? null : null;
  // The newest consent for this payer whose model or price no longer applies: what the person last confirmed.
  const staleConsent = consents.data?.filter((c) => c.status === "model_changed" && paysFor(c)).sort((a, b) => b.created_at - a.created_at)[0] ?? null;
  const money = billingRequired ? { status, offer, payers, payersError, payer, setPayer: choose, walletUrl, accountUrl,
    environment: payerFacts.environment, welcome: payerFacts.welcome } : null;
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
        {automations ? <Automations /> : settings ? <Settings /> : <>
          <div className="ag-toolbar"><h1>{title ?? t("新会话")}</h1>
            {conversation && <div className="ag-toolbar-actions">
              <button className="btn ghost sm" disabled={busy} onClick={() => void manage("rename")}>{t("重命名会话")}</button>
              <button className="btn ghost sm" disabled={!branchMessages(conversation.messages, conversation.head_id).length}
                title={t(chatCopy.exportChatHint[0])} onClick={exportConversation}>{t(chatCopy.exportChat[0])}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("archive")}>{t(conversation.status === "archived" ? "恢复" : "归档")}</button>
              <button className="btn ghost sm" disabled={busy || running} onClick={() => void manage("delete")}>{t("删除会话")}</button>
            </div>}
          </div>
          {actionError != null && <Failure error={actionError} />}
          <Chat key={id ?? "new"} conversation={conversation} loading={!!id && !conversation && !error} error={error} connection={connection}
            spaces={spaces} fileClient={fileClient} configured={configured} act={act} send={send} refresh={refresh} sentDraft={sentDraft}
            walletUrl={walletUrl} organizationPayer={organizationPayer} payerName={organizationName}
            onPersonal={organizationPayer && payers[0] ? () => choose(payers[0]) : undefined}
            status={money && <ComposerStatus money={money} disabled={running} />}
            maximum={money && <TurnMaximum maximum={turnMaximum(single ? null : activeConsent, offer)} />}
            hint={money ? t(chatCopy.turnMaximumHint[0]) : undefined}
            aside={writer && id && <>
              <details className="ag-payment-settings">
                <summary>{t("付款授权设置")}</summary>
              <ConsentPanel consents={consents.data} loading={consents.loading} error={consents.error} refresh={consents.refresh}
                scope={conversation?.scope ?? null} spaces={spaces} payer={payerName} writer={writer} single={single} setSingle={setSingle} revoke={revoke}
                offer={offer ?? null} />
              </details>
              <ConsentRecovery writer={writer} retry={async () => {
                try { await writer.retry(); } finally { consents.refresh(); setTick((n) => n + 1); await refresh(); }
              }} />
            </>} />
        </>}
      </main>
      {budgetIntent && <BudgetDialog intent={budgetIntent} onClose={() => setBudgetIntent(undefined)}
        onQuote={async (params) => {
          const quote = await write("agent.turn.quote", { ...budgetIntent, ...params, ...payerInput(payer, userId) });
          // The quote must name the payer shown in the dialog; a server that ignores the choice
          // (or a different payer) is never confirmed.
          if (!samePayer(payerOf(quote, userId), payer)) throw new Error("quote_payer_mismatch");
          return quote;
        }}
        payerLabel={organizationPayer ? t(chatCopy.payerOrganization[0], { name: payer.name }) : undefined}
        siteUrl={auth?.site_url}
        previous={budgetIntent.conversation_id === id && staleConsent && !organizationPayer ? { terms: staleConsent, budget: staleConsent.budget,
          max_calls: staleConsent.max_calls, run_seconds: staleConsent.run_seconds } : undefined}
        onConfirm={async (quote) => {
          const acceptedIntent = budgetIntent;
          if (writers.current.get(acceptedIntent.conversation_id)?.pending()) throw new PendingConsentOperation();
          checkTurn(await write("agent.turn.start", { ...acceptedIntent, quote_id: quote.id }));
          finishDialogSend(acceptedIntent);
          await refresh();
        }}
        consent={organizationPayer || (budgetIntent.conversation_id === id && (!consents.data || blocking)) ? undefined : {
          scope: dialogScope, spaces, payer: payerName, writer: writerFor(budgetIntent.conversation_id),
          create: async (quote, input, params, retry) => {
            const scope = dialogScope ?? [];
            const target = writerFor(input.conversation_id);
            try {
              return retry ? await target.retry<Consent>() : await target.start<Consent>("agent.consent.create", { ...input },
                (value) => validateCreatedConsent(value, input, quote, scope, params));
            } finally { consents.refresh(); }
          },
          send: async (consent, retry) => {
            const acceptedIntent = budgetIntent;
            const target = writerFor(consent.conversation_id);
            if (retry) await target.retry();
            else await target.start("agent.turn.start", { ...acceptedIntent, consent_id: consent.id }, checkTurn);
            finishDialogSend(acceptedIntent);
            consents.refresh();
            await refresh();
          },
        }} />}
    </div>
  </div>;
}

function checkTurn(value: { turn_id?: unknown }) {
  if (!value || typeof value.turn_id !== "string" || !value.turn_id) throw new Error("invalid_turn_response");
}

export function agentRoutes() {
  return [<Route key="agent" path="/agent/*" element={<RequireSession><AgentApp /></RequireSession>} />];
}
