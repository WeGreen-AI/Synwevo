/** Beside the composer: who pays, the balance, the tightest spending limit, the governed model and
 *  its prices, and a gentle low-balance prompt with the way to top up on the account service. */
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { Modal } from "@synwevo/ui";
import { formatAmount, type Offer } from "./budget";
import { useChatText } from "./ChatParts";
import { fundsState, limitLine, perMillion, type BillingStatus } from "./composer";
import { formatMoney, formatPerMillion } from "./cost";
import { payerKey, spendable, type Payer, type WelcomeState } from "./payers";

export interface Capabilities { vision: boolean; tools: boolean; thinking: boolean }
export type ModelOffer = Offer & { capabilities?: Capabilities | null };
export interface MoneyState {
  /** undefined while loading; null when it could not be read. */
  status: BillingStatus | null | undefined;
  offer: ModelOffer | null | undefined;
  payers: Payer[];
  payersError: boolean;
  payer: Payer;
  setPayer: (payer: Payer) => void;
  walletUrl: string;
  /** The account service's own page (email verification for the welcome allowance). */
  accountUrl?: string;
  /** The payer list's environment, when the billing status could not say. */
  environment?: "test" | "live" | null;
  /** The one-time welcome allowance at first Agent use (from the payer list). */
  welcome?: WelcomeState | null;
}

function money(minor: string | null | undefined, unit: { code: string; scale: number } | null | undefined, locale: string): string | null {
  if (!minor || !unit) return null;
  try { return formatMoney(minor, unit, locale); } catch { return null; }
}

export function ModelPicker({ offers, disabled = false }: { offers: ModelOffer[]; disabled?: boolean }) {
  const { c, locale } = useChatText();
  const [open, setOpen] = useState(false);
  const id = useId();
  const current = offers[0];
  if (!current) return null;
  const price = (offer: ModelOffer, key: "input_uncached" | "input_cached" | "output") => {
    try { return formatPerMillion(offer, key, locale, perMillion(offer, key)); } catch { return "—"; }
  };
  return <>
    <button type="button" className="btn ghost sm ag-model-button" disabled={disabled} aria-haspopup="dialog" onClick={() => setOpen(true)}>
      {c("modelButton", { model: current.model })}
    </button>
    {open && <Modal title={c("modelTitle")} onClose={() => setOpen(false)} describedBy={`${id}-governed`}
      footer={<button type="button" className="btn" onClick={() => setOpen(false)}>{c("close")}</button>}>
      <p id={`${id}-governed`} className="muted">{c("modelGoverned")}</p>
      <ul className="ag-models">{offers.map((offer) => {
        const caps = offer.capabilities;
        return <li key={offer.model} className="ag-model" aria-current={offer === current ? "true" : undefined}>
          <div className="ag-model-head"><strong>{offer.model}</strong>
            {offer === current && <span className="badge ok">{c("modelCurrent")}</span>}
            {offer.environment === "test" && <span className="badge warn">{c("testWallet")}</span>}
          </div>
          <p className="muted small">{c("modelProvider")}</p>
          <h4>{c("modelPrices")}</h4>
          <dl className="ag-model-prices">
            <div><dt>{c("tokensUncached")}</dt><dd>{price(offer, "input_uncached")}</dd></div>
            <div><dt>{c("tokensCached")}</dt><dd>{price(offer, "input_cached")}</dd></div>
            <div><dt>{c("tokensOutput")}</dt><dd>{price(offer, "output")}</dd></div>
            <div><dt>{c("modelHold")}</dt><dd>{money(offer.per_call_hold, offer.unit, locale) ?? formatAmount(offer.per_call_hold, offer.unit)}</dd></div>
          </dl>
          <h4>{c("capabilities")}</h4>
          {caps ? <ul className="ag-caps">
            <li>{c("capText")}</li>
            {caps.tools && <li>{c("capTools")}</li>}
            {caps.thinking && <li>{c("capThinking")}</li>}
            <li className={caps.vision ? undefined : "off"}>{c(caps.vision ? "capVision" : "capNoVision")}</li>
          </ul> : <p className="muted small">{c("capUnknown")}</p>}
        </li>;
      })}</ul>
      {offers.length === 1 && <p className="muted small">{c("modelOnly")}</p>}
    </Modal>}
  </>;
}

/** The funds line for an organization payer: the smaller of its wallet and this member's
 *  remaining monthly allowance, against one call's hold. */
function orgFunds(payer: Payer, status: BillingStatus | null | undefined): ReturnType<typeof fundsState> {
  const left = spendable(payer), unit = payer.facts?.unit, offer = status?.unit;
  if (left === null || !unit || !offer || unit.code !== offer.code || unit.scale !== offer.scale) return "unknown";
  return fundsState({ available: true, balance: { booked: "", held: "", available: left.toString() }, per_call_hold: status?.per_call_hold });
}

