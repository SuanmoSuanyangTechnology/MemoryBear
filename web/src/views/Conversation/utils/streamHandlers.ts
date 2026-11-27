/**
 * Shared SSE stream-handling factory for conversation "send" and "regenerate".
 * Behaviour matches the original handleStreamMessage in index.tsx; only extracted
 * into reusable factory functions.
 */
import { type SSEMessage } from '@/utils/stream'
import type { ChatItem, MemoryTraceEvent, MemoryTraceEventData } from '@/components/Chat/types'
import {
  appendOutputByNodeId,
  finalizeOutputs,
  replaceOutputByNodeId,
} from '@/components/Chat/utils/messageOutputs'
import {
  appendTimelineMessage,
  finalizeTimelineEnd,
  finalizeTimelineError,
  isTimelineStart,
  mergeTimelineAgentLog,
  upsertTimelineAgent,
  upsertTimelineAgentComplete,
  upsertTimelineTool,
} from '@/components/Chat/utils/timelineStream'
import type { StreamData } from '../types';

/** start/node_start event: backfills the most recent user message's id with the real user_message_id */
const applyUserMessageId = (
  prev: Array<ChatItem | ChatItem[]>,
  id?: string,
): Array<ChatItem | ChatItem[]> => {
  if (!id) return prev
  for (let i = prev.length - 1; i >= 0; i--) {
    const entry = prev[i]
    if (!Array.isArray(entry) && entry.role === 'user') {
      if (entry.id === id) return prev
      return prev.map((it, idx) => (idx === i && !Array.isArray(it) ? { ...it, id } : it))
    }
  }
  return prev
}


/** On intervention_required: appends a pending human intervention to the last assistant message */
const appendInterventionRequired = (
  prev: Array<ChatItem | ChatItem[]>,
  data: StreamData,
): Array<ChatItem | ChatItem[]> => {
  const { message_id, execution_id, node_id, node_name, rendered_content, form_fields, actions, timeout_at } = data
  const newIntervention = {
    execution_id,
    node_id: node_id,
    node_name: node_name,
    rendered_content,
    form_fields: form_fields || [],
    actions: actions || [],
    timeout_at,
  }
  const lastList = [...prev]
  const lastIndex = lastList.length - 1
  const lastMsg = lastList[lastIndex]

  if (Array.isArray(lastMsg)) {
    const lastChatIndex = lastMsg.length - 1
    const lastAssistantMsg = lastMsg[lastChatIndex] as ChatItem
    if (lastAssistantMsg?.role === 'assistant') {
      return [
        ...lastList.slice(0, lastIndex),
        [
          ...lastMsg.slice(0, lastChatIndex),
          {
            id: message_id,
            ...lastAssistantMsg,
            meta_data: {
              ...lastAssistantMsg.meta_data,
              waiting_human: true
            },
            interventions: [
              ...(lastAssistantMsg.interventions || []),
              newIntervention
            ]
          }
        ]
      ]
    }
  } else if (lastMsg?.role === 'assistant') {
    return [
      ...lastList.slice(0, lastIndex),
      {
        ...lastMsg,
        meta_data: {
          ...lastMsg.meta_data,
          waiting_human: true
        },
        interventions: [
          ...(lastMsg.interventions || []),
          newIntervention
        ]
      }
    ]
  }
  return prev
}

/** On intervention_timeout: array form appends the intervention; single form marks the matching intervention as timed out */
const markInterventionTimeout = (
  prev: Array<ChatItem | ChatItem[]>,
  data: StreamData,
): Array<ChatItem | ChatItem[]> => {
  const { message_id, execution_id, node_id, node_name, rendered_content, form_fields, actions, timeout_at } = data
  const lastList = [...prev]
  const lastIndex = lastList.length - 1
  const lastMsg = lastList[lastIndex]
  if (Array.isArray(lastMsg)) {
    const lastChatIndex = lastMsg.length - 1
    const lastAssistantMsg = lastMsg[lastChatIndex] as ChatItem
    if (lastAssistantMsg?.role === 'assistant') {
      return [
        ...lastList.slice(0, lastIndex),
        [
          ...lastMsg.slice(0, lastChatIndex),
          {
            id: message_id,
            ...lastAssistantMsg,
            meta_data: {
              ...lastAssistantMsg.meta_data,
              waiting_human: true
            },
            interventions: [
              ...(lastAssistantMsg.interventions || []),
              {
                execution_id,
                node_id: node_id,
                node_name: node_name,
                rendered_content,
                form_fields: form_fields || [],
                actions: actions || [],
                timeout_at,
              }
            ]
          }
        ]
      ]
    }
    return prev
  } else {
    if (!lastMsg?.interventions || lastMsg.interventions.length === 0) {
      return prev
    }

    const filterIndex = lastMsg.interventions.findIndex(it => it.node_id === node_id)
    lastMsg.interventions[filterIndex] = {
      ...lastMsg.interventions[filterIndex],
      resolved_action_id: '__timeout__',
      resolved_kind: 'timeout'
    }

    return [
      ...prev.slice(0, -1),
      {
        ...lastMsg,
      }
    ]
  }
}

