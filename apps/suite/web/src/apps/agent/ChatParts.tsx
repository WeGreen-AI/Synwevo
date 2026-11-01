/** The chat's small, self-contained pieces: the AI-content label, each answer's ledger cost,
 *  refusals with their next step, "what changed" between two pricings, the most an answer can
 *  cost, and the thinking status. Everything shown here is a server fact (the ledger, the
 *  governed offer, the run bound, a refusal code) put into words; nothing is estimated in the page. */
import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useI18n, type Params } from "@synwevo/ui";
import type { Pricing } from "./budget";
import { chatCopy, type ChatText } from "./chat.messages";
import { perMillion, priceChanges, type MaximumShown } from "./composer";
import { bandText, costSummary, costUnit, formatMoney, formatPerMillion, formatTokens, validTurnCost } from "./cost";
import type { Refusal } from "./refusals";
import type { McpCallFacts } from "./types";
import { useText as useIntegrationText } from "./integrations/shared";

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
 *  applies to the new send); `onSetup` opens the one-time setup step with the draft kept.
 *  `focusTick`: each new value moves focus to the next step, or to the notice when it offers none
 *  (the control the person used is gone, e.g. the setup step's button: addendum-2 W10). */
export function RefusalNotice({ refusal, walletUrl, onRetry, onSetup, onPersonal, busy = false, live = false, focusTick = 0 }: {
  refusal: Refusal; walletUrl?: string | null; onRetry?: () => void; onSetup?: () => void;
  /** Switch the payer to the personal wallet (offered when an organization payer was refused). */
  onPersonal?: () => void; busy?: boolean; live?: boolean; focusTick?: number;
}) {
  const { c } = useChatText();
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusTick) return;
    const notice = box.current;
    (notice?.querySelector<HTMLElement>("a[href], button:not([disabled])") ?? notice)?.focus();
  }, [focusTick]);
  const next = refusal.next;
  return <div ref={box} tabIndex={focusTick ? -1 : undefined} className={`notice ${refusal.kind === "price_changed" ? "info" : "warn"} ag-refusal`}
    role={live ? "alert" : undefined} data-refusal={refusal.kind}>
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
      // The hold is a bound ("at most"): rounded up, so the sentence stays true.
      amount: (minor, unit) => formatMoney(minor, unit, locale, { rounding: "up" }),
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
 *  (`turnMaximum` from the billing status), or an older server's per-call hold, said as such; or
 *  that it is being read, or cannot be shown (a paid send then waits for it, W3). */
export function TurnMaximum({ maximum }: { maximum: MaximumShown | null }) {
  const { c, locale } = useChatText();
  if (!maximum) return null;
  // The person's own cap cannot be used (W8): the way on is Agent settings.
  const settings = maximum.key === "turnMaximumCapLow" || maximum.key === "turnMaximumCapUnusable"
    ? <> <Link to="/agent/settings">{c("nextSettings")}</Link></> : null;
  if (!("amount" in maximum)) return <span className="ag-max">{c(maximum.key)}{settings}</span>;
  let amount: string;
  // A bound ("at most", "below one call's hold"): rounded up, so the sentence stays true.
  try { amount = formatMoney(maximum.amount, maximum.unit, locale, { rounding: "up" }); } catch { return null; }
  return <span className="ag-max">{c(maximum.key, { amount })}{settings}</span>;
}

/** "Thinking" without revealing the reasoning itself. */
export function ThinkingStatus() {
  const { c } = useChatText();
  return <p className="ag-thinking" role="status">
    <span className="ag-dots" aria-hidden="true"><i /><i /><i /></span>
    <span>{c("thinking")}</span><span className="ag-thinking-note">{c("thinkingNote")}</span>
  </p>;
}

/** The facts of an external MCP tool call beside its action card: which connection and tool,
 *  that it runs outside Synwevo, how long it took, and whether the server answered with an
 *  error or the result was cut. The arguments and result themselves are the card's Values. */
export function McpCallLine({ mcp, pending }: { mcp: McpCallFacts; pending: boolean }) {
  const { m, duration } = useIntegrationText();
  return <div className="ag-mcp small" data-mcp-connection={mcp.connection}>
    <p className="row wrap muted small">
      <span className="badge info">{m("external")}</span>
      <span>{m("cardConnection", { name: mcp.connection_name })}</span>
      <span>{m("cardTool", { tool: mcp.tool })}</span>
      {mcp.duration_ms != null && <span>{m("cardDuration", { time: duration(mcp.duration_ms) })}</span>}
    </p>
    {(mcp.is_error || mcp.truncated) && <p className="row wrap">
      {mcp.is_error && <span className="badge err">{m("cardIsError")}</span>}
      {mcp.truncated && <span className="badge warn">{m("cardTruncated")}</span>}
    </p>}
    {pending && <p className="notice warn">{m("cardExternalWarning")}</p>}
  </div>;
}
