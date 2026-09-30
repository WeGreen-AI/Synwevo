import { useEffect, useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { registerMessages, t } from "@synwevo/ui";
import { b64urlToBuf, toJSON } from "./webauthn";

registerMessages({
  "正在读取可用的身份验证方式…": { en: "Loading your verification methods…", "zh-Hant": "正在讀取可用的身分驗證方式…" },
  "重新读取验证方式": { en: "Reload verification methods", "zh-Hant": "重新讀取驗證方式" },
  "验证只对这类操作有效，并在几分钟后失效。": { en: "Verification applies only to this kind of action and expires after a few minutes.", "zh-Hant": "驗證只對這類操作有效，並在幾分鐘後失效。" },
  "连续输错口令会结束当前登录。": { en: "Repeated incorrect passwords will end this session.", "zh-Hant": "連續輸錯密碼會結束目前登入。" },
  "使用安全密钥确认并继续": { en: "Confirm with a passkey and continue", "zh-Hant": "使用安全金鑰確認並繼續" },
  "此账户没有可用的近期身份验证方式。请在账户页检查已登记的验证方式。": { en: "No verification method is available for this account. Review your registered methods on the account page.", "zh-Hant": "此帳戶沒有可用的近期身分驗證方式。請在帳戶頁檢查已登記的驗證方式。" },
  "当前浏览器不能使用安全密钥，请换用支持安全密钥的浏览器。": { en: "This browser cannot use passkeys. Use a browser that supports them.", "zh-Hant": "目前瀏覽器無法使用安全金鑰，請改用支援安全金鑰的瀏覽器。" },
  "已取消身份验证，操作尚未执行。可以重新尝试。": { en: "Verification was cancelled. The operation has not run. You can try again.", "zh-Hant": "已取消身分驗證，操作尚未執行。可以重新嘗試。" },
  "没有收到安全密钥的响应。": { en: "No response received from the passkey.", "zh-Hant": "沒有收到安全金鑰的回應。" },
});

export class ReauthClientError extends Error {}

export type ReauthMethod = "password" | "passkey_uv";
type Options = { scope: string; methods: ReauthMethod[] };

/** Capability is read for the signed-in account; a registered credential is never proof. */
export function useReauthMethods(scope: string | null) {
  const [state, setState] = useState<{ scope: string | null; methods: ReauthMethod[] | null; error: unknown }>({ scope: null, methods: null, error: null });
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let live = true;
    setState({ scope, methods: null, error: null });
    if (scope) get<Options>(`/api/auth/reauth/options?scope=${encodeURIComponent(scope)}`)
      .then((out) => { if (live) setState({ scope, methods: out.methods, error: null }); })
      .catch((error) => { if (live) setState({ scope, methods: null, error }); });
    return () => { live = false; };
  }, [scope, revision]);
  const current = state.scope === scope ? state : { methods: null, error: null };
  return { ...current, reload: () => setRevision((value) => value + 1) };
}

/** This ceremony only refreshes the current session's named purpose, never signs in anew. */
export async function reauthenticateWithPasskey(scope: string, signal?: AbortSignal): Promise<void> {
  if (!window.isSecureContext || typeof PublicKeyCredential === "undefined" || !navigator.credentials?.get) {
    throw new ReauthClientError(t("当前浏览器不能使用安全密钥，请换用支持安全密钥的浏览器。"));
  }
  try {
    signal?.throwIfAborted();
    const options = await post<Record<string, unknown>>("/api/auth/reauth/passkey/begin", { scope });
    signal?.throwIfAborted();
    const publicKey = "parseRequestOptionsFromJSON" in PublicKeyCredential
      ? (PublicKeyCredential as unknown as { parseRequestOptionsFromJSON: (value: unknown) => PublicKeyCredentialRequestOptions }).parseRequestOptionsFromJSON(options)
      : { ...(options as unknown as PublicKeyCredentialRequestOptions), challenge: b64urlToBuf(options.challenge as string),
          allowCredentials: (options.allowCredentials as { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] }[] | undefined)
            ?.map((credential) => ({ ...credential, id: b64urlToBuf(credential.id) })) };
    const credential = await navigator.credentials.get({ publicKey, signal }) as PublicKeyCredential | null;
    if (!credential) throw new ReauthClientError(t("没有收到安全密钥的响应。"));
    signal?.throwIfAborted();
    await post("/api/auth/reauth/passkey/finish", { scope, credential: toJSON(credential) });
  } catch (error) {
    if (error instanceof DOMException && ["NotAllowedError", "AbortError"].includes(error.name)) {
      throw new ReauthClientError(t("已取消身份验证，操作尚未执行。可以重新尝试。"));
    }
    throw error;
  }
}
