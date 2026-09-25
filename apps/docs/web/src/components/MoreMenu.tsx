import { t as tr, Menu } from "@synwevo/ui";

export interface MenuItem { label: string; onClick: () => unknown; disabled?: boolean; title?: string }

/** Overflow menu (⋯ / 更多) on the shared Menu: rendered at the document root, keyboard
 *  operable, never clipped by the pane it sits in. */
export default function MoreMenu({ items, label = tr("更多"), ariaLabel }: { items: MenuItem[]; label?: string; ariaLabel?: string }) {
  return <Menu label={label} ariaLabel={ariaLabel || label} items={items.map((it) => ({ label: it.label, onSelect: it.onClick, disabled: it.disabled, title: it.title }))} />;
}
