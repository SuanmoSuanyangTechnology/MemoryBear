/*
 * Selection highlight effect: reacts to selectedNodeId / selectedCategory / regionId.
 */
import { useEffect } from 'react'

import {
  getBaseStrokeWidth,
  calculateHighlightedIds,
  calculateHighlightedByRegion,
  isSingleDirectional,
} from '../utils/utils'
import type { Node, D3Node, D3Link, GraphRefs } from '../types'

interface UseHighlightParams {
  refs: GraphRefs;
  nodes: Node[];
  selectedNodeId?: string | null;
  selectedCategory?: string | null;
  regionId?: string | null;
}

export const useHighlight = ({
  refs,
  nodes,
  selectedNodeId,
  selectedCategory,
  regionId,
}: UseHighlightParams) => {
  const { nodeSelRef, linkSelRef, linkLabelSelRef, gRef, graphStateRef, visibleLabelIdsRef } = refs

  useEffect(() => {
    if (!nodeSelRef.current || !linkSelRef.current || !graphStateRef.current) return

    const { nodes: graphNodes, links: graphLinks } = graphStateRef.current

    let highlightedNodeIds = new Set<string>()
    let highlightedLinkIds = new Set<string>()

    // Support regionId highlight
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

    // When selectedCategory has value, only highlight nodes, not edges
    const isCategoryOnly = selectedCategory && !selectedNodeId
    // When node is selected, only highlight directly connected edges, not other edges of neighboring nodes
    const isNodeSelected = selectedNodeId && !isCategoryOnly
    // When link is selected, only highlight that link, other links grayed out
    const isLinkSelected = !!selectedNodeId && graphLinks.some(link => link.id === selectedNodeId)

    // Check if link is directly connected to selected node
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

    // Update bidirectional edge styles
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
}
