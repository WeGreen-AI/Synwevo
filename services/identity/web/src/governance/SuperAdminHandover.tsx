import { useEffect, useRef, useState } from "react";
import { ApiError, get, post, useSession } from "@synwevo/auth-client";
import { fmtTime, Modal, useI18n } from "@synwevo/ui";
import { useLoad } from "../lib/hooks";
import { b64urlToBuf, toJSON } from "../lib/webauthn";
import { ReauthClientError } from "../lib/reauth";
import { ActionDialog } from "./ActionDialog";
import { errText } from "./labels";
import "./handover.messages";

type Person = { id: string; email: string; display_name: string | null };
export type SuperAdminOffer = {
  id: string; status: string; proposer: Person; recipient: Person; reason: string;
  created_at: number; expires_at: number; accepted_at: number | null; ready_at: number | null;
  grant_id: string | null; can_accept: boolean; unavailable_reason?: string | null;
};
type OfferPage = { items: SuperAdminOffer[]; next_cursor: string | null };
type OfferResult = { operation_id: string; offer: SuperAdminOffer; grant_id?: string };
const STATUS: Record<string, string> = {
  pending: "等待本人接受", accepted: "已接受，待确认后台就绪", ready: "接任已就绪",
  authority_changed: "发起授权已失效", target_unavailable: "接任账户当前不符合条件",
  cancelled: "提议已取消", declined: "本人已拒绝", expired: "提议已过期", invalidated: "提议已失效", revoked: "授权已撤销",
};
const offerPath = (id: string) => encodeURIComponent(id);

function OfferDetails({ offer }: { offer: SuperAdminOffer }) {
  const { t } = useI18n();
  return <div className="col gap8">
    <div className="row wrap gap8"><b>{offer.recipient.display_name || offer.recipient.email}</b>
      <span className="badge">{t(STATUS[offer.status] || "提议当前不可用")}</span></div>
    <p className="small">{t("接任账户：{email}", { email: offer.recipient.email })}</p>
    <p className="small">{t("发起者：{email}", { email: offer.proposer.email })}</p>
    <p className="small">{t("提议理由：{reason}", { reason: offer.reason })}</p>
    <p className="tiny muted">{t("提出于 {created}；接受期限至 {expires}", { created: fmtTime(offer.created_at), expires: fmtTime(offer.expires_at) })}</p>
    {["invalidated", "authority_changed", "target_unavailable"].includes(offer.status) && <p className="notice">{t("发起授权、账户状态或恢复环境已变化。此提议不能继续，请由当前最高管理员重新发起。")}</p>}
    {offer.status === "expired" && <p className="notice">{t("接受期限已过。此提议未产生新授权，请由当前最高管理员重新发起。")}</p>}
  </div>;
}

function useOffers(kind: "admin" | "auth") {
  const [before, setBefore] = useState<string | null>(null);
  const path = `/api/${kind}/super-admin-offers?limit=20${before ? `&before=${encodeURIComponent(before)}` : ""}`;
  const load = useLoad<OfferPage>(path, signal => get(path, signal));
  return { ...load, before, setBefore, refresh: () => { setBefore(null); load.reload(); } };
}
function OfferPages({ list }: { list: ReturnType<typeof useOffers> }) {
  const { t } = useI18n();
  return <div className="row wrap gap8">
    {list.before && <button className="btn sm" onClick={() => list.setBefore(null)}>{t("返回最新提议")}</button>}
    {list.data?.next_cursor && <button className="btn sm" onClick={() => list.setBefore(list.data!.next_cursor)}>{t("更早提议")}</button>}
  </div>;
}

/** A dedicated WebAuthn ceremony submits only to this offer and stage. It does not refresh
 * ordinary admin reauthentication. Keep an uncertain finish payload for an exact idempotent retry. */
