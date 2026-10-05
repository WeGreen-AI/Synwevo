/** Reporting one answer to the platform's reviewers (the safety package's `agent.message.report`).
 *  The person picks a reason and may add a note; the answer's own text is sent only when they tick
 *  "attach" (off by default), so a report never shares more of a conversation than they chose. */
import { useEffect, useId, useRef, useState } from "react";
import { Modal, useI18n, type Params } from "@synwevo/ui";
import { errorCode } from "./refusals";
import { reportCopy, type ReportText } from "./report.messages";

/** The server's categories (synwevo_core.agent_safety.REPORT_CATEGORIES), in this order. */
export const REPORT_CATEGORIES = ["harmful", "illegal", "privacy", "wrong", "other"] as const;
export type ReportCategory = typeof REPORT_CATEGORIES[number];
/** The server keeps at most this much of a note and of an attached answer (agent_safety.py). */
export const REPORT_NOTE_MAX = 1000;
export const REPORT_EXCERPT_MAX = 2000;

export interface ReportChoice { category: ReportCategory | null; note: string; attach: boolean }
export const initialReport: ReportChoice = { category: null, note: "", attach: false };

/** The operation's input; null until a reason is chosen. The answer's text is never sent from
 *  here: the server attaches it, and only when `include_excerpt` is true. */
export function reportInput(conversationId: string, messageId: string, choice: ReportChoice) {
  if (!choice.category || !REPORT_CATEGORIES.includes(choice.category)) return null;
  const note = choice.note.trim().slice(0, REPORT_NOTE_MAX);
  return { conversation_id: conversationId, message_id: messageId, category: choice.category,
    ...(note ? { note } : {}), include_excerpt: choice.attach === true };
}

/** A refused report in words: "exists" is an outcome (already reported), the rest say why it was
 *  not sent. An unknown failure (network, a gateway) may be retried with the same request. */
export function reportOutcome(error: unknown): "exists" | Extract<ReportText, "failedCategory" | "failedGone" | "failedHidden" | "failed"> {
  const code = errorCode(error);
  if (code === "report_exists") return "exists";
  if (code === "report_category_invalid") return "failedCategory";
  // The answer was hidden after an access change: it may be reported, but never quoted.
  if (code === "report_excerpt_hidden") return "failedHidden";
  if (["message_not_found", "conversation_not_found", "conversation_sealed"].includes(code)) return "failedGone";
  return "failed";
}

export type ReportPhase = { state: "editing" | "sending" } | { state: "sent"; withText: boolean } | { state: "exists" }
  | { state: "failed"; text: ReportText };

/** Send one report and say how it ended: sent (with or without the answer's text, as the server
 *  confirms), already reported, or not sent and why. */
export async function sendReport(send: (input: Record<string, unknown>) => Promise<unknown>,
  input: Record<string, unknown>): Promise<ReportPhase> {
  try {
    const out = await send(input) as { included_excerpt?: unknown } | null | undefined;
    return { state: "sent", withText: out?.included_excerpt === true };
  } catch (error) {
    const outcome = reportOutcome(error);
    return outcome === "exists" ? { state: "exists" } : { state: "failed", text: outcome };
  }
}

/** The outcome line: a status once it is settled, an alert when the report was not sent. */
export function ReportStatus({ phase }: { phase: ReportPhase }) {
  const r = useReportText();
  if (phase.state === "sent" || phase.state === "exists") {
    return <p className={`notice ${phase.state === "sent" ? "ok" : "info"}`} role="status">
      {r(phase.state === "exists" ? "exists" : phase.withText ? "sentWithText" : "sent")}</p>;
  }
  if (phase.state === "sending") return <p role="status">{r("sending")}</p>;
  if (phase.state === "failed") return <p className="notice err" role="alert">{r(phase.text)}</p>;
  return null;
}

export function useReportText() {
  const { t } = useI18n();
  return (key: ReportText, params?: Params) => t(reportCopy[key][0], params);
}

