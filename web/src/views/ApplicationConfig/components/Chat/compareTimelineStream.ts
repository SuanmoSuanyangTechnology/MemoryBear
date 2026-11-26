import type { SSEMessage } from '@/utils/stream'
import type { ChatItem } from '@/components/Chat/types'

import type { ChatData } from '../../types/config'
import { mapModelLastVersion } from './helpers'
import {
  appendTimelineChunkToItem,
  completeTimelineAgentOnItem,
  failTimelineItem,
  finishTimelineItem,
  mergeTimelineAgentLogOnItem,
  updateTimelineToolOnItem,
  upsertTimelineAgentOnItem,
} from '@/components/Chat/utils/timelineStream'

type UpdateChatList = (updater: (prev: ChatData[]) => ChatData[]) => void

interface CompareTimelineDeps {
  updateChatList: UpdateChatList
  /** Regenerate streams omit model_config_id; the selected column supplies it. */
  defaultModelId?: string
}

/**
 * Timeline-aware wrapper for model comparison streams. Each column has its own
 * seq sequence, so timeline state is tracked per model_config_id.
 */
export const createCompareTimelineStreamHandler = (
  baseHandler: (data: SSEMessage[]) => void,
  deps: CompareTimelineDeps,
) => {
  const { updateChatList, defaultModelId } = deps
  const timelineColumns = new Set<string>()

  const normalizeEvent = (event?: string) => event?.startsWith('model_') ? event.slice('model_'.length) : (event || '')

  const mapColumn = (
    modelId: string | undefined,
    updater: (item: ChatItem) => ChatItem,
    conversationId?: string,
  ) => updateChatList(prev => mapModelLastVersion(prev, modelId, updater, conversationId))

  return (data: SSEMessage[]) => {
    data.forEach(item => {
      const event = normalizeEvent(item.event)
      const payload = item.data as any
      const modelId = payload?.model_config_id || defaultModelId
      // Cluster (multi_agent) streams carry no model_config_id and target the
      // single-column list; mapModelLastVersion falls back to index 0 for that
      // case. Track its timeline state under a stable key instead of dropping it.
      const columnKey = modelId || '__default__'

      const isSeqEvent = typeof payload?.seq === 'number'
      const isTimelineEnd = event === 'end'
        && (typeof payload?.last_seq === 'number' || payload?.last_message?.seq != null)
      if (!timelineColumns.has(columnKey) && (isSeqEvent || isTimelineEnd)) timelineColumns.add(columnKey)

      if (!timelineColumns.has(columnKey)) {
        baseHandler([item])
        return
      }

      switch (event) {
        case 'message':
          if (isSeqEvent) {
            mapColumn(modelId, current => appendTimelineChunkToItem(current, payload.content || '', payload.seq), payload.conversation_id)
          } else {
            baseHandler([item])
          }
          break
        case 'tool_start':
          mapColumn(modelId, current => updateTimelineToolOnItem(current, payload, 'pending'))
          break
        case 'tool_end':
          mapColumn(modelId, current => updateTimelineToolOnItem(current, payload, 'completed'))
          break
        case 'tool_error':
          mapColumn(modelId, current => updateTimelineToolOnItem(current, payload, 'failed'))
          break
        case 'agent_dispatch':
          mapColumn(modelId, current => upsertTimelineAgentOnItem(current, payload))
          break
        case 'agent_log':
        case 'agent_log_final':
          mapColumn(modelId, current => mergeTimelineAgentLogOnItem(current, payload))
          break
        case 'agent_complete':
          mapColumn(modelId, current => completeTimelineAgentOnItem(current, payload))
          break
        case 'start':
        case 'reasoning':
          baseHandler([item])
          break
        case 'error':
          baseHandler([item])
          mapColumn(modelId, current => failTimelineItem(current, payload))
          break
        case 'end':
          // Base handler keeps ids, audio polling, citations and spinner cleanup.
          baseHandler([item])
          mapColumn(modelId, current => finishTimelineItem(current, payload), payload.conversation_id)
          break
        default:
          baseHandler([item])
      }
    })
  }
}
