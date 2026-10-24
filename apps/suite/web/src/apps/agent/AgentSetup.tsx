/** The one-time "Start using Synwevo Agent" step (design D-5): an inline region, not a modal,
 *  shown when a send needs something the person has not done yet. It lists only what is
 *  missing — (a) the separate DeepSeek data-sharing consent, (b) the terms of service, AI use
 *  rules and pricing, and (c) for the personal payer the spending limits, prefilled with the
 *  server's suggested values — behind two separate, unticked checkboxes, writes them in the
 *  order (a) → (b) → (c) through the existing operations, and only then hands back so the kept
 *  draft is sent. Any failure stops there: the draft stays in the composer and the state is
 *  read again. The card comes back only when a document or consent version changes; a price
 *  change never brings it back. Everything shown is a server fact (`agent.setup.get`,
 *  `agent.provider_consent.get`); nothing is pre-ticked and nothing is inferred from a send. */
import { useEffect, useId, useRef, useState } from "react";
import { useI18n, type Params } from "@synwevo/ui";
import { Loading } from "../../shared/states";
import { formatAmount, majorUnits, type Unit } from "./budget";
import { chatCopy, type ChatText } from "./chat.messages";
import { publicHref, type MoneyUnit, type PublicDocument } from "./composer";
import { formatMoney } from "./cost";
import { agentErrorText } from "./errors";
import { budgetPeriods, parsePayerBudget, payerBudgetCode, payerBudgetErrorText, type BudgetPeriod } from "./payer-budget";
import { payerBudgetCopy, type PayerBudgetText } from "./payer-budget.messages";
import { ProviderConsentFacts } from "./ProviderConsent";
import { providerConsentCopy, type ProviderConsentText } from "./provider-consent.messages";
import { errorCode } from "./refusals";

/** `agent.setup.get` (design §3). `complete` is computed by the server for the personal payer. */
export type SetupDocument = "terms" | "ai-use" | "pricing";
export interface SetupState {
  required: boolean;
  complete: boolean;
  provider_consent: { provider: string; version: string; accepted: boolean; accepted_version: string | null } | null;
  /** `accepted_version` (addendum-2 S4): the person's last accepted version of that document, or
   *  null when they never accepted it (absent from a server before S4). */
  terms: { complete: boolean; documents: { id: SetupDocument; version: string; effective: string; accepted: boolean; accepted_version?: string | null }[] };
  limits: { configured: boolean; enabled: boolean; revision: number; unit: MoneyUnit | null;
    suggested: { daily_budget: string; weekly_budget: string; monthly_budget: string } | null };
}
/** `agent.provider_consent.get`: the facts the Settings card shows (ProviderConsent.tsx). */
export interface ProviderFacts {
  provider: string; version: string; effective_at: string; processor: Record<string, string>;
  accepted: boolean; accepted_version: string | null; required: boolean;
}
export interface SetupItems { consent: boolean; terms: boolean; limits: boolean }

const DOCUMENTS: SetupDocument[] = ["terms", "ai-use", "pricing"];
const UNSIGNED = /^(0|[1-9][0-9]{0,30})$/;
const bad = () => new Error("invalid_setup_response");

