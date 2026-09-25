import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, post, patch, setWorkspaceContext, getSessionGeneration, advanceSessionGeneration } from "./api";
import { getLocale, normalizeLocale, setLocale, subscribeLocale, useI18n } from "@synwevo/ui";
import "./session.messages";

/** Who is signed in, for the whole app.
 *
 *  `null` means signed out; `undefined` means we have not asked yet. The distinction matters:
 *  rendering a login screen while the answer is still in flight makes the app flash a sign-in
 *  form at someone who is already signed in, on every reload.
 */
export interface Me {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
  platform_role: "none" | "operator" | "admin";
  locale?: "zh-Hans" | "zh-Hant" | "en";
  timezone?: string;
  email_verified?: boolean;
  onboarding_completed?: boolean;
  permissions?: string[];
  /** A platform administrator whose current session proved one factor only: the role exists
   *  but cannot be used until the password is re-entered or a second factor signs in. */
  admin_locked?: boolean;
  /** Where "back to my work" may go from the account pages: the portal and each application
   *  this person can open, as the server lists them. */
  return_targets?: { id: string; name: string; url: string; workspace_id?: string | null; workspace_name?: string | null }[];
  workspaces: { id: string; name: string; kind: string; role: string; can_export: boolean; status?: string; organization_id?: string|null; organization_name?: string|null }[];
  /** The workspace this session is currently acting in; null when none is selected. */
  workspace_id?: string | null;
  session?: { id: string; amr: string };
}

export interface AuthState {
  /** How this deployment protects the UI. Only "session" means accounts are in use. */
  mode: "none" | "basic" | "proxy" | "session";
  registration: "open" | "invite" | "closed";
  bootstrap_required: boolean;
  password_login: boolean;
  /** Configurable platform name. The sign-in screen is the platform's, not one application's. */
  platform_name?: string;
  /** Which service this process is: "accounts", "portal", "app", or "all". */
  role?: string;
  /** Server-controlled "back" target from the account pages; empty when there is no portal. */
  portal_url?: string;
  /** Where the account and security pages live: this app's own, or the account service's. */
  account_url?: string;
  /** This application's id (a relying party), sent as `from=` when linking to the account page. */
  app_id?: string | null;
  /** External OpenID providers the account service offers — only the configured ones. */
  external_providers?: { id: string; label: string }[];
}

interface Ctx {
  me: Me | null | undefined;
  state: AuthState | null;
  /** Revalidate this document; a changed binding schedules a full reload. Actions that
   * already finish with a full navigation must persist workspace context and navigate
   * directly, rather than await refresh and race its reload with their destination. */
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  /** True when the deployment has no account system at all (SYNWEVO_UI_AUTH is none/basic). */
  disabled: boolean;
  /** Set when the service could not say who is signed in — a 5xx, a gateway, the network.
   *  Distinct from signed out: sending someone to sign in because the server was down loops
   *  them through the identity provider, minting a sign-in request and a session per pass,
   *  and never tells them the service is the problem. */
  error: ApiError | null;
}

/** Minimum gap between background revalidations triggered by focus / visibility. */
export const REVALIDATE_MIN_MS = 30_000;

const SessionCtx = createContext<Ctx>({ me: undefined, state: null, refresh: async () => {}, signOut: async () => {}, disabled: true, error: null });

