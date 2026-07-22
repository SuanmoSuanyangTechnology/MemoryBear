/*
 * Graph state transform: convert raw nodes/links into D3 simulation data.
 */
import type { Node, Edge, D3Node, D3Link, GraphState } from '../types'
import { isSingleDirectional } from './utils'

/**
 * Build the D3 graph state (nodes/links) from raw props.
 * Returns null when there are no nodes.
 */
export const buildGraphState = (
  nodes: Node[],
  links: Edge[],
  colors: string[],
  t: (key: string) => string,
): GraphState | null => {
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
}