/**
 * Sync the sidebar history and the active conversation id with the conversation
 * reported by the stream. Called on `start`, `error` and `end`/`workflow_end` so
 * an early failure can't leave the newly created conversation out of the list.
 */
export const syncConversation = (
  targetConversationId: string | null | undefined,
  currentConversationId: string | null,
  upsertHistory: (conversationId: string, title?: string) => void,
  setConversationId: (id: string | null) => void,
  title?: string,
) => {
  if (!targetConversationId) return
  upsertHistory(targetConversationId, title)
  if (targetConversationId !== currentConversationId) {
    setConversationId(targetConversationId)
  }
}

export interface StreamHandlerDeps {
  /** Current conversation id */
  conversationId: string | null
  setChatList: React.Dispatch<React.SetStateAction<Array<ChatItem | ChatItem[]>>>
  setConversationId: (id: string | null) => void
  setLoading: (value: boolean) => void
  updateAssistantMessage: (
    content?: string,
    audio_url?: string,
    audio_status?: string,
    citations?: any[],
    suggested_questions?: any[],
    error?: string,
    message_id?: string,
    replace?: boolean,
  ) => void
  updateAssistantReasoningMessage: (content?: string, message_id?: string) => void
  updateAssistantMemoryRetrieval: (
    event: MemoryTraceEvent,
    data: MemoryTraceEventData,
  ) => void
  startAudioPolling: (audioUrl: string, idToPoll: string) => void
  /** Locally update the history list after streaming ends (insert new / refresh updated_at for existing) */
  upsertHistory: (conversationId: string, title?: string) => void
  /** Title source for new conversations (first user message); only used in the send scenario */
  title?: string
  chatIsEnded: React.MutableRefObject<boolean>
  streamLoadingRef: React.MutableRefObject<boolean>
}

