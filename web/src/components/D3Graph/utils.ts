import * as d3 from 'd3'
import type { CommunityD3Node, D3Link, HullDatum, CommunityGraphData, RawCommunityGraphData, RawCommunityNode, RawEntityNode, InitOptions } from './types'

// ─── Colors ───────────────────────────────────────────────────────────────────

export const GRAPH_COLORS = ['#171719', '#155EEF', '#369F21', '#4DA8FF', '#FF5D34', '#9C6FFF', '#FF8A4C', '#8BAEF7', '#FFB048']
export const colorAt = (i: number) => GRAPH_COLORS[i % GRAPH_COLORS.length]

export function connectionToRadius(connections: number): number {
  if (connections <= 1) return 5
  if (connections <= 10) return 8
  if (connections <= 15) return 11
  if (connections <= 20) return 16
  return 22
}

// ─── Arrow markers ────────────────────────────────────────────────────────────

export function addArrowMarkers(
  defs: d3.Selection<SVGDefsElement, unknown, null, undefined>,
  markers: { id: string; color: string }[]
) {
  markers.forEach(({ id, color }) => {
    defs.append('marker')
      .attr('id', id)
      .attr('viewBox', '0 -4 8 8')
      .attr('refX', 8).attr('refY', 0)
      .attr('markerWidth', 6).attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', color)
  })
}

// ─── Zoom ─────────────────────────────────────────────────────────────────────

export function addZoom(
  svg: d3.Selection<SVGSVGElement, unknown, null, undefined>,
  g: d3.Selection<SVGGElement, unknown, null, undefined>
) {
  svg.call(
    d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.2, 4])
      .on('zoom', e => g.attr('transform', e.transform))
  )
}

// ─── Node drag ────────────────────────────────────────────────────────────────

export function makeNodeDrag<N extends d3.SimulationNodeDatum>(
  simulation: d3.Simulation<N, d3.SimulationLinkDatum<N>>
) {
  return d3.drag<SVGGElement, N>()
    .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y })
    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y })
    .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = e.x; d.fy = e.y })
}

// ─── Cluster force ────────────────────────────────────────────────────────────
// Works for both string and number group keys.

export function makeClusterForce<N extends d3.SimulationNodeDatum & { x?: number; y?: number; vx?: number; vy?: number }>(
  nodes: N[],
  getGroup: (d: N) => string | number,
  centers: Record<string | number, { x: number; y: number }>,
  width: number,
  height: number,
  opts: { pullStrength?: number; minSepRatio?: number; pushStrength?: number } = {}
) {
  const { pullStrength = 0.45, minSepRatio = 0.68, pushStrength = 1.0 } = opts
  return (alpha: number) => {
    // pre-group nodes by key to avoid repeated filter() in hot path
    const groups = new Map<string, N[]>()
    nodes.forEach(d => {
      const k = String(getGroup(d))
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k)!.push(d)
    })
    // pull toward group center
    nodes.forEach(d => {
      const c = centers[getGroup(d)]
      if (!c) return
      d.vx = (d.vx ?? 0) + (c.x - (d.x ?? 0)) * pullStrength * alpha
      d.vy = (d.vy ?? 0) + (c.y - (d.y ?? 0)) * pullStrength * alpha
    })
    // live centroids
    const centroids: Record<string, { x: number; y: number; n: number }> = {}
    nodes.forEach(d => {
      const g = String(getGroup(d))
      if (!centroids[g]) centroids[g] = { x: 0, y: 0, n: 0 }
      centroids[g].x += d.x ?? 0
      centroids[g].y += d.y ?? 0
      centroids[g].n++
    })
    Object.values(centroids).forEach(c => { c.x /= c.n; c.y /= c.n })
    // push groups apart
    const keys = Object.keys(centroids)
    const minSep = Math.min(width, height) * minSepRatio
    for (let i = 0; i < keys.length; i++) {
      for (let j = i + 1; j < keys.length; j++) {
        const ci = centroids[keys[i]], cj = centroids[keys[j]]
        const dx = cj.x - ci.x, dy = cj.y - ci.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        if (dist >= minSep) continue
        const push = ((minSep - dist) / dist) * pushStrength * alpha
        const fx = dx * push, fy = dy * push
        groups.get(keys[i])?.forEach(d => { d.vx = (d.vx ?? 0) - fx; d.vy = (d.vy ?? 0) - fy })
        groups.get(keys[j])?.forEach(d => { d.vx = (d.vx ?? 0) + fx; d.vy = (d.vy ?? 0) + fy })
      }
    }
  }
}

// ─── Group centers ────────────────────────────────────────────────────────────

export function buildGroupCenters(
  keys: (string | number)[],
  width: number,
  height: number,
  radiusRatio = 0.4
): Record<string | number, { x: number; y: number }> {
  const centers: Record<string | number, { x: number; y: number }> = {}
  const r = Math.min(width, height) * radiusRatio
  keys.forEach((key, i) => {
    const angle = (i / keys.length) * 2 * Math.PI - Math.PI / 2
    centers[key] = { x: width / 2 + r * Math.cos(angle), y: height / 2 + r * Math.sin(angle) }
  })
  return centers
}

