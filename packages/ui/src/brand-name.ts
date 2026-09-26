/** The accessible name of a brand link with a `label`: it must contain the words people see
 *  ("Synwevo Account"), so voice control can target it, and must not say "Synwevo" twice.
 *  "返回平台首页" becomes "Synwevo Account · 返回平台首页"; "Synwevo 首页" next to the visible
 *  "Synwevo Workspace" becomes "Synwevo Workspace 首页". */
export function brandLinkName(product: string | undefined, label: string | undefined): string | undefined {
  if (!label) return undefined;
  const visible = product ? `Synwevo ${product}` : "Synwevo";
  if (label.includes(visible)) return label;
  if (label.startsWith("Synwevo")) return visible + label.slice("Synwevo".length);
  return `${visible} · ${label}`;
}