export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const { t } = useI18n();
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [state, setState] = useState<AuthState | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const localeAccount = useRef<string | null>(null);
  const refreshEpoch = useRef(0);
  const refreshController = useRef<AbortController | null>(null);
  const validatedContext = useRef<string | null>(null);
  const navigating = useRef(false);
  const [resetting, setResetting] = useState(false);
  const launchWorkspace = useRef(new URLSearchParams(window.location.search).get("synwevo_workspace"));
  const lastRefreshAt = useRef(0);

  const invalidate = useCallback(() => {
    ++refreshEpoch.current;
    refreshController.current?.abort();
    const initial = validatedContext.current === null;
    if (!initial) advanceSessionGeneration();
    setWorkspaceContext(null, { initial });
    // Keep the last binding: a rapid re-login must still cross the reload boundary even
    // if React batches the signed-out and signed-in updates before unmounting old pages.
    setMe(null);
    setDisabled(false);
    setError(null);
  }, []);

  const refresh = useCallback(async () => {
    if (navigating.current) return;
    refreshController.current?.abort();
    const controller = new AbortController();
    refreshController.current = controller;
    const epoch = ++refreshEpoch.current;
    const generation = getSessionGeneration();
    const currentRefresh = () => epoch === refreshEpoch.current && generation === getSessionGeneration()
      && !controller.signal.aborted && !navigating.current;
    const probe = { signal: controller.signal, notifySessionExpiry: false };
    let st: AuthState;
    setError(null);
    lastRefreshAt.current = Date.now();
    // Both probes leave together: which deployment this is and who is signed in are independent
    // questions, and asking them one after the other doubled the wait on every fresh page.
    const mePromise = api<Me>("/api/auth/me", { ...probe, workspaceId: launchWorkspace.current || undefined })
      .then((v) => ({ ok: true as const, value: v }), (e) => ({ ok: false as const, error: e }));
    try {
      st = await api<AuthState>("/api/auth/state", probe);
      if (!currentRefresh()) return;
      setState(st);
    } catch (e) {
      if (!currentRefresh()) return;
      // Only Synwevo's own 404 means "this build has no account routes": behave as before.
      // Anything else — a 5xx, a gateway page, no network — says nothing about how the
      // deployment is configured, and treating it as "no accounts here" opened the app to
      // whoever was in front of it as if authentication were off.
      if (e instanceof ApiError && e.status === 404 && !e.upstream) {
        if (validatedContext.current !== null) invalidate();
        else { setWorkspaceContext(null, { initial: true }); setMe(null); }
        setDisabled(true);
        return;
      }
      setError(asApiError(e));
      return;
    }
    // The server says whether accounts are in use. Inferring it from "the request succeeded"
    // was wrong: these routes are mounted in every auth mode, so on a `basic` deployment this
    // call returns 200 and the app sent people who had already signed in with the shared
    // password to a first-run bootstrap screen.
    if (st.mode !== "session") {
      if (validatedContext.current !== null) invalidate();
      else { setWorkspaceContext(null, { initial: true }); setMe(null); }
      setDisabled(true);
      return;
    }
    setDisabled(false);
    try {
      let current: Me;
      const answer = await mePromise;
      try { if (answer.ok) current = answer.value; else throw answer.error; }
      catch (e) {
        if (!currentRefresh()) return;
        if (launchWorkspace.current || !(e instanceof ApiError) || e.code !== "workspace_context_invalid") throw e;
        // Probe without this tab's header; never clear shared context while awaiting a
        // fallback. Older refreshes cannot redirect subsequent business requests elsewhere.
        current = await api<Me>("/api/auth/me", { ...probe, workspaceId: null });
      }
      if (!currentRefresh()) return;
      if (launchWorkspace.current) {
        launchWorkspace.current = null;
        const url = new URL(window.location.href);
        url.searchParams.delete("synwevo_workspace");
        window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
      }
      const context = JSON.stringify([current.id, current.session?.id ?? null, current.workspace_id ?? null]);
      if (validatedContext.current !== null && validatedContext.current !== context) {
        // Account/session/workspace rollover is a document boundary. No existing editor,
        // query cache or delayed response may be displayed under the new identity/context.
        navigating.current = true;
        ++refreshEpoch.current;
        advanceSessionGeneration();
        setWorkspaceContext(current.workspace_id);
        setResetting(true);
        setMe(undefined);
        window.location.reload();
        return;
      }
      const initial = validatedContext.current === null;
      validatedContext.current = context;
      setWorkspaceContext(current.workspace_id, { initial });
      setMe(current);
    } catch (e) {
      if (!currentRefresh()) return;
      // 401 from Synwevo is the ordinary signed-out answer. Everything else is a failure to
      // find out, and is shown as one — never as "signed out", which would redirect.
      if (e instanceof ApiError && e.status === 401 && !e.upstream) invalidate();
      else setError(asApiError(e));
    }
  }, [invalidate]);

  useEffect(() => {
    void refresh();
    return () => { ++refreshEpoch.current; refreshController.current?.abort(); };
  }, [refresh]);
  useEffect(() => {
    if (!me) { localeAccount.current = null; return; }
    if (localeAccount.current !== me.id) {
      localeAccount.current = me.id;
      const preferred = normalizeLocale(me.locale);
      if (preferred) setLocale(preferred);
    }
    if (me.status !== "active") return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let serial = Promise.resolve();
    const unsubscribe = subscribeLocale(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const locale = getLocale();
        serial = serial.then(async () => {
          if (!active) return;
          try { await patch("/api/auth/locale", { locale }); }
          catch (e) { if (active && !(e instanceof Error && e.name === "AbortError")) setError(asApiError(e)); }
        });
      }, 250);
    });
    return () => { active = false; if (timer) clearTimeout(timer); unsubscribe(); };
  }, [me?.id, me?.status]);
  useEffect(() => {
    const check = (e: PageTransitionEvent) => { if (e.persisted) void refresh(); };
    // Returning to the tab revalidates in the background (the known identity stays on screen),
    // and not more often than REVALIDATE_MIN_MS: focus and visibilitychange fire together, and
    // every click that moved focus back into the window used to cost two more requests.
    const visible = () => {
      if (document.visibilityState !== "visible") return;
      if (Date.now() - lastRefreshAt.current < REVALIDATE_MIN_MS) return;
      void refresh();
    };
    const expired = (event: Event) => {
      const generation = (event as CustomEvent<{ generation?: number }>).detail?.generation;
      if (generation !== undefined && generation !== getSessionGeneration()) return;
      invalidate();
    };
    const otherTab = (e: StorageEvent) => {
      if (e.key === "synwevo.signed-out" && e.newValue) {
        invalidate(); window.location.replace("/logged-out");
      }
    };
    window.addEventListener("pageshow", check);
    window.addEventListener("focus", visible);
    document.addEventListener("visibilitychange", visible);
    window.addEventListener("synwevo:session-expired", expired);
    window.addEventListener("storage", otherTab);
    return () => {
      window.removeEventListener("pageshow", check);
      window.removeEventListener("focus", visible);
      document.removeEventListener("visibilitychange", visible);
      window.removeEventListener("synwevo:session-expired", expired);
      window.removeEventListener("storage", otherTab);
    };
  }, [refresh, invalidate]);

  const signOut = useCallback(async () => {
    const generation = getSessionGeneration();
    try {
      await post("/api/auth/logout", {});
      invalidate();
      try { localStorage.setItem("synwevo.signed-out", String(Date.now())); } catch { /* server revocation remains authoritative */ }
      // A full reload clears every cached query, preview and grid still holding the previous
      // account's data. Wiping React state alone would leave those in memory for the next login.
      window.location.replace("/logged-out");
    } catch (e) { if (generation === getSessionGeneration()) setError(asApiError(e)); throw e; }
  }, [invalidate]);

  return <SessionCtx.Provider value={{ me, state, refresh, signOut, disabled, error }}>
    {resetting ? <div className="empty" role="status">{t("加载中…")}</div> : children}
  </SessionCtx.Provider>;
}

