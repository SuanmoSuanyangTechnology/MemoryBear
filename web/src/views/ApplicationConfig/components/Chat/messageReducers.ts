import type { ChatData } from '../../types'
import type { ChatItem } from '@/components/Chat/types'
import { buildVersionMessages } from '@/components/Chat/utils/messageVersions'

/**
 * Reducers backing the message-level actions of the multi-model comparison chat
 * panel: regenerate (versioning), favorite, feedback, delete, and version-switch
 * rebuild. Each locates the model column that owns the target message id and
 * operates only on that column, leaving the other compared models untouched.
 */

type ModelList = Array<ChatItem | ChatItem[]>

/** Whether the given column list contains a message with the id. */
const containsId = (list: ModelList, id: string): boolean =>
  list.some(entry => (Array.isArray(entry) ? entry.some(m => m.id === id) : entry.id === id))

/** Finds the column index owning the message id (-1 when absent). */
const findColumnIndexById = (prev: ChatData[], id: string): number =>
  prev.findIndex(item => containsId(item.list || [], id))

/**
 * Appends a fresh assistant version onto the targeted message within its column
 * and drops every entry that originally followed it in that column.
 */
export const appendRegenerateVersion = (prev: ChatData[], voId: string): ChatData[] => {
  const colIndex = findColumnIndexById(prev, voId)
  if (colIndex === -1) return prev
  const list = prev[colIndex].list || []
  const filterIndex = list.findIndex(entry =>
    Array.isArray(entry) ? entry.some(v => v.id === voId) : entry.id === voId,
  )
  if (filterIndex === -1) return prev
  const newList = list.slice(0, filterIndex + 1)
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
      ...existingEntry.map(v => ({ ...v, is_current: false })),
      { ...newVersion, version: nextVersion },
    ]
  } else {
    newList[filterIndex] = [
      { ...existingEntry, is_current: false, version: 1 },
      { ...newVersion, version: 2 },
    ]
  }
  const next = [...prev]
  next[colIndex] = { ...next[colIndex], list: newList }
  return next
}

/** Marks the targeted message as (un)favorited within its column. */
export const applyFavorite = (prev: ChatData[], id: string, is_favorited: boolean): ChatData[] => {
  const colIndex = findColumnIndexById(prev, id)
  if (colIndex === -1) return prev
  const next = [...prev]
  next[colIndex] = {
    ...next[colIndex],
    list: (next[colIndex].list || []).map(entry =>
      Array.isArray(entry)
        ? entry.map(m => (m.id === id ? { ...m, is_favorited } : m))
        : entry.id === id ? { ...entry, is_favorited } : entry,
    ),
  }
  return next
}

/** Records the like / dislike feedback on the matching message within its column. */
export const applyFeedback = (
  prev: ChatData[],
  id: string,
  feedback_type: 'like' | 'dislike' | null,
): ChatData[] => {
  const colIndex = findColumnIndexById(prev, id)
  if (colIndex === -1) return prev
  const next = [...prev]
  next[colIndex] = {
    ...next[colIndex],
    list: (next[colIndex].list || []).map(entry =>
      Array.isArray(entry)
        ? entry.map(m => (m.id === id ? { ...m, feedback_type } : m))
        : entry.id === id ? { ...entry, feedback_type } : entry,
    ),
  }
  return next
}

/** Removes the message with the given id from its column, dropping empty version arrays. */
export const removeMessageById = (prev: ChatData[], id: string): ChatData[] => {
  const colIndex = findColumnIndexById(prev, id)
  if (colIndex === -1) return prev
  const list = prev[colIndex].list || []
  const newList = list.reduce<ModelList>((acc, entry) => {
    if (Array.isArray(entry)) {
      const rest = entry.filter(m => m.id !== id)
      if (rest.length > 0) acc.push(rest)
    } else if (entry.id !== id) {
      acc.push(entry)
    }
    return acc
  }, [])
  const next = [...prev]
  next[colIndex] = { ...next[colIndex], list: newList }
  return next
}

/** Applies a version-switch response to the column owning the switched message. */
export const applyVersionMessages = (
  prev: ChatData[],
  id: string,
  res: any,
  getNodeContext: (node_id: string) => { icon?: any },
  openingMessage?: ChatItem | null,
): ChatData[] => {
  const colIndex = findColumnIndexById(prev, id)
  if (colIndex === -1) return prev
  // The switch response omits the opening statement, so re-insert it at the top of
  // the column when provided to keep the greeting visible after switching.
  const rebuilt = buildVersionMessages(res, getNodeContext)
  const next = [...prev]
  next[colIndex] = {
    ...next[colIndex],
    list: openingMessage ? [openingMessage, ...rebuilt] : rebuilt,
  }
  return next
}
