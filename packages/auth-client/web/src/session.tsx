import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { ApiError, get, post } from "./api";

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
  workspaces: { id: string; name: string; kind: string; role: string; can_export: boolean }[];
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
}

interface Ctx {
  me: Me | null | undefined;
  state: AuthState | null;
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

const SessionCtx = createContext<Ctx>({ me: undefined, state: null, refresh: async () => {}, signOut: async () => {}, disabled: true, error: null });

export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [state, setState] = useState<AuthState | null>(null);
  const [disabled, setDisabled] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const refresh = useCallback(async () => {
    let st: AuthState;
    setError(null);
    try {
      st = await get<AuthState>("/api/auth/state");
      setState(st);
    } catch (e) {
      // Only Synwevo's own 404 means "this build has no account routes": behave as before.
      // Anything else — a 5xx, a gateway page, no network — says nothing about how the
      // deployment is configured, and treating it as "no accounts here" opened the app to
      // whoever was in front of it as if authentication were off.
      if (e instanceof ApiError && e.status === 404 && !e.upstream) {
        setDisabled(true);
        setMe(null);
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
      setDisabled(true);
      setMe(null);
      return;
    }
    try {
      setMe(await get<Me>("/api/auth/me"));
    } catch (e) {
      // 401 from Synwevo is the ordinary signed-out answer. Everything else is a failure to
      // find out, and is shown as one — never as "signed out", which would redirect.
      if (e instanceof ApiError && e.status === 401 && !e.upstream) setMe(null);
      else setError(asApiError(e));
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const signOut = useCallback(async () => {
    try { await post("/api/auth/logout", {}); } finally {
      setMe(null);
      // A full reload clears every cached query, preview and grid still holding the previous
      // account's data. Wiping React state alone would leave those in memory for the next login.
      window.location.assign("/login");
    }
  }, []);

  return <SessionCtx.Provider value={{ me, state, refresh, signOut, disabled, error }}>{children}</SessionCtx.Provider>;
}

function asApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, "network", e instanceof Error ? e.message : String(e));
}

/** What a page shows when the service could not answer "who is signed in". A retry, and the
 *  reason — never a redirect, which is what turned an outage into a sign-in loop. */
export function SessionError({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="empty" role="alert">
      <div className="big">暂时无法确认你的登录状态</div>
      <div style={{ maxWidth: 520 }}>{error.message}</div>
      {error.requestId && <div className="tiny muted">请求编号：<span className="mono">{error.requestId}</span></div>}
      <button type="button" className="btn primary" disabled={busy}
        onClick={() => { setBusy(true); Promise.resolve(onRetry()).finally(() => setBusy(false)); }}>
        {busy ? "正在重试…" : "重试"}
      </button>
    </div>
  );
}

/** Wrap routes that need an account. */
export function RequireSession({ children }: { children: React.ReactNode }) {
  const { me, state, disabled, error, refresh } = useSession();
  if (disabled) return <>{children}</>;          // deployment without accounts: unchanged behaviour
  if (error) return <SessionError error={error} onRetry={refresh} />;
  if (me === undefined) return <div className="empty" role="status">加载中…</div>;
  if (me === null) {
    const back = window.location.pathname + window.location.search;
    // Remember where they were going so signing in returns them to it rather than to the home
    // page — losing the document you were opening is a small thing that feels like a big one.
    window.location.assign(signInUrl(state, back));
    return <div className="empty">正在前往登录…</div>;
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
  return state && state.password_login === false
    ? `/auth/start?next=${target}`
    : `/login?next=${target}`;
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
