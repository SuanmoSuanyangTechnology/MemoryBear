import React, { useState, useRef, useMemo, useEffect, useLayoutEffect, type FC } from 'react'

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

  // Keep the tooltip open briefly while the pointer moves from a node into the tooltip.
  const tooltipHideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelTooltipHide = () => {
    if (tooltipHideTimerRef.current) {
      clearTimeout(tooltipHideTimerRef.current)
      tooltipHideTimerRef.current = null
    }
  }

  const updateTooltip = (next: { x: number; y: number; node: CommunityD3Node } | null) => {
    cancelTooltipHide()

    if (next) {
      setTooltip(next)
    } else {
      tooltipHideTimerRef.current = setTimeout(() => {
        setTooltip(null)
        tooltipHideTimerRef.current = null
      }, 150)
    }
  }

  useEffect(() => () => {
    if (tooltipHideTimerRef.current) clearTimeout(tooltipHideTimerRef.current)
  }, [])

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
      { colors, showLegend, defaultZoom, setTooltip: renderTooltip ? updateTooltip : () => {}, onCommunityClickRef, onNodeClickRef }
    )
  }, [graphState, showLegend, defaultZoom])

  const tooltipNode = tooltip && renderTooltipRef.current
    ? renderTooltipRef.current(tooltip.node)
    : null

  const tooltipRef = useRef<HTMLDivElement | null>(null)
  const [tooltipPosition, setTooltipPosition] = useState({ left: 0, top: 0 })

  useLayoutEffect(() => {
    if (!tooltip || !tooltipRef.current) return

    const container = tooltipRef.current.parentElement
    if (!container) return

    const { width, height } = container.getBoundingClientRect()
    const { width: tooltipWidth, height: tooltipHeight } = tooltipRef.current.getBoundingClientRect()
    const gap = 14
    const padding = 8

    const preferredLeft = tooltip.x + gap
    const preferredTop = tooltip.y - 10
    const left = preferredLeft + tooltipWidth > width
      ? tooltip.x - tooltipWidth - gap
      : preferredLeft
    const top = preferredTop + tooltipHeight > height
      ? tooltip.y - tooltipHeight - gap
      : preferredTop

    setTooltipPosition({
      left: Math.min(Math.max(left, padding), Math.max(padding, width - tooltipWidth - padding)),
      top: Math.min(Math.max(top, padding), Math.max(padding, height - tooltipHeight - padding)),
    })
  }, [tooltip])

  if (isEmpty) return <PageEmpty className="rb:h-full" />
  return (
    <div className="rb:absolute rb:inset-0">
      <div ref={containerRef} className="rb:w-full rb:h-full" />
      {tooltipNode ? (
        <div
          ref={tooltipRef}
          style={{ position: 'absolute', left: tooltipPosition.left, top: tooltipPosition.top, zIndex: 20 }}
          onMouseEnter={cancelTooltipHide}
          onMouseLeave={() => updateTooltip(null)}
        >
          {tooltipNode}
        </div>
      ) : undefined}
    </div>
  )
}

export default React.memo(CommunityGraph)
