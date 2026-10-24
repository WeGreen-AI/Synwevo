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
 *  Scope (§7.16): a person in more than one organization can scope the list to one of them — the
 *  server filters, the choice is remembered per account on this browser, and a new conversation
 *  starts in it. A conversation opened from outside the scope (a link, a notification) is still
 *  shown, read on its own.
 *
 *  Availability (B3): the server says which of the reader's organizations have Synwevo Message
 *  switched off (`disabled_organizations`, `platform_disabled`, `messages_enabled`) or are paused
 *  (frozen, deleted and recoverable); the list says so above itself, a conversation there opened
 *  from a link shows the server's refusal in place of the thread, no new conversation is offered
 *  there, and the stream's `organization` event reloads all of it within a heartbeat.
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
import { conversationsPath, localStore, readScope, writeScope } from "./lib/scope";
import { availabilityNotices, closedRefusal, usable, type ListAvailability } from "./lib/availability";
import { mutedNow } from "./lib/thread";
import type { Capabilities, Conversation, Live, Message, Org, Presence, SearchAnswer, Settings, TypingState } from "./types";
import { ConversationList, SearchResults, type ListFilter, type RowAction, type SearchOptions } from "./components/ConversationList";
import { Thread, type Jump } from "./components/Thread";
import { InfoPanel } from "./components/InfoPanel";
import { ForwardDialog, NewConversation, SettingsDialog, type Person } from "./components/dialogs";
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
  // every organization of the reader (`/organizations`); a conversation is started only in a usable one
  const [allOrgs, setAllOrgs] = useState<Org[]>([]);
  const orgs = useMemo(() => allOrgs.filter(usable), [allOrgs]);
  const [availability, setAvailability] = useState<ListAvailability | null>(null);
  const [convs, setConvs] = useState<Conversation[] | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [live, setLive] = useState<Live>("connecting");
  const [dialog, setDialog] = useState<"dm" | "group" | "settings" | null>(null);
  // a group made from a direct message: its organization and the other person, chosen in advance
  const [groupFrom, setGroupFrom] = useState<{ oid: string; person: Person } | null>(null);
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
  // the organization the list is scoped to ("" = every organization), remembered per account
  const [scope, setScopeState] = useState(() => readScope(localStore(), uid, null));
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const setScope = useCallback((oid: string) => { writeScope(localStore(), uid, oid); setScopeState(oid); }, [uid]);
  useEffect(() => { setScopeState(readScope(localStore(), uid, null)); }, [uid]);
  const [orgsKnown, setOrgsKnown] = useState(false);
  // a scope the person can no longer use (they left that organization, or have only one left) is
  // dropped once the organizations are known — never applied to an empty list
  useEffect(() => { if (orgsKnown && scope && !(orgs.length > 1 && orgs.some(o => o.id === scope))) setScope(""); }, [orgsKnown, orgs, scope, setScope]);
  // the open conversation when it is outside the current scope: read on its own, kept fresh with
  // the list; `conv` null = the server does not show it to this reader
  // `refusal`: the server closed it as a whole (B3: Message switched off, the organization paused) —
  // said in place of the thread, not as "not found"
  const [outside, setOutside] = useState<{ id: string; conv: Conversation | null; refusal?: ApiError } | null>(null);
  const cidRef = useRef(cid); cidRef.current = cid;
  const selected = useMemo(() => convs?.find(c => c.id === cid) || (outside && outside.id === cid ? outside.conv : null), [convs, cid, outside]);
  const resolving = !!cid && !!convs && !convs.some(c => c.id === cid) && outside?.id !== cid;
  const [stickyUnread, setStickyUnread] = useState<string | undefined>(undefined);

  const loadConvs = useCallback(() => get<{ items: Conversation[] } & ListAvailability>(conversationsPath(M(), scope)).then(async x => {
    const open = cidRef.current;
    let refusal: ApiError | undefined;
    const alone = open && !x.items.some(c => c.id === open) ? await get<Conversation>(`${M()}/conversations/${open}`).catch(e => { if (closedRefusal(e)) refusal = e; return null; }) : null;
    setConvs(x.items);
    setAvailability({ disabled_organizations: x.disabled_organizations, platform_disabled: x.platform_disabled });
    setOutside(open && !x.items.some(c => c.id === open) ? { id: open, conv: alone, refusal } : null);
    if (!scope) {
      const now = Date.now() / 1000;
      announceMessagesUnread(x.items.filter(c => !mutedNow(c.me, now)).reduce((a, c) => a + Math.max(c.unread, c.me.marked_unread ? 1 : 0), 0));
    } else {
      // a scoped list is not the whole picture: the platform badge asks the server for the total
      void get<{ unread: number }>("/api/auth/messaging/unread-count").then(u => announceMessagesUnread(u.unread)).catch(() => undefined);
    }
    return x.items;
  }), [scope]);
  // a conversation opened from outside the scope (a link, a notification): read it on its own
  useEffect(() => {
    if (!resolving || !cid) return;
    let live = true;
    get<Conversation>(`${M()}/conversations/${cid}`).then(c => { if (live) setOutside({ id: cid, conv: c }); },
      e => { if (live) setOutside({ id: cid, conv: null, refusal: closedRefusal(e) ? e : undefined }); });
    return () => { live = false; };
  }, [resolving, cid]);
  // The application registered itself on its own origin while this tab was open on the account
  // origin's fallback: the legacy routes answer 410, and the conversations are there.
  const failed = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.code === "moved") { window.location.replace(messagesHref(state, { lang: locale, path: cidRef.current ? threadPath(cidRef.current) : "/" })); return; }
    // the list was scoped to an organization that is closed now (B3): every organization again,
    // and the notice above the list says why
    if (scopeRef.current && closedRefusal(e)) { setScope(""); return; }
    setError(e);
  }, [state, locale, setScope]);
  // the list, again whenever the scope changes
  useEffect(() => {
    let live = true;
    loadConvs().catch(e => { if (live) failed(e); });
    return () => { live = false; };
  }, [loadConvs, failed]);
  // the reader's organizations: this application's own answer (the account service's
  // /api/auth/organizations is not served on this origin), again when the stream says a switch changed
  const loadOrgs = useCallback((signal?: AbortSignal) => get<{ items: Org[] }>(`${M()}/organizations`, signal)
    .then(o => { setAllOrgs(o.items); setOrgsKnown(true); }), []);
  useEffect(() => {
    const abort = new AbortController();
    loadOrgs(abort.signal).catch(e => {
      if (abort.signal.aborted) return;
      failed(e);
    });
    void capabilities().then(c => { if (abort.signal.aborted) return; setCaps(c); if (c?.settings) setSettings(c.settings); });
    // drafts of another account on this browser are never shown to this one
    try { Object.keys(localStorage).filter(k => k.startsWith("synwevo.msg.draft.") && !k.startsWith(`synwevo.msg.draft.${uid}.`)).forEach(k => localStorage.removeItem(k)); } catch { /* storage unavailable */ }
    return () => abort.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uid]);
  // the stream and the resync keep one reference to "reload the list", whatever the scope
  const loadRef = useRef(loadConvs); loadRef.current = loadConvs;
  const reload = useCallback(() => loadRef.current(), []);

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
    await reload().catch(() => undefined);
  }, [reload]);
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
        threadRef.current?.resync(); void reload().catch(() => undefined);
      }) as EventListener);
      const forward = (e: MessageEvent) => { try { threadRef.current?.onEvent(JSON.parse(e.data)); } catch { /* ignore */ } };
      // a new message changes the list too (preview, order, unread); one reload per burst
      let listTimer: number | null = null;
      const reloadSoon = () => { if (listTimer != null) return; listTimer = window.setTimeout(() => { listTimer = null; void reload().catch(() => undefined); }, 300); };
      es.addEventListener("message", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_updated", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("message_hidden", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("read", ((e: MessageEvent) => { forward(e); reloadSoon(); }) as EventListener);
      es.addEventListener("receipt", forward as EventListener);
      es.addEventListener("conversation", (e: MessageEvent) => { forward(e); void reload().catch(() => undefined); });
      // B3: Synwevo Message switched off or back on for one of the reader's organizations (or the
      // platform): the organizations, the list and the open thread are read again
      es.addEventListener("organization", () => { void loadOrgs().catch(() => undefined); threadRef.current?.resync(); void reload().catch(() => undefined); });
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
  }, [uid, reload, resyncAll, loadOrgs]);

  const open = (id: string) => { setInfo(false); setStickyUnread(filter === "unread" ? id : undefined); navigate(threadPath(id)); };
  const chooseFilter = (f: ListFilter) => { setStickyUnread(undefined); setFilter(f); };
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
  const notices = useMemo(() => availabilityNotices(allOrgs, availability), [allOrgs, availability]);
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
            <Availability notices={notices} />
            {!convs ? <>{header}<Loading /></>
              : <ConversationList convs={convs} cid={cid} uid={uid} presence={presence} filter={filter} setFilter={chooseFilter} query={query} setQuery={setQuery} onOpen={open}
                onSearch={(q, o) => void doSearch(q, { ...o, cid: o.cid ?? undefined })} onRowAction={(c, a) => void rowAction(c, a)} header={header}
                orgs={orgs} scope={orgs.length > 1 ? scope : ""} setScope={setScope} keepId={stickyUnread && stickyUnread === cid ? stickyUnread : undefined} />}
          </>}
      </aside>
      <section className="msg-thread" aria-label={t("会话")}>
        {!cid ? <div className="msg-empty msg-empty-thread"><p className="big">{t("选择一个会话，或开始新的对话。")}</p></div>
          : !selected ? (convs && !resolving ? <div className="msg-empty" role={outside?.refusal ? "alert" : undefined}>
              <p>{outside?.refusal ? outside.refusal.message : t("找不到这个会话，或你已不在其中。")}</p>
              <button type="button" className="btn" onClick={() => navigate(LIST)}>{t("返回会话列表")}</button></div> : <Loading />)
          : <Thread key={selected.id} conv={selected} uid={uid} live={live} narrow={narrow} coarse={coarse} caps={caps} settings={settings} presence={presence} typing={typing[selected.id] || []}
            infoOpen={showInfo} onBack={() => navigate(LIST)} onOpenInfo={() => setInfo(o => !o)} onChanged={() => void loadConvs()} onForward={setForwarding}
            bind={bind} jump={jump && jump.cid === selected.id ? jump : null} onJumped={() => setJump(null)} />}
      </section>
      {showInfo && selected && <InfoPanel conv={selected} uid={uid} mode={infoMode} presence={presence} onClose={() => setInfo(false)} onChanged={() => { void loadConvs(); }} onLeft={() => { setInfo(false); navigate(LIST); void loadConvs(); }}
        canBlock={!!caps?.blocks} onGroupFrom={c => { if (c.peer) { setGroupFrom({ oid: c.organization_id, person: { user_id: c.peer.user_id, name: c.peer.name } }); setDialog("group"); } }} />}
    </div>
    {(dialog === "dm" || dialog === "group") && <NewConversation kind={dialog} orgs={orgs} onClose={() => { setDialog(null); setGroupFrom(null); }}
      initialOrg={groupFrom && dialog === "group" ? groupFrom.oid : (orgs.length > 1 ? scope : "") || undefined}
      initialChosen={groupFrom && dialog === "group" ? [groupFrom.person] : undefined}
      note={groupFrom && dialog === "group" ? t("之前的私聊记录不会带入新群") : undefined}
      onCreated={c => { setDialog(null); setGroupFrom(null); void loadConvs().then(() => open(c.id)); }} />}
    {dialog === "settings" && settings && <SettingsDialog settings={settings} blocks={!!caps?.blocks} onBlocksChanged={() => { void loadConvs(); }} onClose={() => setDialog(null)} onSaved={s => { setSettings(s); if (!s.show_presence) setPresence({}); void loadConvs(); }} />}
    {forwarding && <ForwardDialog message={forwarding} convs={convs || []} max={caps?.limits?.forward_targets || 5} onClose={() => setForwarding(null)} onDone={() => { setForwarding(null); void loadConvs(); }} />}
  </div>;
}

/** B3: why some of the reader's conversations are not listed now — Synwevo Message switched off
 *  for an organization or platform-wide, or an organization paused. A polite live region present
 *  from the first render, so a change the stream brings is announced. */
function Availability({ notices }: { notices: ReturnType<typeof availabilityNotices> }) {
  const { t } = useI18n();
  return <div className="msg-availability" role="status" aria-live="polite">
    {notices.map(n => <p key={n.key} className="notice warn">{t(n.text, n.params)}</p>)}
  </div>;
}
