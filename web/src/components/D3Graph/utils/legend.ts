import * as d3 from 'd3'
import type { LegendItem } from '../types'

// ─── Legend ───────────────────────────────────────────────────────────────────

const LEGEND_GAP = 12
const LEGEND_RECT_W = 20
const LEGEND_RECT_H = 10
const LEGEND_TEXT_OFFSET = 24
const LEGEND_FONT_SIZE = 11
const LEGEND_ROW_H = 24
const LEGEND_BOTTOM_PAD = 8

// Approximate text width using canvas measureText if available, else char-based estimate
function measureText(text: string, fontSize: number): number {
  try {
    const ctx = document.createElement('canvas').getContext('2d')
    if (ctx) { ctx.font = `${fontSize}px sans-serif`; return ctx.measureText(text).width }
  } catch { /* noop */ }
  return text.length * fontSize * 0.6
}

export function renderLegend(
  svg: d3.Selection<SVGSVGElement, unknown, null, undefined>,
  items: LegendItem[],
  width: number,
  height: number,
  onToggle: (key: string | number, hidden: boolean) => void
) {
  // Compute per-item width: rect + text-offset + textW
  const itemWidths = items.map(item =>
    LEGEND_RECT_W + LEGEND_TEXT_OFFSET + measureText(item.label, LEGEND_FONT_SIZE)
  )

  // Layout items into rows
  const rows: { item: LegendItem; w: number; x: number; row: number }[] = []
  let rowIdx = 0, curX = 0
  itemWidths.forEach((w, i) => {
    const slotW = w + LEGEND_GAP
    if (curX > 0 && curX + w > width - LEGEND_GAP * 2) { rowIdx++; curX = 0 }
    rows.push({ item: items[i], w, x: curX, row: rowIdx })
    curX += slotW
  })

  const totalRows = rowIdx + 1
  const totalH = totalRows * LEGEND_ROW_H
  const baseY = height - totalH - LEGEND_BOTTOM_PAD

  // Center each row
  const rowWidths: number[] = Array(totalRows).fill(0)
  rows.forEach(({ w, row }, i) => {
    rowWidths[row] += w + (i > 0 && rows[i - 1].row === row ? LEGEND_GAP : 0)
  })
  // Recalculate row widths properly
  const rowTotals: number[] = Array(totalRows).fill(0)
  const rowCounts: number[] = Array(totalRows).fill(0)
  rows.forEach(r => { rowCounts[r.row]++; rowTotals[r.row] += r.w })
  rowTotals.forEach((_, ri) => { rowTotals[ri] += Math.max(0, rowCounts[ri] - 1) * LEGEND_GAP })

  const legendG = svg.append('g')

  rows.forEach(({ item, x, row }) => {
    const rowOffsetX = (width - rowTotals[row]) / 2
    const g = legendG.append('g')
      .attr('transform', `translate(${rowOffsetX + x},${baseY + row * LEGEND_ROW_H + LEGEND_ROW_H / 2})`)
      .style('cursor', 'pointer')

    const rect = g.append('rect')
      .attr('x', 0).attr('y', -LEGEND_RECT_H / 2)
      .attr('width', LEGEND_RECT_W).attr('height', LEGEND_RECT_H).attr('rx', 2)
      .attr('fill', item.color)

    const text = g.append('text')
      .text(item.label)
      .attr('x', LEGEND_TEXT_OFFSET).attr('dy', '0.35em')
      .attr('font-size', `${LEGEND_FONT_SIZE}px`).attr('fill', '#5B6167')

    let hidden = false
    g.on('click', () => {
      hidden = !hidden
      rect.attr('fill', hidden ? '#ccc' : item.color)
      text.attr('fill', hidden ? '#bbb' : '#5B6167')
      onToggle(item.key, hidden)
    })
  })
}
