/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 14:06:09 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-04 10:05:39
 */
/**
 * GraphNetworkChart Component
 * 
 * A force-directed graph visualization component built with D3.js.
 * Displays nodes and edges in an interactive network diagram with physics-based layout.
 * Supports zooming, panning, dragging nodes, and click interactions.
 */
import { type FC, useEffect, useRef, type SetStateAction, type Dispatch, useMemo } from 'react'
import * as d3 from 'd3'
import { useTranslation } from 'react-i18next'

import PageEmpty from '@/components/Empty/PageEmpty'

export const Colors = ['#155EEF', '#02AFD5', '#FF5D34', '#6473E9', '#369F21', '#4DA8FF', '#C86AFF', '#F7BA1E', '#5B6167']

export interface Node {
  id: string;
  label: string;
  category: number;
  symbolSize: number;
  name: string;
  itemStyle?: {
    color: string;
  }
  caption: string;
  properties: Record<string, any>
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  vx?: number;
  vy?: number;
  [key: string]: any;
}

export interface Edge {
  id: string;
  source: string;
  target: string;
  type: string;
  caption: string;
  value: number;
  weight: number;
}

export interface EdgeClickData extends Edge {
  id: string;
  source: string;
  target: string;
  label: string;
  type: 'edge';
}

interface GraphNetworkChartProps {
  nodes: Node[];
  links: Edge[];
  categories: { name: string }[];
  colors?: string[];
  onNodeClick: Dispatch<SetStateAction<Node | EdgeClickData | null>>;
  selectedNodeId?: string | null;
  selectedCategory?: string | null;
  regionId?: string | null;
}

interface D3Node extends d3.SimulationNodeDatum {
  id: string;
  name: string;
  category: number;
  symbolSize: number;
  color: string;
  caption: string;
}

interface D3Link extends d3.SimulationLinkDatum<D3Node> {
  id: string;
  source: string | D3Node;
  target: string | D3Node;
  caption?: string;
  type?: string;
  label?: string;
}

