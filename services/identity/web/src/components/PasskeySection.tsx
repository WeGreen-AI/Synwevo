/**
 * Passkeys, in the account and security centre.
 *
 * The browser does the ceremony; this only moves the options and the response between the
 * account service and `navigator.credentials`. Nothing secret passes through here — the private
 * key never leaves the authenticator, which is the whole point of the mechanism.
 *
 * A credential is bound to the domain it was registered under, so one registered before a
 * domain change simply will not be offered again. That is shown as a fact on the row rather
 * than left as a key that mysteriously stops working.
 */
import { useCallback, useState } from "react";
import { del, get, patch, post } from "@synwevo/auth-client";
import { useToast, useI18n } from "@synwevo/ui";
import { MfaReauthPrompt, mfaErrorText, requiresMfaReauth, useMfaStateRefresh } from "./MfaSection";
import { manualCreationOptions, toJSON, type PublicKeyCredentialCreationOptionsJSON } from "../lib/webauthn";
export { b64urlToBuf, bufToB64url, toJSON } from "../lib/webauthn";

type Passkey = {
  id: string;
  name: string;
  created_at: number;
  last_used_at: number | null;
  rp_id: string;
  backed_up: boolean;
  uv_capable: boolean;
  transports: string[];
  usable_here: boolean;
};
type Listing = { items: Passkey[]; reauth_fresh: boolean; rp_id: string; origins: string[] };

const supported = typeof window !== "undefined"
  && typeof window.PublicKeyCredential !== "undefined";

export default function PasskeySection({ timezone = "UTC" }: { timezone?: string }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [data, setData] = useState<Listing | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [name, setName] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await get<Listing>("/api/auth/passkeys"));
      setErr(null);
    } catch (e) {
      setErr(mfaErrorText(e, t));
    }
  }, [t]);
  useMfaStateRefresh(load);

  async function enrol() {
    if (!data?.reauth_fresh || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const options = await post<PublicKeyCredentialCreationOptionsJSON>(
        "/api/auth/passkeys/register/begin", {});
      // Built-in parser where available; the manual path keeps older browsers working.
      const publicKey = "parseCreationOptionsFromJSON" in PublicKeyCredential
        ? (PublicKeyCredential as unknown as {
            parseCreationOptionsFromJSON: (o: unknown) => PublicKeyCredentialCreationOptions;
          }).parseCreationOptionsFromJSON(options)
        : manualCreationOptions(options);
      const cred = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null;
      if (!cred) { setErr(t("没有收到安全密钥的响应")); return; }
      await post("/api/auth/passkeys/register/finish",
                 { credential: toJSON(cred), name: name.trim() || undefined });
      setName("");
      toast(t("安全密钥已登记"), "ok");
      await load();
    } catch (e) {
      if (requiresMfaReauth(e)) setData(value => value && { ...value, reauth_fresh: false });
      // A cancelled prompt is not an error worth shouting about; anything else is.
      if (e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "AbortError")) {
        setErr(t("已取消，或安全密钥没有响应。"));
      } else {
        setErr(mfaErrorText(e, t));
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove(pk: Passkey) {
    if (!data?.reauth_fresh || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await del(`/api/auth/passkeys/${pk.id}`);
      toast(t("已删除"), "ok");
      await load();
    } catch (e) {
      if (requiresMfaReauth(e)) setData(value => value && { ...value, reauth_fresh: false });
      setErr(mfaErrorText(e, t));
    } finally {
      setBusy(false);
    }
  }

  async function rename(pk: Passkey, next: string) {
    if (!next.trim() || next === pk.name) return;
    try {
      await patch(`/api/auth/passkeys/${pk.id}`, { name: next.trim() });
      await load();
    } catch (e) {
      setErr(mfaErrorText(e, t));
    }
  }

  if (!data) return <section className="card card-b col gap12"><h2>{t("安全密钥")}</h2>
    <div className="empty">{err ? <div role="alert">{err} <button className="btn sm" onClick={() => void load()}>{t("重试")}</button></div> : <span role="status">{t("加载中…")}</span>}</div></section>;

  return (
    <section className="card card-b col gap12">
      <h2>{t("安全密钥（Passkey）")}</h2>
      <p className="muted">{t("用设备上的指纹、面容或 PIN 登录。私钥留在你的设备上，仅对 {domain} 生效。", { domain: data.rp_id })}</p>

      {!supported && (
        <div className="notice warn">{t("当前浏览器不能登记安全密钥。请使用支持安全密钥的浏览器。")}</div>
      )}
      {err && <div className="notice err" role="alert">{err}</div>}
      {!data.reauth_fresh && <MfaReauthPrompt onVerified={load} />}

      {data.items.length > 0 && (
        <div className="account-table"><table className="tbl">
          <thead><tr><th>{t("名称")}</th><th>{t("登记时间")}</th><th>{t("最近使用")}</th><th>{t("状态")}</th><th /></tr></thead>
          <tbody>
            {data.items.map((pk) => (
              <tr key={pk.id}>
                <td>
                  <input className="input sm" defaultValue={pk.name} aria-label={t("安全密钥名称")}
                         onBlur={(e) => void rename(pk, e.target.value)} />
                </td>
                <td className="muted sm">{new Date(pk.created_at * 1000).toLocaleDateString(locale, { timeZone: timezone })}</td>
                <td className="muted sm">
                  {pk.last_used_at ? new Date(pk.last_used_at * 1000).toLocaleString(locale, { timeZone: timezone }) : t("从未")}
                </td>
                <td className="sm">
                  {pk.usable_here
                    ? <>{pk.backed_up ? t("已同步到多个设备") : t("仅此设备")}</>
                    : <span className="badge warn" title={t("登记于 {domain}", { domain: pk.rp_id })}>{t("域名已变更，无法使用")}</span>}
                </td>
                <td>
                  <button className="btn danger sm" disabled={busy || !data.reauth_fresh}
                          onClick={() => void remove(pk)}>{t("删除")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}

      {supported && (
        <div className="col gap12">
          <label className="col gap4">
            <span>{t("名称（可选）")}</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)}
                   placeholder={t("例如：工作手机")} maxLength={60} />
          </label>
          <div className="row gap12">
            <button data-reauth-next className="btn primary" disabled={busy || !data.reauth_fresh} onClick={() => void enrol()}>
              {busy ? t("请按照设备提示操作…") : t("登记一个安全密钥")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
