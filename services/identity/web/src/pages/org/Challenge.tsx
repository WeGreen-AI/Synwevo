/** Human verification (Cloudflare Turnstile) for the forms the server challenges.
 *
 * The server decides: `/api/auth/state` says which actions are challenged (`always`) and which
 * only after a risk signal (`risk` — the sign-in form, once the server answered
 * `challenge_required`). Nothing here proves anything by itself: the token is sent as
 * `challenge_token` and the server verifies it with Cloudflare. The widget script is loaded
 * from Cloudflare's exact URL only when a challenged form is on screen (the documentation
 * forbids proxying or caching it), rendered explicitly so the language follows the page,
 * and removed when the form goes away.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, useSession } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";

export type ChallengeAction = "register" | "resend" | "forgot" | "login_email" | "login" | "topup";

interface TurnstileApi {
  render(container: HTMLElement, options: Record<string, unknown>): string;
  reset(id: string): void;
  remove(id: string): void;
}
declare global {
  interface Window { turnstile?: TurnstileApi; __synwevoTurnstileReady?: () => void }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit&onload=__synwevoTurnstileReady";
const LANGUAGE: Record<string, string> = { "zh-Hans": "zh-cn", "zh-Hant": "zh-tw", en: "en" };
let loading: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (loading) return loading;
  loading = new Promise<TurnstileApi>((resolve, reject) => {
    const timer = window.setTimeout(() => { loading = null; reject(new Error("challenge_script_timeout")); }, 20000);
    window.__synwevoTurnstileReady = () => { window.clearTimeout(timer); if (window.turnstile) resolve(window.turnstile); else reject(new Error("challenge_script_missing")); };
    const s = document.createElement("script");
    s.src = SCRIPT; s.async = true; s.defer = true;
    s.onerror = () => { window.clearTimeout(timer); loading = null; reject(new Error("challenge_script_blocked")); };
    document.head.appendChild(s);
  });
  return loading;
}

type Status = "idle" | "loading" | "ready" | "solved" | "expired" | "error";

/** Is this error the server asking for (another) solved challenge? The caller resets the widget. */
export function isChallengeError(e: unknown): e is ApiError {
  return e instanceof ApiError && (e.code === "challenge_required" || e.code === "challenge_failed");
}

export function useChallenge(action: ChallengeAction, opts: { armed?: boolean; enabled?: boolean } = {}) {
  const { state } = useSession();
  const { locale, t } = useI18n();
  const cfg = state?.challenge ?? null;
  const mode = cfg?.actions?.[action];
  const required = opts.enabled !== false && !!cfg && (mode === "always" || (mode === "risk" && opts.armed === true));
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const box = useRef<HTMLDivElement>(null);
  const widget = useRef<string | null>(null);

  useEffect(() => {
    if (!required || !cfg) { setToken(null); setStatus("idle"); return; }
    let cancelled = false;
    setStatus("loading");
    loadTurnstile().then(ts => {
      if (cancelled || !box.current) return;
      widget.current = ts.render(box.current, {
        sitekey: cfg.site_key, action, theme: "light", size: "flexible", language: LANGUAGE[locale] || "auto",
        retry: "auto", "refresh-expired": "auto", "refresh-timeout": "auto",
        callback: (tok: string) => { setToken(tok); setStatus("solved"); },
        "expired-callback": () => { setToken(null); setStatus("expired"); },
        "timeout-callback": () => { setToken(null); setStatus("error"); },
        "error-callback": () => { setToken(null); setStatus("error"); return true; },
      });
      setStatus("ready");
    }).catch(() => { if (!cancelled) setStatus("error"); });
    return () => {
      cancelled = true;
      if (widget.current) { try { window.turnstile?.remove(widget.current); } catch { /* already gone */ } widget.current = null; }
      setToken(null);
    };
  }, [required, cfg?.site_key, action, locale]);   // eslint-disable-line react-hooks/exhaustive-deps

  const reset = useCallback(() => {
    setToken(null);
    if (!widget.current) return;
    try { window.turnstile?.reset(widget.current); setStatus("ready"); } catch { setStatus("error"); }
  }, []);

  /** Spread into the request body; empty when this form is not challenged. */
  const body = useCallback((): Record<string, string> => (required && token ? { challenge_token: token } : {}), [required, token]);

  const element: ReactNode = required ? <div className="field challenge-field" data-status={status}>
    <span className="label">{t("人机验证")}</span>
    <div ref={box} className="challenge-widget" />
    {status === "loading" && <p className="tiny muted" role="status">{t("正在加载人机验证…")}</p>}
    {status === "expired" && <p className="tiny muted" role="status">{t("人机验证已过期，正在刷新。")}</p>}
    {status === "error" && <p className="notice warn" role="alert">{t("人机验证无法加载或未完成。请检查网络或浏览器的内容拦截设置后重试。")} <button type="button" className="btn ghost sm" onClick={reset}>{t("重试")}</button></p>}
  </div> : null;

  return { required, token, status, body, reset, element };
}
