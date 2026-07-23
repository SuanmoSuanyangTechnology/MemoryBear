import type { PanelPos } from './types';

export const CHILD_PANEL_HEIGHT = 280; // max-h-60 (240) + header (~40)
export const CHILD_PANEL_WIDTH = 280; // min-w-70 (280px)
export const CHILD_PANEL_MARGIN = 8;

/**
 * Compute a child-panel position that avoids screen edges.
 * @param rect      Bounding rect of the anchor item the panel expands from.
 * @param anchorTop Top of the reference popup/dropdown so all levels share the
 *                  same vertical edge. Pass null to fall back to aligning the
 *                  panel bottom with the anchor item bottom.
 */
export function calcSmartPanelPos(rect: DOMRect, anchorTop: number | null): PanelPos {
  const spaceRight = window.innerWidth - rect.left;

  // Determine horizontal position: prefer right, fallback to left
  const useRight = spaceRight >= CHILD_PANEL_WIDTH + CHILD_PANEL_MARGIN;
  const horizontal = useRight
    ? window.innerWidth - rect.left + CHILD_PANEL_MARGIN
    : rect.right + CHILD_PANEL_MARGIN;

  // Determine vertical position
  let top: number;
  if (anchorTop != null) {
    top = anchorTop;
  } else {
    top = Math.max(CHILD_PANEL_MARGIN, rect.bottom - CHILD_PANEL_HEIGHT);
  }

  return { top, horizontal, useRight };
}