/** Strictly parse the server's setup state; a malformed value is refused, never guessed. */
export function validateSetup(value: unknown): SetupState {
  const v = value as SetupState;
  if (!v || typeof v !== "object" || typeof v.required !== "boolean" || typeof v.complete !== "boolean") throw bad();
  const consent = v.provider_consent;
  if (consent !== null) {
    if (!consent || typeof consent !== "object" || typeof consent.provider !== "string" || !consent.provider
      || typeof consent.version !== "string" || !consent.version.trim() || typeof consent.accepted !== "boolean"
      || !(consent.accepted_version === null || typeof consent.accepted_version === "string")) throw bad();
  }
  const terms = v.terms;
  if (!terms || typeof terms !== "object" || typeof terms.complete !== "boolean" || !Array.isArray(terms.documents) || terms.documents.length > 10) throw bad();
  for (const document of terms.documents) {
    if (!document || typeof document !== "object" || !DOCUMENTS.includes(document.id) || typeof document.version !== "string"
      || !document.version.trim() || typeof document.effective !== "string" || !document.effective.trim()
      || typeof document.accepted !== "boolean"
      || !(document.accepted_version == null || (typeof document.accepted_version === "string" && !!document.accepted_version.trim()))) throw bad();
  }
  const limits = v.limits;
  if (!limits || typeof limits !== "object" || typeof limits.configured !== "boolean" || typeof limits.enabled !== "boolean"
    || !Number.isSafeInteger(limits.revision) || limits.revision < 0) throw bad();
  if (limits.unit !== null) {
    if (!limits.unit || typeof limits.unit.code !== "string" || !/^[A-Za-z0-9_-]{1,32}$/.test(limits.unit.code)
      || !Number.isInteger(limits.unit.scale) || limits.unit.scale < 0 || limits.unit.scale > 18) throw bad();
  }
  if (limits.suggested !== null) {
    if (!limits.suggested || typeof limits.suggested !== "object") throw bad();
    for (const period of budgetPeriods) {
      const amount = limits.suggested[`${period}_budget`];
      if (typeof amount !== "string" || !UNSIGNED.test(amount)) throw bad();
    }
  }
  return v;
}

/** What the card has to ask for: decided from the server state and the payer. An organization
 *  payer needs no personal limits; a provider without a separate consent asks for none; limits
 *  that exist but are paused are a settings matter, not setup. */
export function setupItems(setup: SetupState, organizationPayer: boolean): SetupItems {
  return {
    consent: !!setup.provider_consent && !setup.provider_consent.accepted,
    terms: !setup.terms.complete || setup.terms.documents.some((document) => !document.accepted),
    limits: !organizationPayer && !setup.limits.configured && !!setup.limits.unit,
  };
}

/** Whether what is missing was accepted before in an earlier version (addendum-2 W5): the server
 *  reports the person's last accepted version. Only then is it worded as an update; otherwise it is
 *  a first use, whatever the reason the step was opened. */
export function reacceptance(setup: SetupState): { terms: boolean; consent: boolean } {
  return {
    terms: setup.terms.documents.some((document) => !document.accepted && !!document.accepted_version),
    consent: !!setup.provider_consent && !setup.provider_consent.accepted && !!setup.provider_consent.accepted_version,
  };
}

/** Whether a send has to go through the card first. An unreadable or malformed state never
 *  blocks: the send goes to the server, whose refusal then names the missing step. */
export function setupNeeded(setup: unknown, organizationPayer: boolean): boolean {
  if (!setup) return false;
  let state: SetupState;
  try { state = validateSetup(setup); } catch { return false; }
  if (!state.required) return false;
  const items = setupItems(state, organizationPayer);
  return items.consent || items.terms || items.limits;
}

/** The server state after this card finished, for the moment until it is read again. */
export function completedSetup(setup: SetupState, organizationPayer: boolean): SetupState {
  return { ...setup, complete: true,
    provider_consent: setup.provider_consent && { ...setup.provider_consent, accepted: true, accepted_version: setup.provider_consent.version },
    terms: { complete: true, documents: setup.terms.documents.map((document) => ({ ...document, accepted: true })) },
    limits: organizationPayer || setup.limits.configured ? setup.limits : { ...setup.limits, configured: true, enabled: true } };
}

type Draft = Record<BudgetPeriod, string>;
function suggestedDraft(setup: SetupState): Draft {
  const unit = setup.limits.unit, suggested = setup.limits.suggested;
  const major = (amount: string) => unit && suggested ? majorUnits(amount, unit) : "";
  return { daily: major(suggested?.daily_budget ?? ""), weekly: major(suggested?.weekly_budget ?? ""), monthly: major(suggested?.monthly_budget ?? "") };
}

