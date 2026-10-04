/** A reviewer reads a submission's repeating group as a table of worded values (acceptance D2:
 *  text, number and date cells rendered as "[object Object]"). Rendered with React's own server
 *  renderer, the same element tree the submission page mounts. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { setLocale, t } from "@synwevo/ui/i18n-core";
import "../src/apps/forms/messages.ts";
import { formatAnswer } from "../src/apps/forms/answers.ts";

// A group as `forms.submission.get` lists it (`_field_list`), and answers as the server stores them.
const GROUP = { id: "f_lines001", type: "repeat", label: "Lines", fields: [
  { id: "f_desc0001", type: "text", label: "What" },
  { id: "f_qty00001", type: "number", label: "Qty" },
  { id: "f_day00001", type: "date", label: "Day" },
  { id: "f_cost0001", type: "money", label: "Cost" },
  { id: "f_kind0001", type: "select", label: "Kind", options: [{ value: "taxi", label: "Taxi fare" }] },
  { id: "f_ok000001", type: "boolean", label: "Receipt" },
] };
const ROWS = [
  { f_desc0001: "Airport <ride>", f_qty00001: "2.50", f_day00001: "2026-10-05", f_cost0001: { amount: "88.00", currency: "CNY" },
    f_kind0001: "taxi", f_ok000001: true },
  { f_desc0001: "Line two", f_qty00001: "1" },
];

function cells(html: string): string[] {
  return [...html.matchAll(/<td>(.*?)<\/td>/g)].map((m) => m[1]);
}

for (const [locale, yes, day] of [["zh-Hans", "是", "2026年10月5日"], ["zh-Hant", "是", "2026年10月5日"], ["en", "Yes", "Oct 5, 2026"]] as const) {
  test(`a repeating group's cells read as values (${locale})`, () => {
    setLocale(locale);
    const html = renderToStaticMarkup(formatAnswer(t, GROUP, ROWS) as any);
    assert.doesNotMatch(html, /\[object Object\]/);
    assert.deepEqual(cells(html), [
      "Airport &lt;ride&gt;", "2.50", day, "88.00 CNY", "Taxi fare", yes,
      "Line two", "1", "", "", "", "",
    ]);
    assert.match(html, /<th scope="col">What<\/th>/);
  });
}

test("single answers of every type read as text, never as an object", () => {
  setLocale("en");
  for (const [f, v, want] of [
    [{ type: "text" }, "a\nb", "a\nb"],
    [{ type: "number" }, "12.50", "12.50"],
    [{ type: "date" }, "2026-01-31", "Jan 31, 2026"],
    [{ type: "money" }, { amount: "1.00", currency: "USD" }, "1.00 USD"],
    [{ type: "multiselect", options: [{ value: "a", label: "Apple" }, { value: "b", label: "Pear" }] }, ["a", "b"], "Apple, Pear"],
    [{ type: "boolean" }, false, "No"],
  ] as const) {
    assert.equal(renderToStaticMarkup(formatAnswer(t, f as any, v) as any), `<span class="fm-pre">${want}</span>`);
  }
});
