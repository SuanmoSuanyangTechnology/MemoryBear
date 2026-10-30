import * as d3 from 'd3'
import type { CommunityD3Node, HullDatum, RawCommunityNode } from '../types'

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

function hullPoints(pts: [number, number][]): [number, number][] {
  const pad = Math.min(40, Math.max(30, 22 + pts.length * 3))
  if (pts.length < CIRCLE_THRESHOLD) {
    const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length
    const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length
    const maxDist = Math.max(0, ...pts.map(([x, y]) => Math.hypot(x - cx, y - cy)))
    return circlePoints(cx, cy, maxDist + pad)
  }
  return expandPoints(toHullPoints(pts), pad)
}

// Keep the same geometry as the rendered hulls when separating communities.
export function separateCommunityHulls(nodes: CommunityD3Node[], communityMap: Map<string, string[]>, centerId?: string) {
  const groups = new Map<string, CommunityD3Node[]>()
  communityMap.forEach((_, id) => groups.set(id, []))
  nodes.forEach(node => groups.get(node.community)?.push(node))

  const gap = 18
  const margin = 8 // Extra room for the smoothed Catmull-Rom curve and stroke.
  const boxes = Array.from(groups, ([id, members]) => {
    if (!members.length) return null
    const points = hullPoints(members.map(node => [node.x ?? 0, node.y ?? 0]))
    return {
      id,
      members,
      minX: Math.min(...points.map(p => p[0])) - margin,
      maxX: Math.max(...points.map(p => p[0])) + margin,
      minY: Math.min(...points.map(p => p[1])) - margin,
      maxY: Math.max(...points.map(p => p[1])) + margin,
    }
  }).filter((box): box is NonNullable<typeof box> => box !== null)

  const shift = (box: typeof boxes[number], dx: number, dy: number) => {
    box.minX += dx; box.maxX += dx; box.minY += dy; box.maxY += dy
    box.members.forEach(node => {
      node.x = (node.x ?? 0) + dx
      node.y = (node.y ?? 0) + dy
    })
  }

  // Resolving a pair may touch another pair, so repeat until all bounding boxes clear.
  for (let pass = 0; pass < boxes.length * boxes.length; pass++) {
    let moved = false
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j]
        const overlapX = Math.min(a.maxX, b.maxX) - Math.max(a.minX, b.minX) + gap
        const overlapY = Math.min(a.maxY, b.maxY) - Math.max(a.minY, b.minY) + gap
        if (overlapX <= 0 || overlapY <= 0) continue
        moved = true
        if (overlapX < overlapY) {
          const direction = (a.minX + a.maxX <= b.minX + b.maxX) ? 1 : -1
          if (a.id !== centerId) shift(a, -direction * overlapX / (b.id === centerId ? 1 : 2), 0)
          if (b.id !== centerId) shift(b, direction * overlapX / (a.id === centerId ? 1 : 2), 0)
        } else {
          const direction = (a.minY + a.maxY <= b.minY + b.maxY) ? 1 : -1
          if (a.id !== centerId) shift(a, 0, -direction * overlapY / (b.id === centerId ? 1 : 2))
          if (b.id !== centerId) shift(b, 0, direction * overlapY / (a.id === centerId ? 1 : 2))
        }
      }
    }
    if (!moved) break
  }
}

export function buildHullData(
  nodes: CommunityD3Node[],
  communityMap: Map<string, string[]>,
  communityCaption: Map<string, string>,
  colors: string[],
  hiddenHullId?: string,
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
    if (!pts.length || id === hiddenHullId) return
    const pathPoints = hullPoints(pts)
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
  onDragStart: () => void,
  onDrag: () => void,
  onCommunityClick?: (node: RawCommunityNode) => void,
  communityNodeMap?: Map<string, RawCommunityNode>,
  selectedCommunityId?: string | null
) {
  let dragNodes: CommunityD3Node[] = []
  let dragStart = { x: 0, y: 0 }
  const communityDrag = d3.drag<SVGPathElement, HullDatum>()
    .on('start', (event, d) => {
      onDragStart()
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
      onDrag()
    })
    .on('end', () => { dragNodes = [] })

  const pathSel = hullG.selectAll<SVGPathElement, HullDatum>('path.hull').data(hulls, d => d.id)
  pathSel.enter().append('path').attr('class', 'hull').style('cursor', 'grab')
    .merge(pathSel)
    .call(communityDrag)
    .attr('d', d => d.path)
    .attr('fill', d => d.color).attr('fill-opacity', d => d.id === selectedCommunityId ? 0.28 : 0.12)
    .attr('stroke', d => d.color)
    .attr('stroke-opacity', d => d.id === selectedCommunityId ? 1 : 0.55)
    .attr('stroke-width', d => d.id === selectedCommunityId ? 3 : 1)
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
