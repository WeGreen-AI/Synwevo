/** Placement of anchored layers (menus, popovers) in the viewport.
 *
 *  Pure functions with no DOM access, so the decisions — flip above when there is no room below,
 *  shift inside the viewport, cap the height and let the layer scroll — can be tested with
 *  `node --test` and are the same in every shell. The React hook in ui.tsx measures and applies.
 *
 *  Why a layer at all: a menu rendered inside a scrolling table wrapper (`overflow-x: auto`
 *  makes the wrapper clip vertically too) was cut off at the wrapper's edge, so the action menu
 *  on the last member row simply did not appear. Rendering the layer at the document root and
 *  positioning it in viewport coordinates removes every clipping ancestor from the picture.
 */
export interface Rect { top: number; left: number; width: number; height: number }
export interface Size { width: number; height: number }
export interface Placement {
  top: number;
  left: number;
  /** The most the layer may be tall before it must scroll internally. */
  maxHeight: number;
  /** Where it ended up relative to the anchor. */
  side: "bottom" | "top";
}

export const LAYER_GAP = 4;
export const LAYER_MARGIN = 8;

/** Place a layer of `size` next to `anchor` inside `viewport`.
 *  `align: "end"` lines the layer's right edge up with the anchor's (row menus sit at the end of
 *  a row); "start" lines up the left edges. */
export function placeLayer(anchor: Rect, size: Size, viewport: Size,
                           opts: { align?: "start" | "end"; gap?: number; margin?: number } = {}): Placement {
  const gap = opts.gap ?? LAYER_GAP;
  const margin = opts.margin ?? LAYER_MARGIN;
  const below = viewport.height - (anchor.top + anchor.height) - gap - margin;
  const above = anchor.top - gap - margin;
  // Prefer below; flip only when below cannot fit the layer and above has more room.
  const side: Placement["side"] = size.height <= below || below >= above ? "bottom" : "top";
  const room = Math.max(0, side === "bottom" ? below : above);
  const height = Math.min(size.height, room);
  const top = side === "bottom" ? anchor.top + anchor.height + gap : Math.max(margin, anchor.top - gap - height);
  let left = opts.align === "end" ? anchor.left + anchor.width - size.width : anchor.left;
  const maxLeft = viewport.width - margin - size.width;
  if (left > maxLeft) left = maxLeft;
  if (left < margin) left = margin;
  return { top: Math.round(top), left: Math.round(left), maxHeight: Math.max(0, Math.floor(room)), side };
}

/** Is the anchor still (at least partly) inside the viewport? A layer whose anchor scrolled
 *  away should close rather than float where its row used to be. */
export function anchorVisible(anchor: Rect, viewport: Size): boolean {
  return anchor.top + anchor.height > 0 && anchor.top < viewport.height
    && anchor.left + anchor.width > 0 && anchor.left < viewport.width;
}
