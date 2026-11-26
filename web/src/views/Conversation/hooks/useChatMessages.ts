/**
 * useChatMessages
 * Manages CRUD of the conversation message list, assistant voice file polling,
 * and normalized writes of conversation details.
 * Logic stays in line with the original index.tsx; only modular extraction is applied.
 */
import { useState, useRef, useEffect } from 'react'

import type {
  ChatItem,
  Intervention,
  MemoryRetrieval,
  MemoryToolCall,
  MemoryTraceEvent,
  MemoryTraceEventData,
} from '@/components/Chat/types'
import { getFileStatusById } from '@/api/fileStorage'
import { buildTimelineSteps, type Timeline, type TimelineChatItem } from '@/views/ApplicationConfig/utils/logTimeline'

const normalizeMemoryInput = (input: MemoryTraceEventData['input']): Record<string, unknown> => {
  if (input && typeof input === 'object' && !Array.isArray(input)) return input
  if (typeof input !== 'string') return {}
  try {
    const parsed = JSON.parse(input) as unknown
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {}
  } catch {
    return {}
  }
}

/** Fold a long_term_memory trace event into the memory_retrieval structure. */
const updateMemoryTrace = (
  current: MemoryRetrieval | undefined,
  event: MemoryTraceEvent,
  data: MemoryTraceEventData,
): MemoryRetrieval | undefined => {
  const toolCalls = current?.tool_calls || []
  const stepId = data.step_id
  const toolName = data.name || data.meta?.tool_type

  if (event === 'tool_start') {
    if (toolName !== 'long_term_memory' || !stepId) return current
    const nextToolCall: MemoryToolCall = {
      step_id: stepId,
      seq: data.seq,
      name: 'long_term_memory',
      input: normalizeMemoryInput(data.input),
      status: 'running',
      stages: [],
    }
    const existingIndex = toolCalls.findIndex(call => call.step_id === stepId)
    return {
      schema_version: 1,
      tool_calls: existingIndex < 0
        ? [...toolCalls, nextToolCall]
        : toolCalls.map((call, index) => index === existingIndex ? nextToolCall : call),
    }
  }

  if (!stepId) return current
  const targetIndex = toolCalls.findIndex(call => call.step_id === stepId)
  if (targetIndex < 0) return current

  if (event === 'memory_stage') {
    if (!data.stage) return current
    return {
      schema_version: current?.schema_version || 1,
      tool_calls: toolCalls.map((call, index) => index === targetIndex
        ? {
          ...call,
          stages: [
            ...call.stages,
            {
              stage: data.stage as string,
              status: data.status || 'completed',
              data: data.data || {},
            },
          ],
        }
        : call),
    }
  }

  return {
    schema_version: current?.schema_version || 1,
    tool_calls: toolCalls.map((call, index) => index === targetIndex
      ? {
        ...call,
        status: event === 'tool_error' ? 'failed' : 'completed',
        ...(event === 'tool_error' ? { error: data.error } : {}),
      }
      : call),
  }
}

/** Rebuild the step timeline from persisted meta_data.timeline so historical messages render like streaming (v0.5.8). */
const enrichTimelineMessage = (msg: ChatItem): TimelineChatItem => {
  if (msg.role !== 'assistant') return msg
  const timeline = (msg.meta_data as Record<string, any> | undefined)?.timeline as Timeline | undefined
  if (!timeline?.steps?.length) return msg
  const built = buildTimelineSteps(timeline, [])
  return {
    ...msg,
    timeline: built,
    subContent: built.steps,
    content: '',
  } as TimelineChatItem
}

const enrichTimelineEntry = (entry: ChatItem | ChatItem[]): ChatItem | ChatItem[] =>
  Array.isArray(entry) ? entry.map(enrichTimelineMessage) : enrichTimelineMessage(entry)

