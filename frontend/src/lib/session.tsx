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
  session?: { id: string; amr: string };
}

export interface AuthState {
  registration: "open" | "invite" | "closed";
  bootstrap_required: boolean;
  password_login: boolean;
}

interface Ctx {
  me: Me | null | undefined;
  state: AuthState | null;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
  /** True when the deployment has no account system at all (DOCWEAVE_UI_AUTH is none/basic). */
  disabled: boolean;
}

const SessionCtx = createContext<Ctx>({ me: undefined, state: null, refresh: async () => {}, signOut: async () => {}, disabled: true });

export const useSession = () => useContext(SessionCtx);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [state, setState] = useState<AuthState | null>(null);
  const [disabled, setDisabled] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setState(await get<AuthState>("/api/auth/state"));
    } catch {
      // No account system in this deployment (older auth modes). The app works as before.
      setDisabled(true);
      setMe(null);
      return;
    }
    try {
      setMe(await get<Me>("/api/auth/me"));
    } catch (e) {
      // 401 is the ordinary signed-out answer, not a failure worth surfacing.
      setMe(e instanceof ApiError && e.status === 401 ? null : null);
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

  return <SessionCtx.Provider value={{ me, state, refresh, signOut, disabled }}>{children}</SessionCtx.Provider>;
}

/** Wrap routes that need an account. */
export function RequireSession({ children }: { children: React.ReactNode }) {
  const { me, disabled } = useSession();
  if (disabled) return <>{children}</>;          // deployment without accounts: unchanged behaviour
  if (me === undefined) return <div className="empty">加载中…</div>;
  if (me === null) {
    const back = window.location.pathname + window.location.search;
    // Remember where they were going so signing in returns them to it rather than to the home
    // page — losing the document you were opening is a small thing that feels like a big one.
    window.location.assign(`/login?next=${encodeURIComponent(back)}`);
    return <div className="empty">正在前往登录…</div>;
  }
  return <>{children}</>;
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
