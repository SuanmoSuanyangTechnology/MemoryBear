/**
 * Shared SSE stream-handling factory for conversation "send" and "regenerate".
 * Behaviour matches the original handleStreamMessage in index.tsx; only extracted
 * into reusable factory functions.
 */
import { type SSEMessage } from '@/utils/stream'
import type { ChatItem, MemoryTraceEvent, MemoryTraceEventData } from '@/components/Chat/types'
import { appendOutputByNodeId, finalizeOutputs } from '@/components/Chat/utils/messageOutputs'
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

  return (data: SSEMessage[]) => {
    data.forEach((item) => {
      const {
        message_id,
        user_message_id,
        file_id,
        node_id,
        content, conversation_id: curId, audio_url, citations, suggested_questions, error,
      } = item.data as StreamData;
      switch (item.event) {
        case 'start':
        case 'node_start': {
          currentConversationId = curId
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
        case 'reasoning':
          updateAssistantReasoningMessage(content)
          if (curId) currentConversationId = curId;
          break
        case 'tool_start':
        case 'memory_stage':
        case 'tool_end':
        case 'tool_error':
          updateAssistantMemoryRetrieval(item.event as MemoryTraceEvent, item.data as MemoryTraceEventData)
          break
        case 'message':
          updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined)
          setChatList(prev => appendOutputByNodeId(prev, node_id, content))
          if (curId) currentConversationId = curId;
          break
        case 'message_replace':
          updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined, undefined, undefined, undefined, undefined, true)
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
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, error)
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || error) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, error)
          }
          setChatList(prev => finalizeOutputs(prev))
          setLoading(false)
          const targetConvId = currentConversationId || conversationId
          if (targetConvId) {
            upsertHistory(targetConvId, title)
          }
          if (currentConversationId && currentConversationId !== conversationId) {
            setConversationId(currentConversationId)
          }
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

  return (data: SSEMessage[]) => {
    data.forEach((item) => {
      const { message_id, user_message_id, file_id, node_id, content, conversation_id: curId, audio_url, citations, suggested_questions, error } = item.data as StreamData;
      switch (item.event) {
        case 'start':
        case 'node_start': {
          currentConversationId = curId
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
        case 'reasoning':
          updateAssistantReasoningMessage(content, messageId)
          if (curId) currentConversationId = curId;
          break
        case 'tool_start':
        case 'memory_stage':
        case 'tool_end':
        case 'tool_error':
          updateAssistantMemoryRetrieval(item.event as MemoryTraceEvent, item.data as MemoryTraceEventData)
          break
        case 'message':
          updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined, undefined, undefined, undefined, messageId)
          setChatList(prev => appendOutputByNodeId(prev, node_id, content))
          if (curId) currentConversationId = curId;
          break
        case 'message_replace':
          updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined, undefined, undefined, undefined, messageId, true)
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
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, error, messageId)
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || error) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, error, messageId)
          }
          setChatList(prev => finalizeOutputs(prev))
          setLoading(false)
          const targetConvId = currentConversationId || conversationId
          if (targetConvId) {
            upsertHistory(targetConvId)
          }
          if (currentConversationId && currentConversationId !== conversationId) {
            setConversationId(currentConversationId)
          }
          chatIsEnded.current = true
          break
        }
      }
    })
  }
}