function OfferProofDialog({ offer, stage, onClose, onDone }: {
  offer: SuperAdminOffer; stage: "accept" | "ready"; onClose: () => void; onDone: (out: OfferResult) => void;
}) {
  const { t } = useI18n();
  const [acknowledged, setAcknowledged] = useState(false);
  const [phase, setPhase] = useState<"idle" | "verifying" | "submitting">("idle");
  const [error, setError] = useState<string | null>(null);
  const proof = useRef<AbortController | null>(null);
  const running = useRef(false);
  const request = useRef<{ credential: Record<string, unknown>; idempotency_key: string } | null>(null);
  useEffect(() => () => proof.current?.abort(), []);
  const path = `/api/${stage === "accept" ? "auth" : "admin"}/super-admin-offers/${offerPath(offer.id)}/${stage}`;
  const close = () => { if (phase !== "submitting") { proof.current?.abort(); onClose(); } };
  async function execute() {
    if (!acknowledged || running.current) return;
    running.current = true; setError(null);
    const control = new AbortController(); proof.current = control;
    try {
      if (!request.current) {
        if (!window.isSecureContext || typeof PublicKeyCredential === "undefined" || !navigator.credentials?.get)
          throw new ReauthClientError(t("当前浏览器不能使用安全密钥，请换用支持安全密钥的浏览器。"));
        setPhase("verifying");
        const { options } = await post<{ options: Record<string, unknown> }>(`${path}/begin`, {});
        control.signal.throwIfAborted();
        const publicKey = "parseRequestOptionsFromJSON" in PublicKeyCredential
          ? (PublicKeyCredential as unknown as { parseRequestOptionsFromJSON: (value: unknown) => PublicKeyCredentialRequestOptions }).parseRequestOptionsFromJSON(options)
          : { ...(options as unknown as PublicKeyCredentialRequestOptions), challenge: b64urlToBuf(options.challenge as string),
              allowCredentials: (options.allowCredentials as { id: string; type: PublicKeyCredentialType; transports?: AuthenticatorTransport[] }[] | undefined)
                ?.map(item => ({ ...item, id: b64urlToBuf(item.id) })) };
        const credential = await navigator.credentials.get({ publicKey, signal: control.signal }) as PublicKeyCredential | null;
        control.signal.throwIfAborted();
        if (!credential) throw new ReauthClientError(t("没有收到安全密钥的响应。"));
        request.current = { credential: toJSON(credential), idempotency_key: crypto.randomUUID() };
      }
      control.signal.throwIfAborted(); setPhase("submitting");
      const out = await post<OfferResult>(`${path}/finish`, request.current);
      request.current = null;
      if (!control.signal.aborted) onDone(out);
    } catch (failure) {
      if (control.signal.aborted) return;
      // A transport failure can follow a successful commit. Retry the exact assertion/key.
      // A definite client refusal requires a new ceremony; spent assertions are never revived.
      if (failure instanceof ApiError && !failure.upstream && failure.status < 500) request.current = null;
      setError(failure instanceof ReauthClientError ? failure.message
        : failure instanceof DOMException && ["NotAllowedError", "AbortError"].includes(failure.name)
          ? t("已取消身份验证，操作尚未执行。可以重新尝试。") : errText(failure));
    } finally {
      if (proof.current === control) proof.current = null;
      running.current = false; setPhase("idle");
    }
  }
  return <Modal title={t(stage === "accept" ? "接受最高管理员接任提议" : "确认后台接任就绪")} onClose={close} role="alertdialog"
    footer={<><button className="btn" disabled={phase === "submitting"} onClick={close}>{t("取消")}</button>
      <button className="btn primary" disabled={!acknowledged || phase !== "idle"} onClick={() => void execute()}>
        {phase === "verifying" ? t("等待安全密钥验证…") : phase === "submitting" ? t("正在提交接任结果…")
          : request.current ? t("重试提交") : t(stage === "accept" ? "验证并接受" : "验证并完成接任")}</button></>}>
    <div className="col gap12"><OfferDetails offer={offer} />
      <p>{t(stage === "accept"
        ? "接受后，你将获得全部平台管理能力。这不授予任何组织、工作空间或文档内容访问权。现有管理员不会自动退出。"
        : "你已进入管理员中心。再次验证后，你会计为可用的接任人；现有管理员仍保留权限，撤销旧授权需要单独确认。")}</p>
      <p className="small muted">{t("此步骤必须由本人使用可验证身份的安全密钥完成。口令或之前的验证不能代替本次确认。")}</p>
      <label className="check"><input type="checkbox" disabled={phase !== "idle"} checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />
        {t("我理解最高管理员的职责和本次操作的影响。")}</label>
      {phase === "verifying" && <p role="status">{t("请完成设备上的安全密钥提示；可以取消，提议会保留。")}</p>}
      {error && <p className="notice err" role="alert">{error}</p>}
      {request.current && error && <p className="notice">{t("提交结果暂未确认。重试会核对同一操作，不会重复授予权限。")}</p>}
    </div>
  </Modal>;
}