/** Stream handler for the normal send scenario (includes human intervention events) */
export const createSendStreamHandler = (deps: StreamHandlerDeps) => {
  const {
    conversationId, setChatList, setConversationId, setLoading,
    updateAssistantMessage, updateAssistantReasoningMessage, updateAssistantMemoryRetrieval,
    startAudioPolling,
    upsertHistory, title, chatIsEnded, streamLoadingRef,
  } = deps

  let currentConversationId: string | null = null
  let hasTimeline = false

  return (data: SSEMessage[]) => {
    data.forEach((item) => {
      console.log('event', item)
      // SSE heartbeat/comment frames carry no event/data; never route them.
      if (item.comment !== undefined || !item.event || item.data === undefined) return
      const payload = item.data as any
      if (!hasTimeline && isTimelineStart(item)) hasTimeline = true
      const {
        message_id,
        user_message_id,
        file_id,
        node_id,
        content, conversation_id: curId, audio_url, citations, suggested_questions, msg, error,
      } = item.data as StreamData;
      switch (item.event) {
        case 'start':
        case 'node_start': {
          if (curId) {
            currentConversationId = curId
            // The conversation is created on the backend at `start`; show it in the
            // sidebar right away so an early `error` (with no `end`) doesn't lose it.
            upsertHistory(curId, title)
          }
          setChatList(prev => {
            const withUserId = applyUserMessageId(prev, user_message_id)
            const lastList = [...withUserId]
            const lastIndex = lastList.length - 1
            const lastMsg = lastList[lastIndex] as ChatItem
            if (lastMsg?.role === 'assistant') {
              return [
                ...lastList.slice(0, lastIndex),
                {
                  id: message_id,
                  ...lastMsg,
                }
              ]
            }
            return withUserId
          })
          break
        }
        case 'error': {
          const errorMsg = msg || error
          if (errorMsg) {
            setChatList(prev => {
              const lastList = [...prev]
              const lastIndex = lastList.length - 1
              const lastMsg = lastList[lastIndex]
              if (lastMsg && !Array.isArray(lastMsg) && lastMsg.role === 'assistant') {
                lastList[lastIndex] = { ...lastMsg, content: errorMsg, meta_data: { ...lastMsg.meta_data, error: errorMsg } }
                return lastList
              }
              return [...prev, { role: 'assistant', content: errorMsg, created_at: Date.now(), meta_data: { error: errorMsg } }]
            })
          }
          // `error` can terminate the stream before `end`; keep sidebar/active id in
          // sync with the id received in `start`.
          syncConversation(
            curId || currentConversationId || conversationId,
            conversationId,
            upsertHistory,
            setConversationId,
            title,
          )
          if (hasTimeline) {
            setChatList(prev => finalizeTimelineError(prev, payload))
            streamLoadingRef.current = false
          }
          setLoading(false)
          chatIsEnded.current = true
          break
        }
        case 'reasoning':
          updateAssistantReasoningMessage(content)
          if (curId) currentConversationId = curId;
          break
        case 'tool_start':
        case 'memory_stage':
        case 'tool_end':
        case 'tool_error':
          // Memory trace frames must always feed memory_retrieval, even when
          // they carry a seq and are also reduced into a timeline tool step.
          updateAssistantMemoryRetrieval(item.event as MemoryTraceEvent, item.data as MemoryTraceEventData)
          if (hasTimeline && typeof payload?.seq === 'number') {
            const status = item.event === 'tool_start' ? 'pending' : item.event === 'tool_error' ? 'failed' : 'completed'
            setChatList(prev => upsertTimelineTool(prev, payload, status))
          }
          break
        case 'agent_dispatch':
          if (hasTimeline) setChatList(prev => upsertTimelineAgent(prev, payload))
          break
        case 'agent_log':
        case 'agent_log_final':
          if (hasTimeline) setChatList(prev => mergeTimelineAgentLog(prev, payload))
          break
        case 'agent_complete':
          if (hasTimeline) setChatList(prev => upsertTimelineAgentComplete(prev, payload))
          break
        case 'message':
          if (hasTimeline && typeof payload?.seq === 'number') {
            setChatList(prev => appendTimelineMessage(prev, content || '', payload.seq))
          } else {
            updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined)
            setChatList(prev => appendOutputByNodeId(prev, node_id, content))
          }
          if (curId) currentConversationId = curId;
          break
        case 'message_replace':
          if (streamLoadingRef.current) streamLoadingRef.current = false
          setChatList(prev => replaceOutputByNodeId(prev, node_id, content))
          if (audio_url) {
            updateAssistantMessage('', audio_url, 'pending')
          }
          if (curId) currentConversationId = curId;
          break
        case 'intervention_required': {
          if (streamLoadingRef.current) streamLoadingRef.current = false
          setChatList(prev => appendInterventionRequired(prev, item.data as StreamData))
          break;
        }
        case 'intervention_timeout': {
          setChatList(prev => markInterventionTimeout(prev, item.data as StreamData))
          break
        }
        case 'end':
        case 'workflow_end': {
          const errorMsg = msg || error
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, errorMsg)
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || errorMsg) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, errorMsg)
          }
          setChatList(prev => finalizeOutputs(prev))
          if (hasTimeline) {
            setChatList(prev => finalizeTimelineEnd(prev, payload))
            streamLoadingRef.current = false
          }
          setLoading(false)
          syncConversation(
            currentConversationId || conversationId,
            conversationId,
            upsertHistory,
            setConversationId,
            title,
          )
          chatIsEnded.current = true
          break
        }
      }
    })
  }
}

