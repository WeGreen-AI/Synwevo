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
import { useCallback, useEffect, useState } from "react";
import { del, get, patch, post } from "@synwevo/auth-client";
import { useToast, useI18n } from "@synwevo/ui";
import { errorText } from "../pages/account/common";

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
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await get<Listing>("/api/auth/passkeys"));
      setErr(null);
    } catch (e) {
      setErr(errorText(e, t));
    }
  }, [t]);
  useEffect(() => { void load(); }, [load]);

  async function reauth(): Promise<boolean> {
    if (!password) {
      setErr(t("请输入你的口令"));
      return false;
    }
    await post("/api/auth/reauth", { password, scope: "mfa" });
    setPassword("");
    return true;
  }

  async function enrol() {
    setBusy(true);
    setErr(null);
    try {
      if (!(await reauth())) return;
      const options = await post<PublicKeyCredentialCreationOptionsJSON>(
        "/api/auth/passkeys/register/begin", {});
      // Built-in parser where available; the manual path keeps older browsers working.
      const publicKey = "parseCreationOptionsFromJSON" in PublicKeyCredential
        ? (PublicKeyCredential as unknown as {
            parseCreationOptionsFromJSON: (o: unknown) => PublicKeyCredentialCreationOptions;
          }).parseCreationOptionsFromJSON(options)
        : manualCreationOptions(options);
      const cred = await navigator.credentials.create({ publicKey }) as PublicKeyCredential | null;
      if (!cred) throw new Error(t("没有收到安全密钥的响应"));
      await post("/api/auth/passkeys/register/finish",
                 { credential: toJSON(cred), name: name.trim() || undefined });
      setName("");
      toast(t("安全密钥已登记"), "ok");
      await load();
    } catch (e) {
      // A cancelled prompt is not an error worth shouting about; anything else is.
      if (e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "AbortError")) {
        setErr(t("已取消，或安全密钥没有响应。"));
      } else {
        setErr(errorText(e, t));
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove(pk: Passkey) {
    setBusy(true);
    setErr(null);
    try {
      if (!(await reauth())) return;
      await del(`/api/auth/passkeys/${pk.id}`);
      toast(t("已删除"), "ok");
      await load();
    } catch (e) {
      setErr(errorText(e, t));
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
      setErr(errorText(e, t));
    }
  }

  if (!data) return <section className="card card-b col gap12"><h2>{t("安全密钥")}</h2>
    <div className="empty">{err ? <div role="alert">{err} <button className="btn sm" onClick={() => void load()}>{t("重试")}</button></div> : <span role="status">{t("加载中…")}</span>}</div></section>;

  return (
    <section className="card card-b col gap12">
      <h2>{t("安全密钥（Passkey）")}</h2>
      <p className="muted">{t("用设备上的指纹、面容或 PIN 登录。私钥留在你的设备上，仅对 {domain} 生效。", { domain: data.rp_id })}</p>

      {!supported && (
        <div className="notice warn">{t("这个浏览器不支持安全密钥。你仍然可以用口令和验证器应用登录。")}</div>
      )}
      {err && <div className="notice err" role="alert">{err}</div>}

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
                  <button className="btn danger sm" disabled={busy}
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
          <label className="col gap4">
            <span>{t("你的口令")}</span>
            <input className="input" type="password" aria-label={t("你的口令")} value={password} autoComplete="current-password"
                   onChange={(e) => setPassword(e.target.value)} />
            <span className="muted sm">{t("增加或删除登录方式需要重新确认身份——刚登录过也要，否则这个提示就等于没有。")}</span>
          </label>
          <div className="row gap12">
            <button className="btn primary" disabled={busy || !password} onClick={() => void enrol()}>
              {busy ? t("请按照设备提示操作…") : t("登记一个安全密钥")}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ plumbing */

type PublicKeyCredentialCreationOptionsJSON = Record<string, unknown>;

function b64urlToBuf(s: string): ArrayBuffer {
  const pad = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64url(b: ArrayBuffer): string {
  const bytes = new Uint8Array(b);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The pre-`parseCreationOptionsFromJSON` path: decode exactly the fields that must be buffers. */
function manualCreationOptions(o: PublicKeyCredentialCreationOptionsJSON): PublicKeyCredentialCreationOptions {
  const src = o as {
    challenge: string;
    user: { id: string; name: string; displayName: string };
    excludeCredentials?: { id: string; type: string; transports?: string[] }[];
  };
  return {
    ...(o as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64urlToBuf(src.challenge),
    user: { ...src.user, id: b64urlToBuf(src.user.id) },
    excludeCredentials: (src.excludeCredentials || []).map((c) => ({
      ...c, id: b64urlToBuf(c.id), type: "public-key" as const,
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
  };
}

/** The shape the server verifies. Shared with the sign-in page. */
export function toJSON(cred: PublicKeyCredential): Record<string, unknown> {
  const r = cred.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const out: Record<string, unknown> = {
    id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: { clientDataJSON: bufToB64url(r.clientDataJSON) } as Record<string, unknown>,
  };
  const resp = out.response as Record<string, unknown>;
  if (r.attestationObject) {
    resp.attestationObject = bufToB64url(r.attestationObject);
    if (typeof r.getTransports === "function") resp.transports = r.getTransports();
  }
  if (r.authenticatorData) resp.authenticatorData = bufToB64url(r.authenticatorData);
  if (r.signature) resp.signature = bufToB64url(r.signature);
  if (r.userHandle) resp.userHandle = bufToB64url(r.userHandle);
  return out;
}

export { b64urlToBuf, bufToB64url };
