/**
 * 人工干预（human-in-the-loop）相关处理。
 * 包含「干预提交后本地标记已解决」的纯函数，以及「恢复执行」的 SSE 流式处理工厂。
 * 逻辑与原 index.tsx 中的 handleInterventionActionClick 完全一致，仅抽离为模块。
 */
import { type ButtonProps } from 'antd'

import { type SSEMessage } from '@/utils/stream'
import type { ChatItem } from '@/components/Chat/types'

/** 干预提交成功后（流式进行中）本地把对应干预标记为已解决 */
export const applyInterventionSubmit = (
  prev: Array<ChatItem | ChatItem[]>,
  node_id: string,
  actionId: string,
  fieldValues: Record<string, string>,
): Array<ChatItem | ChatItem[]> => {
  const lastList = [...prev]
  const lastIndex = lastList.length - 1
  const lastMsg = lastList[lastIndex]
  if (Array.isArray(lastMsg)) {
    const lastChatIndex = lastMsg.length - 1
    const lastAssistantMsg = lastMsg[lastChatIndex] as ChatItem
    if (lastAssistantMsg?.role === 'assistant') {
      if (!lastAssistantMsg?.interventions || lastAssistantMsg.interventions.length === 0) {
        return prev
      }

      const filterIndex = lastAssistantMsg.interventions.findIndex(item => item.node_id === node_id)
      lastAssistantMsg.interventions[filterIndex] = {
        ...lastAssistantMsg.interventions[filterIndex],
        resolved_form_data: fieldValues,
        resolved_action_id: actionId,
      }

      return [
        ...prev.slice(0, -1),
        {
          ...lastMsg,
        }
      ]
    }
    return prev
  } else {
    if (!lastMsg?.interventions || lastMsg.interventions.length === 0) {
      return prev
    }

    const filterIndex = lastMsg.interventions.findIndex(item => item.node_id === node_id)
    lastMsg.interventions[filterIndex] = {
      ...lastMsg.interventions[filterIndex],
      resolved_action_id: actionId,
    }

    return [
      ...prev.slice(0, -1),
      {
        ...lastMsg,
      }
    ]
  }
}

export interface ResumeHandlerDeps {
  /** 触发恢复执行的动作 id */
  actionId: string
  /** 提交的表单数据 */
  fieldValues: Record<string, string>
  /** 触发恢复执行的节点 id */
  node_id: string
  /** 当前会话 id */
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
  startAudioPolling: (audioUrl: string, idToPoll: string) => void
  getHistory: (flag?: boolean) => void
  streamLoadingRef: React.MutableRefObject<boolean>
}

