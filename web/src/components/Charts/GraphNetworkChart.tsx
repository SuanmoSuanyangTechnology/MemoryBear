/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 14:06:09 
 */
import { type FC, useEffect, useRef, type SetStateAction, type Dispatch, useMemo } from 'react'
import * as d3 from 'd3'
import { useTranslation } from 'react-i18next'

import PageEmpty from '@/components/Empty/PageEmpty'
import {
  Colors,
  type Node,
  type EdgeClickData,
  type Edge,
  type D3Node,
  type D3Link,
  getBaseStrokeWidth,
  resetToDefaultState,
  calculateHighlightedIds,
  calculateHighlightedByRegion,
  getActiveRelationLabel,
  isSingleDirectional,
  isBidirectional,
  truncateNodeName,
} from './graphNetworkUtils'

interface GraphNetworkChartProps {
  nodes: Node[];
  links: Edge[];
  colors?: string[];
  onNodeClick: Dispatch<SetStateAction<Node | EdgeClickData | null>>;
  selectedNodeId?: string | null;
  selectedCategory?: string | null;
  activeEdgeDirection?: 'a_to_b' | 'b_to_a' | null;
  activeRelationIndex?: number;
  regionId?: string | null;
}

const GraphNetworkChart: FC<GraphNetworkChartProps> = ({
  nodes,
  links,
  colors = Colors,
  onNodeClick,
  selectedNodeId,
  selectedCategory,
  activeEdgeDirection,
  activeRelationIndex: activeRelationIndexProp,
  regionId,
}) => {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const nodeSelRef = useRef<d3.Selection<SVGGElement, D3Node, SVGGElement, unknown> | null>(null)
  const linkSelRef = useRef<d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown> | null>(null)
  const linkLabelSelRef = useRef<d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown> | null>(null)
  const gRef = useRef<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null)
  const graphStateRef = useRef<{ nodes: D3Node[]; links: D3Link[] } | null>(null)
  const transformRef = useRef<d3.ZoomTransform | null>(null)
  // 跟踪当前应该显示的 label ID 集合，防止因缩放/移动而隐藏
  const visibleLabelIdsRef = useRef<Set<string>>(new Set())

  const graphState = useMemo(() => {
    if (!nodes || nodes.length === 0) return null
    
    const nodeMap = new Map(nodes.map(n => [n.id, n]))
    const getColor = (i: number) => colors[i % colors.length]
    
    const d3Nodes: D3Node[] = nodes.map(n => ({
      id: n.id,
      name: n.name || `${t(`userMemory.${n.caption}`)}_${n.id.slice(-5)}`,
      category: n.category,
      symbolSize: n.symbolSize || 35,
      color: n.itemStyle?.color || getColor(n.category),
      caption: n.caption || ''
    }))

    const d3Links: D3Link[] = links
      .filter(l => nodeMap.has(l.node_a) && nodeMap.has(l.node_b))
      .map(l => {
        const hasAtoB = l.a_to_b && l.a_to_b.length > 0
        const hasBtoA = l.b_to_a && l.b_to_a.length > 0
        let sourceId = l.node_a, targetId = l.node_b

        if (isSingleDirectional(l.edge_type) && !hasAtoB && hasBtoA) {
          sourceId = l.node_b
          targetId = l.node_a
        }

        const firstLabel = hasAtoB ? l.a_to_b[0]?.predicate_surface : 
                          hasBtoA ? l.b_to_a[0]?.predicate_surface : undefined

        return {
          id: `${l.node_a}-${l.node_b}`,
          source: sourceId,
          target: targetId,
          label: firstLabel,
          edge_type: l.edge_type,
          a_to_b: l.a_to_b,
          b_to_a: l.b_to_a
        }
      })
    return { nodes: d3Nodes, links: d3Links }
  }, [nodes, links, colors, t])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !graphState) return
    
    graphStateRef.current = graphState
    const width = container.clientWidth || 600
    const height = container.clientHeight || 518

    d3.select(container).selectAll('svg').remove()
    const svg = d3.select(container).append('svg')
      .attr('width', width).attr('height', height).style('width', '100%').style('height', '100%')

    const g = svg.append('g')
    gRef.current = g

    const simulation = d3.forceSimulation<D3Node>(graphState.nodes)
      .force('link', d3.forceLink<D3Node, D3Link>(graphState.links).id(d => d.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-500))
      .force('center', d3.forceCenter(width / 2, height / 2).strength(0.1))
      .force('collision', d3.forceCollide<D3Node>(d => d.symbolSize + 25))
      .force('x', d3.forceX<D3Node>(width / 2).strength(0.05))
      .force('y', d3.forceY<D3Node>(height / 2).strength(0.05))

    const defs = svg.append('defs')
    const createMarker = (id: string, refX: number, dStr: string) => {
      defs.append('marker')
        .attr('id', id)
        .attr('viewBox', '-6 -6 12 12')
        .attr('refX', refX).attr('refY', 0)
        .attr('markerWidth', 8).attr('markerHeight', 8)
        .attr('orient', 'auto')
        .append('path').attr('d', dStr).attr('fill', '#5B6167')
    }
    createMarker('arrow', 5, 'M-3,-3L5,0L-3,3')
    createMarker('arrow-highlight', 5, 'M-3,-3L5,0L-3,3')
    createMarker('arrow-source', -5, 'M3,-3L-5,0L3,3')
    createMarker('arrow-source-highlight', -5, 'M3,-3L-5,0L3,3')

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('start', () => simulation.stop())
      .on('zoom', e => {
        transformRef.current = e.transform
        g.attr('transform', e.transform)
        
        g.selectAll<SVGGElement, D3Node>('g').each(function(d) {
          const textEl = d3.select(this).select<SVGTextElement>('text')
          if (!d?.name) { textEl.style('display', 'none'); return }
          
          const symbolSize = d.symbolSize
          const fontSize = Math.max(6, Math.min(12, symbolSize * 0.25))
          const lineHeight = fontSize * 1.2
          
          const { lines, startY } = truncateNodeName(d.name, symbolSize, fontSize)
          
          textEl.selectAll('tspan').remove()
          lines.forEach((line, idx) => {
            textEl.append('tspan')
              .attr('x', 0)
              .attr('dy', idx === 0 ? startY : lineHeight)
              .attr('font-size', `${fontSize}px`)
              .text(line)
          })
          
          textEl.style('display', 'block')
        })

        if (linkLabelSelRef.current) {
          linkLabelSelRef.current.style('display', d => {
            const linkId = d.id as string
            // 如果 label 已经在显示中，保持显示
            if (visibleLabelIdsRef.current.has(linkId)) return 'block'
            if (!selectedNodeId) return 'none'
            const sourceId = typeof d.source === 'string' ? d.source : d.source.id
            const targetId = typeof d.target === 'string' ? d.target : d.target.id
            return (sourceId === selectedNodeId || targetId === selectedNodeId) ? 'block' : 'none'
          })
        }
      })
      .on('end', () => simulation.alpha(0.1).restart())
    svg.call(zoom)

    const defaultZoom = graphState.nodes.length < 30 ? 1.2 : graphState.nodes.length < 80 ? 0.9 : 0.6
    if (transformRef.current) {
      svg.call(zoom.transform, transformRef.current)
    } else {
      svg.call(zoom.transform, d3.zoomIdentity.translate(width / 2 * (1 - defaultZoom), height / 2 * (1 - defaultZoom)).scale(defaultZoom))
    }

    const bidirectionalLinks = graphState.links.filter(d => isBidirectional(d.edge_type))
    const otherLinks = graphState.links.filter(d => !isBidirectional(d.edge_type))

    const handleEdgeClick = (_event: any, d: D3Link) => {
      const sourceId = typeof d.source === 'string' ? d.source : d.source.id
      const targetId = typeof d.target === 'string' ? d.target : d.target.id
      onNodeClick(selectedNodeId === d.id ? null : {
        ...d, id: d.id, source: sourceId, target: targetId, label: d.label || '', type: 'edge'
      } as any)
    }

    const bidirectionalGroup = g.append('g').selectAll<SVGLineElement, D3Link>('line').data(bidirectionalLinks).enter()

    const createBidirectionalLine = (className: string) => {
      bidirectionalGroup.append('line')
        .attr('stroke', '#A8ABB2').attr('stroke-opacity', 0.4)
        .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
        .attr('marker-end', d => selectedNodeId && (
          (typeof d.source === 'string' ? d.source : d.source.id) === selectedNodeId ||
          (typeof d.target === 'string' ? d.target : d.target.id) === selectedNodeId
        ) ? 'url(#arrow-highlight)' : 'url(#arrow)')
        .attr('class', className)
        .style('cursor', 'pointer').on('click', handleEdgeClick)
    }
    createBidirectionalLine('bidirectional-a')
    createBidirectionalLine('bidirectional-b')

    const linkSel = g.append('g').selectAll<SVGLineElement, D3Link>('line')
      .data(otherLinks).enter().append('line')
      .attr('stroke', '#A8ABB2').attr('stroke-opacity', 0.4)
      .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
      .attr('marker-end', d => {
        const hasAtoB = d.a_to_b && d.a_to_b.length > 0
        const isHighlighted = selectedNodeId && (
          (typeof d.source === 'string' ? d.source : d.source.id) === selectedNodeId ||
          (typeof d.target === 'string' ? d.target : d.target.id) === selectedNodeId
        )
        return isSingleDirectional(d.edge_type) && !hasAtoB ? 'none' : 
               (isHighlighted ? 'url(#arrow-highlight)' : 'url(#arrow)')
      })
      .style('cursor', 'pointer').on('click', handleEdgeClick)
    linkSelRef.current = linkSel

    g.append('g').selectAll<SVGLineElement, D3Link>('line.hit-area')
      .data(otherLinks).enter().append('line')
      .attr('class', 'hit-area').attr('stroke', 'transparent')
      .attr('stroke-width', 16).attr('stroke-linecap', 'round')
      .style('cursor', 'pointer').style('pointer-events', 'stroke')
      .on('click', handleEdgeClick)

    const linkLabelSel = g.append('g').selectAll<SVGTextElement, D3Link>('text')
      .data(graphState.links.filter(l => l.label)).enter().append('text')
      .text(d => d.label || '').attr('text-anchor', 'middle').attr('font-size', '12px')
      .attr('fill', '#5B6167').attr('dy', -5)
      .style('cursor', 'pointer').style('user-select', 'none').style('display', 'none')
      .on('click', handleEdgeClick)
    linkLabelSelRef.current = linkLabelSel

    const nodeSel = g.append('g').selectAll<SVGGElement, D3Node>('g')
      .data(graphState.nodes).enter().append('g')
      .style('cursor', 'pointer')
      .call(d3.drag<SVGGElement, D3Node>()
        .on('start', (e, d) => { if (!e.active) simulation.alphaTarget(1).restart(); d.fx = d.x; d.fy = d.y })
        .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y })
        .on('end', (e, d) => { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null })
      )
    nodeSelRef.current = nodeSel

    nodeSel.append('circle')
      .attr('class', 'ring')
      .attr('r', d => d.symbolSize * 1.35)
      .attr('fill', 'none')
      .attr('stroke', d => d.color)
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.3).lower()

    nodeSel.append('circle')
      .attr('r', d => d.symbolSize).attr('fill', d => d.color)
      .attr('fill-opacity', 1).attr('stroke', '#fff').attr('stroke-width', 1.5)
      .on('click', (_event: MouseEvent, d: D3Node) => {
        const newSelectedId = selectedNodeId === d.id ? null : d.id
        const originalNode = nodes.find(n => n.id === d.id)
        if (originalNode) onNodeClick(newSelectedId ? originalNode : null as any)
      })

    nodeSel.append('text')
      .attr('x', 0).attr('y', 0).attr('text-anchor', 'middle').attr('dominant-baseline', 'middle')
      .attr('font-size', d => `${Math.max(12, Math.min(14, d.symbolSize * 0.65))}px`)
      .attr('fill', '#171719')
      .style('pointer-events', 'none').style('user-select', 'none')
      .each(function(d) {
        const textEl = d3.select(this)
        const name = d.name || ''
        const symbolSize = d.symbolSize
        const fontSize = Math.max(6, Math.min(12, symbolSize * 0.25))
        const lineHeight = fontSize * 1.2
  
        const { lines, startY } = truncateNodeName(name, symbolSize, fontSize)
        
        textEl.selectAll('tspan').remove()
        lines.forEach((line, idx) => {
          textEl.append('tspan')
            .attr('x', 0)
            .attr('dy', idx === 0 ? startY : lineHeight)
            .attr('font-size', `${fontSize}px`)
            .text(line)
        })
      })

    const calcEndpoints = (source: D3Node, target: D3Node, offset: number = 0) => {
      const dx = (target.x ?? 0) - (source.x ?? 0)
      const dy = (target.y ?? 0) - (source.y ?? 0)
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const perpX = -dy / dist, perpY = dx / dist
      return {
        x1: (source.x ?? 0) + (dx / dist) * (source.symbolSize + 2) + perpX * offset,
        y1: (source.y ?? 0) + (dy / dist) * (source.symbolSize + 2) + perpY * offset,
        x2: (target.x ?? 0) - (dx / dist) * (target.symbolSize + 2) + perpX * offset,
        y2: (target.y ?? 0) - (dy / dist) * (target.symbolSize + 2) + perpY * offset
      }
    }

    const updateHighlight = () => {
      if (!selectedNodeId && !selectedCategory) {
        resetToDefaultState(nodeSel, linkSel, linkLabelSel, g)
        return
      }

      const { highlightedNodeIds, highlightedLinkIds } = calculateHighlightedIds(
        selectedNodeId, selectedCategory, graphState.nodes, graphState.links
      )

      const updateNodeStyle = (sel: d3.Selection<SVGCircleElement, D3Node, any, unknown>, baseRadius: (d: D3Node) => number, isRing: boolean) => {
        const useNodeColor = selectedCategory && !selectedNodeId
        const getScale = (d: D3Node) => highlightedLinkIds.size ? 1 : (highlightedNodeIds.has(d.id) ? 1.2 : 0.8)
        
        sel.transition().duration(200)
          .attr('r', (d) => baseRadius(d) * getScale(d))
          .attr('fill-opacity', (d) => isRing ? 1 : (highlightedNodeIds.has(d.id) ? 1 : 0.15))
          .attr('stroke', (d) => !isRing ? (highlightedNodeIds.has(d.id) ? '#fff' : '#ccc') : (useNodeColor ? d.color : d.color))
          .attr('stroke-width', (d) => isRing ? 1 : (highlightedNodeIds.has(d.id) ? 1.5 : 0.5))
          .attr('stroke-opacity', (d) => isRing ? (highlightedNodeIds.has(d.id) ? 0.3 : 0.1) : 1)
          .transition().duration(200)
          .attr('r', (d) => baseRadius(d) * (highlightedLinkIds.size ? 1 : (highlightedNodeIds.has(d.id) ? 1 : 0.8)))
          .attr('stroke-opacity', (d) => isRing ? (highlightedNodeIds.has(d.id) ? 0.3 : 0.1) : 1)
      }

      updateNodeStyle(nodeSel.selectAll<SVGCircleElement, D3Node>('circle:not(.ring)'), d => d.symbolSize, false)
      updateNodeStyle(nodeSel.selectAll<SVGCircleElement, D3Node>('circle.ring'), d => d.symbolSize * 1.35, true)

      nodeSel.selectAll<SVGTextElement, D3Node>('text')
        .attr('fill', d => highlightedNodeIds.has(d.id) ? '#171719' : '#bbb')
        .attr('font-weight', d => selectedNodeId && !highlightedLinkIds.size && d.id === selectedNodeId ? 'bold' : 'normal')

      const isLinkHighlighted = (d: D3Link) => {
        const linkId = d.id as string
        if (highlightedLinkIds.has(linkId)) return true
        const sourceId = typeof d.source === 'string' ? d.source : d.source.id
        const targetId = typeof d.target === 'string' ? d.target : d.target.id
        return highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId)
      }

      linkSel
        .attr('stroke', '#A8ABB2')
        .attr('stroke-opacity', d => isLinkHighlighted(d) ? 0.6 : 0.15)
        .attr('stroke-width', d => {
          const baseWidth = getBaseStrokeWidth(d.edge_type)
          return isLinkHighlighted(d) ? Math.max(baseWidth, 1.5) : baseWidth * 0.6
        })
        .attr('marker-end', d => {
          const hasAtoB = d.a_to_b && d.a_to_b.length > 0
          if (isSingleDirectional(d.edge_type) && !hasAtoB) return 'none'
          return isLinkHighlighted(d) ? 'url(#arrow-highlight)' : 'url(#arrow)'
        })
        .attr('marker-start', d => {
          const hasBtoA = d.b_to_a && d.b_to_a.length > 0
          if (isSingleDirectional(d.edge_type) && hasBtoA) {
            return isLinkHighlighted(d) ? 'url(#arrow-source-highlight)' : 'url(#arrow-source)'
          }
          return 'none'
        })
        .attr('stroke-dasharray', d => !selectedNodeId || highlightedLinkIds.size ? 'none' : 
                                      ((typeof d.source === 'string' ? d.source : d.source.id) === selectedNodeId ? '5,3' : 'none'))

      linkLabelSel.style('display', d => isLinkHighlighted(d) ? 'block' : 'none')
    }

    const updateBidirectionalHighlight = () => {
      const isLinkSelected = !!selectedNodeId && graphState.links.some(link => link.id === selectedNodeId)
      const sel = graphState.links.find(l => l.id === selectedNodeId)
      const isBidirectionalSelected = isLinkSelected && sel && isBidirectional(sel.edge_type) && 
                                     sel.a_to_b && sel.b_to_a && sel.a_to_b.length > 0 && sel.b_to_a.length > 0
      const activeDirection = isBidirectionalSelected ? (activeEdgeDirection || 'a_to_b') : null

      const updateLine = (className: string, direction: 'a_to_b' | 'b_to_a') => {
        g.selectAll<SVGLineElement, D3Link>(`line.${className}`)
          .attr('stroke-opacity', d => {
            if (!selectedNodeId && !selectedCategory) return 0.4
            if (!isLinkSelected) return 0.15
            if (d.id === selectedNodeId) {
              return isBidirectionalSelected && activeDirection === direction ? 0.6 : (isBidirectionalSelected ? 0.15 : 0.6)
            }
            return 0.15
          })
          .attr('stroke-width', d => {
            const base = getBaseStrokeWidth(d.edge_type)
            if (!isLinkSelected) return base * 0.6
            if (d.id === selectedNodeId) {
              return isBidirectionalSelected && activeDirection === direction ? Math.max(base, 1.5) : 
                     (isBidirectionalSelected ? base * 0.6 : Math.max(base, 1.5))
            }
            return base * 0.6
          })
          .attr('marker-end', d => {
            if (!isLinkSelected) return 'url(#arrow)'
            if (d.id === selectedNodeId) {
              return isBidirectionalSelected && activeDirection === direction ? 'url(#arrow-highlight)' : 
                     (isBidirectionalSelected ? 'url(#arrow)' : 'url(#arrow-highlight)')
            }
            return 'url(#arrow)'
          })
      }

      updateLine('bidirectional-a', 'a_to_b')
      updateLine('bidirectional-b', 'b_to_a')

      linkLabelSel.style('display', d => isLinkSelected && d.id === selectedNodeId ? 'block' : 'none')
    }

    updateHighlight()
    updateBidirectionalHighlight()

    simulation.on('tick', () => {
      g.selectAll<SVGLineElement, D3Link>('line.bidirectional-a')
        .attr('x1', d => calcEndpoints(d.source as D3Node, d.target as D3Node, 3).x1)
        .attr('y1', d => calcEndpoints(d.source as D3Node, d.target as D3Node, 3).y1)
        .attr('x2', d => calcEndpoints(d.source as D3Node, d.target as D3Node, 3).x2)
        .attr('y2', d => calcEndpoints(d.source as D3Node, d.target as D3Node, 3).y2)

      g.selectAll<SVGLineElement, D3Link>('line.bidirectional-b')
        .attr('x1', d => calcEndpoints(d.target as D3Node, d.source as D3Node, 3).x1)
        .attr('y1', d => calcEndpoints(d.target as D3Node, d.source as D3Node, 3).y1)
        .attr('x2', d => calcEndpoints(d.target as D3Node, d.source as D3Node, 3).x2)
        .attr('y2', d => calcEndpoints(d.target as D3Node, d.source as D3Node, 3).y2)

      linkSel.attr('x1', d => calcEndpoints(d.source as D3Node, d.target as D3Node).x1)
             .attr('y1', d => calcEndpoints(d.source as D3Node, d.target as D3Node).y1)
             .attr('x2', d => calcEndpoints(d.source as D3Node, d.target as D3Node).x2)
             .attr('y2', d => calcEndpoints(d.source as D3Node, d.target as D3Node).y2)

      nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)

      linkLabelSel
        .attr('x', d => {
          const endpoints = calcEndpoints(d.source as D3Node, d.target as D3Node)
          return (endpoints.x1 + endpoints.x2) / 2
        })
        .attr('y', d => {
          const endpoints = calcEndpoints(d.source as D3Node, d.target as D3Node)
          return (endpoints.y1 + endpoints.y2) / 2 - 4
        })
        .attr('transform', d => {
          const source = d.source as D3Node, target = d.target as D3Node
          const dx = (target.x ?? 0) - (source.x ?? 0)
          const dy = (target.y ?? 0) - (source.y ?? 0)
          const angle = Math.atan2(dy, dx) * 180 / Math.PI
          const endpoints = calcEndpoints(source, target)
          const cx = (endpoints.x1 + endpoints.x2) / 2
          const cy = (endpoints.y1 + endpoints.y2) / 2 - 4
          const flipAngle = angle > 90 || angle < -90 ? angle + 180 : angle
          return `rotate(${flipAngle}, ${cx}, ${cy})`
        })
    })

    const handleResize = () => {
      if (!container) return
      const newWidth = container.clientWidth
      const newHeight = container.clientHeight
      svg.attr('width', newWidth).attr('height', newHeight)
      simulation.force('center', d3.forceCenter(newWidth / 2, newHeight / 2).strength(0.1))
      simulation.alpha(1).restart()
    }

    resizeObserverRef.current = new ResizeObserver(handleResize)
    resizeObserverRef.current.observe(container)

    return () => {
      simulation.stop()
      resizeObserverRef.current?.disconnect()
      d3.select(container).selectAll('svg').remove()
    }
  }, [graphState, nodes, onNodeClick])

  useEffect(() => {
    if (!nodeSelRef.current || !linkSelRef.current || !graphStateRef.current) return

    const { nodes: graphNodes, links: graphLinks } = graphStateRef.current
    
    let highlightedNodeIds = new Set<string>()
    let highlightedLinkIds = new Set<string>()
    
    // 支持 regionId 高亮
    if (regionId) {
      const result = calculateHighlightedByRegion(graphNodes, nodes, graphLinks, regionId)
      highlightedNodeIds = result.highlightedNodeIds
      highlightedLinkIds = result.highlightedLinkIds
    } else {
      const result = calculateHighlightedIds(
        selectedNodeId, selectedCategory, graphNodes, graphLinks
      )
      highlightedNodeIds = result.highlightedNodeIds
      highlightedLinkIds = result.highlightedLinkIds
    }

    if (!selectedNodeId && !selectedCategory && !regionId) {
      nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle')
        .transition().duration(200)
        .attr('r', d => d.symbolSize).attr('fill-opacity', 1)
        .attr('stroke', '#fff').attr('stroke-width', 1.5)
      
      nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
        .transition().duration(200)
        .attr('r', d => d.symbolSize * 1.35)
        .attr('fill', 'none')
        .attr('stroke', d => d.color)
        .attr('stroke-opacity', 0.3)
      
      nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
        .attr('fill', '#171719').attr('font-weight', 'normal')
      
      linkSelRef.current
        .attr('stroke', '#A8ABB2').attr('stroke-opacity', 0.4)
        .attr('stroke-width', d => getBaseStrokeWidth(d.edge_type))
        .attr('marker-end', d => (isSingleDirectional(d.edge_type) && !d.a_to_b?.length) ? 'none' : 'url(#arrow)')
        .attr('marker-start', d => (isSingleDirectional(d.edge_type) && d.b_to_a?.length) ? 'url(#arrow-source)' : 'none')
        .attr('stroke-dasharray', 'none')

      if (linkLabelSelRef.current) linkLabelSelRef.current.style('display', 'none')
      visibleLabelIdsRef.current.clear()
      return
    }

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle:not(.ring)')
      .transition().duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.2 : d.symbolSize * 0.8)
      .attr('fill-opacity', d => highlightedNodeIds.has(d.id) ? 1 : 0.15)
      .attr('stroke', d => highlightedNodeIds.has(d.id) ? '#fff' : '#ccc')
      .attr('stroke-width', d => highlightedNodeIds.has(d.id) ? 1.5 : 0.5)

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
      .transition().duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 * 1.2 : d.symbolSize * 1.35)
      .attr('stroke', d => highlightedNodeIds.has(d.id) ? d.color : '#ccc')
      .attr('stroke-width', 1)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.3 : 0.1)

    nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
      .attr('fill', d => highlightedNodeIds.has(d.id) ? '#171719' : '#bbb')

    // selectedCategory 有值时，仅高亮节点，不高亮边
    const isCategoryOnly = selectedCategory && !selectedNodeId
    // 选中节点时，仅高亮直接相连的边，不高亮邻居节点的其他边
    const isNodeSelected = selectedNodeId && !isCategoryOnly
    // 选中边时，只高亮该边，其他边置灰
    const isLinkSelected = !!selectedNodeId && graphLinks.some(link => link.id === selectedNodeId)

    // 判断边是否直接与选中节点相连
    const isDirectlyConnectedToSelectedNode = (d: D3Link): boolean => {
      if (!selectedNodeId) return false
      const sourceId = typeof d.source === 'string' ? d.source : d.source.id
      const targetId = typeof d.target === 'string' ? d.target : d.target.id
      return sourceId === selectedNodeId || targetId === selectedNodeId
    }

    linkSelRef.current
      .attr('stroke-opacity', d => {
        if (isLinkSelected) return d.id === selectedNodeId ? 0.6 : 0.15
        if (isCategoryOnly) return 0.15
        if (isNodeSelected) return isDirectlyConnectedToSelectedNode(d) ? 0.6 : 0.15
        const linkId = d.id as string
        if (highlightedLinkIds.has(linkId)) return 0.6
        return 0.15
      })
      .attr('stroke-width', d => {
        const baseWidth = getBaseStrokeWidth(d.edge_type)
        if (isLinkSelected) return d.id === selectedNodeId ? Math.max(baseWidth, 1.5) : baseWidth * 0.6
        if (isCategoryOnly) return baseWidth * 0.6
        if (isNodeSelected) return isDirectlyConnectedToSelectedNode(d) ? Math.max(baseWidth, 1.5) : baseWidth * 0.6
        const linkId = d.id as string
        if (highlightedLinkIds.has(linkId)) return Math.max(baseWidth, 1.5)
        return baseWidth * 0.6
      })

    if (linkLabelSelRef.current) {
      const visibleIds = new Set<string>()
      linkLabelSelRef.current.style('display', d => {
        const linkId = d.id as string
        let shouldShow = false
        if (isLinkSelected) {
          shouldShow = d.id === selectedNodeId
        } else if (isNodeSelected) {
          shouldShow = isDirectlyConnectedToSelectedNode(d)
        } else if (highlightedLinkIds.has(linkId)) {
          shouldShow = true
        }
        if (shouldShow) visibleIds.add(linkId)
        return shouldShow ? 'block' : 'none'
      })
      visibleLabelIdsRef.current = visibleIds
    }

    // 更新双向边的样式
    if (gRef.current) {
      const updateBidirectionalLine = (className: string) => {
        gRef.current?.selectAll<SVGLineElement, D3Link>(`line.${className}`)
          .attr('stroke-opacity', d => {
            if (isLinkSelected) return d.id === selectedNodeId ? 0.6 : 0.15
            if (isCategoryOnly) return 0.15
            if (isNodeSelected) return isDirectlyConnectedToSelectedNode(d) ? 0.6 : 0.15
            const linkId = d.id as string
            if (highlightedLinkIds.has(linkId)) return 0.6
            return 0.15
          })
          .attr('stroke-width', d => {
            const baseWidth = getBaseStrokeWidth(d.edge_type)
            if (isLinkSelected) return d.id === selectedNodeId ? Math.max(baseWidth, 1.5) : baseWidth * 0.6
            if (isCategoryOnly) return baseWidth * 0.6
            if (isNodeSelected) return isDirectlyConnectedToSelectedNode(d) ? Math.max(baseWidth, 1.5) : baseWidth * 0.6
            const linkId = d.id as string
            if (highlightedLinkIds.has(linkId)) return Math.max(baseWidth, 1.5)
            return baseWidth * 0.6
          })
          .attr('marker-end', d => {
            if (isLinkSelected) return d.id === selectedNodeId ? 'url(#arrow-highlight)' : 'url(#arrow)'
            if (isCategoryOnly) return 'url(#arrow)'
            if (isNodeSelected) return isDirectlyConnectedToSelectedNode(d) ? 'url(#arrow-highlight)' : 'url(#arrow)'
            const linkId = d.id as string
            if (highlightedLinkIds.has(linkId)) return 'url(#arrow-highlight)'
            return 'url(#arrow)'
          })
      }
      updateBidirectionalLine('bidirectional-a')
      updateBidirectionalLine('bidirectional-b')
    }
  }, [selectedNodeId, selectedCategory, regionId])

  useEffect(() => {
    if (!gRef.current || !graphStateRef.current) return

    const { links: graphLinks } = graphStateRef.current
    const isLinkSelected = !!selectedNodeId && graphLinks.some(link => link.id === selectedNodeId)
    const sel = graphLinks.find(l => l.id === selectedNodeId)
    
    if (!isLinkSelected || !sel) return

    // 支持 BIDIRECTIONAL、MULTI_BIDIRECTIONAL 和 UNIDIRECTIONAL_MULTI 类型
    const isBidirectionalEdge = isBidirectional(sel.edge_type)
    const isUnidirectionalMultiEdge = isSingleDirectional(sel.edge_type) && 
                                       sel.a_to_b && sel.a_to_b.length > 1
    
    if (!isBidirectionalEdge && !isUnidirectionalMultiEdge) return

    const isBidirectionalSelected = isBidirectionalEdge && sel.a_to_b && sel.b_to_a && sel.a_to_b.length > 0 && sel.b_to_a.length > 0
    const activeDirection = isBidirectionalSelected ? (activeEdgeDirection || 'a_to_b') : null

    const updateLine = (className: string, direction: 'a_to_b' | 'b_to_a') => {
      gRef.current?.selectAll<SVGLineElement, D3Link>(`line.${className}`)
        .attr('stroke-opacity', d => d.id === selectedNodeId && isBidirectionalSelected && 
                                    activeDirection === direction ? 0.6 : 0.15)
        .attr('stroke-width', d => {
          const base = getBaseStrokeWidth(d.edge_type)
          return d.id === selectedNodeId && isBidirectionalSelected && 
                 activeDirection === direction ? Math.max(base, 1.5) : base * 0.6
        })
        .attr('marker-end', d => d.id === selectedNodeId && isBidirectionalSelected && 
                                 activeDirection === direction ? 'url(#arrow-highlight)' : 'url(#arrow)')
    }

    if (isBidirectionalSelected) {
      updateLine('bidirectional-a', 'a_to_b')
      updateLine('bidirectional-b', 'b_to_a')
    }

    if (linkLabelSelRef.current && sel) {
      const targetLabel = getActiveRelationLabel(sel.a_to_b, sel.b_to_a, activeRelationIndexProp ?? 0)
      linkLabelSelRef.current.text(d => d.id === selectedNodeId ? targetLabel : d.label || '')
      // 选中边时，添加该边到 visibleLabelIds
      visibleLabelIdsRef.current.add(selectedNodeId as string)
    }
  }, [activeEdgeDirection, activeRelationIndexProp, selectedNodeId])

  if (!nodes || nodes.length === 0) {
    return <PageEmpty />
  }

  return <div ref={containerRef} className="rb:absolute rb:inset-0" />
}

export default GraphNetworkChart