export default function AgentSetup({ setup, organizationPayer, hasDraft, maximum, siteUrl, read, write, onComplete, onChanged,
  focus = false, focusTick = 0, onDismiss, onRetryRead }: {
  /** The validated `agent.setup.get`; undefined while loading, null when it could not be read. */
  setup: SetupState | null | undefined;
  organizationPayer: boolean;
  /** A message waits in the composer: the button sends it. */
  hasDraft: boolean;
  /** The most one answer can cost now (`agent.billing.status.run.maximum`), for the terms sentence. */
  maximum: { amount: string; unit: MoneyUnit } | null;
  siteUrl?: string | null;
  read: (operation: string) => Promise<unknown>;
  write: (operation: string, input: Record<string, unknown>) => Promise<unknown>;
  /** Everything is recorded: send the kept draft (the caller shows any refusal of that send). */
  onComplete: () => Promise<void> | void;
  /** Read the setup state again (after a partial completion or a changed version). */
  onChanged: () => void;
  /** Move focus to the card's heading (Send was pressed while setup was incomplete). */
  focus?: boolean;
  /** Bumped on every later Send so focus moves again without remounting the card. */
  focusTick?: number;
  onDismiss?: () => void;
  onRetryRead?: () => void;
}) {
  // useI18n directly (not ChatParts' helper): this card is also rendered on its own.
  const { t, locale } = useI18n();
  const c = (key: ChatText, params?: Params) => t(chatCopy[key][0], params);
  const pc = (key: ProviderConsentText, params?: Record<string, string>) => t(providerConsentCopy[key][0], params);
  const pb = (key: PayerBudgetText, params?: Record<string, string>) => t(payerBudgetCopy[key][0], params);
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [facts, setFacts] = useState<ProviderFacts | null>(null);
  const [factsError, setFactsError] = useState(false);
  const [factsTick, setFactsTick] = useState(0);
  const [consentChecked, setConsentChecked] = useState(false);
  const [termsChecked, setTermsChecked] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [issue, setIssue] = useState<BudgetPeriod | null>(null);
  const [partial, setPartial] = useState<unknown>(null);
  const [termsUpdated, setTermsUpdated] = useState(false);
  const [busy, setBusy] = useState(false);
  // Steps already recorded by this card: never written twice while the state is read again.
  const recorded = useRef<Set<keyof SetupItems>>(new Set());
  const items = setup ? setupItems(setup, organizationPayer) : { consent: false, terms: false, limits: false };
  const needConsent = items.consent && !recorded.current.has("consent");
  const needTerms = items.terms && !recorded.current.has("terms");
  const needLimits = items.limits && !recorded.current.has("limits");
  const again = setup ? reacceptance(setup) : { terms: false, consent: false };
  // "You only do this once" is a first use's sentence, not a re-acceptance's.
  const firstUse = !(needTerms && again.terms) && !(needConsent && again.consent);
  useEffect(() => { if (focus) heading.current?.focus(); }, [focus, focusTick]);
  useEffect(() => {
    if (!needConsent) return;
    let live = true;
    setFactsError(false);
    read("agent.provider_consent.get").then((value) => {
      const v = value as ProviderFacts;
      if (!live) return;
      if (!v || typeof v !== "object" || typeof v.version !== "string" || !v.version || !v.processor || typeof v.processor !== "object") { setFactsError(true); return; }
      setFacts(v);
    }).catch(() => { if (live) setFactsError(true); });
    return () => { live = false; };
  }, [needConsent, factsTick]);  // eslint-disable-line react-hooks/exhaustive-deps
  const unit: Unit | null = setup?.limits.unit ?? null;
  const limits = draft ?? (setup ? suggestedDraft(setup) : { daily: "", weekly: "", monthly: "" });
  const ready = (!needConsent || (consentChecked && !!facts)) && (!needTerms || termsChecked);
  const money = (minor: string, u: MoneyUnit) => { try { return formatMoney(minor, u, locale); } catch { return formatAmount(minor, u); } };
  const amount = maximum ? money(maximum.amount, maximum.unit) : c("setupAmountUnknown");
  const shownLimit = (period: BudgetPeriod) => {
    if (!unit) return limits[period];
    try { return money(parsePayerBudget(limits[period], unit.scale), unit); } catch { return limits[period]; }
  };
  // Why the step stopped, when the card does not already say it (a new document or notice version).
  const partialCode = partial != null ? errorCode(partial) : "";
  const reason = partial == null || ["terms_version_changed", "provider_consent_version_changed"].includes(partialCode) ? ""
    : payerBudgetCode(partial) && payerBudgetErrorText(partial, "refused") !== "refused" ? pb(payerBudgetErrorText(partial, "refused"))
    : agentErrorText(partial, t);
  const link = (page: PublicDocument, key: "docTerms" | "docAiUse" | "docPricing") =>
    <a href={publicHref(siteUrl, page, locale)} target="_blank" rel="noopener noreferrer">{c(key)}<span className="sr-only">{c("docNewTab")}</span></a>;

  async function submit(event: { preventDefault(): void }) {
    event.preventDefault();
    if (busy || !ready || !setup) return;
    let budgets: Record<`${BudgetPeriod}_budget`, string> | null = null;
    if (needLimits) {
      if (!unit) return;
      const parsed = {} as Record<`${BudgetPeriod}_budget`, string>;
      for (const period of budgetPeriods) {
        try { parsed[`${period}_budget`] = parsePayerBudget(limits[period], unit.scale); }
        catch { setIssue(period); if (typeof document !== "undefined") document.getElementById(`${id}-${period}`)?.focus(); return; }
      }
      budgets = parsed;
    }
    setIssue(null); setPartial(null); setBusy(true);
    try {
      if (needConsent) {
        await write("agent.provider_consent.accept", { version: facts!.version });
        recorded.current.add("consent");
      }
      if (needTerms) {
        await write("agent.terms.accept", { documents: setup.terms.documents.map((document) => ({ id: document.id, version: document.version })) });
        recorded.current.add("terms");
      }
      if (needLimits && budgets) {
        await write("agent.billing.budget.set", { unit_code: unit!.code, expected_revision: setup.limits.revision, enabled: true, ...budgets });
        recorded.current.add("limits");
      }
    } catch (error) {
      const code = errorCode(error);
      if (code === "terms_version_changed") { setTermsChecked(false); setTermsUpdated(true); }
      if (code === "provider_consent_version_changed") { setConsentChecked(false); setFacts(null); setFactsTick((n) => n + 1); }
      setPartial(error);
      setBusy(false);
      onChanged();
      return;
    }
    setBusy(false);
    await onComplete();
  }

  const title = <h2 id={`${id}-title`} className="ag-setup-title" ref={heading} tabIndex={-1}>{c("setupTitle")}</h2>;
  // Nothing is missing and this step recorded nothing (addendum-2 W4): there is nothing to agree to,
  // so no "Agree and send" that would write nothing; the step only reads the state again.
  const nothing = !!setup && !needConsent && !needTerms && !needLimits && recorded.current.size === 0;
  if (setup === undefined) {
    return <section className="notice info ag-setup" role="region" aria-labelledby={`${id}-title`}>{title}<Loading /></section>;
  }
  if (setup === null) {
    return <section className="notice warn ag-setup" role="region" aria-labelledby={`${id}-title`}>{title}
      <p role="alert">{c("setupUnavailable")}</p>
      <div className="ag-setup-actions">
        {onRetryRead && <button type="button" className="btn primary" onClick={onRetryRead}>{c("setupRetry")}</button>}
        {onDismiss && <button type="button" className="btn ghost" onClick={onDismiss}>{c("setupLater")}</button>}
      </div>
    </section>;
  }
  if (nothing) {
    return <section className="notice info ag-setup" role="region" aria-labelledby={`${id}-title`}>{title}
      <p role="status">{c("setupNothing")}{hasDraft ? <> {c("setupNothingDraft")}</> : null}</p>
      <div className="ag-setup-actions">
        {onRetryRead && <button type="button" className="btn primary" onClick={onRetryRead}>{c("setupRecheck")}</button>}
        {onDismiss && <button type="button" className="btn ghost" onClick={onDismiss}>{c("close")}</button>}
      </div>
    </section>;
  }
  return <section className="notice info ag-setup" role="region" aria-labelledby={`${id}-title`}>
    {title}
    {firstUse && <p className="ag-setup-intro">{c("setupIntro")}</p>}
    <form className="ag-setup-form" noValidate onSubmit={(event) => void submit(event)}>
      {needConsent && <fieldset className="ag-setup-item" disabled={busy}>
        <legend>{pc("title")}</legend>
        {facts ? <ProviderConsentFacts facts={facts} siteUrl={siteUrl} newTab={c("docNewTab")} />
          : factsError ? <p className="notice err"><span>{c("setupUnavailable")}</span>{" "}
          <button type="button" className="btn sm" onClick={() => setFactsTick((n) => n + 1)}>{c("setupRetry")}</button></p>
          : <Loading />}
        <label className="ag-check" htmlFor={`${id}-consent`}>
          <input type="checkbox" id={`${id}-consent`} checked={consentChecked} disabled={!facts || busy}
            onChange={(event) => setConsentChecked(event.target.checked)} />
          <span>{c("setupConsentCheck")}</span>
        </label>
      </fieldset>}
      {needTerms && <fieldset className="ag-setup-item" disabled={busy}>
        <legend className="sr-only">{c("docsTitle")}</legend>
        {(termsUpdated || again.terms) && <p className="notice warn" role="status">{c("setupTermsUpdated")}</p>}
        <label className="ag-check" htmlFor={`${id}-terms`}>
          <input type="checkbox" id={`${id}-terms`} checked={termsChecked} disabled={busy}
            onChange={(event) => setTermsChecked(event.target.checked)} />
          <span>{c("setupTerms", { amount })}</span>
        </label>
        <p className="small ag-setup-links">{link("terms", "docTerms")}{" · "}{link("ai-use", "docAiUse")}{" · "}{link("pricing", "docPricing")}</p>
      </fieldset>}
      {needLimits && unit && <fieldset className="ag-setup-item" disabled={busy}>
        <legend className="sr-only">{pb("title")}</legend>
        <p>{c("setupLimits", { daily: shownLimit("daily"), weekly: shownLimit("weekly"), monthly: shownLimit("monthly") })}</p>
        <div className="ag-setup-limits">{budgetPeriods.map((period) => <label key={period} className="field" htmlFor={`${id}-${period}`}>
          <span>{pb(`${period}Input`, { unit: unit.code })}</span>
          <input className="input" id={`${id}-${period}`} type="text" inputMode="decimal" autoComplete="off" spellCheck={false} maxLength={128}
            value={limits[period]} aria-invalid={issue === period} aria-describedby={issue === period ? `${id}-issue` : undefined}
            onChange={(event) => { setIssue(null); setDraft({ ...limits, [period]: event.target.value }); }} />
        </label>)}</div>
        {issue && <p className="notice err" id={`${id}-issue`} role="alert">{pb("invalidAmount")}</p>}
      </fieldset>}
      {partial != null && <p className="notice warn" role="alert">{c("setupPartial")}{reason ? <> {reason}</> : null}</p>}
      <div className="ag-setup-actions">
        <button type="submit" className="btn primary" disabled={!ready || busy}>{hasDraft ? c("setupAgreeSend") : c("setupAgreeStart")}</button>
        {onDismiss && <button type="button" className="btn ghost" disabled={busy} onClick={onDismiss}>{c("setupLater")}</button>}
      </div>
    </form>
  </section>;
}