const regionMapping: Record<string, string[]> = {
  prefrontal: ['Statement'],
  frontal: ['ExtractedEntity'],
  parietal: ['Perceptual'],
  occipital: ['Chunk'],
  cerebellum: ['AssistantPruned', 'AssistantOriginal'],
  brainstem: ['Dialogue', 'Conversation'],
  hippocampus: ['MemorySummary'],
  amygdala: ['Statement'],
}
const GraphNetworkChart: FC<GraphNetworkChartProps> = ({
  nodes,
  links,
  categories: _categories,
  colors = Colors,
  onNodeClick,
  selectedNodeId,
  selectedCategory,
  regionId,
}) => {
  const { t } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)
  const nodeSelRef = useRef<d3.Selection<SVGGElement, D3Node, SVGGElement, unknown> | null>(null)
  const linkSelRef = useRef<d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown> | null>(null)
  const linkLabelSelRef = useRef<d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown> | null>(null)
  const graphStateRef = useRef<{ nodes: D3Node[]; links: D3Link[] } | null>(null)
  const zoomScaleRef = useRef<number>(1)
  const transformRef = useRef<d3.ZoomTransform | null>(null)
  const svgRef = useRef<d3.Selection<SVGSVGElement, unknown, any, unknown> | null>(null)
  const isZoomingRef = useRef<boolean>(false)
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
      .filter(l => nodeMap.has(l.source) && nodeMap.has(l.target))
      .map(l => ({
        id: l.id,
        source: l.source,
        target: l.target,
        caption: l.caption,
        type: l.type,
        label: l.type === 'EXTRACTED_RELATIONSHIP' ? (l as any).properties?.predicate || undefined : undefined,
      }))
    return { nodes: d3Nodes, links: d3Links }
  }, [nodes, links, colors])

  useEffect(() => {
    const container = containerRef.current
    if (!container || !graphState) return
    
    graphStateRef.current = graphState

    const width = container.clientWidth || 600
    const height = container.clientHeight || 518

    d3.select(container).selectAll('svg').remove()

    const svg = d3.select(container).append('svg')
      .attr('width', width)
      .attr('height', height)
      .style('width', '100%')
      .style('height', '100%')
    
    svgRef.current = svg

    const g = svg.append('g')

    const simulation = d3.forceSimulation<D3Node>(graphState.nodes)
      .force('link', d3.forceLink<D3Node, D3Link>(graphState.links).id(d => d.id).distance(120))
      .force('charge', d3.forceManyBody().strength(-500))
      .force('center', d3.forceCenter(width / 2, height / 2).strength(0.1))
      .force('collision', d3.forceCollide<D3Node>(d => d.symbolSize + 25))
      .force('x', d3.forceX<D3Node>(width / 2).strength(0.05))
      .force('y', d3.forceY<D3Node>(height / 2).strength(0.05))

    const defs = svg.append('defs')
    defs.append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -3 6 6')
      .attr('refX', 6).attr('refY', 0)
      .attr('markerWidth', 4).attr('markerHeight', 4)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-3L6,0L0,3')
      .attr('fill', 'rgba(91, 97, 103, 0.4)')

    defs.append('marker')
      .attr('id', 'arrow-highlight')
      .attr('viewBox', '0 -3 6 6')
      .attr('refX', 6).attr('refY', 0)
      .attr('markerWidth', 4).attr('markerHeight', 4)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-3L6,0L0,3')
      .attr('fill', 'rgba(91, 97, 103, 0.5)')

    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on('start', () => {
        isZoomingRef.current = true
        simulation.stop()
      })
      .on('zoom', e => {
        transformRef.current = e.transform
        g.attr('transform', e.transform)
        zoomScaleRef.current = e.transform.k
        const currentZoom = e.transform.k
        g.selectAll<SVGGElement, D3Node>('g').each(function(d) {
          const nodeGroup = d3.select(this)
          const textEl = nodeGroup.select<SVGTextElement>('text')
          
          if (!d.name) {
            textEl.style('display', 'none')
            return
          }
          
          const fontSize = Math.max(6, Math.min(12, d.symbolSize * 0.25 * currentZoom))
          const maxWidth = d.symbolSize * currentZoom * 1.2
          const charWidth = fontSize * 0.55
          const maxChars = Math.floor(maxWidth / charWidth)
          
          if (d.name.length <= maxChars) {
            textEl.text(d.name)
          } else {
            textEl.text(d.name.slice(0, maxChars - 1) + '...')
          }
          textEl.style('display', 'block')
        })
        
        if (selectedNodeId && linkLabelSelRef.current) {
          linkLabelSelRef.current.style('display', d => {
            const sourceId = typeof d.source === 'string' ? d.source : d.source.id
            const targetId = typeof d.target === 'string' ? d.target : d.target.id
            if (sourceId === selectedNodeId || targetId === selectedNodeId) {
              return 'block'
            }
            return 'none'
          })
        } else if (linkLabelSelRef.current) {
          linkLabelSelRef.current.style('display', 'none')
        }
      })
      .on('end', () => {
        isZoomingRef.current = false
        simulation.alpha(0.1).restart()
      })
    svg.call(zoom)

    const defaultZoom = graphState.nodes.length < 30 ? 1.2 : graphState.nodes.length < 80 ? 0.9 : 0.6
    if (transformRef.current) {
      svg.call(zoom.transform, transformRef.current)
      zoomScaleRef.current = transformRef.current.k
    } else {
      svg.call(zoom.transform, d3.zoomIdentity
        .translate(width / 2 * (1 - defaultZoom), height / 2 * (1 - defaultZoom))
        .scale(defaultZoom)
      )
      zoomScaleRef.current = defaultZoom
    }

    const linkSel = g.append('g').selectAll<SVGLineElement, D3Link>('line')
      .data(graphState.links)
      .enter()
      .append('line')
      .attr('stroke', '#A8ABB2')
      .attr('stroke-opacity', 0.4)
      .attr('stroke-width', 0.8)
      .attr('marker-end', d => {
        const sourceId = typeof d.source === 'string' ? d.source : d.source.id
        const targetId = typeof d.target === 'string' ? d.target : d.target.id
        return (selectedNodeId && (sourceId === selectedNodeId || targetId === selectedNodeId)) 
          ? 'url(#arrow-highlight)' 
          : 'url(#arrow)'
      })
      .style('cursor', 'pointer')
      .on('click', (_event, d) => {
        const sourceId = typeof d.source === 'string' ? d.source : d.source.id
        const targetId = typeof d.target === 'string' ? d.target : d.target.id
        onNodeClick?.(selectedNodeId === d.id ? null : {
          ...d,
          id: d.id,
          source: sourceId,
          target: targetId,
          label: d.label || '',
          type: 'edge',
        } as any)
      })
    linkSelRef.current = linkSel

    const linkLabelSel = g.append('g').selectAll<SVGTextElement, D3Link>('text')
      .data(graphState.links.filter(l => l.label))
      .enter()
      .append('text')
      .text(d => d.label || '')
      .attr('text-anchor', 'middle')
      .attr('font-size', '12px')
      .attr('fill', '#5B6167')
      .attr('dy', -5)
      .style('pointer-events', 'none')
      .style('user-select', 'none')
      .style('display', 'none')
    linkLabelSelRef.current = linkLabelSel

    const nodeSel = g.append('g').selectAll<SVGGElement, D3Node>('g')
      .data(graphState.nodes)
      .enter()
      .append('g')
      .style('cursor', 'pointer')
      .call(d3.drag<SVGGElement, D3Node>()
        .on('start', (event, d) => {
          if (!event.active) simulation.alphaTarget(0.3).restart()
          d.fx = d.x
          d.fy = d.y
        })
        .on('drag', (event, d) => {
          d.fx = event.x
          d.fy = event.y
        })
        .on('end', (event, d) => {
          if (!event.active) simulation.alphaTarget(0)
          d.fx = null
          d.fy = null
        })
      )
    nodeSelRef.current = nodeSel

    nodeSel.append('circle')
      .attr('class', 'ring')
      .attr('r', d => d.symbolSize * 1.35)
      .attr('fill', 'none')
      .attr('stroke', d => d.color)
      .attr('stroke-width', 1)
      .attr('stroke-opacity', 0.3)
      .lower()

    nodeSel.append('circle')
      .attr('r', d => d.symbolSize)
      .attr('fill', d => d.color)
      .attr('fill-opacity', 0.85)
      .attr('stroke', '#fff')
      .attr('stroke-width', 1.5)
      .on('click', (_event: MouseEvent, d: D3Node) => {
        const newSelectedId = selectedNodeId === d.id ? null : d.id
        const originalNode = nodes.find(n => n.id === d.id)
        if (originalNode) {
          onNodeClick?.(newSelectedId ? originalNode : null as any)
        }
      })

    nodeSel.append('text')
      .attr('x', 0)
      .attr('y', 0)
      .attr('text-anchor', 'middle')
      .attr('dominant-baseline', 'middle')
      .attr('font-size', d => {
        const fontSize = Math.max(12, Math.min(14, d.symbolSize * 0.65))
        return `${fontSize}px`
      })
      .attr('fill', '#171719')
      .style('pointer-events', 'none')
      .style('user-select', 'none')
      .each(function(d) {
        const text = d3.select(this)
        const name = d.name || ''
        const fontSize = Math.max(6, Math.min(12, d.symbolSize * 0.25))
        const maxWidth = d.symbolSize * 1.2
        const lineHeight = fontSize * 1.2
        const maxLines = Math.floor((d.symbolSize * 1.2) / lineHeight) || 1
        
        const words = name.split('')
        let line: string[] = []
        let lines: string[] = []
        let currentWidth = 0
        const charWidth = fontSize * 0.55
        
        for (let i = 0; i < words.length; i++) {
          const word = words[i]
          const wordWidth = charWidth
          if (currentWidth + wordWidth > maxWidth && line.length > 0) {
            lines.push(line.join(''))
            line = [word]
            currentWidth = wordWidth
          } else {
            line.push(word)
            currentWidth += wordWidth
          }
        }
        if (line.length > 0) {
          lines.push(line.join(''))
        }
        
        if (lines.length > maxLines) {
          lines = lines.slice(0, maxLines)
          if (lines[maxLines - 1]) {
            lines[maxLines - 1] = lines[maxLines - 1].slice(0, -1) + '...'
          }
        }
        
        text.selectAll('tspan').remove()
        
        if (lines.length === 1) {
          text.text(lines[0])
        } else {
          const totalHeight = (lines.length - 1) * lineHeight
          const startY = -totalHeight / 2
          
          lines.forEach((lineText, i) => {
            text.append('tspan')
              .attr('x', 0)
              .attr('dy', i === 0 ? `${startY / fontSize}em` : `${lineHeight / fontSize}em`)
              .text(lineText)
          })
        }
      })

    const highlightNodes = () => {
      if (!selectedNodeId && !selectedCategory) {
        nodeSel.selectAll<SVGCircleElement, D3Node>('circle')
          .transition()
          .duration(200)
          .attr('r', d => d.symbolSize)
          .attr('fill-opacity', 0.85)
          .attr('stroke', '#fff')
          .attr('stroke-width', 1.5)
        nodeSel.selectAll<SVGCircleElement, D3Node>('circle.ring')
          .transition()
          .duration(200)
          .attr('r', d => d.symbolSize * 1.35)
          .attr('stroke-opacity', 0.3)
        nodeSel.selectAll<SVGTextElement, D3Node>('text')
          .attr('fill', '#171719')
          .attr('font-weight', 'normal')
        linkSel
          .attr('stroke', '#A8ABB2')
          .attr('stroke-opacity', 0.4)
          .attr('stroke-width', 0.8)
          .attr('marker-end', 'url(#arrow)')
          .attr('stroke-dasharray', 'none')
        linkLabelSel
          .style('display', 'none')
        return
      }

      const highlightedNodeIds = new Set<string>()
      const highlightedLinkIds = new Set<string>()
      
      if (selectedNodeId) {
        const isLink = graphState.links.some(link => link.id === selectedNodeId)
        
        if (isLink) {
          highlightedLinkIds.add(selectedNodeId)
        } else {
          highlightedNodeIds.add(selectedNodeId)
          graphState.links.forEach(link => {
            const sourceId = typeof link.source === 'string' ? link.source : link.source.id
            const targetId = typeof link.target === 'string' ? link.target : link.target.id
            if (sourceId === selectedNodeId) highlightedNodeIds.add(targetId)
            if (targetId === selectedNodeId) highlightedNodeIds.add(sourceId)
          })
        }
      } else if (selectedCategory) {
        graphState.nodes.forEach(node => {
          if (node.caption === selectedCategory) {
            highlightedNodeIds.add(node.id)
          }
        })
      }

      nodeSel.selectAll<SVGCircleElement, D3Node>('circle')
        .transition()
        .duration(200)
        .attr('r', d => highlightedLinkIds.size ? d.symbolSize : (highlightedNodeIds.has(d.id) ? d.symbolSize * 1.2 : d.symbolSize * 0.8))
        .attr('fill-opacity', d => highlightedLinkIds.size ? 0.85 : (highlightedNodeIds.has(d.id) ? 0.85 : 0.15))
        .attr('stroke', d => highlightedLinkIds.size ? '#fff' : (highlightedNodeIds.has(d.id) ? '#fff' : '#ccc'))
        .attr('stroke-width', d => highlightedLinkIds.size ? 1.5 : (highlightedNodeIds.has(d.id) ? 1.5 : 0.5))
        .transition()
        .duration(200)
        .attr('r', d => highlightedLinkIds.size ? d.symbolSize : (highlightedNodeIds.has(d.id) ? d.symbolSize : d.symbolSize * 0.8))
      
      nodeSel.selectAll<SVGCircleElement, D3Node>('circle.ring')
        .transition()
        .duration(200)
        .attr('r', d => highlightedLinkIds.size ? d.symbolSize * 1.35 : (highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 * 1.2 : d.symbolSize * 1.35 * 0.8))
        .attr('stroke-opacity', d => highlightedLinkIds.size ? 0.3 : (highlightedNodeIds.has(d.id) ? 0.6 : 0.1))
        .transition()
        .duration(200)
        .attr('r', d => highlightedLinkIds.size ? d.symbolSize * 1.35 : (highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 : d.symbolSize * 1.35 * 0.8))
        .attr('stroke-opacity', d => highlightedLinkIds.size ? 0.3 : (highlightedNodeIds.has(d.id) ? 0.4 : 0.1))
      
      nodeSel.selectAll<SVGTextElement, D3Node>('text')
        .attr('fill', d => highlightedLinkIds.size ? '#171719' : (highlightedNodeIds.has(d.id) ? '#171719' : '#bbb'))
        .attr('font-weight', d => selectedNodeId && !highlightedLinkIds.size && d.id === selectedNodeId ? 'bold' : 'normal')

      linkSel
        .attr('stroke', d => {
          const linkId = d.id as string
          if (highlightedLinkIds.has(linkId)) return '#A8ABB2'
          const sourceId = typeof d.source === 'string' ? d.source : d.source.id
          const targetId = typeof d.target === 'string' ? d.target : d.target.id
          return highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId) ? '#A8ABB2' : '#A8ABB2'
        })
        .attr('stroke-opacity', d => {
          const linkId = d.id as string
          if (highlightedLinkIds.has(linkId)) return 0.6
          const sourceId = typeof d.source === 'string' ? d.source : d.source.id
          const targetId = typeof d.target === 'string' ? d.target : d.target.id
          return highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId) ? 0.6 : 0.15
        })
        .attr('stroke-width', d => {
          const linkId = d.id as string
          if (highlightedLinkIds.has(linkId)) return 1.5
          const sourceId = typeof d.source === 'string' ? d.source : d.source.id
          const targetId = typeof d.target === 'string' ? d.target : d.target.id
          return highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId) ? 1.5 : 0.5
        })
        .attr('marker-end', d => {
          const linkId = d.id as string
          if (highlightedLinkIds.has(linkId)) return 'url(#arrow-highlight)'
          const sourceId = typeof d.source === 'string' ? d.source : d.source.id
          const targetId = typeof d.target === 'string' ? d.target : d.target.id
          return highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId) ? 'url(#arrow-highlight)' : 'url(#arrow)'
        })
        .attr('stroke-dasharray', d => {
          const sourceId = typeof d.source === 'string' ? d.source : d.source.id
          const targetId = typeof d.target === 'string' ? d.target : d.target.id
          if (selectedNodeId && !highlightedLinkIds.size && sourceId === selectedNodeId) return '5,3'
          if (selectedNodeId && !highlightedLinkIds.size && targetId === selectedNodeId) return 'none'
          return 'none'
        })
        linkLabelSel
          .style('display', d => {
            const linkId = d.id as string
            if (highlightedLinkIds.has(linkId)) return 'block'
            if (!highlightedLinkIds.size) {
              const sourceId = typeof d.source === 'string' ? d.source : d.source.id
              const targetId = typeof d.target === 'string' ? d.target : d.target.id
              return (highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId)) ? 'block' : 'none'
            }
            return 'none'
          })
    }

    highlightNodes()

    simulation.on('tick', () => {
      linkSel
        .attr('x1', d => (d.source as D3Node).x ?? 0)
        .attr('y1', d => (d.source as D3Node).y ?? 0)
        .attr('x2', d => {
          const source = d.source as D3Node
          const target = d.target as D3Node
          const dx = (target.x ?? 0) - (source.x ?? 0)
          const dy = (target.y ?? 0) - (source.y ?? 0)
          const dist = Math.sqrt(dx * dx + dy * dy) || 1
          return (target.x ?? 0) - (dx / dist) * (target.symbolSize + 2)
        })
        .attr('y2', d => {
          const source = d.source as D3Node
          const target = d.target as D3Node
          const dx = (target.x ?? 0) - (source.x ?? 0)
          const dy = (target.y ?? 0) - (source.y ?? 0)
          const dist = Math.sqrt(dx * dx + dy * dy) || 1
          return (target.y ?? 0) - (dy / dist) * (target.symbolSize + 2)
        })

      nodeSel.attr('transform', d => `translate(${d.x ?? 0},${d.y ?? 0})`)

      linkLabelSel
        .attr('x', d => {
          const source = d.source as D3Node
          const target = d.target as D3Node
          return ((source.x ?? 0) + (target.x ?? 0)) / 2
        })
        .attr('y', d => {
          const source = d.source as D3Node
          const target = d.target as D3Node
          return ((source.y ?? 0) + (target.y ?? 0)) / 2 - 8
        })
        .attr('transform', d => {
          const source = d.source as D3Node
          const target = d.target as D3Node
          const dx = (target.x ?? 0) - (source.x ?? 0)
          const dy = (target.y ?? 0) - (source.y ?? 0)
          const angle = Math.atan2(dy, dx) * 180 / Math.PI
          const cx = ((source.x ?? 0) + (target.x ?? 0)) / 2
          const cy = ((source.y ?? 0) + (target.y ?? 0)) / 2 - 8
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
      simulation.alpha(0.3).restart()
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
    if (!selectedCategory || !nodeSelRef.current || !linkSelRef.current || !graphStateRef.current) return

    const { nodes: graphNodes } = graphStateRef.current

    const highlightedNodeIds = new Set<string>()

    graphNodes.forEach(node => {
      if (node.caption === selectedCategory) {
        highlightedNodeIds.add(node.id)
      }
    })

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.2 : d.symbolSize * 0.8)
      .attr('fill-opacity', d => highlightedNodeIds.has(d.id) ? 0.85 : 0.15)
      .attr('stroke', d => highlightedNodeIds.has(d.id) ? '#fff' : '#ccc')
      .attr('stroke-width', d => highlightedNodeIds.has(d.id) ? 1.5 : 0.5)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize : d.symbolSize * 0.8)

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 * 1.2 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.6 : 0.1)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.4 : 0.1)

    nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
      .attr('fill', d => highlightedNodeIds.has(d.id) ? '#171719' : '#bbb')
      .attr('font-weight', 'normal')

    linkSelRef.current
      .attr('stroke', '#A8ABB2')
      .attr('stroke-opacity', 0.4)
      .attr('stroke-width', 0.8)
      .attr('marker-end', 'url(#arrow)')
      .attr('stroke-dasharray', 'none')

    if (linkLabelSelRef.current) {
      linkLabelSelRef.current.style('display', 'none')
    }

  }, [selectedCategory])

  useEffect(() => {
    if (!nodeSelRef.current || !linkSelRef.current || !graphStateRef.current) return

    const { links: graphLinks } = graphStateRef.current
    
    const highlightedNodeIds = new Set<string>()
    const highlightedLinkIds = new Set<string>()
    
    if (selectedNodeId) {
      const isLink = graphLinks.some(link => link.id === selectedNodeId)
      
      if (isLink) {
        highlightedLinkIds.add(selectedNodeId)
      } else {
        highlightedNodeIds.add(selectedNodeId)
        graphLinks.forEach(link => {
          const sourceId = typeof link.source === 'string' ? link.source : link.source.id
          const targetId = typeof link.target === 'string' ? link.target : link.target.id
          if (sourceId === selectedNodeId) {
            highlightedNodeIds.add(targetId)
            highlightedLinkIds.add(link.id)
          }
          if (targetId === selectedNodeId) {
            highlightedNodeIds.add(sourceId)
            highlightedLinkIds.add(link.id)
          }
        })
      }
    }

    if (!selectedNodeId && !selectedCategory) {
      nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle')
        .transition()
        .duration(200)
        .attr('r', d => d.symbolSize)
        .attr('fill-opacity', 0.85)
        .attr('stroke', '#fff')
        .attr('stroke-width', 1.5)
      
      nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
        .transition()
        .duration(200)
        .attr('r', d => d.symbolSize * 1.35)
        .attr('stroke-opacity', 0.3)
      
      nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
        .attr('fill', '#171719')
        .attr('font-weight', 'normal')
      
      linkSelRef.current
        .attr('stroke', '#A8ABB2')
        .attr('stroke-opacity', 0.4)
        .attr('stroke-width', 0.8)
        .attr('marker-end', 'url(#arrow)')
        .attr('stroke-dasharray', 'none')

      if (linkLabelSelRef.current) {
        linkLabelSelRef.current.style('display', 'none')
      }

      return
    }

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.2 : d.symbolSize * 0.8)
      .attr('fill-opacity', d => highlightedNodeIds.has(d.id) ? 0.85 : 0.15)
      .attr('stroke', d => highlightedNodeIds.has(d.id) ? '#fff' : '#ccc')
      .attr('stroke-width', d => highlightedNodeIds.has(d.id) ? 1.5 : 0.5)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize : d.symbolSize * 0.8)
    
    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 * 1.2 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.6 : 0.1)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.4 : 0.1)
    
    nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
      .attr('fill', d => highlightedNodeIds.has(d.id) ? '#171719' : '#bbb')
      .attr('font-weight', d => selectedNodeId && d.id === selectedNodeId ? 'bold' : 'normal')

    linkSelRef.current
      .attr('stroke', '#A8ABB2')
      .attr('stroke-opacity', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 0.6 : 0.15
      })
      .attr('stroke-width', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 1.5 : 0.5
      })
      .attr('marker-end', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 'url(#arrow-highlight)' : 'url(#arrow)'
      })
      .attr('stroke-dasharray', 'none')

      console.log('linkLabelSelRef', linkLabelSelRef)
    if (linkLabelSelRef.current) {
      linkLabelSelRef.current
        .style('display', d => {
          const linkId = d.id as string
          return highlightedLinkIds.has(linkId) ? 'block' : 'none'
        })
    }

  }, [selectedNodeId])

  useEffect(() => {
    if (!regionId || !nodeSelRef.current || !linkSelRef.current || !graphStateRef.current) return

    const { nodes: graphNodes, links: graphLinks } = graphStateRef.current

    const targetTypes = regionMapping[regionId] || []
    
    const highlightedNodeIds = new Set<string>()
    const highlightedLinkIds = new Set<string>()

    graphNodes.forEach(node => {
      const originalNode = nodes.find(n => n.id === node.id)
      if (!originalNode) return

      const nodeType = originalNode.caption

      if (regionId === 'amygdala') {
        if (nodeType === 'Statement' && 
            originalNode.properties && 
            (originalNode.properties.emotion_type !== undefined || 
             originalNode.properties.emotion_intensity !== undefined)) {
          highlightedNodeIds.add(node.id)
        }
      } else {
        if (targetTypes.includes(nodeType)) {
          highlightedNodeIds.add(node.id)
        }
      }
    })

    graphLinks.forEach(link => {
      const sourceId = typeof link.source === 'string' ? link.source : link.source.id
      const targetId = typeof link.target === 'string' ? link.target : link.target.id
      if (highlightedNodeIds.has(sourceId) || highlightedNodeIds.has(targetId)) {
        highlightedLinkIds.add(link.id as string)
      }
    })

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.2 : d.symbolSize * 0.8)
      .attr('fill-opacity', d => highlightedNodeIds.has(d.id) ? 0.85 : 0.15)
      .attr('stroke', d => highlightedNodeIds.has(d.id) ? '#fff' : '#ccc')
      .attr('stroke-width', d => highlightedNodeIds.has(d.id) ? 1.5 : 0.5)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize : d.symbolSize * 0.8)

    nodeSelRef.current.selectAll<SVGCircleElement, D3Node>('circle.ring')
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 * 1.2 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.6 : 0.1)
      .transition()
      .duration(200)
      .attr('r', d => highlightedNodeIds.has(d.id) ? d.symbolSize * 1.35 : d.symbolSize * 1.35 * 0.8)
      .attr('stroke-opacity', d => highlightedNodeIds.has(d.id) ? 0.4 : 0.1)

    nodeSelRef.current.selectAll<SVGTextElement, D3Node>('text')
      .attr('fill', d => highlightedNodeIds.has(d.id) ? '#171719' : '#bbb')
      .attr('font-weight', 'normal')

    linkSelRef.current
      .attr('stroke', '#A8ABB2')
      .attr('stroke-opacity', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 0.6 : 0.15
      })
      .attr('stroke-width', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 1.5 : 0.5
      })
      .attr('marker-end', d => {
        const linkId = d.id as string
        return highlightedLinkIds.has(linkId) ? 'url(#arrow-highlight)' : 'url(#arrow)'
      })
      .attr('stroke-dasharray', 'none')

    if (linkLabelSelRef.current) {
      linkLabelSelRef.current
        .style('display', d => {
          const linkId = d.id as string
          return highlightedLinkIds.has(linkId) ? 'block' : 'none'
        })
    }

  }, [regionId, nodes])

  if (!nodes || nodes.length === 0) {
    return <PageEmpty />
  }

  return (
    <div ref={containerRef} className="rb:w-full rb:h-full" />
  )
}

export default GraphNetworkChart
