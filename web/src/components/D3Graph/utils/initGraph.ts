import * as d3 from 'd3'
import type { CommunityD3Node, D3Link, HullDatum, RawCommunityNode, InitOptions } from '../types'
import { addArrowMarkers, makeNodeDrag, makeClusterForce, buildGroupCenters } from './forces'
import { buildHullData, renderHulls } from './hull'
import { renderLegend } from './legend'

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
    .force('cohesion', (alpha: number) => {
      const centroids = new Map<string, { x: number; y: number; n: number }>()
      nodes.forEach(d => {
        const c = centroids.get(d.community)
        if (c) { c.x += d.x ?? 0; c.y += d.y ?? 0; c.n++ }
        else centroids.set(d.community, { x: d.x ?? 0, y: d.y ?? 0, n: 1 })
      })
      centroids.forEach(c => { c.x /= c.n; c.y /= c.n })
      nodes.forEach(d => {
        const c = centroids.get(d.community)
        if (!c || c.n < 2) return
        d.vx = (d.vx ?? 0) + (c.x - (d.x ?? 0)) * 0.15 * alpha
        d.vy = (d.vy ?? 0) + (c.y - (d.y ?? 0)) * 0.15 * alpha
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
