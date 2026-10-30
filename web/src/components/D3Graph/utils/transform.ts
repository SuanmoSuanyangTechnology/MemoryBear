import type { CommunityD3Node, CommunityGraphData, RawCommunityGraphData, RawCommunityNode } from '../types'
import { GRAPH_COLORS } from './forces'

// The server owns membership and ordering, including the final virtual group.
export function buildCommunityGraphData(raw: RawCommunityGraphData, colors: string[] = GRAPH_COLORS): CommunityGraphData | null {
  if (!raw.groups.length) return null
  const palette = colors.length ? colors : GRAPH_COLORS
  const nodes: CommunityD3Node[] = []
  const communityMap = new Map<string, string[]>()
  const communityCaption = new Map<string, string>()
  const communityNodeMap = new Map<string, RawCommunityNode>()
  const usedIds = new Set(raw.groups.flatMap(group => group.scenes.map(scene => scene.id)))

  raw.groups.forEach((group, index) => {
    const caption = group.kind === "UNASSIGNED" ? '' : group.properties.topic_name || group.properties.topic_scope || group.id
    const color = palette[index % palette.length]
    communityCaption.set(group.id, caption)
    communityNodeMap.set(group.id, group)
    const members = group.scenes.map(scene => scene.id)
    communityMap.set(group.id, members)
    group.scenes.forEach(scene => nodes.push({
      id: scene.id,
      name: (scene.properties.topic_scope || scene.properties.content || scene.id).slice(0, 20),
      label: scene.label,
      community: group.id,
      communityName: caption,
      symbolSize: 17,
      color,
      properties: scene.properties,
      unassignedReason: scene.unassigned_reason,
    }))
    // A layout anchor keeps an empty community visible and clickable without
    // inventing a scene or changing the API's scene/member counts.
    if (!members.length) {
      let anchorId = `empty-community:${group.id}`
      while (usedIds.has(anchorId)) anchorId += '_'
      usedIds.add(anchorId)
      members.push(anchorId)
      nodes.push({ id: anchorId, name: caption, label: group.label, community: group.id,
        communityName: caption, symbolSize: 17, color, isEmptyCommunity: true })
    }
  })
  return { nodes, links: [], communityMap, communityCaption, communityNodeMap }
}
