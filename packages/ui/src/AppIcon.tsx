import { useState, type CSSProperties } from "react";
import { appIconAsset } from "./app-icons/assets";

export type { AppIconId } from "./app-icons/assets";
export type AppIconSize = 16 | 20 | 24 | 32 | 48;

/** Application identity, separate from the parent Brand/Logo and from action icons.
 * Use stable registry IDs or existing module paths. Names, routes of
 * business objects, workspace IDs and the legacy doc/chat hint never select an identity.
 * Decorative by default; pass a translated label only when there is no adjacent name.
 * Selection/disabled/focus stay on the enclosing link, button or menu item, using its existing
 * design-system states. A failed/unknown image uses a neutral grid, never a different app.
 */
export function AppIcon({ id, size = 20, label, className, style }: {
  id?: string | null;
  size?: AppIconSize;
  label?: string;
  className?: string;
  style?: CSSProperties;
}) {
  const asset = appIconAsset(id);
  const [failed, setFailed] = useState<string | null>(null);
  const available = asset && failed !== id;
  const css = {
    ...style, width: size, height: size,
    "--app-icon-scale": asset?.scale ?? 1,
    "--app-icon-x": `${asset?.x ?? 0}%`, "--app-icon-y": `${asset?.y ?? 0}%`,
  } as CSSProperties;
  return <span className={`app-icon${className ? ` ${className}` : ""}`} style={css}
    data-app-icon={id || "unknown"} data-icon-fallback={!available || undefined}
    role={label ? "img" : undefined} aria-label={label || undefined} aria-hidden={label ? undefined : true}>
    {available ? <img key={id} src={asset.files[1].src}
      srcSet={asset.files.map(file => `${file.src} ${file.width}w`).join(", ")}
      sizes={`${Math.ceil(size * asset.scale)}px`} width={size} height={size} alt=""
      decoding="async" draggable={false} onError={() => setFailed(id || null)} />
      : <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor"
        strokeWidth="1.5" aria-hidden="true" focusable="false">
        <rect x="4" y="4" width="6" height="6" rx="1.5" /><rect x="14" y="4" width="6" height="6" rx="1.5" />
        <rect x="4" y="14" width="6" height="6" rx="1.5" /><rect x="14" y="14" width="6" height="6" rx="1.5" />
      </svg>}
  </span>;
}