export function useChatMessages() {
  const [chatList, setChatList] = useState<Array<ChatItem | ChatItem[]>>([])
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})
  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const streamLoadingRef = useRef(false)
  console.log('chatList', chatList)
  /** Clear all polling timers on unmount */
  useEffect(() => {
    return () => {
      audioPollingRef.current.forEach((timer) => clearInterval(timer))
      audioPollingRef.current.clear()
    }
  }, [])

  /** Sync the message audio_status from the polled state */
  useEffect(() => {
    if (!Object.keys(audioStatusMap).length) return
    setChatList(prev => prev.map(msg => {
      if (Array.isArray(msg)
        ? msg.some(i => i.role === 'assistant' && i.meta_data?.audio_url && audioStatusMap[i.meta_data.audio_url])
        : msg.role === 'assistant' && msg.meta_data?.audio_url && audioStatusMap[msg.meta_data.audio_url]
      ) {
        return Array.isArray(msg)
          ? msg.map(i => ({
            ...i,
            meta_data: {
              ...i.meta_data,
              audio_status: audioStatusMap[i.meta_data?.audio_url || '']
            }
          }))
          : {
            ...msg,
            meta_data: {
              ...msg.meta_data,
              audio_status: audioStatusMap[msg.meta_data?.audio_url || '']
            }
          }
      }
      return msg
    }))
  }, [audioStatusMap, chatList.length])

  /** Poll the voice file conversion status */
  const startAudioPolling = (audioUrl: string, idToPoll: string) => {
    if (audioPollingRef.current.has(idToPoll)) return
    const fileId = audioUrl.split('/').pop()
    if (!fileId) return
    const timer = setInterval(() => {
      getFileStatusById(fileId)
        .then(res => {
          const { status } = res as { status: string }
          if (status && status !== 'pending') {
            setAudioStatusMap(prev => ({ ...prev, [idToPoll]: status }))
            clearInterval(audioPollingRef.current.get(idToPoll))
            audioPollingRef.current.delete(idToPoll)
          }
        })
        .catch(() => {
          clearInterval(audioPollingRef.current.get(idToPoll))
          audioPollingRef.current.delete(idToPoll)
        })
    }, 2000)
    audioPollingRef.current.set(idToPoll, timer)
  }

  /** Append a user message */
  const addUserMessage = (conversation_id: string | null, message: string = '', files?: any[]) => {
    setChatList(prev => [...prev, {
      conversation_id,
      role: 'user',
      content: message,
      created_at: Date.now(),
      meta_data: {
        files
      },
    }])
  }

  /** Append an assistant message (with a message_id it appends a new version to that message) */
  const addAssistantMessage = (message_id?: string) => {
    const assistantMsg: ChatItem = {
      created_at: Date.now(),
      role: 'assistant',
      content: '',
      is_current: true,
      version: 0
    }
    if (message_id) {
      setChatList(prev => {
        const lastChatList = [...prev]
        const filterIndex = lastChatList.findIndex(item => Array.isArray(item) ? item.some(i => i.id === message_id) : item.id === message_id)
        if (filterIndex !== -1) {
          const target = lastChatList[filterIndex]
          const isArr = Array.isArray(target)
          const filterItem = isArr ? target[0] : target
          const newFilterItem: ChatItem[] = [
            ...(isArr
              ? [...target.map(v => ({ ...v, is_current: false }))]
              : [{ ...filterItem, version: 1, is_current: false }]
            ),
            {
              ...assistantMsg,
              version: isArr ? target.length + 1 : 2
            }
          ]
          lastChatList[filterIndex] = newFilterItem
          return [...lastChatList]
        }
        return prev
      })
    } else {
      setChatList(prev => [
        ...prev,
        assistantMsg
      ])
    }
  }

  /** Stream-update the assistant message body/voice/citations/suggested questions/errors (replace=true overwrites the whole body) */
  const updateAssistantMessage = (
    content: string = '',
    audio_url?: string,
    audio_status?: string,
    citations?: any[],
    suggested_questions?: any[],
    error?: string,
    message_id?: string,
    replace?: boolean
  ) => {
    if (!content && !audio_url && (!citations || citations?.length < 1) && (!suggested_questions || suggested_questions?.length < 1) && typeof error !== 'string') return
    if (streamLoadingRef.current) streamLoadingRef.current = false
    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      const lastChatList = Array.isArray(lastList[lastIndex]) ? lastList[lastIndex] : [lastList[lastIndex]]
      const lastChatIndex = lastChatList.length - 1
      const lastMsg = lastChatList[lastChatIndex]
      const assistantMsg: ChatItem = {
        id: message_id,
        ...lastMsg,
        content: replace ? content : lastMsg.content + content,
        meta_data: {
          ...(lastMsg.meta_data || {}),
          audio_url: audio_url || lastMsg.meta_data?.audio_url,
          audio_status: audio_status || lastMsg.meta_data?.audio_status,
          citations: citations || lastMsg.meta_data?.citations,
          suggested_questions: suggested_questions || lastMsg.meta_data?.suggested_questions,
          error: error || lastMsg.meta_data?.error,
          outputs: replace ? undefined : lastMsg.meta_data?.outputs
        }
      }
      if (lastMsg?.role === 'assistant') {
        return [
          ...lastList.slice(0, lastIndex),
          message_id
            ? [
              ...lastChatList.slice(0, lastChatIndex),
              assistantMsg
            ] : assistantMsg
        ]
      }
      return prev
    })
  }

  /** Stream-update the assistant message thinking content */
  const updateAssistantReasoningMessage = (content: string = '', message_id?: string) => {
    if (!content) return
    if (streamLoadingRef.current) streamLoadingRef.current = false
    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      if (Array.isArray(lastList[lastIndex])) {
        const lastChatList = lastList[lastIndex]
        const lastChatIndex = lastChatList.length - 1
        const lastMsg = lastChatList[lastChatIndex]
        if (lastMsg?.role === 'assistant') {
          return [
            ...lastList.slice(0, lastIndex),
            [
              ...lastChatList.slice(0, lastChatIndex),
              {
                id: message_id,
                ...lastMsg,
                meta_data: {
                  ...(lastMsg.meta_data || {}),
                  reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + content
                }
              }
            ]
          ]
        }
      } else {
        const lastMsg = lastList[lastIndex]
        if (lastMsg?.role === 'assistant') {
          return [
            ...lastList.slice(0, lastIndex),
            {
              id: message_id,
              ...lastMsg,
              meta_data: {
                ...(lastMsg.meta_data || {}),
                reasoning_content: (lastMsg.meta_data?.reasoning_content || '') + content
              }
            }
          ]
        }
      }
      return prev
    })
  }

  /** Normalize long-term memory tool events into the memory_retrieval structure used by historical messages */
  const updateAssistantMemoryRetrieval = (
    event: MemoryTraceEvent,
    data: MemoryTraceEventData,
  ) => {
    if (streamLoadingRef.current) streamLoadingRef.current = false
    setChatList(prev => {
      const lastList = [...prev]
      const lastIndex = lastList.length - 1
      const lastEntry = lastList[lastIndex]
      const versions = Array.isArray(lastEntry) ? lastEntry : [lastEntry]
      const lastChatIndex = versions.length - 1
      const lastMsg = versions[lastChatIndex]
      if (lastMsg?.role !== 'assistant') return prev

      const memoryRetrieval = updateMemoryTrace(lastMsg.meta_data?.memory_retrieval, event, data)
      if (memoryRetrieval === lastMsg.meta_data?.memory_retrieval) return prev

      const assistantMsg: ChatItem = {
        ...lastMsg,
        meta_data: {
          ...(lastMsg.meta_data || {}),
          memory_retrieval: memoryRetrieval,
        },
      }
      return [
        ...lastList.slice(0, lastIndex),
        Array.isArray(lastEntry)
          ? [...versions.slice(0, lastChatIndex), assistantMsg]
          : assistantMsg,
      ]
    })
  }

  /** Write conversation details: normalize messages, attach pending manual interventions, and restart voice polling as needed */
  const applyChatDetail = (response: { messages: ChatItem[]; pending_intervention?: Record<string, any> }) => {
    const messages = response?.messages || []
    const historyAudioUrls = new Set(messages.map(m => m.meta_data?.audio_url).filter(Boolean))
    audioPollingRef.current.forEach((timer, key) => {
      if (!historyAudioUrls.has(key)) {
        clearInterval(timer)
        audioPollingRef.current.delete(key)
      }
    })
    messages.forEach(msg => {
      if (Array.isArray(msg)
        ? msg.some(item => item.is_current && item.role === 'assistant' && item.meta_data?.audio_url && item.meta_data?.audio_status === 'pending')
        : msg.role === 'assistant' && msg.meta_data?.audio_url && msg.meta_data?.audio_status === 'pending'
      ) {
        const audio_url = Array.isArray(msg) ? msg.find(item => item.is_current)?.meta_data?.audio_url : msg.meta_data?.audio_url
        startAudioPolling(audio_url, audio_url)
      }
    })
    setChatList(messages.map(msg => {
      if (Array.isArray(msg)) {
        return msg.map(item => {
          if (item.role === 'assistant' && item.meta_data?.audio_url && audioPollingRef.current.has(item.meta_data.audio_url)) {
            const pendingIntervention = item.id ? response?.pending_intervention?.[item.id] || {} : {}
            item.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
              ...intervention,
              execution_id: pendingIntervention.execution_id,
            }))
            return { ...item, meta_data: { ...item.meta_data } }
          }
          return item
        })
      } else if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioPollingRef.current.has(msg.meta_data.audio_url)) {
        const pendingIntervention = msg.id ? response?.pending_intervention?.[msg.id] || {} : {}
        msg.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
          ...intervention,
          execution_id: pendingIntervention.execution_id,
        }))
        return { ...msg, meta_data: { ...msg.meta_data } }
      } else if (msg.role === 'assistant') {
        const pendingIntervention = msg.id ? response?.pending_intervention?.[msg.id] || {} : {}
        msg.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
          ...intervention,
          execution_id: pendingIntervention.execution_id,
        }))
        return { ...msg, meta_data: { ...msg.meta_data } }
      }
      msg.status = msg.role === 'user' ? undefined : msg.status
      return msg
    }).map(enrichTimelineEntry))
  }

  /** Merge earlier messages into the head of the list */
  const applyEarlierMessages = (response: { messages: ChatItem[]; pending_intervention?: Record<string, any> }) => {
    const messages = response?.messages || []
    const historyAudioUrls = new Set(messages.map(m => m.meta_data?.audio_url).filter(Boolean))
    audioPollingRef.current.forEach((timer, key) => {
      if (!historyAudioUrls.has(key)) {
        clearInterval(timer)
        audioPollingRef.current.delete(key)
      }
    })
    messages.forEach(msg => {
      if (Array.isArray(msg)
        ? msg.some(item => item.is_current && item.role === 'assistant' && item.meta_data?.audio_url && item.meta_data?.audio_status === 'pending')
        : msg.role === 'assistant' && msg.meta_data?.audio_url && msg.meta_data?.audio_status === 'pending'
      ) {
        const audio_url = Array.isArray(msg) ? msg.find(item => item.is_current)?.meta_data?.audio_url : msg.meta_data?.audio_url
        startAudioPolling(audio_url, audio_url)
      }
    })
    const processedMessages = messages.map(msg => {
      if (Array.isArray(msg)) {
        return msg.map(item => {
          if (item.role === 'assistant' && item.meta_data?.audio_url && audioPollingRef.current.has(item.meta_data.audio_url)) {
            const pendingIntervention = item.id ? response?.pending_intervention?.[item.id] || {} : {}
            item.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
              ...intervention,
              execution_id: pendingIntervention.execution_id,
            }))
            return { ...item, meta_data: { ...item.meta_data } }
          }
          return item
        })
      } else if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioPollingRef.current.has(msg.meta_data.audio_url)) {
        const pendingIntervention = msg.id ? response?.pending_intervention?.[msg.id] || {} : {}
        msg.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
          ...intervention,
          execution_id: pendingIntervention.execution_id,
        }))
        return { ...msg, meta_data: { ...msg.meta_data } }
      } else if (msg.role === 'assistant') {
        const pendingIntervention = msg.id ? response?.pending_intervention?.[msg.id] || {} : {}
        msg.interventions = (pendingIntervention.interventions || []).map((intervention: Intervention) => ({
          ...intervention,
          execution_id: pendingIntervention.execution_id,
        }))
        return { ...msg, meta_data: { ...msg.meta_data } }
      }
      msg.status = msg.role === 'user' ? undefined : msg.status
      return msg
    }).map(enrichTimelineEntry)
    setChatList(prev => {
      // Keep opening statement messages at the beginning
      const openingMessages = prev.filter(item => {
        const msg = Array.isArray(item) ? item.find(i => i.is_current) : item
        return msg?.role === 'system' && msg?.content?.includes('opening')
      })
      const currentMessages = prev.filter(item => {
        const msg = Array.isArray(item) ? item.find(i => i.is_current) : item
        return !(msg?.role === 'system' && msg?.content?.includes('opening'))
      })
      return [...openingMessages, ...processedMessages, ...currentMessages]
    })
  }

  return {
    chatList,
    setChatList,
    audioPollingRef,
    streamLoadingRef,
    startAudioPolling,
    addUserMessage,
    addAssistantMessage,
    updateAssistantMessage,
    updateAssistantReasoningMessage,
    updateAssistantMemoryRetrieval,
    applyChatDetail,
    applyEarlierMessages,
  }
}
