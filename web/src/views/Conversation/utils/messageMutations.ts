/**
 * 会话消息列表的公共纯函数
 * 供反馈 / 收藏等按消息 id 局部更新的场景复用。
 */
import type { ChatItem } from '@/components/Chat/types'

/** 把消息项归一化为数组形式（单条消息会被包裹为单元素数组） */
export const toChatArray = (item: ChatItem | ChatItem[]): ChatItem[] =>
  Array.isArray(item) ? item : [item]

/**
 * 按消息 id 找到对应项并就地合并补丁字段（如 feedback_type、is_favorited）。
 * 数组形态时仅更新 id 命中的子消息，单条形态时直接更新。返回新的列表引用。
 */
export const applyMessagePatchById = (
  prev: Array<ChatItem | ChatItem[]>,
  id: string,
  patch: Partial<ChatItem>,
): Array<ChatItem | ChatItem[]> => {
  const lastList = [...prev]
  const filterIndex = lastList.findIndex(item => Array.isArray(item) ? item.some(msg => msg.id === id) : item.id === id)
  if (filterIndex === -1) return lastList
  const filterItem = lastList[filterIndex]
  if (Array.isArray(filterItem)) {
    filterItem.forEach(msg => {
      if (msg.id === id) Object.assign(msg, patch)
    })
  } else {
    Object.assign(filterItem, patch)
  }
  lastList[filterIndex] = filterItem
  return [...lastList]
}
