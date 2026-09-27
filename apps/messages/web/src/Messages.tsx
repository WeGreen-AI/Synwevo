/** Messages: direct messages and groups inside an organization.
 *
 *  What the server says is what is shown: a message is "sent" only when the server has stored it
 *  (until then it is pending, and a retry carries the same client id so a lost acknowledgement
 *  never becomes a duplicate); history is whatever the server lets this member read; the stream
 *  is a hint that something changed and every (re)connect resyncs from the history API with the
 *  last sequence this tab knows. When the stream cannot be held open the page polls and says so.
 *
 *  Layout: the conversation list and the thread side by side; the info panel is a third column
 *  from 1200px, a drawer on medium screens, and a full page on phones, where the list, the
 *  thread and the info are one pane at a time with a real back control. The page height follows
 *  the visual viewport on touch devices so the composer is never under the software keyboard. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, announceMessagesUnread, get, messagesHref, useSession } from "@synwevo/auth-client";
import { Menu, useI18n, useMediaQuery } from "@synwevo/ui";
import { Topbar } from "./Topbar";
import { FlowError, Loading } from "./flow";
import { LIST, M, capabilities, setPrefix, threadPath } from "./api";
import type { Capabilities, Conversation, Live, Message, Org } from "./types";
import { ConversationList, SearchResults, type ListFilter } from "./components/ConversationList";
import { Thread, type Jump } from "./components/Thread";
import { InfoPanel } from "./components/InfoPanel";
import { NewConversation } from "./components/dialogs";
import { IcPlus } from "./components/icons";
import "./messages.css";
import "./messages.messages";

/** On touch devices the software keyboard shrinks the visual viewport but not `100dvh`; the page
 *  follows `visualViewport.height` so the composer stays above the keyboard. */
function useVisualViewportHeight(enabled: boolean) {
  useEffect(() => {
    const root = document.documentElement;
    const vv = window.visualViewport;
    if (!enabled || !vv) { root.style.removeProperty("--msg-vh"); return; }
    const apply = () => { root.style.setProperty("--msg-vh", `${Math.round(vv.height)}px`); };
    apply();
    vv.addEventListener("resize", apply); vv.addEventListener("scroll", apply);
    return () => { vv.removeEventListener("resize", apply); vv.removeEventListener("scroll", apply); root.style.removeProperty("--msg-vh"); };
  }, [enabled]);
}

