import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { isImeKey, useModalFocus } from "@synwevo/ui";

export interface Command {
  id: string;
  label: string;
  hint?: string;
  keys?: string;
  group: string;
  disabled?: boolean;
  run: () => void;
}

/** Command palette (⌘K / Ctrl+K).  Every entry runs a real action of the current screen;
 *  typing filters by label, hint and group.  Safe with Chinese input methods: keys typed
 *  while composing (isComposing, or keyCode 229 as Safari reports it) are never treated as
 *  shortcuts.  It is a modal like any other: Tab stays inside, Escape closes only the palette,
 *  and focus goes back to where it was.  The caller mounts it only while it is open. */
export default function CommandPalette({ commands, onClose }: { commands: Command[]; onClose: () => void }) {
  useI18n();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const uid = useId();
  const { onKeyDown: trapKeys, isTop } = useModalFocus(boxRef, onClose);

  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    const usable = commands.filter((c) => !c.disabled || t);
    if (!t) return usable;
    return usable.filter((c) => `${c.label} ${c.hint || ""} ${c.group}`.toLowerCase().includes(t));
  }, [commands, q]);

  useEffect(() => { setI(0); }, [q]);
  useEffect(() => {
    listRef.current?.querySelectorAll<HTMLElement>("[data-cmd]")[i]?.scrollIntoView({ block: "nearest" });
  }, [i]);

  const optId = (k: number) => `${uid}-opt-${k}`;
  const pick = (c: Command | undefined) => {
    if (!c || c.disabled) return;
    onClose();
    setTimeout(c.run, 0);       // let the palette close (and hand focus back) before the action opens anything
  };
  return (
    <div className="cmdk-back" onMouseDown={(e) => { if (e.target === e.currentTarget && isTop()) onClose(); }}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label={tr("命令面板")} ref={boxRef} tabIndex={-1}
        onKeyDown={(e) => {
          if (isImeKey(e)) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(hits.length - 1, x + 1)); return; }
          if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(0, x - 1)); return; }
          if (e.key === "Enter") { e.preventDefault(); pick(hits[i]); return; }
          trapKeys(e);          // Escape (this palette only, propagation stopped) and the Tab trap
        }}>
        <input className="cmdk-input" value={q} onChange={(e) => setQ(e.target.value)} data-autofocus
          placeholder={tr("搜索命令…（↑↓ 选择，回车执行，Esc 关闭）")} aria-label={tr("搜索命令")} role="combobox" aria-expanded="true"
          aria-controls={`${uid}-list`} aria-autocomplete="list" aria-activedescendant={hits[i] ? optId(i) : undefined} />
        <div className="cmdk-list" id={`${uid}-list`} role="listbox" aria-label={tr("命令")} ref={listRef}>
          {hits.length === 0 && <div className="cmdk-empty" role="presentation">{tr("没有匹配的命令")}</div>}
          {hits.map((c, k) => {
            const head = k === 0 || hits[k - 1].group !== c.group;
            return (
              <div key={c.id} role="presentation">
                {head && <div className="cmdk-group" role="presentation">{c.group}</div>}
                <div data-cmd id={optId(k)} role="option" aria-selected={k === i} aria-disabled={c.disabled || undefined}
                  className={`cmdk-item${k === i ? " on" : ""}${c.disabled ? " off" : ""}`}
                  onMouseMove={() => setI(k)} onClick={() => pick(c)}>
                  <span className="grow">
                    <span className="t">{c.label}</span>
                    {c.hint && <span className="tiny muted" style={{ marginLeft: 8 }}>{c.hint}</span>}
                  </span>
                  {c.keys && <kbd className="kbd">{c.keys}</kbd>}
                </div>
              </div>
            );
          })}
        </div>
        <div className="cmdk-foot tiny muted">{tr("⌘K / Ctrl+K 打开 · ? 查看全部快捷键")}</div>
      </div>
    </div>
  );
}
