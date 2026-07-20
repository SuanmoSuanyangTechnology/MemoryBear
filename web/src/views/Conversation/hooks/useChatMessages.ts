/**
 * useChatMessages
 * 管理会话消息列表的增删改、助手语音文件轮询，以及会话详情的归一化写入。
 * 逻辑与原 index.tsx 保持一致，仅做模块化抽离。
 */
import { useState, useRef, useEffect } from 'react'

import type { ChatItem, Intervention } from '@/components/Chat/types'
import { getFileStatusById } from '@/api/fileStorage'

export function useChatMessages() {
  const [chatList, setChatList] = useState<Array<ChatItem | ChatItem[]>>([])
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})
  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const streamLoadingRef = useRef(false)

  /** 组件卸载时清理所有轮询定时器 */
  useEffect(() => {
    return () => {
      audioPollingRef.current.forEach((timer) => clearInterval(timer))
      audioPollingRef.current.clear()
    }
  }, [])

  /** 根据轮询得到的状态同步更新消息的 audio_status */
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

  /** 轮询语音文件转换状态 */
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

  /** 追加一条用户消息 */
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

  /** 追加一条助手消息（message_id 存在时表示为指定消息追加新版本） */
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

  /** 流式更新助手消息正文/语音/引用/建议问题/错误（replace 为 true 时整体替换正文） */
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

  /** 流式更新助手消息的思考内容 */
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

  /** 写入会话详情：归一化消息、挂载待处理人工干预、并按需重启语音轮询 */
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
    }))
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
    applyChatDetail,
  }
}
