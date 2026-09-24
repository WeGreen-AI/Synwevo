/** Colours that come out of uploaded files, made safe to put into a style attribute.
 *
 *  The DOCX/XLSX/PPTX renderers pass shading, highlight and font colours straight through from
 *  the file (`w:shd/@w:fill`, `w:highlight/@w:val`, cell fills), and a React style value is not
 *  sanitised: `background: "#000 url(https://attacker/x)"` is a valid shorthand, so opening a
 *  crafted source or template made the browser fetch an address of the file author's choosing —
 *  the viewer's IP and the moment they looked, sent to a stranger. Stored built-in editor JSON
 *  reaches the same attributes through the API without going through the editor.
 *
 *  Only plain colour syntax passes: hex, rgb()/rgba() with numbers, or a bare keyword. None of
 *  them can contain `url(`, `image-set(` or a second shorthand layer. Anything else is dropped
 *  and the element keeps its default colour — a missing tint is harmless, a request is not.
 *
 *  No imports: tested directly with `node --test`.
 */
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const BARE_HEX = /^(?:[0-9a-f]{6}|[0-9a-f]{8})$/i;
const NUM = String.raw`\s*\d{1,3}(?:\.\d+)?%?\s*`;
const RGB = new RegExp(String.raw`^rgba?\(${NUM},${NUM},${NUM}(?:,\s*(?:0|1|0?\.\d+|\d{1,3}%)\s*)?\)$`, "i");
const KEYWORD = /^[a-z]{3,24}$/i;

export function cssColor(v: unknown): string | undefined {
  if (typeof v !== "string") return undefined;
  const s = v.trim();
  if (!s || s.length > 40) return undefined;
  if (HEX.test(s)) return s;
  if (BARE_HEX.test(s)) return `#${s}`;             // OOXML stores fills without the '#'
  if (RGB.test(s)) return s;
  if (KEYWORD.test(s) && s.toLowerCase() !== "auto") return s;
  return undefined;
}

/** Word's highlight palette (ST_HighlightColor). Values outside it fall back to cssColor. */
export const HIGHLIGHT: Record<string, string> = {
  yellow: "#ffff00", green: "#00ff00", cyan: "#00ffff", magenta: "#ff00ff", blue: "#0000ff", red: "#ff0000",
  darkBlue: "#000080", darkCyan: "#008080", darkGreen: "#008000", darkMagenta: "#800080", darkRed: "#800000",
  darkYellow: "#808000", darkGray: "#808080", lightGray: "#c0c0c0", black: "#000000", white: "#ffffff",
};

export function highlightColor(v: unknown): string | undefined {
  if (typeof v === "string" && Object.prototype.hasOwnProperty.call(HIGHLIGHT, v)) return HIGHLIGHT[v];
  return cssColor(v);
}

/** Colours of mappings and marks, made safe for style attributes and SVG paint.
 *
 *  A mapping's `color` is whatever the client that saved it sent: the server keeps it as given
 *  (only slot colours are checked), so any editor in the workspace can store
 *  `url(//host/x)` and every other member's browser would fetch it when the badge is drawn.
 *  Mapping colours come from a fixed hex palette, so only `#rgb` / `#rrggbb` pass — which also
 *  keeps them valid input for hexA(). Anything else becomes the neutral grey of an unmapped field.
 */
export const MARK_GREY = "#94a3b8";
const MARK_HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/i;

export function markColor(v: unknown, fallback: string = MARK_GREY): string {
  if (typeof v !== "string") return fallback;
  const s = v.trim();
  return MARK_HEX.test(s) ? s : fallback;
}
