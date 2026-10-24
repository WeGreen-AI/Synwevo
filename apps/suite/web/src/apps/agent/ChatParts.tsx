/** The chat's small, self-contained pieces: the AI-content label, each answer's ledger cost,
 *  refusals with their next step, "what changed" between two pricings, the most an answer can
 *  cost, and the thinking status. Everything shown here is a server fact (the ledger, the
 *  governed offer, the run bound, a refusal code) put into words; nothing is estimated in the page. */
import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { useI18n, type Params } from "@synwevo/ui";
import type { Pricing } from "./budget";
import { chatCopy, type ChatText } from "./chat.messages";
import { perMillion, priceChanges, type MoneyUnit } from "./composer";
import { bandText, costSummary, costUnit, formatMoney, formatPerMillion, formatTokens, validTurnCost } from "./cost";
import type { Refusal } from "./refusals";

export function useChatText() {
  const { t, locale } = useI18n();
  return { c: (key: ChatText, params?: Params) => t(chatCopy[key][0], params), t, locale };
}

/** DeepSeek Open Platform terms 8.1 and the owner's global decision: visible, never loud. */
export function AiLabel() {
  const { c } = useChatText();
  return <p className="ag-ai-label"><span className="ag-ai-mark" aria-hidden="true">AI</span>{c("aiLabel")}</p>;
}

/** The settled cost beside an answer, with an expandable breakdown. Renders nothing for a turn
 *  without a ledger run (or a malformed value): no figure is better than a guessed one. */
export function TurnCostLine({ cost, payerName }: { cost: unknown;
  /** The organization's name for an organization-paid answer, when the payer list has it. */
  payerName?: (payer: { kind: "organization"; id: string }) => string | null | undefined }) {
  const { c, locale } = useChatText();
  const [open, setOpen] = useState(false);
  const id = useId();
  const value = validTurnCost(cost);
  if (!value) return null;
  let money: (minor: string) => string;
  try {
    const unit = costUnit(value);
    formatMoney("0", unit, locale);
    money = (minor) => formatMoney(minor, unit, locale);
  } catch { return null; }
  const summary = costSummary(value);
  const text = c(summary.key, summary.amount !== undefined ? { amount: money(summary.amount) } : undefined);
  const band = bandText(value.band);
  const tokens = (count: number) => c("tokensCount", { count: formatTokens(count, locale) });
  const row = (label: string, content: string) => <div><dt>{label}</dt><dd>{content}</dd></div>;
  return <div className={`ag-cost is-${value.state}`}>
    {/* Keyed by state so the reveal plays again when a held or pending figure settles. */}
    <button key={value.state} type="button" className="ag-cost-toggle" aria-expanded={open} aria-controls={`${id}-cost`}
      onClick={() => setOpen((v) => !v)}>
      <span className="ag-cost-dot" aria-hidden="true" />
      <span className="ag-cost-summary">{text}</span>
      <span className="sr-only">{c("costDetails")}</span>
      <span className="ag-chevron" aria-hidden="true" />
    </button>
    <div id={`${id}-cost`} className="ag-cost-detail" role="region" aria-label={c("costDetails")} hidden={!open}>
      <dl>
        {row(c("costCharged"), money(value.charged_minor))}
        {value.held_minor !== "0" && row(c("costHeldLabel"), money(value.held_minor))}
        {row(c("costState"), c(value.state === "settled" ? "stateSettled" : value.state === "held" ? "stateHeld" : "statePending"))}
        {value.payer && row(c("costPayer"), value.payer.kind === "user" ? c("payerPersonal")
          : (() => { const name = payerName?.(value.payer as { kind: "organization"; id: string }); return name ? c("payerOrganization", { name }) : c("payerOrganizationUnnamed"); })())}
        {value.model && row(c("costModel"), value.model)}
        {band && row(c("costBand"), c(band))}
        {row(c("tokensCached"), tokens(value.tokens.input_cached))}
        {row(c("tokensUncached"), tokens(value.tokens.input_uncached))}
        {row(c("tokensOutput"), tokens(value.tokens.output))}
        {row(c("tokensReasoning"), tokens(value.tokens.reasoning))}
      </dl>
      <p className="ag-cost-note">{c("costSource")}</p>
    </div>
  </div>;
}

