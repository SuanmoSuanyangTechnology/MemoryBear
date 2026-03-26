/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-13 15:17:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-24 12:19:57 
 */
import React, { useState, useRef, useMemo, useEffect, type FC } from 'react'

import { GRAPH_COLORS, initCommunityGraph } from './utils'
import { useD3Graph } from './hooks'
import type { CommunityD3Node, D3Link, CommunityGraphProps } from './types'
import PageEmpty from '@/components/Empty/PageEmpty'

// ─── Component ────────────────────────────────────────────────────────────────
// Renders a D3-powered community graph with optional tooltip and legend.

const CommunityGraph: FC<CommunityGraphProps> = ({
  data,
  empty: emptyProp,
  colors = GRAPH_COLORS,
  renderTooltip,
  showLegend = true,
  onCommunityClick,
  onNodeClick,
  defaultZoom = 1,
}) => {
  // Tooltip position and hovered node state
  const [tooltip, setTooltip] = useState<{ x: number; y: number; node: CommunityD3Node } | null>(null)

  // Keep callback refs stable to avoid re-initializing the graph on every render
  const onCommunityClickRef = useRef(onCommunityClick)
  const onNodeClickRef = useRef(onNodeClick)
  const renderTooltipRef = useRef(renderTooltip)
  useEffect(() => { onCommunityClickRef.current = onCommunityClick }, [onCommunityClick])
  useEffect(() => { onNodeClickRef.current = onNodeClick }, [onNodeClick])
  useEffect(() => { renderTooltipRef.current = renderTooltip }, [renderTooltip])

  const graphState = useMemo(() => data, [data])
  // Show empty state when explicitly flagged or when there are no nodes
  const isEmpty = emptyProp ?? !data?.nodes.length

  // Initialize (or re-initialize) the D3 graph whenever relevant state changes
  const containerRef = useD3Graph((container) => {
    if (!graphState) return
    return initCommunityGraph(
      container,
      graphState.nodes,
      graphState.links as D3Link[],
      graphState.communityMap,
      graphState.communityCaption,
      graphState.communityNodeMap,
      { colors, showLegend, defaultZoom, setTooltip: renderTooltip ? setTooltip : () => {}, onCommunityClickRef, onNodeClickRef }
    )
  }, [graphState, showLegend, defaultZoom])

  // Resolve tooltip content: use custom renderer if provided, otherwise fall back to DefaultTooltip
  const tooltipNode = tooltip && renderTooltipRef.current
    ? renderTooltipRef.current(tooltip.node)
    : null

  if (isEmpty) return <PageEmpty className="rb:h-full" />
  return (
    <div className="rb:w-full rb:h-full rb:relative">
      <div ref={containerRef} className="rb:w-full rb:h-full" />
      {tooltipNode ? (
        <div style={{ position: 'absolute', left: tooltip!.x + 14, top: tooltip!.y - 10, pointerEvents: 'none', zIndex: 20 }}>
          {tooltipNode}
        </div>
      ) : undefined}
    </div>
  )
}

export default React.memo(CommunityGraph)