/** Stream handler for the regenerate scenario (appends a new version to the specified message) */
export const createRegenerateStreamHandler = (deps: StreamHandlerDeps & { messageId: string }) => {
  const {
    messageId, conversationId, setChatList, setConversationId, setLoading,
    updateAssistantMessage, updateAssistantReasoningMessage, updateAssistantMemoryRetrieval,
    startAudioPolling,
    upsertHistory, chatIsEnded, streamLoadingRef,
  } = deps

  let currentConversationId: string | null = null
  let hasTimeline = false

  return (data: SSEMessage[]) => {
    data.forEach((item) => {
      // SSE heartbeat/comment frames carry no event/data; never route them.
      if (item.comment !== undefined || !item.event || item.data === undefined) return
      const payload = item.data as any
      if (!hasTimeline && isTimelineStart(item)) hasTimeline = true
      const { message_id, user_message_id, file_id, node_id, content, conversation_id: curId, audio_url, citations, suggested_questions, msg, error } = item.data as StreamData;

      switch (item.event) {
        case 'start':
        case 'node_start': {
          if (curId) {
            currentConversationId = curId
            upsertHistory(curId)
          }
          setChatList(prev => {
            const withUserId = applyUserMessageId(prev, user_message_id)
            const lastList = [...withUserId]
            const lastIndex = lastList.length - 1
            const lastEntry = lastList[lastIndex]

            if (Array.isArray(lastEntry)) {
              const lastChatIndex = lastEntry.length - 1
              const lastMsg = lastEntry[lastChatIndex]
              if (lastMsg?.role === 'assistant') {
                return [
                  ...lastList.slice(0, lastIndex),
                  [
                    ...lastEntry.slice(0, lastChatIndex),
                    { id: message_id, ...lastMsg },
                  ],
                ]
              }
            } else if (lastEntry?.role === 'assistant') {
              return [
                ...lastList.slice(0, lastIndex),
                { id: message_id, ...lastEntry },
              ]
            }
            return withUserId
          })
          break
        }
        case 'error': {
          const errorMsg = msg || error
          if (errorMsg) {
            updateAssistantMessage('', undefined, undefined, undefined, undefined, errorMsg, messageId)
          }
          syncConversation(
            curId || currentConversationId || conversationId,
            conversationId,
            upsertHistory,
            setConversationId,
          )
          if (hasTimeline) {
            setChatList(prev => finalizeTimelineError(prev, payload))
            streamLoadingRef.current = false
          }
          setLoading(false)
          chatIsEnded.current = true
          break
        }
        case 'reasoning':
          updateAssistantReasoningMessage(content, messageId)
          if (curId) currentConversationId = curId;
          break
        case 'tool_start':
        case 'memory_stage':
        case 'tool_end':
        case 'tool_error':
          // Memory trace frames must always feed memory_retrieval, even when
          // they carry a seq and are also reduced into a timeline tool step.
          updateAssistantMemoryRetrieval(item.event as MemoryTraceEvent, item.data as MemoryTraceEventData)
          if (hasTimeline && typeof payload?.seq === 'number') {
            const status = item.event === 'tool_start' ? 'pending' : item.event === 'tool_error' ? 'failed' : 'completed'
            setChatList(prev => upsertTimelineTool(prev, payload, status))
          }
          break
        case 'agent_dispatch':
          if (hasTimeline) setChatList(prev => upsertTimelineAgent(prev, payload))
          break
        case 'agent_log':
        case 'agent_log_final':
          if (hasTimeline) setChatList(prev => mergeTimelineAgentLog(prev, payload))
          break
        case 'agent_complete':
          if (hasTimeline) setChatList(prev => upsertTimelineAgentComplete(prev, payload))
          break
        case 'message':
          if (hasTimeline && typeof payload?.seq === 'number') {
            setChatList(prev => appendTimelineMessage(prev, content || '', payload.seq))
          } else {
            updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined, undefined, undefined, undefined, messageId)
            setChatList(prev => appendOutputByNodeId(prev, node_id, content))
          }
          if (curId) currentConversationId = curId;
          break
        case 'message_replace':
          if (streamLoadingRef.current) streamLoadingRef.current = false
          setChatList(prev => replaceOutputByNodeId(prev, node_id, content))
          if (audio_url) {
            updateAssistantMessage('', audio_url, 'pending', undefined, undefined, undefined, messageId)
          }
          if (curId) currentConversationId = curId;
          break
        case 'intervention_required': {
          if (streamLoadingRef.current) streamLoadingRef.current = false
          setChatList(prev => appendInterventionRequired(prev, item.data as StreamData))
          break;
        }
        case 'intervention_timeout': {
          setChatList(prev => markInterventionTimeout(prev, item.data as StreamData))
          break
        }
        case 'end':
        case 'workflow_end': {
          const errorMsg = msg || error
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, errorMsg, messageId)
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || errorMsg) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, errorMsg, messageId)
          }
          setChatList(prev => finalizeOutputs(prev))
          if (hasTimeline) {
            setChatList(prev => finalizeTimelineEnd(prev, payload))
            streamLoadingRef.current = false
          }
          setLoading(false)
          syncConversation(
            currentConversationId || conversationId,
            conversationId,
            upsertHistory,
            setConversationId,
          )
          chatIsEnded.current = true
          break
        }
      }
    })
  }
}