/** A refusal in a sentence, and the one thing to do about it. `live` announces a fresh refusal;
 *  refusals already in the transcript are static text. `onRetry` sends again (a changed price
 *  applies to the new send); `onSetup` opens the one-time setup step with the draft kept. */
export function RefusalNotice({ refusal, walletUrl, onRetry, onSetup, onPersonal, busy = false, live = false }: {
  refusal: Refusal; walletUrl?: string | null; onRetry?: () => void; onSetup?: () => void;
  /** Switch the payer to the personal wallet (offered when an organization payer was refused). */
  onPersonal?: () => void; busy?: boolean; live?: boolean;
}) {
  const { c } = useChatText();
  const next = refusal.next;
  return <div className={`notice ${refusal.kind === "price_changed" ? "info" : "warn"} ag-refusal`} role={live ? "alert" : undefined}
    data-refusal={refusal.kind}>
    <p>{c(refusal.text)}</p>
    {next === "topup" && walletUrl && <a className="btn sm primary" href={walletUrl}>{c("nextTopUp")}</a>}
    {next === "wallet" && walletUrl && <a className="btn sm" href={walletUrl}>{c("nextWallet")}</a>}
    {next === "personal" && onPersonal && <button type="button" className="btn sm" disabled={busy} onClick={onPersonal}>{c("nextPersonal")}</button>}
    {next === "settings" && <Link className="btn sm" to="/agent/settings">{c("nextSettings")}</Link>}
    {next === "setup" && onSetup && <button type="button" className="btn sm primary" disabled={busy} onClick={onSetup}>{c("nextSetup")}</button>}
    {next === "retry" && onRetry && <button type="button" className="btn sm primary" disabled={busy} onClick={onRetry}>
      {c(refusal.kind === "price_changed" ? "nextRetryNewPrice" : "nextRetry")}</button>}
  </div>;
}

/** What differs between the pricing of the person's last answer and the pricing on offer now;
 *  nothing when they are the same (the notice's sentence stands alone). */
export function PriceChangeList({ before, after }: { before: Pricing; after: Pricing }) {
  const { c, locale } = useChatText();
  const id = useId();
  let changes: ReturnType<typeof priceChanges>;
  try {
    changes = priceChanges(before, after, {
      rate: (pricing, key) => formatPerMillion(pricing, key, locale, perMillion(pricing, key)),
      amount: (minor, unit) => formatMoney(minor, unit, locale),
    });
  } catch { return null; }
  if (!changes.length) return null;
  return <section className="ag-price-change" aria-labelledby={`${id}-title`}>
    <h3 id={`${id}-title`}>{c("changedTitle")}</h3>
    <dl>{changes.map((change) => <div key={change.field}>
      <dt>{c(change.field)}</dt>
      <dd><del><span className="sr-only">{c("changedBefore")} </span>{change.before}</del>
        <span aria-hidden="true"> → </span><ins><span className="sr-only">{c("changedAfter")} </span>{change.after}</ins></dd>
    </div>)}</dl>
  </section>;
}

/** The most the next answer can cost, before sending: the run bound the server will issue
 *  (`turnMaximum` from the billing status), or an older server's per-call hold, said as such. */
export function TurnMaximum({ maximum }: { maximum: { key: "turnMaximum" | "turnMaximumCall"; amount: string; unit: MoneyUnit } | null }) {
  const { c, locale } = useChatText();
  if (!maximum) return null;
  let amount: string;
  try { amount = formatMoney(maximum.amount, maximum.unit, locale); } catch { return null; }
  return <span className="ag-max">{c(maximum.key, { amount })}</span>;
}

/** "Thinking" without revealing the reasoning itself. */
export function ThinkingStatus() {
  const { c } = useChatText();
  return <p className="ag-thinking" role="status">
    <span className="ag-dots" aria-hidden="true"><i /><i /><i /></span>
    <span>{c("thinking")}</span><span className="ag-thinking-note">{c("thinkingNote")}</span>
  </p>;
}