export function SuperAdminOfferDialog({ user, onClose, onDone }: {
  user: { id: string; email: string }; onClose: () => void; onDone: (out: OfferResult) => void;
}) {
  const { t } = useI18n();
  const [plan, setPlan] = useState<{ preview_token: string; expires_at: number } | null>(null);
  const [key, setKey] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let current = true; setLoading(true); setError(null);
    post<{ preview_token: string; expires_at: number }>("/api/admin/super-admin-offers/preview", { target_user_id: user.id })
      .then(out => { if (current) { setPlan(out); setKey(crypto.randomUUID()); setAcknowledged(false); } })
      .catch(failure => { if (current) setError(errText(failure)); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [user.id, revision]);
  if (!plan || error) return <Modal title={t("发起最高管理员接任")} onClose={onClose}><div className="col gap12">
    {loading ? <p role="status">{t("正在预览…")}</p> : <><p className="notice err" role="alert">{error}</p><button className="btn" onClick={() => setRevision(value => value + 1)}>{t("重试")}</button></>}
  </div></Modal>;
  return <ActionDialog key={key} title={t("发起最高管理员接任")} confirmLabel={t("创建接任提议")} reason="required" ready={acknowledged && !loading}
    impact={<div className="col gap8"><p>{t("向 {email} 提议接任最高管理员。提议有效期为24小时，本人接受前不会获得新权限。", { email: user.email })}</p>
      <p>{t("本人须使用安全密钥接受，进入后台后再次验证就绪，才计为可用接任人。现有管理员不会自动退出。")}</p>
      <p className="small muted">{t("预览有效至 {time}，授权或账户变化后需重新预览。", { time: fmtTime(plan.expires_at) })}</p></div>}
    run={reason => post<OfferResult>("/api/admin/super-admin-offers", { target_user_id: user.id, preview_token: plan.preview_token, idempotency_key: key, reason })}
    onClose={onClose} onDone={onDone}>
    <button type="button" className="btn sm" disabled={loading} onClick={() => setRevision(value => value + 1)}>{t("重新预览")}</button>
    <label className="check"><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />{t("我已核对接任账户、权限与验证要求。")}</label>
  </ActionDialog>;
}

export function SuperAdminOffersAdmin() {
  const { t } = useI18n();
  const list = useOffers("admin");
  const [cancel, setCancel] = useState<{ offer: SuperAdminOffer; key: string } | null>(null);
  const [result, setResult] = useState<string | null>(null);
  return <section id="admin-super-offers" className="card card-b col gap12" aria-labelledby="super-offers-heading">
    <div className="row wrap gap8"><h2 id="super-offers-heading" className="grow">{t("最高管理员接任提议")}</h2><button className="btn sm" onClick={list.refresh} disabled={list.loading}>{t("刷新")}</button></div>
    <p className="muted small">{t("提议不立即授予权限。本人接受且完成后台就绪验证后，才计为可用接任人。")}</p>
    {result && <p className="notice ok" role="status">{result}</p>}
    {!!list.error && <p className="notice err" role="alert">{errText(list.error)}</p>}
    {list.loading && <p role="status">{t("加载中…")}</p>}
    {!list.loading && !list.error && !list.data?.items.length && <p className="muted">{t("暂无接任提议。可从账户的平台权限详情发起。")}</p>}
    {list.data?.items.map(offer => <article key={offer.id} data-offer-id={offer.id} className="notice col gap12"><OfferDetails offer={offer} />
      {offer.status === "pending" && <div><button className="btn sm" onClick={() => setCancel({ offer, key: crypto.randomUUID() })}>{t("取消接任提议")}</button></div>}
    </article>)}
    <OfferPages list={list} />
    {cancel && <ActionDialog title={t("取消接任提议")} confirmLabel={t("取消接任提议")} reason="required"
      impact={t("取消向 {email} 发出的待接受提议。已经接受的授权需要通过角色撤销处理。", { email: cancel.offer.recipient.email })}
      run={reason => post<OfferResult>(`/api/admin/super-admin-offers/${offerPath(cancel.offer.id)}/cancel`, { reason, idempotency_key: cancel.key })}
      onClose={() => setCancel(null)} onDone={out => { setCancel(null); list.refresh(); setResult(t("提议已取消，操作编号：{id}", { id: out.operation_id })); }} />}
  </section>;
}

export function SuperAdminInbox() {
  const { t } = useI18n(); const { refresh } = useSession();
  const list = useOffers("auth");
  const [accept, setAccept] = useState<SuperAdminOffer | null>(null);
  const [decline, setDecline] = useState<{ offer: SuperAdminOffer; key: string } | null>(null);
  const [result, setResult] = useState<OfferResult | null>(null);
  const [targeted, setTargeted] = useState(window.location.hash === "#admin-handover");
  useEffect(() => {
    const follow = () => {
      const here = window.location.hash === "#admin-handover";
      setTargeted(here);
      if (here) list.reload();
    };
    window.addEventListener("hashchange", follow);
    window.addEventListener("focus", list.reload);
    return () => { window.removeEventListener("hashchange", follow); window.removeEventListener("focus", list.reload); };
  }, [list.reload]);
  useEffect(() => { if (targeted && list.data) document.getElementById("admin-handover")?.scrollIntoView({ block: "start" }); }, [targeted, !!list.data]);
  if (!targeted && !list.data?.items.length && !result) return null;
  return <section id="admin-handover" className="card card-b col gap12 account-section" aria-labelledby="handover-inbox-heading">
    <div className="row wrap gap8"><h2 id="handover-inbox-heading" className="secttl grow">{t("最高管理员接任")}</h2><button className="btn sm" onClick={list.refresh} disabled={list.loading}>{t("刷新")}</button></div>
    <p className="small muted">{t("这里只显示向你本人发出的提议。接受是实际授予平台权限，需要本人使用安全密钥确认。")}</p>
    {list.loading && <p role="status">{t("加载中…")}</p>}
    {!!list.error && <p className="notice err" role="alert">{errText(list.error)}</p>}
    {!list.loading && !list.error && !list.data?.items.length && <p>{t("你没有接任提议。")}</p>}
    {result && <div className="notice ok col gap8" role="status"><p>{t(result.offer.status === "accepted" ? "已接受接任，操作编号：{id}" : "已拒绝提议，操作编号：{id}", { id: result.operation_id })}</p>
      {result.offer.status === "accepted" && <a className="btn primary" href="/admin?tab=admins">{t("进入管理员中心完成就绪验证")}</a>}</div>}
    {list.data?.items.map(offer => <article key={offer.id} data-offer-id={offer.id} className="notice col gap12"><OfferDetails offer={offer} />
      {offer.status === "pending" && <>
        {offer.unavailable_reason === "passkey_required" && <p className="notice">{t("接受前，请先在登录与安全中登记可验证身份的安全密钥，再回来刷新。")}</p>}
        <div className="row wrap gap8"><button className="btn primary" disabled={!offer.can_accept} onClick={() => setAccept(offer)}>{t("使用安全密钥接受")}</button>
          {offer.unavailable_reason === "passkey_required" && <a className="btn" href="#security">{t("前往登录与安全")}</a>}
          <button className="btn" onClick={() => setDecline({ offer, key: crypto.randomUUID() })}>{t("拒绝接任提议")}</button></div>
      </>}
      {offer.status === "accepted" && result?.offer.id !== offer.id && <a className="btn primary" href="/admin?tab=admins">{t("进入管理员中心完成就绪验证")}</a>}
    </article>)}
    <OfferPages list={list} />
    {accept && <OfferProofDialog offer={accept} stage="accept" onClose={() => setAccept(null)} onDone={out => {
      setAccept(null); setResult(out); list.refresh(); void refresh();
    }} />}
    {decline && <ActionDialog title={t("拒绝接任提议")} confirmLabel={t("拒绝接任提议")}
      impact={t("拒绝这项提议不会改变你当前的权限。需要接任时，可请当前最高管理员重新发起。")}
      run={() => post<OfferResult>(`/api/auth/super-admin-offers/${offerPath(decline.offer.id)}/decline`, { idempotency_key: decline.key })}
      onClose={() => setDecline(null)} onDone={out => { setDecline(null); setResult(out); list.refresh(); }} />}
  </section>;
}

export function SuperAdminReadiness({ onInspect, onChanged }: { onInspect: (user: { id: string; email: string }) => void; onChanged: () => void }) {
  const { t } = useI18n(); const { refresh } = useSession();
  const list = useOffers("auth");
  const offered = list.data?.items.find(offer => offer.status === "accepted");
  const path = offered ? `/api/admin/super-admin-offers/${offerPath(offered.id)}/readiness` : null;
  const ready = useLoad<{ offer: SuperAdminOffer; can_confirm: boolean; methods: string[] }>(path, signal => get(path!, signal));
  const [verifying, setVerifying] = useState(false);
  const [result, setResult] = useState<OfferResult | null>(null);
  if (!offered && !result && !list.error) return null;
  return <section id="admin-handover-ready" className="card card-b col gap12" aria-labelledby="handover-ready-heading">
    <h2 id="handover-ready-heading">{t("完成最高管理员接任")}</h2>
    {!!list.error && <p className="notice err" role="alert">{errText(list.error)}</p>}
    {result ? <div className="notice ok col gap8" role="status"><p>{t("接任已就绪，操作编号：{id}。旧管理员仍保留权限。", { id: result.operation_id })}</p>
      <button className="btn" onClick={() => onInspect(result.offer.proposer)}>{t("查看原管理员的授权")}</button></div> : <>
      {offered && <p>{t("你已获得最高管理员权限。完成这次独立安全密钥验证后，才计入可用接任人；唯一的现有管理员在此之前仍受保护。")}</p>}
      {!!ready.error && <p className="notice err" role="alert">{errText(ready.error)}</p>}
      {ready.loading && <p role="status">{t("正在核实接任授权…")}</p>}
      <div className="row wrap gap8"><button className="btn primary" disabled={!ready.data?.can_confirm || ready.loading || !!ready.error} onClick={() => setVerifying(true)}>{t("使用安全密钥验证就绪")}</button>
        <button className="btn" onClick={() => { list.refresh(); ready.reload(); }}>{t("刷新")}</button></div>
    </>}
    {verifying && ready.data && <OfferProofDialog offer={ready.data.offer} stage="ready" onClose={() => setVerifying(false)} onDone={out => {
      setVerifying(false); setResult(out); list.refresh(); onChanged(); void refresh();
    }} />}
  </section>;
}
