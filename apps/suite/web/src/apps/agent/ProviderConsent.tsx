import { useCallback, useEffect, useState } from "react";
import { ApiError, useSession } from "@synwevo/auth-client";
import { useDialog, useI18n } from "@synwevo/ui";
import { newKey, op, read } from "../../shared/ops";
import { Loading } from "../../shared/states";
import { publicHref } from "./composer";
import { providerConsentCopy, type ProviderConsentText } from "./provider-consent.messages";

/** agent.provider_consent.get — the separate, versioned consent to send content to DeepSeek
 *  (synwevo_core.agent_safety). The chat's "consent" refusal links here (#ag-provider-consent). */
export interface ConsentState {
  provider: "deepseek";
  version: string;
  effective_at: string;
  processor: Record<string, string>;
  data_location: string;
  accepted: boolean;
  accepted_version: string | null;
  accepted_at: number | null;
  required: boolean;
  history: { provider: string; version: string; action: "accepted" | "withdrawn"; at: number }[];
}

/** The notice itself, exactly as versioned by PROVIDER_TERMS: the same text in Agent settings and
 *  in the one-time "Start using Synwevo Agent" step (AgentSetup), so the consent a person gives
 *  in either place is to the same words. `newTab` opens the links in a new tab (the setup step
 *  keeps an unsent message in the composer) and names that for screen readers. */
export function ProviderConsentFacts({ facts, siteUrl, newTab }: {
  facts: Pick<ConsentState, "processor" | "version" | "effective_at">;
  siteUrl?: string | null;
  newTab?: string;
}) {
  const { t, locale } = useI18n();
  const c = (key: ProviderConsentText, params?: Record<string, string>) => t(providerConsentCopy[key][0], params);
  const target = newTab ? { target: "_blank", rel: "noopener noreferrer" } : {};
  const opens = newTab ? <span className="sr-only">{newTab}</span> : null;
  return <>
    <p>{c("intro")}</p>
    <dl className="ag-consent-facts">
      <div><dt>{c("processor")}</dt><dd>{facts.processor[locale] ?? facts.processor["zh-Hans"]}</dd></div>
      <div><dt>{c("location")}</dt><dd>{c("locationCN")}</dd></div>
      <div><dt>{c("training")}</dt><dd>{c("trainingPolicy")}</dd></div>
      <div><dt>{c("scope")}</dt><dd>{c("scopeText")}</dd></div>
    </dl>
    <p className="muted small">{c("withdrawNote")}</p>
    <p className="muted small">{c("version", { version: facts.version, date: facts.effective_at })}
      {" · "}<a href={publicHref(siteUrl, "privacy", locale)} {...target}>{c("privacy")}{opens}</a>
      {" · "}<a href={publicHref(siteUrl, "ai-use", locale)} {...target}>{c("rules")}{opens}</a></p>
  </>;
}

export default function ProviderConsent() {
  const { t, locale } = useI18n();
  // The privacy notice and AI use rules live on the public site, not on this app's host.
  const { state: auth } = useSession();
  const dialog = useDialog();
  const c = (key: ProviderConsentText, params?: Record<string, string>) => t(providerConsentCopy[key][0], params);
  const [state, setState] = useState<ConsentState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setState(await read<ConsentState>("agent.provider_consent.get", {}, null)); setError(null); }
    catch { setError(c("failed")); }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void load(); }, [load]);
  const when = (seconds: number | null) => seconds == null ? "" :
    new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(seconds * 1000);

  async function act(action: "accept" | "withdraw") {
    if (!state) return;
    if (action === "withdraw" && !await dialog.confirm(c("withdrawTitle"), c("withdrawBody"), c("withdraw"), true)) return;
    setBusy(true); setError(null);
    try {
      if (action === "accept") await op("agent.provider_consent.accept", { version: state.version }, { key: newKey() });
      else await op("agent.provider_consent.withdraw", {}, { key: newKey() });
      await load();
    } catch (e) {
      // A newer notice was published while this page was open: show it before asking again.
      if (e instanceof ApiError && e.code === "provider_consent_version_changed") { await load(); setError(c("versionChanged")); }
      else setError(c("failed"));
    } finally { setBusy(false); }
  }

  const changed = state && !state.accepted && state.accepted_version;
  return <section className="card" id="ag-provider-consent" aria-labelledby="ag-provider-consent-title">
    <div className="card-h"><h2 id="ag-provider-consent-title">{c("title")}</h2></div>
    <div className="card-b col gap12">
      {state === null && !error ? <Loading /> : state && <>
        <ProviderConsentFacts facts={state} siteUrl={auth?.site_url} />
        <div role="status" aria-live="polite">
          {state.accepted ? <p className="notice ok">{c("accepted", { date: when(state.accepted_at) })}</p>
            : changed ? <p className="notice warn">{c("changed", { version: state.accepted_version! })}</p>
            : state.required ? <p className="notice warn">{c("notAccepted")}</p>
            : <p className="muted">{c("notRequired")}</p>}
        </div>
        <div className="row gap8">
          {!state.accepted && <button type="button" className="btn primary" disabled={busy} onClick={() => void act("accept")}>{busy ? c("saving") : c("accept")}</button>}
          {state.accepted && <button type="button" className="btn danger" disabled={busy} onClick={() => void act("withdraw")}>{busy ? c("saving") : c("withdraw")}</button>}
        </div>
        {state.history.length > 0 && <details>
          <summary>{c("history")}</summary>
          <ul className="small">{state.history.slice().reverse().map(h => <li key={`${h.at}-${h.action}`}>
            {c(h.action === "accepted" ? "historyAccepted" : "historyWithdrawn", { date: when(h.at), version: h.version })}</li>)}</ul>
        </details>}
      </>}
      {error && <p className="notice err" role="alert">{error}</p>}
    </div>
  </section>;
}