export default function Messages() {
  const { t, locale } = useI18n(); const { me, state } = useSession(); const navigate = useNavigate(); const { cid } = useParams();
  setPrefix(state?.role);
  const narrow = useMediaQuery("(max-width: 720px)");
  const wide = useMediaQuery("(min-width: 1200px)");
  const coarse = useMediaQuery("(pointer: coarse)");
  useVisualViewportHeight(coarse);
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [convs, setConvs] = useState<Conversation[] | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [dialog, setDialog] = useState<"dm" | "group" | null>(null);
  const [info, setInfo] = useState(false);
  const [filter, setFilter] = useState<ListFilter>("all");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<{ q: string; items: Message[] } | null>(null);
  const [jump, setJump] = useState<(Jump & { cid: string }) | null>(null);
  const jumpN = useRef(0);
  const uid = me?.id || "";
  const selected = useMemo(() => convs?.find(c => c.id === cid) || null, [convs, cid]);

  const loadConvs = useCallback(() => get<{ items: Conversation[] }>(`${M()}/conversations`).then(x => {
    setConvs(x.items);
    announceMessagesUnread(x.items.filter(c => !c.me.muted).reduce((a, c) => a + c.unread, 0));
    return x.items;
  }), []);
  useEffect(() => {
    const abort = new AbortController();
    // the organizations a conversation can be started in: this application's own answer (the
    // account service's /api/auth/organizations is not served on this origin)
    Promise.all([get<{ items: Org[] }>(`${M()}/organizations`, abort.signal), loadConvs()]).then(([o]) => setOrgs(o.items.filter(x => x.status === "active"))).catch(e => {
      if (abort.signal.aborted) return;
      // The application registered itself on its own origin while this tab was open on the
      // account origin's fallback: the legacy routes answer 410, and the conversations are there.
      if (e instanceof ApiError && e.code === "moved") { window.location.replace(messagesHref(state, { lang: locale, path: cid ? threadPath(cid) : "/" })); return; }
      setError(e);
    });
    void capabilities().then(c => { if (!abort.signal.aborted) setCaps(c); });
    // drafts of another account on this browser are never shown to this one
    try { Object.keys(localStorage).filter(k => k.startsWith("synwevo.msg.draft.") && !k.startsWith(`synwevo.msg.draft.${uid}.`)).forEach(k => localStorage.removeItem(k)); } catch { /* storage unavailable */ }
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadConvs, uid]);

  // ---- the stream: a hint that something changed; the thread resyncs itself from history
  const threadRef = useRef<{ onEvent: (ev: any) => void; resync: () => void } | null>(null);
  const bind = useCallback((fns: { onEvent: (ev: any) => void; resync: () => void }) => { threadRef.current = fns; }, []);
  useEffect(() => {
    if (!uid) return;
    let es: EventSource | null = null; let failures = 0; let poll: number | null = null; let closed = false;
    const startPolling = () => { setLive("polling"); if (poll == null) poll = window.setInterval(() => { threadRef.current?.resync(); void loadConvs().catch(() => undefined); }, 5000); };
    const stopPolling = () => { if (poll != null) { window.clearInterval(poll); poll = null; } };
    const connect = () => {
      if (closed) return;
      es = new EventSource(`${M()}/stream`);
      es.addEventListener("hello", () => { failures = 0; stopPolling(); setLive("live"); threadRef.current?.resync(); void loadConvs().catch(() => undefined); });
      const forward = (e: MessageEvent) => { try { threadRef.current?.onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
      // a new message changes the list too (preview, order, unread); one reload per burst
      let listTimer: number | null = null;
      const reloadSoon = () => { if (listTimer != null) return; listTimer = window.setTimeout(() => { listTimer = null; void loadConvs().catch(() => undefined); }, 300); };
      es.addEventListener("message", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_updated", forward as EventListener);
      es.addEventListener("read", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("conversation", (e: MessageEvent) => { forward(e); void loadConvs().catch(() => undefined); });
      es.addEventListener("end", () => { es?.close(); es = null; startPolling(); });
      es.onerror = () => { failures += 1; if (failures >= 3) startPolling(); };   // EventSource retries by itself
    };
    connect();
    return () => { closed = true; es?.close(); stopPolling(); };
  }, [uid, loadConvs]);

  const open = (id: string) => { setInfo(false); navigate(threadPath(id)); };
  // a search hit: the thread named opens at that message; the request is consumed once, by that thread
  const openAt = (id: string, seq: number) => { setInfo(false); jumpN.current += 1; setJump({ cid: id, seq, n: jumpN.current }); if (id !== cid) navigate(threadPath(id)); };
  const doSearch = async (q: string) => {
    const text = q.trim();
    if (text.length < 2) { setError(new ApiError(400, "invalid_field", t("搜索至少需要 2 个字符。"))); return; }
    try { setError(null); const x = await get<{ items: Message[] }>(`${M()}/search?q=${encodeURIComponent(text)}`); setSearch({ q: text, items: x.items }); } catch (e) { setError(e); }
  };
  const infoMode = narrow ? "page" : wide ? "column" : "drawer";
  const showInfo = info && !!selected;
  useEffect(() => { if (!cid) setInfo(false); }, [cid]);

  const newMenu = <Menu ariaLabel={t("新会话")} buttonClass="btn primary sm icon msg-new-btn" trigger={<IcPlus />} align="end" items={[
    { label: t("新私聊"), onSelect: () => setDialog("dm"), disabled: !orgs.length, hint: !orgs.length ? t("你还不属于任何组织") : undefined },
    { label: t("新群聊"), onSelect: () => setDialog("group"), disabled: !orgs.length, hint: !orgs.length ? t("你还不属于任何组织") : undefined },
  ]} />;

  return <div className="app msg-page"><Topbar />
    <div className={`msg-layout ${cid ? "thread" : "list"} ${showInfo ? "info" : ""} ${showInfo && infoMode === "column" ? "three" : ""}`}>
      <aside className="msg-side" aria-label={t("会话列表")}>
        {search ? <SearchResults q={search.q} items={search.items} convs={convs || []} onOpen={openAt} onClose={() => setSearch(null)} />
          : <>
            {error && <div style={{ padding: "0 8px" }}><FlowError error={error} /></div>}
            {!convs ? <><div className="msg-side-h"><h1 className="msg-side-title">{t("消息")}</h1>{newMenu}</div><Loading /></>
              : <ConversationList convs={convs} cid={cid} uid={uid} filter={filter} setFilter={setFilter} query={query} setQuery={setQuery} onOpen={open} onSearch={q => void doSearch(q)}
                header={<div className="msg-side-h"><h1 className="msg-side-title">{t("消息")}</h1>{newMenu}</div>} />}
          </>}
      </aside>
      <section className="msg-thread" aria-label={t("会话")}>
        {!cid ? <div className="msg-empty msg-empty-thread"><p className="big">{t("选择一个会话，或开始新的对话。")}</p></div>
          : !selected ? (convs ? <div className="msg-empty"><p>{t("找不到这个会话，或你已不在其中。")}</p><button type="button" className="btn" onClick={() => navigate(LIST)}>{t("返回会话列表")}</button></div> : <Loading />)
          : <Thread key={selected.id} conv={selected} uid={uid} live={live} narrow={narrow} coarse={coarse} caps={caps} infoOpen={showInfo} onBack={() => navigate(LIST)} onOpenInfo={() => setInfo(o => !o)}
            onChanged={() => void loadConvs()} bind={bind} jump={jump && jump.cid === selected.id ? jump : null} onJumped={() => setJump(null)} />}
      </section>
      {showInfo && selected && <InfoPanel conv={selected} uid={uid} mode={infoMode} onClose={() => setInfo(false)} onChanged={() => { void loadConvs(); }} onLeft={() => { setInfo(false); navigate(LIST); void loadConvs(); }} />}
    </div>
    {dialog && <NewConversation kind={dialog} orgs={orgs} onClose={() => setDialog(null)} onCreated={c => { setDialog(null); void loadConvs().then(() => open(c.id)); }} />}
  </div>;
}
