import { useCallback, useEffect, useRef, useState } from "react";
import QRCode from "qrcode";
import { get, post } from "@synwevo/auth-client";
import { useToast, useI18n } from "@synwevo/ui";
import { errorText } from "../pages/account/common";

interface State {
  totp: boolean;
  passkeys: number;
  recovery_remaining: number;
  key_external: boolean;
  reauth_fresh: boolean;
}

interface Begin { secret: string; uri: string; digits: number; period: number }

/** Second-factor management on the account page.
 *
 *  Three flows, each with the same shape: prove who you are, then do the thing, then be told
 *  exactly what changed. The recovery codes are shown once and the panel says so before showing
 *  them, because "copy these now" after the fact is a worse moment to find out.
 */
export default function MfaSection() {
  const { t } = useI18n();
  const [st, setSt] = useState<State | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [password, setPassword] = useState("");
  const [needPw, setNeedPw] = useState(false);
  const [begin, setBegin] = useState<Begin | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [disabling, setDisabling] = useState(false);
  const toast = useToast();
  const errRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    get<State>("/api/auth/mfa").then((next) => { setSt(next); setErr(null); }).catch((e) => setErr(errorText(e, t)));
  }, [t]);
  useEffect(load, [load]);

  useEffect(() => {
    if (!begin) { setQr(null); return; }
    // Rendered locally. The seed never goes to a third-party chart service — that would hand
    // the shared secret to someone else entirely.
    QRCode.toString(begin.uri, { type: "svg", margin: 1, width: 180 })
      .then(setQr).catch(() => setQr(null));
  }, [begin]);

  const fail = (e: any) => {
    setErr(errorText(e, t));
    requestAnimationFrame(() => errRef.current?.focus());
  };

  const run = async (fn: () => Promise<void>) => {
    if (busy) return;
    setBusy(true); setErr(null);
    try { await fn(); } catch (e: any) {
      if (e?.code === "reauth_required" || e?.status === 403) setNeedPw(true);
      fail(e);
    } finally { setBusy(false); }
  };

  const reauth = () => run(async () => {
    await post("/api/auth/mfa/reauth", { password });
    setPassword(""); setNeedPw(false);
    load();
  });

  const startEnrol = () => run(async () => {
    setBegin(await post<Begin>("/api/auth/mfa/totp/begin", {}));
  });

  const confirm = () => run(async () => {
    const r = await post<{ recovery_codes: string[] }>("/api/auth/mfa/totp/confirm", { code });
    setCode(""); setBegin(null);
    setCodes(r.recovery_codes);
    toast(t("动态验证码已启用"), "ok");
    load();
  });

  const disable = () => run(async () => {
    await post("/api/auth/mfa/totp/disable", { code });
    setCode(""); setDisabling(false);
    toast(t("动态验证码已关闭"), "ok");
    load();
  });

  const regen = () => run(async () => {
    const r = await post<{ recovery_codes: string[] }>("/api/auth/mfa/recovery/regenerate", {});
    setCodes(r.recovery_codes);
    toast(t("已生成新的恢复码，旧的全部失效"), "ok");
    load();
  });

  if (!st) return <section className="card card-b col gap12 mt12"><h2 className="secttl">{t("两步验证")}</h2>{err ? <div className="notice err" role="alert">{err} <button className="btn sm" onClick={load}>{t("重试")}</button></div> : <div role="status">{t("加载中…")}</div>}</section>;

  return (
    <section className="card card-b col gap12 mt12">
      <div className="row">
        <h2 className="secttl grow">{t("两步验证")}</h2>
        {st.totp
          ? <span className="badge ok">{t("已启用（动态验证码）")}</span>
          : <span className="badge">{t("未启用")}</span>}
      </div>

      {err && <div className="notice err" role="alert" tabIndex={-1} ref={errRef}>{err}</div>}

      {/* Shown once, above everything, because it is the thing people lose. */}
      {codes && (
        <div className="notice warn col gap8">
          <b>{t("请立刻保存这些恢复码——它们只显示这一次。")}</b>
          <div>{t("每个只能用一次。手机丢失时，用其中一个即可登录。重新生成会让以上全部失效。")}</div>
          <ul className="reccodes">{codes.map((c) => <li key={c} className="mono">{c}</li>)}</ul>
          <div className="row">
            <button className="btn sm" onClick={async () => {
              try { await navigator.clipboard.writeText(codes.join("\n")); toast(t("恢复码已复制到剪贴板"), "ok"); }
              catch { toast(t("复制失败，请手动保存恢复码"), "err"); }
            }}>{t("复制全部")}</button>
            <button className="btn sm ghost" onClick={() => setCodes(null)}>{t("我已保存")}</button>
          </div>
        </div>
      )}

      {needPw && (
        <div className="notice info col gap8">
          <b>{t("这是敏感操作，请重新输入口令确认。")}</b>
          <div className="row gap4">
            <input aria-label={t("确认两步验证操作的口令")} className="input grow" type="password" value={password} autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); reauth(); } }} />
            <button className="btn primary" onClick={reauth} disabled={busy || !password}>{t("确认")}</button>
          </div>
        </div>
      )}

      {!st.totp && !begin && (
        <>
          <p className="muted">{t("启用后，登录除了口令还需要手机上的 6 位动态验证码。支持任意标准验证器应用。")}</p>
          <div><button className="btn primary" onClick={startEnrol} disabled={busy}>{t("启用动态验证码")}</button></div>
        </>
      )}

      {begin && (
        <div className="col gap8">
          <p className="muted">{t("用验证器应用扫描二维码，或手动输入下面的密钥，然后填入应用显示的 6 位数字。")}</p>
          <div className="row gap12 wrap" style={{ alignItems: "flex-start" }}>
            {qr && <div className="qrbox" aria-label={t("验证器二维码")} dangerouslySetInnerHTML={{ __html: qr }} />}
            <div className="col gap8">
              <div>
                <div className="tiny muted">{t("手动输入密钥（不方便扫码时用）")}</div>
                <code className="mono seedbox">{begin.secret}</code>
              </div>
              <label className="field" style={{ maxWidth: 200 }}>
                <span>{t("应用显示的 6 位数字")}</span>
                <input className="input mono" value={code} inputMode="numeric" autoComplete="one-time-code"
                  maxLength={6} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); confirm(); } }} />
              </label>
              <div className="row gap4">
                <button className="btn primary" onClick={confirm} disabled={busy || code.length !== 6}>{t("确认启用")}</button>
                <button className="btn ghost" onClick={() => { setBegin(null); setCode(""); }}>{t("取消")}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {st.totp && (
        <>
          <dl className="kv">
            <dt>{t("可用恢复码")}</dt>
            <dd>
              {t("{count} 个", { count: st.recovery_remaining })}
              {st.recovery_remaining <= 2 && <span className="badge warn" style={{ marginLeft: 6 }}>{t("快用完了")}</span>}
            </dd>
          </dl>
          <div className="row gap4 wrap">
            <button className="btn" onClick={regen} disabled={busy}>{t("重新生成恢复码")}</button>
            {!disabling
              ? <button className="btn danger" onClick={() => setDisabling(true)}>{t("关闭动态验证码")}</button>
              : (
                <div className="row gap4">
                  {/* A current code is required to turn it off, not just to turn it on. */}
                  <input className="input mono" style={{ width: 120 }} value={code} inputMode="numeric"
                    aria-label={t("关闭两步验证的当前验证码")} maxLength={6} placeholder={t("当前验证码")} autoComplete="one-time-code"
                    onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
                  <button className="btn danger" onClick={disable} disabled={busy || code.length !== 6}>{t("确认关闭")}</button>
                  <button className="btn ghost" onClick={() => { setDisabling(false); setCode(""); }}>{t("取消")}</button>
                </div>
              )}
          </div>
          <p className="tiny muted">{t("关闭两步验证需要输入一个当前有效的验证码——只有口令还不够。")}</p>
        </>
      )}


    </section>
  );
}
