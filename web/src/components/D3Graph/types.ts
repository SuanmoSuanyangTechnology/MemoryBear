import type { ReactNode, RefObject } from 'react'
import type * as d3 from 'd3'

// ─── Raw input types (mirror of API response, no external dependency) ─────────
// These interfaces map 1-to-1 with the graph API response shape.

export interface SceneTimes {
  started_at: number | null
  ended_at: number | null
  created_at: number | null
  updated_at: number | null
}

export interface RawSceneNode {
  id: string
  label: 'SceneSummary'
  unassigned_reason: 'NO_PARENT' | 'INVALID_PARENT_ID' | 'PARENT_NOT_FOUND' | null
  properties: SceneTimes & {
    content: string
    topic_name?: string | null
    topic_scope: string | null
    conversation_id: string | null
    turn_count: number | null
    close_reason: string | null
    community_eligibility: string | null
    community_category_l1: string | null
    community_status: string | null
    scene_community_id: string | null
  }
}

export interface RawCommunityNode {
  id: string
  label: 'SceneCommunity' | 'UnassignedSceneGroup'
  kind: 'COMMUNITY' | 'UNASSIGNED'
  virtual: boolean
  scene_count: number
  scenes: RawSceneNode[]
  properties: SceneTimes & {
    category_l1: string | null
    topic_name: string | null
    topic_scope: string | null
    summary: string | null
    member_count: number
    boundary_instance_anchor: string | null
    boundary_lifecycle_anchor: string | null
    boundary_primary_matter: string | null
    boundary_include_rule: string | null
    boundary_exclude_rule: string | null
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
  groups: RawCommunityNode[]
  statistics: {
    total_scene_communities: number
    total_scene_summaries: number
    assigned_scene_summaries: number
    unassigned_scene_summaries: number
    total_groups: number
  }
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
  properties?: RawSceneNode['properties']
  communityName?: string
  unassignedReason?: RawSceneNode['unassigned_reason']
  isEmptyCommunity?: boolean
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
  selectedNodeId?: string | null
  selectedCommunityId?: string | null
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
  selectedCommunityIdRef: RefObject<string | null | undefined>
}

export interface LegendItem {
  key: string | number
  label: string
  color: string
}