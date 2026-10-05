/** Beside the composer: who pays, the balance, the tightest spending limit, the governed model and
 *  its prices, and a gentle low-balance prompt with the way to top up on the account service. */
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { Modal } from "@synwevo/ui";
import { formatAmount, type Offer } from "./budget";
import { useChatText } from "./ChatParts";
import { fundsState, limitLine, perMillion, type BillingStatus } from "./composer";
import { formatMoney, formatPerMillion } from "./cost";
import { payerKey, type Payer } from "./payers";

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

export function ComposerStatus({ money: state, disabled = false }: { money: MoneyState; disabled?: boolean }) {
  const { c, locale } = useChatText();
  const id = useId();
  const { status, payer, payers } = state;
  const personal = payer.kind === "user";
  const unit = status?.unit ?? null;
  // An organization's balance is shown only when the server listed it for this member.
  const balance = personal ? status?.balance?.available : payer.balance?.available;
  const balanceUnit = personal ? unit : payer.balance ? { code: payer.balance.currency, scale: payer.balance.scale } : null;
  const shown = money(balance, balanceUnit, locale);
  const held = personal ? money(status?.balance?.held, unit, locale) : null;
  const sameUnit = !!balanceUnit && !!unit && balanceUnit.code === unit.code && balanceUnit.scale === unit.scale;
  const funds = sameUnit ? fundsState({ available: true, balance: { booked: "", held: "", available: balance ?? "" }, per_call_hold: status?.per_call_hold }) : "unknown";
  const limit = personal ? limitLine(status) : null;
  const limitText = limit && (limit.amount !== undefined
    ? c(limit.key, { amount: money(limit.amount, unit, locale) ?? limit.amount, period: c(limit.period!) }) : c(limit.key));
  return <section className="ag-money" aria-label={c("moneyStatus")}>
    <div className="ag-money-row">
      {payers.length > 1 ? <label className="ag-payer" htmlFor={`${id}-payer`}><span>{c("payer")}</span>
        <select id={`${id}-payer`} className="input sm" value={payerKey(payer)} disabled={disabled}
          onChange={(event) => { const next = payers.find((p) => payerKey(p) === event.target.value); if (next) state.setPayer(next); }}>
          {payers.map((p) => <option key={payerKey(p)} value={payerKey(p)}>{p.kind === "user" ? c("payerPersonal") : c("payerOrganization", { name: p.name })}</option>)}
        </select></label>
        : <span className="ag-chip">{c("payer")} · {c("payerPersonal")}</span>}
      <span className={`ag-chip ag-balance${funds === "empty" ? " is-empty" : funds === "low" ? " is-low" : ""}`} aria-live="polite">
        {shown ? c("balance", { amount: shown }) : status === undefined ? "…" : c("balanceUnknown")}
        {held && status?.balance?.held !== "0" && <span className="ag-chip-sub">{c("balanceHeld", { amount: held })}</span>}
      </span>
      {limitText && (limit!.key === "limitRemaining" ? <span className="ag-chip">{limitText}</span>
        : <Link className="ag-chip ag-chip-link" to="/agent/settings">{limitText}</Link>)}
      {status?.environment === "test" && <span className="badge warn">{c("testWallet")}</span>}
      <span className="spacer" />
      {state.offer && <ModelPicker offers={[state.offer]} />}
    </div>
    {(funds === "empty" || funds === "low") && <p className={`ag-low${funds === "empty" ? " is-empty" : ""}`}>
      <span>{c(funds === "empty" ? "emptyBalance" : "lowBalance")}</span>
      <a className="btn sm primary" href={state.walletUrl}>{c("topUp")}</a>
    </p>}
    {state.payersError && <p className="muted small">{c("payerUnavailable")}</p>}
  </section>;
}

/** The most this turn can cost, before sending (the active consent's per-run budget, else the
 *  offer's per-call hold until the quote fixes the run's total). */
export function TurnMaximum({ maximum }: { maximum: { key: "turnMaximum" | "turnMaximumCall"; amount: string; unit: { code: string; scale: number } } | null }) {
  const { c, locale } = useChatText();
  if (!maximum) return null;
  const amount = money(maximum.amount, maximum.unit, locale);
  return amount ? <span className="ag-max">{c(maximum.key, { amount })}</span> : null;
}