export function ComposerStatus({ money: state, disabled = false }: { money: MoneyState; disabled?: boolean }) {
  const { c, locale } = useChatText();
  const id = useId();
  const { status, payer, payers } = state;
  const personalPayer = payer.kind === "user";
  const facts = payer.facts;
  const unit = status?.unit ?? null;
  const frozen = !!facts?.frozen;
  // The paid service is not open: say that, not a balance nobody can spend.
  const closed = status?.available === false && status.reason === "not_configured";
  // The personal balance comes from the billing status (with what is on hold), else from the
  // payer list; an organization's only from what the server listed for this member.
  const fromStatus = personalPayer && !!status?.balance;
  const balance = fromStatus ? status!.balance!.available : facts?.available;
  const balanceUnit = fromStatus ? unit : facts?.unit ?? null;
  const shown = money(balance, balanceUnit, locale);
  const held = fromStatus ? money(status!.balance!.held, unit, locale) : null;
  const sameUnit = !!balanceUnit && !!unit && balanceUnit.code === unit.code && balanceUnit.scale === unit.scale;
  const funds = frozen ? "unknown" : !personalPayer ? orgFunds(payer, status)
    : sameUnit ? fundsState({ available: true, balance: { booked: "", held: "", available: balance ?? "" }, per_call_hold: status?.per_call_hold }) : "unknown";
  const limit = personalPayer ? limitLine(status) : null;
  const limitText = limit && (limit.amount !== undefined
    ? c(limit.key, { amount: money(limit.amount, unit, locale) ?? limit.amount, period: c(limit.period!) }) : c(limit.key));
  const monthly = !personalPayer && facts?.monthly ? { amount: money(facts.monthly.remaining, facts.unit, locale), limit: money(facts.monthly.limit, facts.unit, locale) } : null;
  const usePersonal = !personalPayer && payers[0] ? () => state.setPayer(payers[0]) : undefined;
  const test = status?.environment === "test" || (!status && state.environment === "test");
  return <section className="ag-money" aria-label={c("moneyStatus")}>
    <div className="ag-money-row">
      {payers.length > 1 ? <label className="ag-payer" htmlFor={`${id}-payer`}><span>{c("payer")}</span>
        <select id={`${id}-payer`} className="input sm" value={payerKey(payer)} disabled={disabled}
          onChange={(event) => { const next = payers.find((p) => payerKey(p) === event.target.value); if (next) state.setPayer(next); }}>
          {payers.map((p) => <option key={payerKey(p)} value={payerKey(p)}>{p.kind === "user" ? c("payerPersonal") : c("payerOrganization", { name: p.name })}</option>)}
        </select></label>
        : <span className="ag-chip">{c("payer")} · {c("payerPersonal")}</span>}
      <span className={`ag-chip ag-balance${frozen || funds === "empty" ? " is-empty" : funds === "low" ? " is-low" : ""}`} aria-live="polite">
        {frozen ? c("frozenWallet") : closed ? c("paidNotOpen") : shown ? c(personalPayer ? "balance" : "orgBalance", { amount: shown })
          : personalPayer && status === undefined ? "…" : c("balanceUnknown")}
        {!frozen && held && status?.balance?.held !== "0" && <span className="ag-chip-sub">{c("balanceHeld", { amount: held })}</span>}
      </span>
      {limitText && (limit!.key === "limitRemaining" ? <span className="ag-chip">{limitText}</span>
        : <Link className="ag-chip ag-chip-link" to="/agent/settings">{limitText}</Link>)}
      {monthly?.amount && monthly.limit && <span className="ag-chip">{c("orgAllowance", { amount: monthly.amount, limit: monthly.limit })}</span>}
      {test && <span className="badge warn">{c("testWallet")}</span>}
      <span className="spacer" />
      {state.offer && <ModelPicker offers={[state.offer]} />}
    </div>
    {frozen ? <p className="ag-low is-empty" role="status">
      <span>{c(personalPayer ? "frozenNote" : "orgFrozenNote")}</span>
      {personalPayer ? <a className="btn sm" href={state.walletUrl}>{c("nextWallet")}</a>
        : usePersonal && <button type="button" className="btn sm" disabled={disabled} onClick={usePersonal}>{c("nextPersonal")}</button>}
    </p> : (funds === "empty" || funds === "low") && <p className={`ag-low${funds === "empty" ? " is-empty" : ""}`}>
      {personalPayer ? <>
        <span>{c(funds === "empty" ? "emptyBalance" : "lowBalance")}</span>
        <a className="btn sm primary" href={state.walletUrl}>{c("topUp")}</a>
      </> : <>
        {/* A member cannot top up the organization's wallet; the personal wallet is the way on. */}
        <span>{c(funds === "empty" ? "orgEmpty" : "orgLow")}</span>
        {usePersonal && <button type="button" className="btn sm" disabled={disabled} onClick={usePersonal}>{c("nextPersonal")}</button>}
      </>}
    </p>}
    {personalPayer && state.welcome === "granted" && <p className="ag-welcome-note" role="status">{c("welcomeGranted")}</p>}
    {personalPayer && state.welcome === "email_unverified" && <p className="ag-welcome-note">
      <span>{c("welcomeVerify")}</span>{state.accountUrl && <> <a href={state.accountUrl}>{c("welcomeVerifyLink")}</a></>}</p>}
    {state.payersError && <p className="muted small">{c("payerUnavailable")}</p>}
  </section>;
}
