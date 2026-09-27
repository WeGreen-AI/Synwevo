import { useState } from "react";
import { get } from "@synwevo/auth-client";
import { fmtTime, registerMessages, useI18n } from "@synwevo/ui";
import { useAdminData } from "./data";

registerMessages({
  "登录方式": { en: "Sign-in methods", "zh-Hant": "登入方式" },
  "外部账户登录（OpenID Connect）": { en: "External account sign-in (OpenID Connect)", "zh-Hant": "外部帳戶登入（OpenID Connect）" },
  "每个提供商在本部署的配置状态。密钥不会显示，也不能在这里填写：它在账户服务的环境配置里，改动后重启账户服务生效。": {
    en: "Each provider's configuration state in this deployment. The secret is never shown and cannot be entered here: it lives in the account service's environment configuration and takes effect after the account service restarts.",
    "zh-Hant": "每個提供者在本部署的設定狀態。密鑰不會顯示，也不能在這裡填寫：它在帳戶服務的環境設定裡，改動後重新啟動帳戶服務生效。" },
  "已启用": { en: "Enabled", "zh-Hant": "已啟用" },
  "未配置": { en: "Not configured", "zh-Hant": "未設定" },
  "回调地址（在提供商控制台登记这一条）": { en: "Callback URL (register exactly this at the provider)", "zh-Hant": "回呼位址（在提供商主控台登記這一條）" },
  "权限范围": { en: "Scopes", "zh-Hant": "權限範圍" },
  "发行者": { en: "Issuer", "zh-Hant": "發行者" },
  "客户端 ID": { en: "Client ID", "zh-Hant": "用戶端 ID" },
  "客户端密钥": { en: "Client secret", "zh-Hant": "用戶端密鑰" },
  "已填写": { en: "Present", "zh-Hant": "已填寫" },
  "未填写": { en: "Missing", "zh-Hant": "未填寫" },
  "已关联账户": { en: "Linked accounts", "zh-Hant": "已連結帳戶" },
  "发现文档": { en: "Discovery document", "zh-Hant": "探索文件" },
  "尚未检查": { en: "Not checked yet", "zh-Hant": "尚未檢查" },
  "可达（{at}）": { en: "Reachable ({at})", "zh-Hant": "可連線（{at}）" },
  "不可达：{error}（{at}）": { en: "Unreachable: {error} ({at})", "zh-Hant": "無法連線：{error}（{at}）" },
  "立即检查连接": { en: "Check connection now", "zh-Hant": "立即檢查連線" },
  "正在检查…": { en: "Checking…", "zh-Hant": "正在檢查…" },
  "缺少的配置项": { en: "Missing settings", "zh-Hant": "缺少的設定項" },
  "配置项名称": { en: "Setting names", "zh-Hant": "設定項名稱" },
  "本部署没有启用外部账户登录模块。": { en: "External account sign-in is not enabled in this deployment.", "zh-Hant": "本部署沒有啟用外部帳戶登入模組。" },
  "多租户发行者：按令牌中的 tenant 校验": { en: "Multi-tenant issuer: validated per token tenant", "zh-Hant": "多租戶發行者：依權杖中的 tenant 驗證" },
  "加载中…": { en: "Loading…", "zh-Hant": "載入中…" },
  "重试": { en: "Retry", "zh-Hant": "重試" },
  "刷新": { en: "Refresh", "zh-Hant": "重新整理" },
});

type Discovery = { checked: false } | { checked: true; ok: true; issuer?: string; at: number } | { checked: true; ok: false; error: string; message?: string; at: number };
type Item = {
  id: string; label: string; configured: boolean; redirect_uri: string; env: Record<string, string>;
  missing?: string[]; issuer?: string; issuer_template?: boolean; scopes?: string; client_id_hint?: string;
  client_secret_set?: boolean; linked_accounts?: number; discovery?: Discovery;
};
type Status = { items: Item[]; enabled: boolean };

/** Masked provider configuration for administrators: what to paste into the Google / Microsoft
 *  console, what is missing, whether the provider answers. Nothing here is a secret; the page
 *  cannot set one either (see docs/operations.md §7). */
