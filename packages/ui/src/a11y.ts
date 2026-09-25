/** Keyboard, focus and motion helpers shared by the three shells.
 *
 *  This file has no imports on purpose: it holds the decisions (is this keystroke part of an IME
 *  composition? which dialog is on top? should this scroll animate?) as plain functions, so they
 *  can be tested with `node --test` without a DOM or a bundler, and every shell makes the same
 *  decision the same way.
 */

/** Minimal shape of a keyboard event: a React synthetic event or a native KeyboardEvent. */
export interface KeyLike {
  key?: string;
  keyCode?: number;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean; keyCode?: number };
}

/** True while the keystroke belongs to an input-method composition (pinyin, kana, …).
 *
 *  Enter confirms a candidate and Escape cancels one; neither is meant for the page. Checking
 *  `isComposing` alone is not enough: Safari reports the keydown that commits a candidate with
 *  `isComposing === false` but `keyCode === 229`, and compositionstart can arrive after the
 *  keydown that opened the IME. MDN's keydown guidance is exactly this pair of checks. */
export function isImeKey(e: KeyLike | null | undefined): boolean {
  if (!e) return false;
  const n = e.nativeEvent;
  if (e.isComposing || n?.isComposing) return true;
  return e.keyCode === 229 || n?.keyCode === 229;
}

/** The user asked the OS for less motion. Read at call time so a change applies immediately. */
export function prefersReducedMotion(): boolean {
  try {
    return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Behaviour for programmatic scrolling. `behavior: "smooth"` passed from JS animates even when
 *  the stylesheet sets `scroll-behavior: auto` under reduced motion, so every call site asks here. */
export function scrollBehavior(): "auto" | "smooth" {
  return prefersReducedMotion() ? "auto" : "smooth";
}

/** Elements that take part in the Tab order inside a dialog. */
export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';

/** Where Tab (or Shift+Tab) lands inside a trap of `count` focusable elements when focus is at
 *  `index` (-1 = focus is on the container itself or outside). Wraps at both ends. */
export function trapIndex(count: number, index: number, backwards: boolean): number {
  if (count <= 0) return -1;
  if (index < 0) return backwards ? count - 1 : 0;
  if (backwards) return index === 0 ? count - 1 : index - 1;
  return index === count - 1 ? 0 : index + 1;
}

/** What a key does when the topmost dialog is open but focus is no longer inside it.
 *
 *  That happens without anyone leaving the dialog: the focused button is removed (a footer
 *  replaced after 保存 succeeded) or disabled (both footer buttons while a request runs), and the
 *  browser drops focus to <body>. The dialog's own keydown handler sits on the dialog element,
 *  so it never saw those keys: Escape stopped closing it and Tab walked into the page behind a
 *  dialog that is still modal. Escape closes; Tab and Shift+Tab re-enter at the first or last
 *  control, as they would have from the dialog's own edges. */
export function strayKeyAction(key: string | undefined, shiftKey: boolean): "close" | "first" | "last" | null {
  if (key === "Escape") return "close";
  if (key === "Tab") return shiftKey ? "last" : "first";
  return null;
}

/* ------------------------------------------------------------------ dialog stack
 * Every open modal registers here. Only the top one reacts to Escape and to a click on its
 * backdrop, and page-level shortcuts stay quiet while any is open: a confirm raised over the
 * mapping dialog must not also close the mapping dialog, and ⌘Z must not undo an edit on the
 * page behind a question that is still being asked. */
const stack: string[] = [];
let seq = 0;

export function pushModal(): string {
  const id = `m${++seq}`;
  stack.push(id);
  return id;
}

export function popModal(id: string): void {
  const i = stack.lastIndexOf(id);
  if (i >= 0) stack.splice(i, 1);
}

export function isTopModal(id: string): boolean {
  return stack.length > 0 && stack[stack.length - 1] === id;
}

export function anyModalOpen(): boolean {
  return stack.length > 0;
}

/* The element the pointer last pressed. WebKit does not focus a button on click, so when a
 * dialog opens from a click `document.activeElement` is <body> and the dialog would have nowhere
 * to return focus to; this remembers the real opener. Installed once, on first use. */
let lastPressed: Element | null = null;
let pressListening = false;
export function lastPointerTarget(): HTMLElement | null {
  if (!pressListening && typeof document !== "undefined") {
    pressListening = true;
    document.addEventListener("pointerdown", (e) => {
      const t = e.target instanceof Element ? e.target.closest(FOCUSABLE) : null;
      lastPressed = t;
    }, true);
  }
  // `HTMLElement` may not exist where this module is evaluated without a DOM (the account shell's
  // validation script imports the package under Vite SSR), so test the shape, not the class.
  const el = lastPressed as (HTMLElement & { isConnected: boolean }) | null;
  return el && el.isConnected && typeof el.focus === "function" ? el : null;
}
