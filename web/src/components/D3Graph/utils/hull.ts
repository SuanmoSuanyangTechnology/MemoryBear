import * as d3 from 'd3'
import type { CommunityD3Node, D3Link, HullDatum, RawCommunityNode } from '../types'

// ─── Hull helpers ─────────────────────────────────────────────────────────────

const smoothLine = d3.line<[number, number]>()
  .x(d => d[0]).y(d => d[1])
  .curve(d3.curveCatmullRomClosed.alpha(0.5))

function expandPoints(pts: [number, number][], pad: number): [number, number][] {
  const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length
  const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length
  return pts.map(([x, y]) => {
    const dx = x - cx, dy = y - cy
    const len = Math.sqrt(dx * dx + dy * dy) || 1
    return [x + (dx / len) * pad, y + (dy / len) * pad]
  })
}

function toHullPoints(pts: [number, number][]): [number, number][] {
  if (pts.length === 1) {
    const [x, y] = pts[0]
    return [[x - 1, y - 1], [x + 1, y - 1], [x, y + 1]]
  }
  if (pts.length === 2) {
    const [[x1, y1], [x2, y2]] = pts
    return [[x1, y1], [x2, y2], [(x1 + x2) / 2, (y1 + y2) / 2 - 1]]
  }
  return d3.polygonHull(pts) ?? pts
}

const CIRCLE_THRESHOLD = 4 // Use circle when node count < this value
const CIRCLE_SEGMENTS = 32

function circlePoints(cx: number, cy: number, r: number): [number, number][] {
  return Array.from({ length: CIRCLE_SEGMENTS }, (_, i) => {
    const a = (i / CIRCLE_SEGMENTS) * 2 * Math.PI
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)] as [number, number]
  })
}

export function buildHullData(
  nodes: CommunityD3Node[],
  communityMap: Map<string, string[]>,
  communityCaption: Map<string, string>,
  colors: string[]
): HullDatum[] {
  const getColor = (i: number) => colors[i % colors.length]
  const byComm = new Map<string, [number, number][]>()
  communityMap.forEach((_, id) => byComm.set(id, []))
  nodes.forEach(d => {
    if (d.x != null && d.y != null) byComm.get(d.community)?.push([d.x, d.y])
  })

  const hulls: HullDatum[] = []
  let ci = 0
  byComm.forEach((pts, id) => {
    const color = getColor(ci++)
    if (!pts.length) return
    let pathPoints: [number, number][]
    const pad = Math.min(40, 15 + pts.length * 3)
    if (pts.length < CIRCLE_THRESHOLD) {
      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length
      const maxDist = Math.max(0, ...pts.map(([x, y]) => Math.sqrt((x - cx) ** 2 + (y - cy) ** 2)))
      pathPoints = circlePoints(cx, cy, maxDist + pad)
    } else {
      pathPoints = expandPoints(toHullPoints(pts), pad) as [number, number][]
    }
    const path = smoothLine(pathPoints)
    if (!path) return
    hulls.push({
      id, path, color,
      labelX: pathPoints.reduce((s, p) => s + p[0], 0) / pathPoints.length,
      labelY: Math.min(...pathPoints.map(p => p[1])) - 10,
      dashed: pts.length <= 2,
      caption: communityCaption.get(id) ?? id,
    })
  })
  return hulls
}

// ─── Hull render ──────────────────────────────────────────────────────────────

export function renderHulls(
  hullG: d3.Selection<SVGGElement, unknown, null, undefined>,
  hulls: HullDatum[],
  hiddenCommunities: Set<string>,
  nodes: CommunityD3Node[],
  simulation: d3.Simulation<CommunityD3Node, D3Link>,
  onCommunityClick?: (node: RawCommunityNode) => void,
  communityNodeMap?: Map<string, RawCommunityNode>
) {
  let dragNodes: CommunityD3Node[] = []
  let dragStart = { x: 0, y: 0 }
  const communityDrag = d3.drag<SVGPathElement, HullDatum>()
    .on('start', (event, d) => {
      dragNodes = nodes.filter(n => n.community === d.id)
      dragStart = { x: event.x, y: event.y }
      dragNodes.forEach(n => { n.fx = n.x; n.fy = n.y })
    })
    .on('drag', (event) => {
      const dx = event.x - dragStart.x, dy = event.y - dragStart.y
      dragStart = { x: event.x, y: event.y }
      dragNodes.forEach(n => {
        n.fx = (n.fx ?? n.x ?? 0) + dx; n.fy = (n.fy ?? n.y ?? 0) + dy
        n.x = n.fx; n.y = n.fy
      })
      simulation.alpha(0).restart()
    })
    .on('end', () => { dragNodes = [] })

  const pathSel = hullG.selectAll<SVGPathElement, HullDatum>('path.hull').data(hulls, d => d.id)
  pathSel.enter().append('path').attr('class', 'hull').style('cursor', 'grab')
    .merge(pathSel)
    .call(communityDrag)
    .attr('d', d => d.path)
    .attr('fill', d => d.color).attr('fill-opacity', 0.08)
    .attr('stroke', d => d.color).attr('stroke-opacity', 0.5).attr('stroke-width', 1.5)
    .attr('stroke-dasharray', 'none')
    .style('display', d => hiddenCommunities.has(d.id) ? 'none' : null)
    .on('click', (event, d) => {
      if ((event as MouseEvent).defaultPrevented) return
      const node = communityNodeMap?.get(d.id)
      if (node) onCommunityClick?.(node)
    })
  pathSel.exit().remove()

  const labelSel = hullG.selectAll<SVGTextElement, HullDatum>('text.hull-label').data(hulls, d => d.id)
  labelSel.enter().append('text').attr('class', 'hull-label')
    .attr('text-anchor', 'middle').attr('font-size', '12px').attr('font-weight', '500')
    .style('pointer-events', 'none')
    .merge(labelSel)
    .attr('x', d => d.labelX).attr('y', d => d.labelY)
    .attr('fill', d => d.color)
    .style('display', d => hiddenCommunities.has(d.id) ? 'none' : null)
    .text(d => d.caption)
  labelSel.exit().remove()
}
