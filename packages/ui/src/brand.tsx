import type { CSSProperties, ReactNode } from "react";
import { LOGO_ASPECT, LOGO_FILES, type LogoVersion } from "./brand/assets";
import { brandLinkName } from "./brand-name";

export type { LogoVersion } from "./brand/assets";
export { brandLinkName } from "./brand-name";

/* ------------------------------------------------------------ logo */
/** The confirmed Synwevo logo, in its three versions:
 *
 *  * `full` — the woven S mark with the Synwevo wordmark. The primary brand position of a page:
 *    top bars with room for it, the sign-in frame.
 *  * `mark` — the S alone. Where the full logo would have to shrink below legibility: phone top
 *    bars, loading and error states, icons.
 *  * `wordmark` — the name alone. Footers and other places that only need the name.
 *
 *  The pixels come from `scripts/brand_assets.py`, which crops all three out of the confirmed
 *  full logo, so they match it exactly. Never retype "Synwevo" in a font to stand in for the
 *  wordmark, and never put "Synwevo" as text next to `full` or `wordmark` — they already say it.
 *
 *  The size is given as a height in CSS pixels; the width follows the artwork. Explicit
 *  width/height attributes reserve the space before the image arrives, and `srcSet` lets the
 *  browser pick the rendition for the screen density. */
export type Breakpoint = 900 | 720 | 480 | 380;

export function Logo({ version, height, alt = "Synwevo", markBelow, className }: {
  version: LogoVersion;
  /** Rendered height in CSS px. */
  height: number;
  /** Text alternative; pass "" when a surrounding label already names the brand. */
  alt?: string;
  /** Show the mark instead of the full logo at this viewport width or narrower. */
  markBelow?: Breakpoint;
  className?: string;
}) {
  const main = rendition(version, height);
  const compact = markBelow && version !== "mark" ? rendition("mark", height) : null;
  // Forced colours (Windows high contrast) leave raster images alone, so a near-black logo can
  // vanish on a black Canvas. The wrapper carries the same artwork as a mask so the stylesheet
  // can paint it in CanvasText there; outside forced colours the mask is unused.
  const style = { "--logo-mask": `url("${largest(version)}")`,
                  ...(compact ? { "--logo-mask-compact": `url("${largest("mark")}")` } : {}) } as CSSProperties;
  const img = (
    <img className="logo-img" src={main.src} srcSet={main.srcSet} sizes={`${main.width}px`}
         width={main.width} height={height} alt={alt} draggable={false} />
  );
  return (
    <span className={`logo logo-${version}${compact ? ` logo-mark-below-${markBelow}` : ""}${className ? ` ${className}` : ""}`} style={style}>
      {compact ? (
        <picture>
          <source media={`(max-width: ${markBelow}px)`} srcSet={compact.srcSet} sizes={`${compact.width}px`}
                  width={compact.width} height={height} />
          {img}
        </picture>
      ) : img}
    </span>
  );
}

function rendition(version: LogoVersion, height: number) {
  const files = LOGO_FILES[version];
  // `src` is the fallback for a browser without srcset support: the rendition closest to 2×.
  const fallback = files.find((f) => f.height >= height * 2) ?? files[files.length - 1];
  return {
    width: Math.round(height * LOGO_ASPECT[version]),
    src: fallback.src,
    srcSet: files.map((f) => `${f.src} ${f.width}w`).join(", "),
  };
}

function largest(version: LogoVersion) {
  const files = LOGO_FILES[version];
  return files[files.length - 1].src;
}

/* ------------------------------------------------------------ brand */
const BRAND_HEIGHT = { md: 24, lg: 32 } as const;

/** The brand position of a top bar or frame: the full logo, followed by the product (Workspace,
 *  Account, Docs) as text. `BrandMark` is the inner content for callers that need their own
 *  anchor (a router Link) or no anchor at all; `Brand` wraps it in a plain link.
 *
 *  Narrow screens, in order of preference: first the product text goes (`productBelow`; the
 *  large brand of the sign-in frame and the portal does this at 380px by default, so the full
 *  logo stays readable on the smallest phones), then — in bars too crowded even for that — the
 *  full logo becomes the mark (`markBelow`), never a full logo shrunk until the wordmark is
 *  unreadable. `hideTextOnMobile` is the older name for `markBelow={900}`. */
export function BrandMark({ product, hideTextOnMobile = false, markBelow, productBelow, size = "md", alt = "Synwevo" }: {
  product?: string; hideTextOnMobile?: boolean; markBelow?: Breakpoint; productBelow?: Breakpoint;
  size?: "md" | "lg"; alt?: string;
}) {
  const below = markBelow ?? (hideTextOnMobile ? 900 : undefined);
  // the product text goes first: at the wider of its own breakpoint and the swap to the mark
  const productGoes = Math.max(below ?? 0, productBelow ?? (size === "lg" ? 380 : 0)) as Breakpoint | 0 || undefined;
  return (
    <>
      <Logo version="full" height={BRAND_HEIGHT[size]} alt={alt} markBelow={below} />
      {product ? <span className={`product${productGoes ? ` hide-below-${productGoes}` : ""}`}>{product}</span> : null}
    </>
  );
}

/** A brand link. When `label` is given (the link's purpose, e.g. "back to the platform home") it
 *  becomes the accessible name, and it always starts with the visible words so that people who
 *  navigate by voice can say what they see. */
export function Brand({ href = "/", product, size, label, hideTextOnMobile, markBelow, productBelow }: {
  href?: string; product?: string; size?: "lg"; label?: string; hideTextOnMobile?: boolean; markBelow?: Breakpoint;
  productBelow?: Breakpoint;
}) {
  const name = brandLinkName(product, label);
  return (
    <a className={`brand${size ? ` ${size}` : ""}`} href={href} aria-label={name}>
      <BrandMark product={product} size={size ?? "md"} hideTextOnMobile={hideTextOnMobile} markBelow={markBelow}
                 productBelow={productBelow} alt={name ? "" : "Synwevo"} />
    </a>
  );
}

/* ------------------------------------------------------------ whole-page states */
/** A whole page that is only a message: loading, redirecting to sign-in, a session or route
 *  error. The mark sits above the message so a person landing here still knows where they are;
 *  it is decorative (the message says what is happening) and it does not move. Pages with a top
 *  bar use StatusPage instead, which already carries the full logo. */
export function PageState({ children, role, as: Tag = "div" }: {
  children: ReactNode; role?: "status" | "alert"; as?: "div" | "main";
}) {
  // A <main> keeps its landmark role unless the caller asks for a live region explicitly.
  return (
    <Tag className="empty page-state" role={role ?? (Tag === "main" ? undefined : "status")}>
      <Logo version="mark" height={32} alt="" className="page-state-mark" />
      {children}
    </Tag>
  );
}
