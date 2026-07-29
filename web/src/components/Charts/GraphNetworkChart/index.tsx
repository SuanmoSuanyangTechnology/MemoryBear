import { type FC, useRef, type SetStateAction, type Dispatch, useMemo } from 'react'
import * as d3 from 'd3'
import { useTranslation } from 'react-i18next'

import PageEmpty from '@/components/Empty/PageEmpty'
import { Colors } from './utils/utils'
import { buildGraphState } from './utils/graphTransform'
import { useRenderGraph } from './hooks/useRenderGraph'
import { useHighlight } from './hooks/useHighlight'
import { useRelationHighlight } from './hooks/useRelationHighlight'
import type { Node, EdgeClickData, Edge, D3Node, D3Link, GraphState, GraphRefs } from './types'

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

  const refs: GraphRefs = {
    resizeObserverRef: useRef<ResizeObserver | null>(null),
    nodeSelRef: useRef<d3.Selection<SVGGElement, D3Node, SVGGElement, unknown> | null>(null),
    linkSelRef: useRef<d3.Selection<SVGLineElement, D3Link, SVGGElement, unknown> | null>(null),
    linkLabelSelRef: useRef<d3.Selection<SVGTextElement, D3Link, SVGGElement, unknown> | null>(null),
    gRef: useRef<d3.Selection<SVGGElement, unknown, null, undefined> | null>(null),
    graphStateRef: useRef<GraphState | null>(null),
    transformRef: useRef<d3.ZoomTransform | null>(null),
    // Track the set of label IDs that should be currently visible, preventing them from being hidden due to zoom/pan
    visibleLabelIdsRef: useRef<Set<string>>(new Set()),
  }

  const graphState = useMemo(
    () => buildGraphState(nodes, links, colors, t),
    [nodes, links, colors, t],
  )

  useRenderGraph({
    containerRef,
    graphState,
    refs,
    nodes,
    onNodeClick,
    selectedNodeId,
    selectedCategory,
    activeEdgeDirection,
  })

  useHighlight({
    refs,
    nodes,
    selectedNodeId,
    selectedCategory,
    regionId,
  })

  useRelationHighlight({
    refs,
    selectedNodeId,
    activeEdgeDirection,
    activeRelationIndex: activeRelationIndexProp,
  })

  if (!nodes || nodes.length === 0) {
    return <PageEmpty />
  }

  return <div ref={containerRef} className="rb:absolute rb:inset-0" />
}

export default GraphNetworkChart