export default function ReportDialog({ conversationId, messageId, send, onClose, onReported }: {
  conversationId: string; messageId: string;
  /** Runs `agent.message.report` (the page's idempotent writer: a retry of the same input reuses its key). */
  send: (input: Record<string, unknown>) => Promise<unknown>;
  onClose: () => void;
  /** The answer is reported (now, or before): its control says so. */
  onReported: (messageId: string) => void;
}) {
  const r = useReportText();
  const id = useId();
  const [choice, setChoice] = useState<ReportChoice>(initialReport);
  const [phase, setPhase] = useState<ReportPhase>({ state: "editing" });
  const [missing, setMissing] = useState(false);
  const done = phase.state === "sent" || phase.state === "exists";
  const busy = phase.state === "sending";
  const closeButton = useRef<HTMLButtonElement>(null);
  // The form is replaced by the outcome: keep keyboard focus in the dialog, on its one action.
  useEffect(() => { if (done) closeButton.current?.focus(); }, [done]);

  async function submit() {
    if (busy || done) return;
    const input = reportInput(conversationId, messageId, choice);
    if (!input) { setMissing(true); return; }
    setPhase({ state: "sending" });
    const next = await sendReport(send, input);
    setPhase(next);
    if (next.state === "sent" || next.state === "exists") onReported(messageId);
  }

  const footer = done
    ? <button type="button" className="btn primary" ref={closeButton} onClick={onClose}>{r("close")}</button>
    : <>
      <button type="button" className="btn" onClick={onClose}>{r("cancel")}</button>
      <button type="submit" form={`${id}-form`} className="btn primary" disabled={busy}>
        {r(busy ? "sending" : phase.state === "failed" ? "retry" : "send")}</button>
    </>;
  return <Modal title={r("title")} onClose={onClose} size="narrow" describedBy={`${id}-intro`} footer={<div className="ag-report-actions">{footer}</div>}>
    <div className="ag-report">
      <p id={`${id}-intro`} className="muted">{r("intro")}</p>
      {done ? <ReportStatus phase={phase} />
        : <form id={`${id}-form`} noValidate onSubmit={(event) => { event.preventDefault(); void submit(); }}>
          <fieldset className="ag-report-reasons" disabled={busy} aria-describedby={missing ? `${id}-missing` : undefined}>
            <legend>{r("reason")}</legend>
            {REPORT_CATEGORIES.map((category, index) => <label key={category} className="ag-check">
              <input type="radio" name={`${id}-reason`} value={category} checked={choice.category === category}
                data-autofocus={index === 0 ? true : undefined}
                onChange={() => { setChoice((c) => ({ ...c, category })); setMissing(false); }} />
              <span>{r(category)}</span>
            </label>)}
          </fieldset>
          {missing && <p id={`${id}-missing`} className="err small" role="alert">{r("chooseReason")}</p>}
          <label className="field" htmlFor={`${id}-note`}>{r("note")}
            <textarea id={`${id}-note`} className="input" rows={3} maxLength={REPORT_NOTE_MAX} value={choice.note} disabled={busy}
              aria-describedby={`${id}-note-hint`} onChange={(event) => setChoice((c) => ({ ...c, note: event.target.value }))} />
          </label>
          <p id={`${id}-note-hint`} className="muted small">{r("noteHint", { count: REPORT_NOTE_MAX })}</p>
          <label className="ag-check">
            <input type="checkbox" checked={choice.attach} disabled={busy} aria-describedby={`${id}-attach-hint`}
              onChange={(event) => setChoice((c) => ({ ...c, attach: event.target.checked }))} />
            <span>{r("attach")}<small id={`${id}-attach-hint`} className="muted">{r("attachHint", { count: REPORT_EXCERPT_MAX })}</small></span>
          </label>
          <ReportStatus phase={phase} />
        </form>}
    </div>
  </Modal>;
}
