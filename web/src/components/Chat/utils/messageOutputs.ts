import type { ChatItem } from '../types'
import { mapLastVersion } from './messageVersions'

/**
 * Shared pure helpers for the per-node `meta_data.outputs` accumulation used to
 * render multi-answer replies (several output nodes each forming their own
 * segment within one assistant reply). Centralised here so the workflow debug
 * chat, trial run and conversation stream handlers all share identical behaviour.
 *
 * All helpers operate on the last assistant version of the chat list via
 * {@link mapLastVersion}, keeping the single-message / version-array structure.
 */

type ChatList = Array<ChatItem | ChatItem[]>

/**
 * message event: accumulates streaming content into the last assistant message's
 * meta_data.outputs, keyed by node_id. Appends to the matching node_id if present,
 * otherwise creates a new entry `{ node_id, content, status: 'running' }`.
 */
export const appendOutputByNodeId = (
  prev: ChatList,
  node_id?: string,
  content: string = '',
): ChatList => {
  if (!node_id || !content) return prev
  return mapLastVersion(prev, (current) => {
    if (current?.role !== 'assistant') return current
    const outputs = [...(current.meta_data?.outputs || [])]
    const filterIndex = outputs.findIndex(o => o.node_id === node_id)
    if (filterIndex < 0) {
      outputs.push({ node_id, content, status: 'running' })
    } else {
      outputs[filterIndex] = {
        ...outputs[filterIndex],
        content: (outputs[filterIndex].content || '') + content,
      }
    }
    return { ...current, meta_data: { ...(current.meta_data || {}), outputs } }
  })
}

/**
 * message_replace event: replaces one node output when node_id is present.
 * Without node_id, replaces the complete response and clears segmented outputs.
 */
export const replaceOutputByNodeId = (
  prev: ChatList,
  node_id?: string,
  content: string = '',
): ChatList =>
  mapLastVersion(prev, (current) => {
    if (current?.role !== 'assistant') return current
    if (!node_id) {
      return {
        ...current,
        content,
        meta_data: { ...(current.meta_data || {}), outputs: undefined },
      }
    }

    const outputs = [...(current.meta_data?.outputs || [])]
    const outputIndex = outputs.findIndex(output => output.node_id === node_id)
    if (outputIndex < 0) {
      outputs.push({ node_id, content, status: 'running' })
    } else {
      outputs[outputIndex] = { ...outputs[outputIndex], content }
    }

    return {
      ...current,
      content: outputs.length === 1 ? outputs[0].content : current.content,
      meta_data: { ...(current.meta_data || {}), outputs },
    }
  })

/** On stream end, marks any still-running outputs segments of the last assistant message as success. */
export const finalizeOutputs = (prev: ChatList): ChatList =>
  mapLastVersion(prev, (current) => {
    if (!current.meta_data?.outputs?.length) return current
    return {
      ...current,
      meta_data: {
        ...current.meta_data,
        outputs: current.meta_data.outputs.map(o =>
          o.status === 'running' ? { ...o, status: 'success' } : o
        ),
      },
    }
  })
