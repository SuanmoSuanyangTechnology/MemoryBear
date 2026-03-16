import type { ReactNode, RefObject } from 'react'
import type * as d3 from 'd3'

// ─── Raw input types (mirror of API response, no external dependency) ─────────
// These interfaces map 1-to-1 with the graph API response shape.

export interface RawCommunityNode {
  id: string
  label: 'Community'
  properties: {
    name: string
    summary: string
    member_entity_ids: string[]
    member_count: number
    core_entities: string[]
    community_id: string
    end_user_id?: string
    updated_at?: string
  }
}

export interface RawEntityNode {
  id: string
  label: 'ExtractedEntity'
  properties: {
    name: string
    description: string
    entity_type: string
    community_name?: string
    [key: string]: unknown
  }
}

export interface RawEdge {
  id: string
  source: string
  target: string
}

export interface RawCommunityGraphData {
  nodes: (RawCommunityNode | RawEntityNode)[]
  edges: RawEdge[]
}

// ─── D3 graph types ───────────────────────────────────────────────────────────
// Runtime node shape used by D3 simulations; extends SimulationNodeDatum for x/y/vx/vy.

export interface CommunityD3Node extends d3.SimulationNodeDatum {
  id: string
  name: string
  community: string
  label: string
  symbolSize: number
  color: string
  properties?: RawEntityNode['properties']
}

export interface D3Link extends d3.SimulationLinkDatum<CommunityD3Node> {
  isCross: boolean
}

// Convex-hull shape rendered behind each community cluster.
export interface HullDatum {
  id: string
  path: string
  color: string
  labelX: number
  labelY: number
  dashed: boolean
  caption: string
}

// Fully transformed graph data ready to be passed into initCommunityGraph.
export interface CommunityGraphData {
  nodes: CommunityD3Node[]
  links: Array<{ source: string; target: string; isCross: boolean }>
  communityMap: Map<string, string[]>
  communityCaption: Map<string, string>
  communityNodeMap: Map<string, RawCommunityNode>
}

// Props accepted by the CommunityGraph React component.
export interface CommunityGraphProps {
  data: CommunityGraphData | null
  empty?: boolean
  colors?: string[]
  renderTooltip?: (node: CommunityD3Node) => ReactNode
  showLegend?: boolean
  onCommunityClick?: (node: RawCommunityNode) => void
  onNodeClick?: (node: CommunityD3Node) => void
  defaultZoom?: number
}

// Options forwarded from the React component into the D3 initializer.
export interface InitOptions {
  colors: string[]
  showLegend: boolean
  defaultZoom: number
  setTooltip: (s: { x: number; y: number; node: CommunityD3Node } | null) => void
  onCommunityClickRef: RefObject<((node: RawCommunityNode) => void) | undefined>
  onNodeClickRef: RefObject<((node: CommunityD3Node) => void) | undefined>
}
