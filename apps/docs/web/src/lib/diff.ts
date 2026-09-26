/** Character-level difference between two short texts (a block of a template before and after an
 *  edit), as runs to render: equal, inserted, deleted. A plain longest-common-subsequence over
 *  characters — the inputs are bounded excerpts (a few hundred characters), where this is exact
 *  and instant; nothing is guessed. Combining marks and surrogate pairs stay together because
 *  the texts are split by code point, not by UTF-16 unit. */
export type DiffRun = { kind: "equal" | "ins" | "del"; text: string };

export function charDiff(before: string, after: string): DiffRun[] {
  const a = Array.from(before), b = Array.from(after);
  const n = a.length, m = b.length;
  if (!n && !m) return [];
  if (!n) return [{ kind: "ins", text: after }];
  if (!m) return [{ kind: "del", text: before }];
  // lcs[i][j] = length of the longest common subsequence of a[i..] and b[j..]
  const lcs: Uint16Array[] = [];
  for (let i = 0; i <= n; i++) lcs.push(new Uint16Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const runs: DiffRun[] = [];
  const push = (kind: DiffRun["kind"], ch: string) => {
    const last = runs[runs.length - 1];
    if (last && last.kind === kind) last.text += ch; else runs.push({ kind, text: ch });
  };
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { push("equal", a[i]); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { push("del", a[i]); i++; }
    else { push("ins", b[j]); j++; }
  }
  while (i < n) push("del", a[i++]);
  while (j < m) push("ins", b[j++]);
  return runs;
}

/** True when the difference is worth showing as a diff at all (something changed). */
export const hasChange = (runs: DiffRun[]) => runs.some((r) => r.kind !== "equal");
