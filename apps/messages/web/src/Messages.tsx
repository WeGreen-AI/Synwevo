/** Messages: direct messages and groups inside an organization.
 *
 *  What the server says is what is shown: a message is "sent" only when the server has stored it
 *  (until then it is pending, and a retry carries the same client id so a lost acknowledgement
 *  never becomes a duplicate); history is whatever the server lets this member read; the stream
 *  is a hint that something changed — every event carries an id the browser replays from, a
 *  `resync` sends the page to `GET /sync` and the history API, and when the stream cannot be
 *  held open the page polls `/sync` and says so. Presence heartbeats go out only from a visible
 *  page; typing hints and presence are shown only while the server says so.
 *
 *  Layout: the conversation list and the thread side by side; the info panel is a third column
 *  from 1200px, a drawer on medium screens, and a full page on phones, where the list, the
 *  thread and the info are one pane at a time with a real back control. The page height follows
 *  the visual viewport on touch devices so the composer is never under the software keyboard. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, announceMessagesUnread, get, messagesHref, post, put, useSession } from "@synwevo/auth-client";
import { Menu, useI18n, useMediaQuery } from "@synwevo/ui";
import { Topbar } from "./Topbar";
import { FlowError, Loading } from "./flow";
import { LIST, M, capabilities, heartbeat, setPrefix, sync as syncSince, threadPath } from "./api";
import type { Capabilities, Conversation, Live, Message, Org, Presence, SearchAnswer, Settings, TypingState } from "./types";
import { ConversationList, SearchResults, type ListFilter, type RowAction, type SearchOptions } from "./components/ConversationList";
import { Thread, type Jump } from "./components/Thread";
import { InfoPanel } from "./components/InfoPanel";
import { ForwardDialog, NewConversation, SettingsDialog } from "./components/dialogs";
import { IcPlus, IcSettings } from "./components/icons";
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
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [dialog, setDialog] = useState<"dm" | "group" | "settings" | null>(null);
  const [forwarding, setForwarding] = useState<Message | null>(null);
  const [info, setInfo] = useState(false);
  const [filter, setFilter] = useState<ListFilter>("all");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<{ q: string; opts: SearchOptions; answer: SearchAnswer } | null>(null);
  const [jump, setJump] = useState<(Jump & { cid: string }) | null>(null);
  const [presence, setPresence] = useState<Record<string, Presence>>({});
  const [typing, setTyping] = useState<Record<string, TypingState[]>>({});
  const jumpN = useRef(0);
  const cursor = useRef(0);
  const uid = me?.id || "";
  const selected = useMemo(() => convs?.find(c => c.id === cid) || null, [convs, cid]);

  const loadConvs = useCallback(() => get<{ items: Conversation[] }>(`${M()}/conversations`).then(x => {
    setConvs(x.items);
    const now = Date.now() / 1000;
    announceMessagesUnread(x.items.filter(c => !c.me.muted && !(typeof c.me.muted_until === "number" && c.me.muted_until > now)).reduce((a, c) => a + Math.max(c.unread, c.me.marked_unread ? 1 : 0), 0));
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
    void capabilities().then(c => { if (abort.signal.aborted) return; setCaps(c); if (c?.settings) setSettings(c.settings); });
    // drafts of another account on this browser are never shown to this one
    try { Object.keys(localStorage).filter(k => k.startsWith("synwevo.msg.draft.") && !k.startsWith(`synwevo.msg.draft.${uid}.`)).forEach(k => localStorage.removeItem(k)); } catch { /* storage unavailable */ }
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadConvs, uid]);

  // ---- presence heartbeats (§7.3): only from a visible page, only while the person shows presence
  useEffect(() => {
    const interval = caps?.presence?.interval_s;
    if (!uid || !interval || (settings && !settings.show_presence)) return;
    let timer: number | null = null;
    const tick = () => { if (document.visibilityState === "visible") void heartbeat(); };
    const start = () => { if (timer == null) { tick(); timer = window.setInterval(tick, interval * 1000); } };
    const stop = () => { if (timer != null) { window.clearInterval(timer); timer = null; } };
    const onVisible = () => { if (document.visibilityState === "visible") start(); else stop(); };
    onVisible();
    document.addEventListener("visibilitychange", onVisible);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisible); };
  }, [uid, caps?.presence?.interval_s, settings]);

  // typing hints expire by themselves (§7.3): prune once a second while any are shown
  useEffect(() => {
    if (!Object.values(typing).some(list => list.length)) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setTyping(cur => { let changed = false; const next: Record<string, TypingState[]> = {}; for (const [k, list] of Object.entries(cur)) { const kept = list.filter(x => x.until > now); if (kept.length !== list.length) changed = true; if (kept.length) next[k] = kept; } return changed ? next : cur; });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [typing]);

  // ---- the stream: a hint that something changed; the thread resyncs itself from history
  const threadRef = useRef<{ onEvent: (ev: any) => void; resync: () => void } | null>(null);
  const bind = useCallback((fns: { onEvent: (ev: any) => void; resync: () => void }) => { threadRef.current = fns; }, []);
  /** `GET /sync` since the last cursor (§7.10): which conversations changed (the list reloads),
   *  which were left, which messages vanished (marked in place); then the thread catches up. */
  const resyncAll = useCallback(async () => {
    try {
      const s = await syncSince(cursor.current);
      cursor.current = s.cursor;
      if (s.recalled.length) for (const cidX of new Set(s.recalled.map(r => r.conversation_id))) threadRef.current?.onEvent({ type: "sync_recalled", conversation_id: cidX, items: s.recalled.filter(r => r.conversation_id === cidX) });
    } catch { /* the list reload below still tells the truth */ }
    threadRef.current?.resync();
    await loadConvs().catch(() => undefined);
  }, [loadConvs]);
  useEffect(() => {
    if (!uid) return;
    let es: EventSource | null = null; let failures = 0; let poll: number | null = null; let closed = false;
    const startPolling = () => { setLive("polling"); if (poll == null) poll = window.setInterval(() => { void resyncAll(); }, 5000); };
    const stopPolling = () => { if (poll != null) { window.clearInterval(poll); poll = null; } };
    const connect = () => {
      if (closed) return;
      es = new EventSource(`${M()}/stream`);                     // the browser replays with Last-Event-ID by itself
      es.addEventListener("hello", ((e: MessageEvent) => {
        failures = 0; stopPolling(); setLive("live");
        try { const h = JSON.parse(e.data); if (typeof h.last_event_id === "number" && !cursor.current) cursor.current = h.last_event_id; } catch { /* ignore */ }
        threadRef.current?.resync(); void loadConvs().catch(() => undefined);
      }) as EventListener);
      const forward = (e: MessageEvent) => { try { threadRef.current?.onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
      // a new message changes the list too (preview, order, unread); one reload per burst
      let listTimer: number | null = null;
      const reloadSoon = () => { if (listTimer != null) return; listTimer = window.setTimeout(() => { listTimer = null; void loadConvs().catch(() => undefined); }, 300); };
      es.addEventListener("message", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_updated", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_hidden", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("read", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("receipt", forward as EventListener);
      es.addEventListener("conversation", (e: MessageEvent) => { forward(e); void loadConvs().catch(() => undefined); });
      es.addEventListener("typing", ((e: MessageEvent) => {
        try {
          const ev = JSON.parse(e.data);
          setTyping(cur => {
            const list = (cur[ev.conversation_id] || []).filter(x => x.user_id !== ev.user_id);
            if (ev.state === "start") list.push({ user_id: ev.user_id, name: ev.name, until: Date.now() + (ev.expires_in_s || 6) * 1000 });
            return { ...cur, [ev.conversation_id]: list };
          });
        } catch { /* ignore */ }
      }) as EventListener);
      es.addEventListener("presence", ((e: MessageEvent) => { try { const ev = JSON.parse(e.data); setPresence(cur => ({ ...cur, [ev.user_id]: ev.state ?? null })); } catch { /* ignore */ } }) as EventListener);
      es.addEventListener("resync", () => { void resyncAll(); });
      es.addEventListener("end", () => { es?.close(); es = null; startPolling(); });
      es.onerror = () => { failures += 1; if (failures >= 3) startPolling(); };   // EventSource retries by itself
    };
    connect();
    return () => { closed = true; es?.close(); stopPolling(); };
  }, [uid, loadConvs, resyncAll]);

  const open = (id: string) => { setInfo(false); navigate(threadPath(id)); };
  // a search hit: the thread named opens at that message; the request is consumed once, by that thread
  const openAt = (id: string, seq: number) => { setInfo(false); jumpN.current += 1; setJump({ cid: id, seq, n: jumpN.current }); if (id !== cid) navigate(threadPath(id)); };
  const doSearch = async (q: string, opts: SearchOptions) => {
    const text = q.trim();
    if (text.length < 2) { setError(new ApiError(400, "invalid_field", t("搜索至少需要 2 个字符。"))); return; }
    try {
      setError(null);
      const params = new URLSearchParams({ q: text });
      if (opts.cid) params.set("cid", opts.cid);
      if (opts.has_attachment) params.set("has_attachment", "1");
      const answer = await get<SearchAnswer>(`${M()}/search?${params.toString()}`);
      setSearch({ q: text, opts, answer });
    } catch (e) { setError(e); }
  };
  const rowAction = async (c: Conversation, a: RowAction) => {
    try {
      if (a === "unread") await post(`${M()}/conversations/${c.id}/unread`, {});
      else await put(`${M()}/conversations/${c.id}/preferences`, a === "pin" ? { pinned: true } : a === "unpin" ? { pinned: false } : a === "mute" ? { muted: true } : { muted: false });
      await loadConvs();
    } catch (e) { setError(e); }
  };
  const infoMode = narrow ? "page" : wide ? "column" : "drawer";
  const showInfo = info && !!selected;
  useEffect(() => { if (!cid) setInfo(false); }, [cid]);

  const header = <div className="msg-side-h"><h1 className="msg-side-title">{t("消息")}</h1>
    {settings && <button type="button" className="btn ghost sm icon" onClick={() => setDialog("settings")} aria-label={t("消息设置")} title={t("消息设置")}><IcSettings size={18} /></button>}
    <Menu ariaLabel={t("新会话")} buttonClass="btn primary sm icon msg-new-btn" trigger={<IcPlus />} align="end" items={[
      { label: t("新私聊"), onSelect: () => setDialog("dm"), disabled: !orgs.length, hint: !orgs.length ? t("你还不属于任何组织") : undefined },
      { label: t("新群聊"), onSelect: () => setDialog("group"), disabled: !orgs.length, hint: !orgs.length ? t("你还不属于任何组织") : undefined },
    ]} />
  </div>;

  return <div className="app msg-page"><Topbar />
    <div className={`msg-layout ${cid ? "thread" : "list"} ${showInfo ? "info" : ""} ${showInfo && infoMode === "column" ? "three" : ""}`}>
      <aside className="msg-side" aria-label={t("会话列表")}>
        {/* leaving the results also clears the box: it doubles as the list filter, and a list filtered by a finished search would look empty */}
        {search ? <SearchResults q={search.q} answer={search.answer} opts={search.opts} convs={convs || []} cid={cid} onSearch={(q, o) => void doSearch(q, o)} onOpen={openAt} onClose={() => { setSearch(null); setQuery(""); }} />
          : <>
            {error && <div style={{ padding: "0 8px" }}><FlowError error={error} /></div>}
            {!convs ? <>{header}<Loading /></>
              : <ConversationList convs={convs} cid={cid} uid={uid} presence={presence} filter={filter} setFilter={setFilter} query={query} setQuery={setQuery} onOpen={open}
                onSearch={(q, o) => void doSearch(q, { ...o, cid: o.cid ?? undefined })} onRowAction={(c, a) => void rowAction(c, a)} header={header} />}
          </>}
      </aside>
      <section className="msg-thread" aria-label={t("会话")}>
        {!cid ? <div className="msg-empty msg-empty-thread"><p className="big">{t("选择一个会话，或开始新的对话。")}</p></div>
          : !selected ? (convs ? <div className="msg-empty"><p>{t("找不到这个会话，或你已不在其中。")}</p><button type="button" className="btn" onClick={() => navigate(LIST)}>{t("返回会话列表")}</button></div> : <Loading />)
          : <Thread key={selected.id} conv={selected} uid={uid} live={live} narrow={narrow} coarse={coarse} caps={caps} settings={settings} presence={presence} typing={typing[selected.id] || []}
            infoOpen={showInfo} onBack={() => navigate(LIST)} onOpenInfo={() => setInfo(o => !o)} onChanged={() => void loadConvs()} onForward={setForwarding}
            bind={bind} jump={jump && jump.cid === selected.id ? jump : null} onJumped={() => setJump(null)} />}
      </section>
      {showInfo && selected && <InfoPanel conv={selected} uid={uid} mode={infoMode} presence={presence} onClose={() => setInfo(false)} onChanged={() => { void loadConvs(); }} onLeft={() => { setInfo(false); navigate(LIST); void loadConvs(); }} />}
    </div>
    {(dialog === "dm" || dialog === "group") && <NewConversation kind={dialog} orgs={orgs} onClose={() => setDialog(null)} onCreated={c => { setDialog(null); void loadConvs().then(() => open(c.id)); }} />}
    {dialog === "settings" && settings && <SettingsDialog settings={settings} onClose={() => setDialog(null)} onSaved={s => { setSettings(s); if (!s.show_presence) setPresence({}); void loadConvs(); }} />}
    {forwarding && <ForwardDialog message={forwarding} convs={convs || []} max={caps?.limits?.forward_targets || 5} onClose={() => setForwarding(null)} onDone={() => { setForwarding(null); void loadConvs(); }} />}
  </div>;
}
