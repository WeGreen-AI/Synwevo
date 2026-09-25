import { t as tr } from "@synwevo/ui";
import "./messages";
/**
 * The per-row action menu on the member and invitation lists — the shared Menu with the
 * account shell's item shape. It renders at the document root and flips above the button near
 * the bottom of the screen, so the last row's menu is never clipped by the scrolling table.
 */
import { Menu } from "@synwevo/ui";

export interface RowMenuItem {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  /** Why it is disabled. */
  hint?: string;
  danger?: boolean;
}

export function RowMenu({ items, label }: { items: RowMenuItem[]; label: string }) {
  return <Menu ariaLabel={label} label={tr("操作")} items={items} />;
}
