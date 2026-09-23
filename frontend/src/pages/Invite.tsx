import { useEffect, useRef, useState } from "react";
import { get, post } from "../lib/api";
import { fmtTime } from "../lib/ui";
import { useSession } from "../lib/session";

interface Preview {
  state: "pending" | "accepted" | "revoked" | "expired";
  email: string;
  role: string;
  role_label: string;
  workspace: string | null;
  inviter: string | null;
  expires_at: number;
  needs_account: boolean;
  signed_in_as?: string;
}

const DEAD: Record<string, { title: string; body: string }> = {
  accepted: { title: "这个邀请已经用过了", body: "如果你已经加入过，直接登录即可；否则请让邀请人重新发送一封。" },
  revoked: { title: "这个邀请已被撤销", body: "邀请人取消了这次邀请，或者重新发了一封新的。请使用最新的邀请邮件。" },
  expired: { title: "这个邀请已过期", body: "为了安全，邀请链接有有效期。请让邀请人重新发送。" },
};

/** The page the emailed link lands on.
 *
 *  Opening it does nothing on its own — it reads the invitation and stops. Joining requires
 *  pressing a button, so a mail scanner that follows the URL cannot accept on someone's behalf.
 */
export default function Invite() {
  const token = new URLSearchParams(window.location.search).get("token") || "";
  const { refresh } = useSession();
  const [pv, setPv] = useState<Preview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const errRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!token) { setLoadErr("这个链接缺少邀请令牌，请直接使用邮件里的完整链接。"); return; }
    get<Preview>(`/api/auth/invitations/preview?token=${encodeURIComponent(token)}`)
      .then(setPv)
      .catch((e) => setLoadErr(e.message));
  }, [token]);

  const accept = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setErr(null);
    try {
      await post("/api/auth/invitations/accept", {
        token,
        password: pv?.needs_account ? password : undefined,
        display_name: pv?.needs_account ? (name || null) : undefined,
      });
      await refresh();
      window.location.assign("/");
    } catch (e2: any) {
      setErr(e2?.message || "接受邀请失败，请重试。");
      setPassword("");
      requestAnimationFrame(() => errRef.current?.focus());
    } finally {
      setBusy(false);
    }
  };

  if (loadErr) {
    return (
      <div className="authpage"><div className="authcard">
        <div className="authbrand"><span className="logo">W</span>DocWeave</div>
        <h1>邀请链接无效</h1>
        <p className="authlede">{loadErr}</p>
        <a className="btn" href="/login">前往登录</a>
      </div></div>
    );
  }
  if (!pv) return <div className="empty">加载中…</div>;

  if (pv.state !== "pending") {
    const d = DEAD[pv.state];
    return (
      <div className="authpage"><div className="authcard">
        <div className="authbrand"><span className="logo">W</span>DocWeave</div>
        <h1>{d.title}</h1>
        <p className="authlede">{d.body}</p>
        <a className="btn primary" href="/login">前往登录</a>
      </div></div>
    );
  }

  // Signed in as someone else. Said here rather than letting the server refuse after they have
  // filled in a form — and the way out is offered, because "sign out first" with no button is
  // an instruction, not a solution.
  const wrongAccount = pv.signed_in_as && pv.signed_in_as.toLowerCase() !== pv.email.toLowerCase();

  return (
    <div className="authpage">
      <form className="authcard" onSubmit={accept} noValidate>
        <div className="authbrand"><span className="logo">W</span>DocWeave</div>
        <h1>加入「{pv.workspace}」</h1>
        <p className="authlede">
          {pv.inviter ? <><b>{pv.inviter}</b> 邀请 </> : "邀请 "}
          <b>{pv.email}</b> 以「{pv.role_label}」身份加入这个工作空间。
          <br />链接有效期至 {fmtTime(pv.expires_at)}。
        </p>

        {err && <div className="notice err autherr" role="alert" tabIndex={-1} ref={errRef}>{err}</div>}

        {wrongAccount ? (
          <>
            <div className="notice warn">
              你当前登录的是 <b>{pv.signed_in_as}</b>，而这个邀请是发给 <b>{pv.email}</b> 的。
              请先退出当前账户，再用这个链接加入。
            </div>
            <button type="button" className="btn primary authsubmit"
              onClick={async () => { await post("/api/auth/logout", {}); window.location.reload(); }}>
              退出当前账户
            </button>
          </>
        ) : (
          <>
            {pv.needs_account ? (
              <>
                <label className="field">
                  <span>你的名称<span className="muted">（可选）</span></span>
                  <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" />
                </label>
                <label className="field">
                  <span>设置口令</span>
                  <div className="authpw">
                    <input className="input" type={show ? "text" : "password"} value={password}
                      onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required />
                    <button type="button" className="btn ghost sm authshow" onClick={() => setShow((s) => !s)}
                      aria-pressed={show} aria-label={show ? "隐藏口令" : "显示口令"}>{show ? "隐藏" : "显示"}</button>
                  </div>
                  <span className="tiny muted">至少 15 个字符。长句子比复杂符号更好记也更安全。</span>
                </label>
                {/* The address is fixed by the invitation, so it is shown, not asked for. */}
                <p className="tiny muted">账户邮箱为 {pv.email}，由邀请决定，无法在这里修改。</p>
              </>
            ) : !pv.signed_in_as ? (
              // Existing account, nobody signed in. Joining has to prove control of that
              // account, so say so now instead of letting the server refuse after a click —
              // and carry the invitation through the login so they land back here.
              <>
                <p className="tiny muted">这个邮箱已有 DocWeave 账户。请先登录，登录后会自动回到这个邀请。</p>
                <a className="btn primary authsubmit"
                  href={`/login?next=${encodeURIComponent(`/invite?token=${token}`)}`}>登录并继续</a>
              </>
            ) : (
              <p className="tiny muted">点击下面的按钮，把 {pv.email} 加入这个空间。</p>
            )}
            {(pv.needs_account || pv.signed_in_as) && (
              <button className="btn primary authsubmit" type="submit" disabled={busy}>
                {busy ? "处理中…" : "接受邀请并加入"}
              </button>
            )}
          </>
        )}
      </form>
    </div>
  );
}
