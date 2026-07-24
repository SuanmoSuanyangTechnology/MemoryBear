import * as d3 from 'd3'

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

export function makeNodeDrag<N extends d3.SimulationNodeDatum & { x?: number; y?: number }>(
  simulation: d3.Simulation<N, d3.SimulationLinkDatum<N>>,
) {
  return d3.drag<SVGGElement, N>()
    .on('start', (_e, d) => { d.fx = d.x; d.fy = d.y })
    .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; d.x = e.x; d.y = e.y; simulation.alpha(0).restart() })
    .on('end', (e, d) => { d.fx = e.x; d.fy = e.y })
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
