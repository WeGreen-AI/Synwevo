/** The composer's emoji picker: the Unicode catalogue in groups, a "recent" row remembered per
 *  browser, and a keyboard path — group tabs, then arrows across the grid, Enter picks, Escape
 *  returns to the button. Anchored above or below the button by the shared layer placement. */
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useI18n } from "@synwevo/ui";
import { EMOJI_GROUPS, RECENT_KEY, pushRecent, readRecent } from "../lib/emoji";
import { Popover } from "./layers";

const COLS = 8;

export function EmojiPicker({ open, anchor, onClose, onPick }: {
  open: boolean; anchor: RefObject<HTMLElement | null>; onClose: (refocus: boolean) => void; onPick: (emoji: string) => void;
}) {
  const { t } = useI18n();
  const [recent, setRecent] = useState<string[]>(() => readRecent(typeof localStorage !== "undefined" ? localStorage : null));
  const groups = useMemo(() => (recent.length ? [{ name: "最近使用", items: recent }, ...EMOJI_GROUPS] : EMOJI_GROUPS), [recent]);
  const [tab, setTab] = useState(0);
  const [focus, setFocus] = useState(0);
  const grid = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) { setTab(0); setFocus(0); } }, [open]);
  const items = groups[Math.min(tab, groups.length - 1)]?.items || [];
  useEffect(() => {
    if (!open) return;
    grid.current?.querySelectorAll<HTMLButtonElement>("button")[focus]?.focus({ preventScroll: false });
  }, [focus, open, tab]);
  const pick = (e: string) => {
    const next = pushRecent(recent, e);
    setRecent(next);
    try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* not remembered */ }
    onPick(e);
  };
  const onGridKey = (e: React.KeyboardEvent) => {
    const n = items.length; if (!n) return;
    const move = (d: number) => { e.preventDefault(); setFocus(f => Math.max(0, Math.min(n - 1, f + d))); };
    if (e.key === "ArrowRight") move(1); else if (e.key === "ArrowLeft") move(-1);
    else if (e.key === "ArrowDown") move(COLS); else if (e.key === "ArrowUp") move(-COLS);
    else if (e.key === "Home") { e.preventDefault(); setFocus(0); } else if (e.key === "End") { e.preventDefault(); setFocus(n - 1); }
  };
  return <Popover open={open} anchor={anchor} onClose={onClose} label={t("表情")} className="msg-emoji" align="start" autoFocus={false}>
    <div className="msg-emoji-tabs" role="tablist" aria-label={t("表情分类")}>
      {groups.map((g, i) => <button key={g.name} type="button" role="tab" aria-selected={i === tab} className={`msg-emoji-tab ${i === tab ? "on" : ""}`}
        tabIndex={i === tab ? 0 : -1} onClick={() => { setTab(i); setFocus(0); }}
        onKeyDown={e => { if (e.key === "ArrowRight") { e.preventDefault(); setTab((i + 1) % groups.length); } if (e.key === "ArrowLeft") { e.preventDefault(); setTab((i - 1 + groups.length) % groups.length); } }}
        title={t(g.name)}>
        <span aria-hidden="true">{g.items[0]}</span><span className="sr-only">{t(g.name)}</span>
      </button>)}
    </div>
    <div className="msg-emoji-grid" ref={grid} role="group" aria-label={t(groups[tab]?.name || "表情")} onKeyDown={onGridKey} style={{ ["--cols" as any]: COLS }}>
      {items.map((e, i) => <button key={`${e}-${i}`} type="button" tabIndex={i === focus ? 0 : -1} data-autofocus={i === 0 ? true : undefined}
        onFocus={() => setFocus(i)} onClick={() => pick(e)} aria-label={e}>{e}</button>)}
    </div>
  </Popover>;
}
