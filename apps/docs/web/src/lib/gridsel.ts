/** Keyboard range selection on a grid, with an explicit anchor.
 *
 *  The grid used to extend a Shift+Arrow selection from `r2`/`c2` of the normalised rectangle and
 *  then normalise again, which loses which corner was the anchor: from C5, Shift+↑ gave rows 4–5
 *  and the next Shift+↑ collapsed to row 4 instead of growing to 3–5. Keeping the anchor and the
 *  moving (active) cell separately is the spreadsheet model and fixes both directions.
 *
 *  No imports: tested with `node --test`.
 */
export interface Cell { r: number; c: number }
export interface Rect { r1: number; c1: number; r2: number; c2: number }

export function rectOf(a: Cell, b: Cell): Rect {
  return { r1: Math.min(a.r, b.r), c1: Math.min(a.c, b.c), r2: Math.max(a.r, b.r), c2: Math.max(a.c, b.c) };
}

export function moveCursor(anchor: Cell, active: Cell, dr: number, dc: number, extend: boolean, rows: number, cols: number):
  { anchor: Cell; active: Cell; rect: Rect } {
  const clampR = (r: number) => Math.max(0, Math.min(Math.max(0, rows - 1), r));
  const clampC = (c: number) => Math.max(0, Math.min(Math.max(0, cols - 1), c));
  const next = { r: clampR(active.r + dr), c: clampC(active.c + dc) };
  const a = extend ? anchor : next;
  return { anchor: a, active: next, rect: rectOf(a, next) };
}

/** Recover anchor/active from a rectangle chosen with the mouse: the anchor is the corner where
 *  the drag started (top-left after normalising), the active cell the opposite corner. */
export function cornersOf(rect: Rect): { anchor: Cell; active: Cell } {
  return { anchor: { r: rect.r1, c: rect.c1 }, active: { r: rect.r2, c: rect.c2 } };
}
