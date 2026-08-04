/**
 * Human-in-the-loop intervention handlers.
 * Includes a pure function that locally marks an intervention as resolved after submit,
 * and a factory for SSE stream handling on "resume execution".
 * Logic matches the original handleInterventionActionClick in index.tsx, extracted as a module.
 */
import { type ButtonProps } from 'antd'

import { type SSEMessage } from '@/utils/stream'
import type { ChatItem } from '@/components/Chat/types'

/** After intervention submit succeeds (while streaming), locally mark the matching intervention as resolved */
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
  /** Action id that triggers resume execution */
  actionId: string
  /** Submitted form data */
  fieldValues: Record<string, string>
  /** Node id that triggers resume execution */
  node_id: string
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
  startAudioPolling: (audioUrl: string, idToPoll: string) => void
  /** Locally update the history list after streaming ends (insert new / refresh updated_at for existing) */
  upsertHistory: (conversationId: string, title?: string) => void
  streamLoadingRef: React.MutableRefObject<boolean>
}

/** Stream handler for the resume-submit scenario */
export const createResumeStreamHandler = (deps: ResumeHandlerDeps) => {
  const {
    actionId, fieldValues, node_id,
    conversationId, setChatList, setConversationId, setLoading,
    updateAssistantMessage, updateAssistantReasoningMessage, startAudioPolling,
    upsertHistory, streamLoadingRef,
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

              // Find the last intervention and update its form_fields default_value
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
        case 'workflow_end': {
          if (audio_url) {
            updateAssistantMessage(content, audio_url, 'pending', citations, suggested_questions, error)
            const { file_id } = item.data as { file_id?: string }
            const idToPoll = file_id || audio_url || ''
            const fileId = audio_url.split('/').pop()
            if (fileId && idToPoll) {
              startAudioPolling(audio_url, idToPoll)
            }
          }
          if ((citations && citations.length > 0) || (suggested_questions && suggested_questions.length > 0) || error) {
            updateAssistantMessage(content || '', audio_url, undefined, citations, suggested_questions, error)
          }
          setLoading(false)
          const targetConvId = currentConversationId || conversationId
          if (targetConvId) {
            upsertHistory(targetConvId)
          }
          if (currentConversationId && currentConversationId !== conversationId) {
            setConversationId(currentConversationId)
          }
          break
        }
      }
    })
  }
}
