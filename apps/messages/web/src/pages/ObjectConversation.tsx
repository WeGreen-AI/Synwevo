/** `/objects/:app/:kind/:id` — the conversation about an application object (contract with the
 *  Docs↔Message linkage, CI-73). Docs links people here through `messagesHref(state, { path })`,
 *  so the path also survives the sign-in round trip as a plain `next=`.
 *
 *  On mount it asks the server for the object's conversation (`POST …/objects/{app}/{kind}/{id}/
 *  conversation`) and moves to the thread it names, replacing this address in history. A 404 is
 *  the one answer with its own page — the object does not exist or this person may not see it —
 *  with the same way back to the workspace the top bar offers. Anything else is the usual error
 *  notice with a retry. Nothing here invents a conversation when the server has none. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { platformHandoffUrl, post, useSession } from "@synwevo/auth-client";
import { PageState, StatusPage, useI18n } from "@synwevo/ui";
import { M, setPrefix, threadPath } from "../api";
import { resolveObjectConversation } from "../lib/objects";
import { FlowError } from "../flow";
import "../messages.messages";

export default function ObjectConversation() {
  const { app = "", kind = "", id = "" } = useParams();
  const navigate = useNavigate();
  const { state } = useSession();
  const { t, locale } = useI18n();
  setPrefix(state?.role);
  const [status, setStatus] = useState<"loading" | "missing" | "error">("loading");
  const [error, setError] = useState<unknown>(null);
  const inflight = useRef(false);
  const attempt = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    setStatus("loading"); setError(null);
    const r = await resolveObjectConversation((path, body) => post(path, body), M(), app, kind, id);
    inflight.current = false;
    if (r.kind === "open") { navigate(threadPath(r.id), { replace: true }); return; }
    if (r.kind === "missing") { setStatus("missing"); return; }
    setError(r.error); setStatus("error");
  }, [app, kind, id, navigate]);
  useEffect(() => { void attempt(); }, [attempt]);

  const workspace = state?.portal_url ? (platformHandoffUrl(state.portal_url, { next: "/workspace", lang: locale }) ?? state.portal_url) : null;
  if (status === "loading") return <PageState>{t("正在打开会话…")}</PageState>;
  if (status === "missing") {
    return <StatusPage product="Message" brandHref="/" title={t("找不到这个对象")}>
      <p>{t("找不到这个对象，或你没有权限查看。")}</p>
      {workspace ? <a className="btn" href={workspace}>{t("返回工作空间")}</a> : <a className="btn" href="/">{t("返回会话列表")}</a>}
    </StatusPage>;
  }
  return <StatusPage product="Message" brandHref="/" title={t("无法打开会话")} role="alert">
    <FlowError error={error} />
    <div className="row gap8"><button type="button" className="btn primary" onClick={() => void attempt()}>{t("重试")}</button><a className="btn" href="/">{t("返回会话列表")}</a></div>
  </StatusPage>;
}
