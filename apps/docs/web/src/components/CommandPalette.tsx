import { useEffect, useMemo, useRef, useState } from "react";

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
 *  while composing are never treated as shortcuts. */
export default function CommandPalette({ commands, open, onClose }: { commands: Command[]; open: boolean; onClose: () => void }) {
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    const usable = commands.filter((c) => !c.disabled || t);
    if (!t) return usable;
    return usable.filter((c) => `${c.label} ${c.hint || ""} ${c.group}`.toLowerCase().includes(t));
  }, [commands, q]);

  useEffect(() => {
    if (open) {
      setQ("");
      setI(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);
  useEffect(() => { setI(0); }, [q]);
  useEffect(() => {
    listRef.current?.querySelectorAll<HTMLElement>("[data-cmd]")[i]?.scrollIntoView({ block: "nearest" });
  }, [i]);

  if (!open) return null;
  const pick = (c: Command | undefined) => {
    if (!c || c.disabled) return;
    onClose();
    setTimeout(c.run, 0);       // let the dialog close before the action opens another one
  };
  return (
    <div className="cmdk-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label="命令面板"
        onKeyDown={(e) => {
          if (e.nativeEvent.isComposing) return;
          if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(hits.length - 1, x + 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(0, x - 1)); }
          if (e.key === "Enter") { e.preventDefault(); pick(hits[i]); }
          if (e.key === "Escape") { e.preventDefault(); onClose(); }
        }}>
        <input ref={inputRef} className="cmdk-input" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="搜索命令…（↑↓ 选择，回车执行，Esc 关闭）" aria-label="搜索命令" role="combobox" aria-expanded aria-controls="cmdk-list" />
        <div className="cmdk-list" id="cmdk-list" role="listbox" ref={listRef}>
          {hits.length === 0 && <div className="cmdk-empty">没有匹配的命令</div>}
          {hits.map((c, k) => {
            const head = k === 0 || hits[k - 1].group !== c.group;
            return (
              <div key={c.id}>
                {head && <div className="cmdk-group">{c.group}</div>}
                <div data-cmd role="option" aria-selected={k === i} aria-disabled={c.disabled}
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
        <div className="cmdk-foot tiny muted">⌘K / Ctrl+K 打开 · ? 查看全部快捷键</div>
      </div>
    </div>
  );
}
