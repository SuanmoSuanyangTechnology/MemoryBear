import type { ChatItem } from '../types'

/**
 * Shared pure helpers for chat lists that support regenerate version arrays,
 * i.e. `Array<ChatItem | ChatItem[]>` where an entry may be a single message or
 * an ordered list of regenerated versions. These are used by every chat panel
 * (workflow debug, trial run, multi-model compare) to avoid duplicating the
 * version-array bookkeeping.
 */

type ChatList = Array<ChatItem | ChatItem[]>

/**
 * Applies an updater to the latest version of the last chat entry, preserving
 * the single-message / version-array structure.
 */
export const mapLastVersion = (
  list: ChatList,
  updater: (item: ChatItem) => ChatItem,
): ChatList => {
  if (list.length === 0) return list
  const lastIndex = list.length - 1
  const lastEntry = list[lastIndex]
  if (Array.isArray(lastEntry)) {
    const newVersions = [...lastEntry]
    newVersions[newVersions.length - 1] = updater(lastEntry[lastEntry.length - 1])
    return [...list.slice(0, lastIndex), newVersions]
  }
  return [...list.slice(0, lastIndex), updater(lastEntry)]
}

/** Flattens a chat list into a single array of messages, expanding version arrays. */
export const flattenChatList = (list: ChatList): ChatItem[] =>
  list.flatMap((item) => (Array.isArray(item) ? item : [item]))

/**
 * Appends a fresh assistant version onto the targeted message and drops every
 * entry that originally followed it.
 */
export const appendRegenerateVersion = (prev: ChatList, voId: string): ChatList => {
  const filterIndex = prev.findIndex((item) =>
    Array.isArray(item)
      ? item.some((v) => v.id === voId)
      : item.id === voId,
  )
  if (filterIndex === -1) return prev
  const newList = prev.slice(0, filterIndex + 1)
  const existingEntry = newList[filterIndex]
  const newVersion: ChatItem = {
    role: 'assistant',
    content: '',
    created_at: Date.now(),
    is_current: true,
  }
  if (Array.isArray(existingEntry)) {
    const nextVersion = existingEntry.length + 1
    newList[filterIndex] = [
      ...existingEntry.map((v) => ({ ...v, is_current: false })),
      { ...newVersion, version: nextVersion },
    ]
  } else {
    newList[filterIndex] = [
      { ...existingEntry, is_current: false, version: 1 },
      { ...newVersion, version: 2 },
    ]
  }
  return newList
}

/** Marks the targeted message as (un)favorited. */
export const applyFavorite = (prev: ChatList, id: string, is_favorited: boolean): ChatList => {
  const lastList = [...prev]
  const filterIndex = lastList.findIndex(item => Array.isArray(item) ? item.some(msg => msg.id === id) : item.id === id)
  if (filterIndex === -1) return lastList
  const filterItem = lastList[filterIndex]
  if (Array.isArray(filterItem)) {
    lastList[filterIndex] = filterItem.map(msg => msg.id === id ? { ...msg, is_favorited } : msg)
  } else {
    lastList[filterIndex] = { ...filterItem, is_favorited }
  }
  return [...lastList]
}

/** Records the like / dislike feedback on the matching message. */
export const applyFeedback = (
  prev: ChatList,
  id: string,
  feedback_type: 'like' | 'dislike' | null,
): ChatList => {
  const lastList = [...prev]
  const filterIndex = lastList.findIndex(item => Array.isArray(item) ? item.some(msg => msg.id === id) : item.id === id)
  if (filterIndex === -1) return lastList
  const filterItem = lastList[filterIndex]
  if (Array.isArray(filterItem)) {
    lastList[filterIndex] = filterItem.map(msg => msg.id === id ? { ...msg, feedback_type } : msg)
  } else {
    lastList[filterIndex] = { ...filterItem, feedback_type }
  }
  return [...lastList]
}

/** Removes the message with the given id, dropping empty version arrays. */
export const removeMessageById = (prev: ChatList, id: string): ChatList =>
  prev.reduce<ChatList>((acc, item) => {
    if (Array.isArray(item)) {
      const rest = item.filter(msg => msg.id !== id)
      if (rest.length > 0) acc.push(rest)
    } else if (item.id !== id) {
      acc.push(item)
    }
    return acc
  }, [])

/**
 * Rebuilds a chat list from a version-switch response, enriching each message
 * with its node execution sources and pending interventions.
 */
export const buildVersionMessages = (
  res: any,
  getNodeContext: (node_id: string) => { icon?: any },
): ChatList => {
  const { node_executions_map, messages = [], pending_intervention } = res || {}
  const enrichMessage = (msg: any) => {
    const base = { ...msg, status: msg.role === 'user' ? null : msg.status }
    if (!msg.id || !(node_executions_map?.[msg.id] || pending_intervention?.[msg.id])) {
      return base
    }
    const executions = node_executions_map?.[msg.id] || []
    const interventions = pending_intervention?.[msg.id]?.interventions || []
    const lastExecution = executions?.[executions.length - 1]
    const meta = lastExecution?.meta
    let sourceList: any[] = []

    if (meta?.sources?.length > 0 && (meta?.tool_type === 'knowledge_retrieval' || meta?.tool_type === 'skill')) {
      const groupedSources = meta?.sources.reduce((acc: any, source: any) => {
        const key = source.knowledge_name || source.knowledge_id || source.name || source.id || 'default'
        if (!acc[key]) {
          acc[key] = { ...source, name: source.knowledge_name || source.name, contentList: [source.content] }
        } else {
          acc[key].contentList.push(source.content)
        }
        return acc
      }, {})
      sourceList = Object.values(groupedSources).map((group: any) => ({
        ...lastExecution,
        icon: getNodeContext(lastExecution?.node_id).icon,
        node_name: group.name,
        content: {
          input: lastExecution?.input || '',
          output: group.contentList.join('\n') || lastExecution?.output,
          error: lastExecution?.error,
        },
      }))
    } else if (meta?.sources?.length > 0) {
      sourceList = meta?.sources?.map((source: any) => ({
        ...lastExecution,
        icon: getNodeContext(lastExecution?.node_id).icon,
        name: source.name || 'default',
        content: {
          input: lastExecution?.input || '',
          output: source.content || lastExecution?.output,
          error: lastExecution?.error,
        },
      }))
    } else {
      sourceList = executions.map(({ input, output, cycle_items = [], error, process, ...node }: any) => {
        const converted: any = { ...node, icon: getNodeContext(node?.node_id).icon, content: { input, output, process, error } }
        if (node.node_type === 'loop' && Array.isArray(cycle_items) && cycle_items.length > 0) {
          converted.subContent = cycle_items.map(({ input: cInput, output: cOutput, error: cError, process: cProcess, ...cNode }: any) => ({
            ...cNode,
            icon: getNodeContext(cNode?.node_id).icon,
            content: { input: cInput, output: cOutput, process: cProcess, error: cError },
          }))
        }
        return converted
      })
    }
    return { ...base, subContent: sourceList, interventions }
  }
  return messages.map((item: any) =>
    Array.isArray(item) ? item.map(enrichMessage) : enrichMessage(item),
  )
}
