import type { CommunityD3Node, CommunityGraphData, RawCommunityGraphData, RawCommunityNode, RawEntityNode } from '../types'
import { GRAPH_COLORS, connectionToRadius } from './forces'

// ─── Community graph data transform ─────────────────────────────────────────

export function buildCommunityGraphData(raw: RawCommunityGraphData, colors: string[] = GRAPH_COLORS): CommunityGraphData | null {
  const getColor = (i: number) => colors[i % colors.length]

  const communityNodes = raw.nodes.filter(n => n.label === 'Community') as RawCommunityNode[]
  const communityCaption = new Map<string, string>()
  const communityMap = new Map<string, string[]>()

  communityNodes.forEach(n => {
    communityCaption.set(n.id, n.properties.name)
    communityMap.set(n.id, n.properties.member_entity_ids)
  })

  const entityToCommunity = new Map<string, string>()
  communityMap.forEach((members, commId) => members.forEach(eid => entityToCommunity.set(eid, commId)))

  const commKeys = Array.from(communityMap.keys())
  const commIndex = new Map(commKeys.map((k, i) => [k, i]))

  const entityNodes = raw.nodes.filter(n => n.label === 'ExtractedEntity') as RawEntityNode[]
  const entityNodeSet = new Set(entityNodes.map(n => n.id))

  const connectionCount: Record<string, number> = {}
  raw.edges.forEach(e => {
    if (entityNodeSet.has(e.source)) connectionCount[e.source] = (connectionCount[e.source] || 0) + 1
    if (entityNodeSet.has(e.target)) connectionCount[e.target] = (connectionCount[e.target] || 0) + 1
  })

  const nodes: CommunityD3Node[] = entityNodes.map(n => {
    const commId = entityToCommunity.get(n.id) ?? commKeys[0]
    return {
      id: n.id,
      name: n.properties.name,
      community: commId,
      label: n.label,
      symbolSize: connectionToRadius(connectionCount[n.id] || 0),
      color: getColor(commIndex.get(commId) ?? 0),
      properties: n.properties,
    }
  })

  if (!nodes.length) return null

  const links = raw.edges
    .filter(e => entityNodeSet.has(e.source) && entityNodeSet.has(e.target))
    .map(e => ({
      source: e.source,
      target: e.target,
      isCross: entityToCommunity.get(e.source) !== entityToCommunity.get(e.target),
    }))

  const communityNodeMap = new Map<string, RawCommunityNode>(
    communityNodes.map(n => [n.id, n])
  )
  return { nodes, links, communityMap, communityCaption, communityNodeMap }
}
