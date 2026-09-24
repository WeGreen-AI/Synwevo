/** The 值对照替换 (value map) editor: text in, table out, without eating what is being typed.
 *
 *  The textarea used to display a string re-derived from the parsed table, so every line that did
 *  not parse yet — a new empty line, 「1」 before the 「=」 — vanished on the next render, and
 *  nobody could type a new mapping. The raw text is now kept as typed; this turns it into the
 *  table and names the lines that are not `原值=新值` yet, so the editor can say so.
 *
 *  No imports: tested with `node --test`.
 */
export function parseValueMap(text: string): { table: Record<string, string>; bad: number[] } {
  const table: Record<string, string> = {};
  const bad: number[] = [];
  text.split("\n").forEach((line, i) => {
    if (!line.trim()) return;
    const k = line.indexOf("=");
    if (k > 0) table[line.slice(0, k)] = line.slice(k + 1);
    else bad.push(i + 1);
  });
  return { table, bad };
}

export function formatValueMap(table: Record<string, string> | null | undefined): string {
  return Object.entries(table || {}).map(([k, v]) => `${k}=${v}`).join("\n");
}

/** Same table? Used to avoid overwriting the text being typed when the parent echoes it back. */
export function sameTable(a: Record<string, string> | null | undefined, b: Record<string, string> | null | undefined): boolean {
  const ea = Object.entries(a || {}), eb = Object.entries(b || {});
  return ea.length === eb.length && ea.every(([k, v]) => (b || {})[k] === v);
}