// ─── Community graph data transform ─────────────────────────────────────────

export function buildCommunityGraphData(raw: RawCommunityGraphData, colors: string[] = GRAPH_COLORS): CommunityGraphData | null {
  const getColor = (i: number) => colors[i % colors.length]

  const communityNodes = raw.nodes.filter(n => n.label === 'Community') as RawCommunityNode[]
  const communityCaption = new Map<string, string>()
  const communityMap = new Map<string, string[]>()

  communityNodes.forEach(n => {
    communityCaption.set(n.id, n.properties.name)
    communityMap.set(n.id, n.properties.member_entity_ids)
  })

  const entityToCommunity = new Map<string, string>()
  communityMap.forEach((members, commId) => members.forEach(eid => entityToCommunity.set(eid, commId)))

  const commKeys = Array.from(communityMap.keys())
  const commIndex = new Map(commKeys.map((k, i) => [k, i]))

  const entityNodes = raw.nodes.filter(n => n.label === 'ExtractedEntity') as RawEntityNode[]
  const entityNodeSet = new Set(entityNodes.map(n => n.id))

  const connectionCount: Record<string, number> = {}
  raw.edges.forEach(e => {
    if (entityNodeSet.has(e.source)) connectionCount[e.source] = (connectionCount[e.source] || 0) + 1
    if (entityNodeSet.has(e.target)) connectionCount[e.target] = (connectionCount[e.target] || 0) + 1
  })

  const nodes: CommunityD3Node[] = entityNodes.map(n => {
    const commId = entityToCommunity.get(n.id) ?? commKeys[0]
    return {
      id: n.id,
      name: n.properties.name,
      community: commId,
      label: n.label,
      symbolSize: connectionToRadius(connectionCount[n.id] || 0),
      color: getColor(commIndex.get(commId) ?? 0),
      properties: n.properties,
    }
  })

  if (!nodes.length) return null

  const links = raw.edges
    .filter(e => entityNodeSet.has(e.source) && entityNodeSet.has(e.target))
    .map(e => ({
      source: e.source,
      target: e.target,
      isCross: entityToCommunity.get(e.source) !== entityToCommunity.get(e.target),
    }))

  const communityNodeMap = new Map<string, RawCommunityNode>(
    communityNodes.map(n => [n.id, n])
  )
  return { nodes, links, communityMap, communityCaption, communityNodeMap }
}

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

const CIRCLE_THRESHOLD = 4 // 节点数 < 此值时使用圆形
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
    if (pts.length < CIRCLE_THRESHOLD) {
      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length
      const cy = pts.reduce((s, p) => s + p[1], 0) / pts.length
      pathPoints = circlePoints(cx, cy, 60)
    } else {
      pathPoints = expandPoints(toHullPoints(pts), 60) as [number, number][]
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
      if (!event.active) simulation.alphaTarget(0.3).restart()
      dragNodes = nodes.filter(n => n.community === d.id)
      dragStart = { x: event.x, y: event.y }
      dragNodes.forEach(n => { n.fx = n.x; n.fy = n.y })
    })
    .on('drag', (event) => {
      const dx = event.x - dragStart.x, dy = event.y - dragStart.y
      dragStart = { x: event.x, y: event.y }
      dragNodes.forEach(n => { n.fx = (n.fx ?? n.x ?? 0) + dx; n.fy = (n.fy ?? n.y ?? 0) + dy })
    })
    .on('end', (event) => { if (!event.active) simulation.alphaTarget(0) })

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

// ─── Community graph init ─────────────────────────────────────────────────────

