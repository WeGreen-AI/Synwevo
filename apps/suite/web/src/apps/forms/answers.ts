/** How a submitted answer reads to a reviewer: one sentence of text per value, and a repeating
 *  group as a table whose every cell is worded by the same rule as a single answer.
 *
 *  No JSX here (`createElement`), so the tests render exactly what the submission page shows. */
import { createElement, type ReactNode } from "react";
import { formatDate } from "@synwevo/ui/i18n-core";

type T = (key: string, params?: Record<string, any>) => string;
/** A field as `forms.submission.get` lists it. */
export interface Shown { id?: string; type: string; label?: string; options?: { value: string; label: string }[]; fields?: Shown[] }

/** A single value as text, for every type a repeating group may hold and for the top-level
 *  fields alike. An empty answer is "". */
export function answerText(t: T, f: Shown, v: any): string {
  if (v === undefined || v === null || v === "") return "";
  switch (f.type) {
    case "money": return typeof v === "object" ? `${v.amount ?? ""} ${v.currency ?? ""}`.trim() : String(v);
    case "boolean": return v === true ? t("是") : t("否");
    case "select": return f.options?.find((o) => o.value === v)?.label ?? String(v);
    case "multiselect": return (Array.isArray(v) ? v : [v]).map((x) => f.options?.find((o) => o.value === x)?.label ?? String(x)).join(t("、"));
    case "date": {
      // A calendar day, not a moment: read and shown in UTC so no time zone moves it.
      const m = typeof v === "string" ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(v) : null;
      return m ? formatDate(Date.UTC(+m[1], +m[2] - 1, +m[3]), { dateStyle: "medium", timeZone: "UTC" }) : String(v);
    }
    case "datetime": {
      const at = new Date(v).getTime();
      return Number.isNaN(at) ? String(v) : formatDate(at, { dateStyle: "medium", timeStyle: "short" });
    }
    default: return typeof v === "object" ? JSON.stringify(v) : String(v);
  }
}

/** A value as the submission page shows it. */
export function formatAnswer(t: T, f: Shown, v: any): ReactNode {
  if (f.type !== "repeat") return createElement("span", { className: "fm-pre" }, answerText(t, f, v));
  const subs = f.fields ?? [];
  const rows = Array.isArray(v) ? v : [];
  return createElement("div", { className: "fm-table-wrap" },
    createElement("table", { className: "fm-table" },
      createElement("thead", null, createElement("tr", null, subs.map((s) => createElement("th", { key: s.id, scope: "col" }, s.label ?? "")))),
      createElement("tbody", null, rows.map((row, i) => createElement("tr", { key: i },
        subs.map((s) => createElement("td", { key: s.id }, answerText(t, s, s.id ? (row ?? {})[s.id] : undefined))))))));
}