export function IdentityProvidersTab({ onDenied }: { onDenied: () => void }) {
  const { t } = useI18n();
  const load = useAdminData<Status>("/api/admin/identity-providers", onDenied);
  const [probe, setProbe] = useState<Status | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState<string | null>(null);
  const data = probe || load.data;
  const check = async () => {
    setProbing(true); setProbeError(null);
    try { setProbe(await get<Status>("/api/admin/identity-providers?probe=1")); }
    catch (e) { setProbeError(e instanceof Error ? e.message : String(e)); }
    finally { setProbing(false); }
  };
  if (load.loading && !data) return <div className="empty">{t("加载中…")}</div>;
  if (load.err && !data) return <div className="notice err" role="alert">{load.err} <button className="btn" onClick={load.reload}>{t("重试")}</button></div>;
  if (!data) return null;
  return <section className="card card-b col gap12">
    <div className="row gap8 wrap"><h2 className="grow">{t("外部账户登录（OpenID Connect）")}</h2>
      <button className="btn sm" disabled={load.loading} onClick={() => { setProbe(null); load.reload(); }}>{t("刷新")}</button>
      {data.enabled && data.items.some((i) => i.configured) && <button className="btn sm primary" disabled={probing} onClick={check}>{t(probing ? "正在检查…" : "立即检查连接")}</button>}
    </div>
    <p className="muted">{t("每个提供商在本部署的配置状态。密钥不会显示，也不能在这里填写：它在账户服务的环境配置里，改动后重启账户服务生效。")}</p>
    {probeError && <p className="notice err" role="alert">{probeError}</p>}
    {!data.enabled && <p className="notice warn">{t("本部署没有启用外部账户登录模块。")}</p>}
    <div className="idp-grid">
      {data.items.map((item) => <article key={item.id} className={`idp-card${item.configured ? " on" : ""}`} aria-labelledby={`idp-${item.id}`}>
        <div className="row gap8"><h3 id={`idp-${item.id}`} className="grow">{item.label}</h3>
          <span className={`badge ${item.configured ? "ok" : ""}`}>{t(item.configured ? "已启用" : "未配置")}</span></div>
        <dl className="idp-facts">
          <dt>{t("回调地址（在提供商控制台登记这一条）")}</dt><dd><code>{item.redirect_uri}</code></dd>
          {item.configured ? <>
            <dt>{t("权限范围")}</dt><dd><code>{item.scopes}</code></dd>
            <dt>{t("发行者")}</dt><dd><code>{item.issuer}</code>{item.issuer_template && <span className="cell-note">{t("多租户发行者：按令牌中的 tenant 校验")}</span>}</dd>
            <dt>{t("客户端 ID")}</dt><dd><code>{item.client_id_hint}</code> <span className="muted small">{item.env.client_id}</span></dd>
            <dt>{t("客户端密钥")}</dt><dd>{t(item.client_secret_set ? "已填写" : "未填写")} <span className="muted small">{item.env.client_secret}</span></dd>
            <dt>{t("已关联账户")}</dt><dd>{item.linked_accounts}</dd>
            <dt>{t("发现文档")}</dt><dd>{!item.discovery || !item.discovery.checked ? t("尚未检查")
              : item.discovery.ok ? t("可达（{at}）", { at: fmtTime(item.discovery.at) })
              : <span className="cell-note err">{t("不可达：{error}（{at}）", { error: item.discovery.message || item.discovery.error, at: fmtTime(item.discovery.at) })}</span>}</dd>
          </> : <>
            <dt>{t("缺少的配置项")}</dt><dd><ul className="idp-missing">{item.missing?.map((name) => <li key={name}><code>{name}</code></li>)}</ul></dd>
            <dt>{t("配置项名称")}</dt><dd className="muted small">{Object.values(item.env).filter((n) => !item.missing?.includes(n)).map((n) => <code key={n} style={{ marginRight: 6 }}>{n}</code>)}</dd>
          </>}
        </dl>
      </article>)}
    </div>
  </section>;
}