export function initCommunityGraph(
  container: HTMLDivElement,
  nodes: CommunityD3Node[],
  links: D3Link[],
  communityMap: Map<string, string[]>,
  communityCaption: Map<string, string>,
  communityNodeMap: Map<string, RawCommunityNode>,
  opts: InitOptions
) {
  const { colors, showLegend, defaultZoom, setTooltip, onCommunityClickRef, onNodeClickRef } = opts
  const getColor = (i: number) => colors[i % colors.length]

  const width = container.clientWidth || 600
  const height = container.clientHeight || 518

  const svg = d3.select(container).append('svg')
    .attr('width', width).attr('height', height)
    .style('width', '100%').style('height', '100%')

  const g = svg.append('g')

  const zoom = d3.zoom<SVGSVGElement, unknown>()
    .scaleExtent([0.2, 4])
    .on('zoom', e => g.attr('transform', e.transform))
  svg.call(zoom)
  if (defaultZoom !== 1) {
    svg.call(zoom.transform, d3.zoomIdentity
      .translate(width / 2 * (1 - defaultZoom), height / 2 * (1 - defaultZoom))
      .scale(defaultZoom)
    )
  }

  const defs = svg.append('defs')
  addArrowMarkers(defs, [{ id: 'arrow', color: 'rgba(91, 97, 103, 0.7)' }])

  const commKeys = Array.from(communityMap.keys())
  const centers = buildGroupCenters(commKeys, width, height, 0.45)
  const linkedIds = new Set(links.flatMap(l => [l.source as string, l.target as string]))

  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink<CommunityD3Node, D3Link>(links).id(d => d.id).distance(60))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(width / 2, height / 2).strength(0.02))
    .force('collision', d3.forceCollide<CommunityD3Node>(d => d.symbolSize + 16))
    .force('cluster', makeClusterForce(nodes, d => d.community, centers, width, height, {
      pullStrength: 0.45, minSepRatio: 0.68, pushStrength: 1.0,
    }))
    .force('isolatedPull', (alpha: number) => {
      nodes.forEach(d => {
        if (linkedIds.has(d.id)) return
        const c = centers[d.community]
        if (!c) return
        d.vx = (d.vx ?? 0) + (c.x - (d.x ?? 0)) * 0.4 * alpha
        d.vy = (d.vy ?? 0) + (c.y - (d.y ?? 0)) * 0.4 * alpha
      })
    })

  const hullG = g.append('g').attr('class', 'hulls')
  const hiddenCommunities = new Set<string>()

  const linkSel = g.append('g').selectAll<SVGLineElement, D3Link>('line')
    .data(links).enter().append('line')
    .attr('stroke', '#5B6167')
    .attr('stroke-opacity', d => d.isCross ? 0.3 : 0.5)
    .attr('stroke-width', d => d.isCross ? 1 : 1.2)
    .attr('marker-end', 'url(#arrow)')

  const nodeSel = g.append('g').selectAll<SVGGElement, CommunityD3Node>('g')
    .data(nodes).enter().append('g')
    .call(makeNodeDrag(simulation))

  nodeSel.append('circle')
    .attr('r', d => d.symbolSize)
    .attr('fill', d => d.color).attr('fill-opacity', 0.85)
    .attr('stroke', '#fff').attr('stroke-width', 1.5)
    .style('cursor', 'pointer')
    .on('mouseenter', (event: MouseEvent, d: CommunityD3Node) => {
      const { left, top } = container.getBoundingClientRect()
      setTooltip({ x: event.clientX - left, y: event.clientY - top, node: d })
    })
    .on('mousemove', (event: MouseEvent) => {
      const { left, top } = container.getBoundingClientRect()
      const nd = d3.select<SVGCircleElement, CommunityD3Node>(event.target as SVGCircleElement).datum()
      setTooltip({ x: event.clientX - left, y: event.clientY - top, node: nd })
    })
    .on('mouseleave', () => setTooltip(null))
    .on('click', (_event: MouseEvent, d: CommunityD3Node) => onNodeClickRef.current?.(d))

  nodeSel.append('text')
    .text(d => d.name)
    .attr('x', 0).attr('dy', d => -(d.symbolSize + 5))
    .attr('text-anchor', 'middle').attr('font-size', '11px').attr('fill', '#444')
    .style('pointer-events', 'none')

  if (showLegend) {
    renderLegend(
      svg,
      commKeys.map((cid, i) => ({ key: cid, label: communityCaption.get(cid) ?? cid, color: getColor(i) })),
      width, height,
      (key, hidden) => {
        const cid = key as string
        if (hidden) hiddenCommunities.add(cid)
        else hiddenCommunities.delete(cid)
        nodeSel.style('display', d => hiddenCommunities.has(d.community) ? 'none' : null)
        linkSel.style('display', d => {
          const s = d.source as CommunityD3Node, t = d.target as CommunityD3Node
          return hiddenCommunities.has(s.community) || hiddenCommunities.has(t.community) ? 'none' : null
        })
        hullG.selectAll<SVGPathElement, HullDatum>('path.hull').style('display', d => hiddenCommunities.has(d.id) ? 'none' : null)
        hullG.selectAll<SVGTextElement, HullDatum>('text.hull-label').style('display', d => hiddenCommunities.has(d.id) ? 'none' : null)
      }
    )
  }

  simulation.on('tick', () => {
    linkSel
      .attr('x1', d => (d.source as CommunityD3Node).x ?? 0)
      .attr('y1', d => (d.source as CommunityD3Node).y ?? 0)
      .attr('x2', d => {
        const s = d.source as CommunityD3Node, t = d.target as CommunityD3Node
        const dx = (t.x ?? 0) - (s.x ?? 0), dy = (t.y ?? 0) - (s.y ?? 0)
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        return (t.x ?? 0) - (dx / dist) * (t.symbolSize + 2)
      })
      .attr('y2', d => {
        const s = d.source as CommunityD3Node, t = d.target as CommunityD3Node
        const dx = (t.x ?? 0) - (s.x ?? 0), dy = (t.y ?? 0) - (s.y ?? 0)
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        return (t.y ?? 0) - (dy / dist) * (t.symbolSize + 2)
      })
    nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)
    renderHulls(hullG, buildHullData(nodes, communityMap, communityCaption, colors), hiddenCommunities, nodes, simulation, (n) => onCommunityClickRef.current?.(n), communityNodeMap)
  })

  return () => { simulation.stop(); d3.select(container).selectAll('svg').remove() }
}

// ─── Legend ───────────────────────────────────────────────────────────────────

export interface LegendItem {
  key: string | number
  label: string
  color: string
}

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
