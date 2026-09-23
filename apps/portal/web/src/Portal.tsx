/**
 * The platform home page.
 *
 * What it shows is deliberately narrow: who you are, which workspace you are acting in, and
 * which applications you can actually open. It does not list projects, jobs or files — those
 * live inside the applications, behind their own object-level authorization, and a home page
 * that aggregated them would be a way to read titles you have no right to read.
 *
 * There are no placeholder cards for applications that do not exist. A card here is a door
 * that opens.
 */
import { useCallback, useEffect, useState } from "react";

type App = {
  id: string;
  name: string;
  icon: string | null;
  launch_url: string;
  workspace_id: string | null;
  workspace_name: string | null;
};
type Workspace = { id: string; name: string; kind: string; role: string };
type Home = {
  platform: { name: string };
  user: { id: string; email: string; display_name: string | null; platform_role: string };
  workspaces: Workspace[];
  current_workspace_id: string | null;
  apps: App[];
  account_url: string;
};

type State =
  | { phase: "loading" }
  | { phase: "signed-out" }
  | { phase: "error"; message: string }
  | { phase: "ready"; home: Home };

function csrf(): string {
  const m = document.cookie.match(/(?:^|;\s*)dw_csrf=([^;]*)/);
  return m ? decodeURIComponent(m[1]) : "";
}

export function Portal() {
  const [state, setState] = useState<State>({ phase: "loading" });
  const [switching, setSwitching] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/portal/home", { credentials: "same-origin" });
      if (r.status === 401) return setState({ phase: "signed-out" });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        return setState({ phase: "error", message: body.message || `请求失败（HTTP ${r.status}）` });
      }
      setState({ phase: "ready", home: await r.json() });
    } catch {
      setState({ phase: "error", message: "无法连接到平台服务，请确认服务正在运行" });
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // Signing in is a full-page navigation, not a fetch: the browser has to follow the redirect
  // chain to the account service and back for the session cookie to be set on this origin.
  const signIn = () => {
    const here = window.location.pathname + window.location.search;
    window.location.href = `/auth/start?next=${encodeURIComponent(here)}`;
  };

  async function switchWorkspace(id: string) {
    setSwitching(id);
    try {
      const r = await fetch("/api/portal/workspace", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-DocWeave-CSRF": csrf() },
        body: JSON.stringify({ workspace_id: id }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        setState({ phase: "error", message: body.message || "切换工作空间失败" });
        return;
      }
      // Reload rather than patch locally: which applications are available depends on the
      // workspace, and the server is the only thing that knows the new answer.
      await load();
    } finally {
      setSwitching(null);
    }
  }

  if (state.phase === "loading") {
    return (
      <Shell title="工作平台">
        <div className="pt-skeleton" role="status" aria-live="polite">
          <span className="pt-sr">正在载入…</span>
          <div className="pt-skel-line" style={{ width: "40%" }} />
          <div className="pt-skel-grid">
            <div className="pt-skel-card" /><div className="pt-skel-card" />
          </div>
        </div>
      </Shell>
    );
  }

  if (state.phase === "signed-out") {
    return (
      <Shell title="工作平台">
        <div className="pt-empty">
          <h1 className="pt-h1">欢迎回来</h1>
          <p className="pt-lead">使用你的平台账号登录，查看可以使用的应用。</p>
          <button className="pt-btn pt-btn-primary" onClick={signIn} autoFocus>登录</button>
        </div>
      </Shell>
    );
  }

  if (state.phase === "error") {
    return (
      <Shell title="工作平台">
        <div className="pt-empty">
          <h1 className="pt-h1">出了点问题</h1>
          <p className="pt-lead pt-err">{state.message}</p>
          <button className="pt-btn" onClick={() => { setState({ phase: "loading" }); void load(); }}>
            重试
          </button>
        </div>
      </Shell>
    );
  }

  const { home } = state;
  const current = home.workspaces.find((w) => w.id === home.current_workspace_id);

  return (
    <Shell
      title={home.platform.name}
      right={
        <div className="pt-account">
          <a className="pt-link" href={`${home.account_url}/account`}>账户与安全</a>
          <span className="pt-who" title={home.user.email}>
            {home.user.display_name || home.user.email}
          </span>
        </div>
      }
    >
      <h1 className="pt-h1">你的应用</h1>

      {home.workspaces.length > 1 && (
        <div className="pt-ws" role="group" aria-label="切换工作空间">
          <span className="pt-ws-label">工作空间</span>
          {home.workspaces.map((w) => (
            <button
              key={w.id}
              className={"pt-chip" + (w.id === home.current_workspace_id ? " is-on" : "")}
              aria-pressed={w.id === home.current_workspace_id}
              disabled={switching !== null}
              onClick={() => void switchWorkspace(w.id)}
            >
              {switching === w.id ? "切换中…" : w.name}
            </button>
          ))}
        </div>
      )}

      {home.apps.length === 0 ? (
        <div className="pt-none">
          <p className="pt-lead">
            {home.workspaces.length === 0
              ? "你还不属于任何工作空间。请联系管理员邀请你加入后，这里会出现可以使用的应用。"
              : `${current ? `「${current.name}」` : "当前工作空间"}还没有启用任何应用。`}
          </p>
          <p className="pt-hint">你的账户和安全设置随时可用。</p>
        </div>
      ) : (
        <ul className="pt-grid">
          {home.apps.map((a) => (
            <li key={a.id}>
              <a className="pt-card" href={a.launch_url}>
                <span className="pt-icon" aria-hidden="true">{glyph(a.icon)}</span>
                <span className="pt-card-body">
                  <span className="pt-card-name">{a.name}</span>
                  {a.workspace_name && <span className="pt-card-ws">{a.workspace_name}</span>}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}

function glyph(icon: string | null) {
  if (icon === "doc") {
    return (
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.7">
        <path d="M6 3h8l4 4v14H6z" strokeLinejoin="round" />
        <path d="M14 3v4h4M9 12h6M9 16h6" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" strokeWidth="1.7">
      <rect x="4" y="4" width="7" height="7" rx="1.5" />
      <rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" />
      <rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  );
}

function Shell({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="pt">
      <header className="pt-top">
        <span className="pt-brand">{title}</span>
        {right}
      </header>
      <main className="pt-main">{children}</main>
    </div>
  );
}
