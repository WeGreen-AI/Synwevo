/**
 * External sign-in methods (Google, Microsoft, …) linked to this account.
 *
 * Linking and unlinking are security actions: both need a fresh re-authentication, exactly like
 * adding or removing a passkey, and the server refuses to unlink the last way into the account.
 * Providers appear only when the deployment has configured them; a linked identity whose
 * provider is no longer configured is still listed, marked as unavailable, and can be removed.
 */
import { useCallback, useEffect, useState } from "react";
import { del, get, post } from "@synwevo/auth-client";
import { useI18n, useToast } from "@synwevo/ui";
import { errorText } from "../pages/account/common";
import { ProviderMark } from "../pages/Login";

type Identity = { id: string; provider: string; label: string; email: string | null; email_verified: boolean;
  display_name: string | null; created_at: number; last_used_at: number | null; available: boolean };
type Listing = { items: Identity[]; providers: { id: string; label: string }[]; reauth_fresh: boolean; other_methods: number };

export default function LinkedIdentities({ timezone = "UTC" }: { timezone?: string }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [data, setData] = useState<Listing | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [linkedNotice, setLinkedNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setData(await get<Listing>("/api/auth/external/identities")); setErr(null); }
    catch (e) { setErr(errorText(e, t)); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const linked = new URLSearchParams(window.location.search).get("linked");
    if (!linked) return;
    setLinkedNotice(linked);
    const url = new URL(window.location.href); url.searchParams.delete("linked");
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }, []);

  async function reauth(): Promise<boolean> {
    if (data?.reauth_fresh) return true;
    if (!password) { setErr(t("请输入你的口令")); return false; }
    await post("/api/auth/reauth", { password, scope: "mfa" });
    setPassword("");
    return true;
  }
  async function link(id: string) {
    setBusy(true); setErr(null);
    try {
      if (!(await reauth())) return;
      const { url } = await post<{ url: string }>(`/api/auth/external/${encodeURIComponent(id)}/link`, { locale });
      window.location.assign(url);
    } catch (e) { setErr(errorText(e, t)); setBusy(false); }
  }
  async function unlink(row: Identity) {
    setBusy(true); setErr(null);
    try {
      if (!(await reauth())) return;
      await del(`/api/auth/external/identities/${encodeURIComponent(row.id)}`);
      toast(t("已解除关联"), "ok");
      await load();
    } catch (e) { setErr(errorText(e, t)); } finally { setBusy(false); }
  }

  if (!data) return <section className="card card-b col gap12"><h2>{t("外部登录方式")}</h2>
    <div className="empty">{err ? <div role="alert">{err} <button className="btn sm" onClick={() => void load()}>{t("重试")}</button></div> : <span role="status">{t("加载中…")}</span>}</div></section>;
  if (!data.providers.length && !data.items.length) return null;
  const linkable = data.providers.filter(p => !data.items.some(i => i.provider === p.id));
  return <section className="card card-b col gap12">
    <h2>{t("外部登录方式")}</h2>
    <p className="muted">{t("用已关联的 Google 或 Microsoft 等账户登录。关联按提供商的稳定标识进行，不会仅凭邮箱合并账户；两步验证仍然适用。")}</p>
    {linkedNotice && <div className="notice ok" role="status">{t("已关联 {label}。", { label: data.items.find(i => i.provider === linkedNotice)?.label || linkedNotice })}</div>}
    {err && <div className="notice err" role="alert">{err}</div>}
    {data.items.length > 0 && <div className="account-table"><table className="tbl">
      <thead><tr><th>{t("提供商")}</th><th>{t("提供商中的邮箱")}</th><th>{t("关联时间")}</th><th>{t("最近使用")}</th><th /></tr></thead>
      <tbody>{data.items.map(row => <tr key={row.id}>
        <td><span className="row gap4"><ProviderMark id={row.provider} />{row.label}{!row.available && <span className="badge warn">{t("已停用")}</span>}</span></td>
        <td className="sm">{row.email || "—"}{row.email && !row.email_verified && <span className="badge warn" style={{ marginLeft: 6 }}>{t("未验证")}</span>}</td>
        <td className="muted sm">{new Date(row.created_at * 1000).toLocaleDateString(locale, { timeZone: timezone })}</td>
        <td className="muted sm">{row.last_used_at ? new Date(row.last_used_at * 1000).toLocaleString(locale, { timeZone: timezone }) : t("从未")}</td>
        <td><button className="btn danger sm" disabled={busy} onClick={() => void unlink(row)}>{t("解除关联")}</button></td>
      </tr>)}</tbody></table></div>}
    {data.other_methods === 0 && data.items.length === 1 && <p className="notice info">{t("这是账户唯一的登录方式。请先设置口令或添加安全密钥，再解除关联。")}</p>}
    {!data.reauth_fresh && (linkable.length > 0 || data.items.length > 0) && <label className="col gap4">
      <span>{t("你的口令")}</span>
      <input className="input" type="password" aria-label={t("你的口令")} value={password} autoComplete="current-password" onChange={e => setPassword(e.target.value)} />
      <span className="muted sm">{t("增加或删除登录方式需要重新确认身份——刚登录过也要，否则这个提示就等于没有。")}</span>
    </label>}
    {linkable.length > 0 && <div className="row gap12 wrap">
      {linkable.map(p => <button key={p.id} type="button" className="btn flow-provider" disabled={busy || (!data.reauth_fresh && !password)} onClick={() => void link(p.id)}><ProviderMark id={p.id} />{t("关联 {label}", { label: p.label })}</button>)}
    </div>}
  </section>;
}