/** 恢复执行（resume-submit）场景的流式处理器 */
export const createResumeStreamHandler = (deps: ResumeHandlerDeps) => {
  const {
    actionId, fieldValues, node_id,
    conversationId, setChatList, setConversationId, setLoading,
    updateAssistantMessage, updateAssistantReasoningMessage, startAudioPolling,
    getHistory, streamLoadingRef,
  } = deps

  let currentConversationId: string | null = null

  return (data: SSEMessage[]) => {
    data.forEach((item) => {
      const {
        message_id,
        execution_id,
        node_id: nodeId,
        node_name,
        content, conversation_id: curId, audio_url, citations, suggested_questions, error,
        rendered_content, form_fields, actions, timeout_at
      } = item.data as {
        message_id: string;
        execution_id: string;
        node_id: string;
        node_name: string;
        content: string; conversation_id: string; audio_url?: string;
        citations?: {
          document_id: string;
          file_name: string;
          knowledge_id: string;
          score: string;
        }[];
        error?: string;
        suggested_questions?: string[];
        rendered_content?: string;
        form_fields?: {
          id: string;
          default_value?: string;
        }[]
        actions?: {
          id: string;
          label: string;
          variant: ButtonProps['type'];
        }[];
        timeout_at?: number;
      }
      switch (item.event) {
        case 'start':
          setChatList(prev => {
            const lastList = [...prev]
            const lastIndex = lastList.length - 1
            const lastMsg = lastList[lastIndex]
            if (Array.isArray(lastMsg)) {
              const lastChatIndex = lastMsg.length - 1
              const lastAssistantMsg = lastMsg[lastChatIndex] as ChatItem
              if (lastAssistantMsg?.role === 'assistant') {
                if (!lastAssistantMsg?.interventions || lastAssistantMsg.interventions.length === 0) {
                  return prev
                }

                const filterIndex = lastAssistantMsg.interventions.findIndex(it => it.node_id === nodeId)
                lastAssistantMsg.interventions[filterIndex] = {
                  ...lastAssistantMsg.interventions[filterIndex],
                  resolved_action_id: actionId,
                }

                return [
                  ...prev.slice(0, -1),
                  {
                    ...lastMsg,
                  }
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
                resolved_form_data: fieldValues,
                resolved_action_id: actionId,
              }

              return [
                ...prev.slice(0, -1),
                {
                  ...lastMsg,
                }
              ]
            }
          })
          break
        case 'reasoning':
          updateAssistantReasoningMessage(content)
          if (curId) currentConversationId = curId;
          break
        case 'message':
          setChatList(prev => {
            const lastList = [...prev]
            const lastIndex = lastList.length - 1
            let lastMsg = lastList[lastIndex]

            if (Array.isArray(lastMsg)) {
              const lastChatIndex = lastMsg.length - 1
              const lastAssistantMsg = lastMsg[lastChatIndex] as ChatItem

              if (!lastAssistantMsg?.interventions || lastAssistantMsg.interventions.length === 0) {
                return prev
              }
              const updatedInterventions = [
                ...lastAssistantMsg.interventions.slice(0, -1),
                {
                  ...lastAssistantMsg.interventions[lastAssistantMsg.interventions.length - 1],
                  resolved_form_data: fieldValues,
                  resolved_action_id: actionId,
                }
              ]
              lastMsg = [
                ...lastMsg.slice(0, -1),
                {
                  ...lastAssistantMsg,
                  interventions: updatedInterventions,
                  meta_data: {
                    ...lastAssistantMsg.meta_data,
                    waiting_human: false
                  }
                }
              ]

              return [...lastList]
            } else {
              if (!lastMsg?.interventions || lastMsg.interventions.length === 0) {
                return prev
              }

              // 找到最后一条 intervention 并更新其 form_fields 的 default_value
              const updatedInterventions = [
                ...lastMsg.interventions.slice(0, -1),
                {
                  ...lastMsg.interventions[lastMsg.interventions.length - 1],
                  resolved_form_data: fieldValues,
                  resolved_action_id: actionId,
                }
              ]

              return [
                ...prev.slice(0, -1),
                {
                  ...lastMsg,
                  interventions: updatedInterventions,
                  meta_data: {
                    ...lastMsg.meta_data,
                    waiting_human: false
                  }
                }
              ]
            }
          })
          updateAssistantMessage(content, audio_url, audio_url ? 'pending' : undefined)
          if (curId) currentConversationId = curId;
          break
        case 'intervention_required':
          if (streamLoadingRef.current) streamLoadingRef.current = false
          setChatList(prev => {
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
            }
            return prev
          })
          break;
        case 'intervention_timeout':
          setChatList(prev => {
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
          })
          break
        case 'end':
        case 'workflow_end':
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, error)
            const { file_id } = item.data as { file_id?: string }
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          } else {
            getHistory(true)
            if (currentConversationId && currentConversationId !== conversationId) {
              setConversationId(currentConversationId)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || error) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, error)
          }
          setLoading(false)
          getHistory(true)
          if (currentConversationId && currentConversationId !== conversationId) {
            setConversationId(currentConversationId)
          }
          break
      }
    })
  }
}