function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, "network", e instanceof Error ? e.message : String(e));
}

/** What a page shows when the service could not answer "who is signed in". A retry, and the
 *  reason — never a redirect, which is what turned an outage into a sign-in loop. */
export function SessionError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const [busy, setBusy] = useState(false);
  const { t } = useI18n();
  return (
    <div className="empty" role="alert">
      <div className="big">{t("暂时无法确认你的登录状态")}</div>
      <div style={{ maxWidth: 520 }}>{error.message}</div>
      {error.requestId && <div className="tiny muted">{t("请求编号：")}<span className="mono">{error.requestId}</span></div>}
      <button type="button" className="btn primary" disabled={busy}
        onClick={() => { setBusy(true); Promise.resolve(onRetry()).finally(() => setBusy(false)); }}>
        {busy ? t("正在重试…") : t("重试")}
      </button>
    </div>
  );
}

/** Wrap routes that need an account. */
export function RequireSession({ children }: { children: React.ReactNode }) {
  const { me, state, disabled, error, refresh } = useSession();
  const { t } = useI18n();
  if (disabled) return <>{children}</>;          // deployment without accounts: unchanged behaviour
  if (error) return <SessionError error={error} onRetry={refresh} />;
  if (me === undefined) return <div className="empty" role="status">{t("加载中…")}</div>;
  if (me === null) {
    const back = window.location.pathname + window.location.search;
    // Remember where they were going so signing in returns them to it rather than to the home
    // page — losing the document you were opening is a small thing that feels like a big one.
    window.location.assign(signInUrl(state, back));
    return <div className="empty">{t("正在前往登录…")}</div>;
  }
  return <>{children}</>;
}

/**
 * Where signing in happens.
 *
 * On a relying party there is no local password form — `/api/auth/login` is not even served —
 * so the only correct destination is the single sign-on start, which sends the browser to the
 * account service and brings it back to `next`. Sending someone to a local `/login` there gave
 * them a password box that could not work, on the very page whose whole point is that the
 * password is typed in exactly one place.
 */
export function signInUrl(state: AuthState | null, next: string): string {
  const target = encodeURIComponent(next || "/");
  const lang = encodeURIComponent(getLocale());
  return state && state.password_login === false
    ? `/auth/start?next=${target}&lang=${lang}`
    : `/login?next=${target}&lang=${lang}`;
}

/** Only ever returns a same-origin path, so a crafted ?next= cannot bounce someone off-site. */
export function safeNext(raw: string | null): string {
  if (!raw) return "/";
  try {
    const u = new URL(raw, window.location.origin);
    if (u.origin !== window.location.origin) return "/";
    return u.pathname + u.search + u.hash;
  } catch {
    return "/";
  }
}
