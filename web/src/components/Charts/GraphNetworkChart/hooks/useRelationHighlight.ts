/*
 * Relation highlight effect: reacts to activeEdgeDirection / activeRelationIndex for
 * bidirectional and unidirectional-multi edges.
 */
import { useEffect } from 'react'

import {
  getBaseStrokeWidth,
  getActiveRelationLabel,
  isSingleDirectional,
  isBidirectional,
} from '../utils/utils'
import type { D3Link, GraphRefs } from '../types'

interface UseRelationHighlightParams {
  refs: GraphRefs;
  selectedNodeId?: string | null;
  activeEdgeDirection?: 'a_to_b' | 'b_to_a' | null;
  activeRelationIndex?: number;
}

export const useRelationHighlight = ({
  refs,
  selectedNodeId,
  activeEdgeDirection,
  activeRelationIndex,
}: UseRelationHighlightParams) => {
  const { gRef, graphStateRef, linkLabelSelRef, visibleLabelIdsRef } = refs

  useEffect(() => {
    if (!gRef.current || !graphStateRef.current) return

    const { links: graphLinks } = graphStateRef.current
    const isLinkSelected = !!selectedNodeId && graphLinks.some(link => link.id === selectedNodeId)
    const sel = graphLinks.find(l => l.id === selectedNodeId)

    if (!isLinkSelected || !sel) return

    // Support BIDIRECTIONAL, MULTI_BIDIRECTIONAL and UNIDIRECTIONAL_MULTI types
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
      const targetLabel = getActiveRelationLabel(sel.a_to_b, sel.b_to_a, activeRelationIndex ?? 0)
      linkLabelSelRef.current.text(d => d.id === selectedNodeId ? targetLabel : d.label || '')
      // When link is selected, add this link to visibleLabelIds
      visibleLabelIdsRef.current.add(selectedNodeId as string)
    }
  }, [activeEdgeDirection, activeRelationIndex, selectedNodeId])
}
