/**
 * Conversation action factories.
 * All user-action handlers (send, regenerate, intervention, feedback, favorite,
 * delete, version switch, memory/thinking toggle, etc.) are extracted here as a
 * single factory that receives the shared state from useConversation as deps.
 * Pattern matches streamHandlers.ts / interventionHandlers.ts.
 */
import { App } from 'antd'

import {
  sendConversation, regenerateMessage, feedbackMessage, deleteConversationMessage,
  switchMessageVersion, interventionsSubmit, interventionsResumeSubmit, favoriteMessage,
} from '@/api/application'
import { replaceVariables, buildOpeningStatementMessage } from '@/components/Chat/openingStatement'
import type { ChatItem } from '@/components/Chat/types'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { Variable as AppVariable } from '@/views/ApplicationConfig/components/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import type { TFunction } from 'i18next'

import type { ShareModalRef, ReportModalRef } from '../types'
import { createSendStreamHandler, createRegenerateStreamHandler } from './streamHandlers'
import { applyInterventionSubmit, createResumeStreamHandler } from './interventionHandlers'
import { applyMessagePatchById } from './messageMutations'

export interface ConversationActionDeps {
  t: TFunction
  messageApi: ReturnType<typeof App.useApp>['message']
  modal: ReturnType<typeof App.useApp>['modal']
  shareToken: string | null
  conversation_id: string | null
  message: string
  webSearch: boolean
  memory: boolean
  thinking: boolean
  features: FeaturesConfigForm
  config: Record<string, any>
  loading: boolean
  disabled: boolean
  setConversationId: (id: string | null) => void
  setLoading: (value: boolean) => void
  setMemory: (value: boolean) => void
  setThinking: React.Dispatch<React.SetStateAction<boolean>>
  setFileList: React.Dispatch<React.SetStateAction<any[]>>
  setMessage: React.Dispatch<React.SetStateAction<string>>
  setChatList: React.Dispatch<React.SetStateAction<Array<ChatItem | ChatItem[]>>>
  chatIsEnded: React.MutableRefObject<boolean>
  streamLoadingRef: React.MutableRefObject<boolean>
  toolbarRef: React.MutableRefObject<ChatToolbarRef | null>
  abortRef: React.MutableRefObject<(() => void) | null>
  skipChatDetailRef: React.MutableRefObject<boolean>
  shareModalRef: React.RefObject<ShareModalRef>
  reportModalRef: React.RefObject<ReportModalRef>
  addUserMessage: (conversation_id: string | null, message: string, files: any[]) => void
  addAssistantMessage: (messageId?: string) => void
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
  upsertHistory: (conversationId: string, title?: string) => void
  getHistory: (flag?: boolean) => void
  getChatDetail: () => void
}

export function createConversationActions(deps: ConversationActionDeps) {
  const {
    t, messageApi, modal, shareToken, conversation_id, message, webSearch, memory, thinking,
    features, config, loading, disabled, setConversationId, setLoading, setMemory, setThinking,
    setFileList, setMessage, setChatList, chatIsEnded, streamLoadingRef, toolbarRef, abortRef,
    skipChatDetailRef, shareModalRef, reportModalRef, addUserMessage, addAssistantMessage,
    updateAssistantMessage, updateAssistantReasoningMessage, startAudioPolling, upsertHistory,
    getHistory, getChatDetail,
  } = deps

  /** Validate toolbar variables; returns whether sending is allowed and the params */
  const validateVariables = () => {
    const variables = toolbarRef.current?.getVariables() || []
    let isCanSend = true
    const params: Record<string, any> = {}
    if (variables.length > 0) {
      const needRequired: string[] = []
      variables.forEach(vo => {
        params[vo.name] = vo.value ?? vo.defaultValue
        if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
          isCanSend = false
          needRequired.push(vo.name)
        }
      })
      if (needRequired.length) {
        messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
      }
    }
    return { isCanSend, params }
  }

  /** Send a message and handle the streaming response */
  const handleSend = (msg?: string) => {
    if (!shareToken || shareToken === '') return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const { isCanSend, params } = validateVariables()
    if (!isCanSend) return

    // New conversation streaming: skip the duplicate getChatDetail call in useEffect
    if (!conversation_id) {
      skipChatDetailRef.current = true
    }

    setLoading(true)
    chatIsEnded.current = false
    streamLoadingRef.current = true
    addUserMessage(conversation_id, msg || message, files)
    addAssistantMessage()
    toolbarRef.current?.setFiles([])
    setFileList([])

    const handleStreamMessage = createSendStreamHandler({
      conversationId: conversation_id,
      setChatList,
      setConversationId,
      setLoading,
      updateAssistantMessage,
      updateAssistantReasoningMessage,
      startAudioPolling,
      upsertHistory,
      title: msg || message,
      chatIsEnded,
      streamLoadingRef,
    })

    sendConversation({
      web_search: webSearch,
      memory,
      message: msg || message || '',
      stream: true,
      conversation_id: conversation_id || null,
      files: files.map(file => {
        if (file.url) {
          return file
        } else {
          return {
            type: file.type,
            transfer_method: 'local_file',
            upload_file_id: file.response.data.file_id,
            file_type: file.response.data.file_type,
            size: file.response.data.file_size,
            name: file.response.data.file_name
          }
        }
      }),
      variables: params,
      thinking,
    }, handleStreamMessage, shareToken, (abort) => { abortRef.current = abort })
      .catch(() => {
        setLoading(false)
        streamLoadingRef.current = false
        chatIsEnded.current = true
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        chatIsEnded.current = true
      })
  }

  /** Human intervention action click: submit directly if streaming, otherwise resume execution */
  const handleInterventionActionClick = (actionId: string, fieldValues: Record<string, string>, execution_id?: string, node_id?: string) => {
    if (!execution_id || !node_id || !shareToken) {
      return
    }
    const data = {
      node_id,
      action_id: actionId,
      form_data: fieldValues,
    }
    if (loading) {
      interventionsSubmit(shareToken, execution_id, data)
        .then(() => {
          setChatList(prev => applyInterventionSubmit(prev, node_id, actionId, fieldValues))
        })
    } else {
      const handleStreamMessage = createResumeStreamHandler({
        actionId,
        fieldValues,
        node_id,
        conversationId: conversation_id,
        setChatList,
        setConversationId,
        setLoading,
        updateAssistantMessage,
        updateAssistantReasoningMessage,
        startAudioPolling,
        upsertHistory,
        streamLoadingRef,
      })
      interventionsResumeSubmit(shareToken, execution_id, data, handleStreamMessage)
    }
  }

  /** Regenerate the specified assistant message */
  const regenerateMessages = (vo: ChatItem) => {
    if (!shareToken || shareToken === '' || !vo.id) return
    const { isCanSend, params } = validateVariables()
    if (!isCanSend) return

    setLoading(true)
    chatIsEnded.current = false
    streamLoadingRef.current = true
    addAssistantMessage(vo.id)

    const handleStreamMessage = createRegenerateStreamHandler({
      messageId: vo.id,
      conversationId: conversation_id,
      setChatList,
      setConversationId,
      setLoading,
      updateAssistantMessage,
      updateAssistantReasoningMessage,
      startAudioPolling,
      upsertHistory,
      chatIsEnded,
      streamLoadingRef,
    })

    regenerateMessage(vo.id as string, {
      web_search: webSearch,
      memory,
      stream: true,
      variables: params,
      thinking,
    }, handleStreamMessage, shareToken, (abort) => { abortRef.current = abort })
      .catch(() => {
        setLoading(false)
        streamLoadingRef.current = false
        chatIsEnded.current = true
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        chatIsEnded.current = true
      })
  }

  /** Switch to a conversation or start a new one */
  const handleChangeHistory = (id: string | null) => {
    if (disabled && id === null) return
    // Manual conversation switch: ensure getChatDetail runs normally
    skipChatDetailRef.current = false
    if (id !== conversation_id) setConversationId(id)
    if (abortRef.current) {
      getHistory(true)
    }
    abortRef.current?.()
    abortRef.current = null
    if (!id) {
      setMessage('')
      // During new-conversation streaming, conversation_id is still null, so setConversationId
      // won't trigger useEffect — manually reset the chat list and streaming state
      const variables = toolbarRef.current?.getVariables() || []
      const openingMsg = buildOpeningStatementMessage(features?.opening_statement, {
        variables,
        withTimestamp: true,
        extra: { is_hidden_refresh: true },
      })
      setChatList(openingMsg ? [openingMsg] : [])
    }
  }

  const handleChangeMemory = () => {
    if (config.app_type === 'workflow') return
    const value = !memory
    modal.confirm({
      title: value ? t('memoryConversation.memoryTipTitle') : t('memoryConversation.memoryCancelTipTitle'),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => {
        setMemory(value)
      },
      onCancel: () => {
        setMemory(!value)
      }
    })
  }

  const handleChangeDeepThinking = () => {
    setThinking(prev => !prev)
  }

  const handleChangeVariables = (variables: Variable[]) => {
    setChatList(prev => {
      const firstMsg = prev[0] as ChatItem
      if (firstMsg && firstMsg.role === 'assistant' && firstMsg.content && features?.opening_statement?.enabled && features?.opening_statement.statement && variables.length > 0) {
        firstMsg.content = replaceVariables(features?.opening_statement.statement, variables as unknown as AppVariable[])
        return [firstMsg, ...prev.slice(1)]
      }
      return prev
    })
  }

  const deleteMessage = (vo: ChatItem) => {
    if (!shareToken || shareToken === '' || !vo.id) return
    modal.confirm({
      title: t('common.confirmDelete'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteConversationMessage(shareToken, vo.id as string)
          .then(() => {
            getChatDetail()
            messageApi.success(t('common.deleteSuccess'))
          })
      }
    })
  }

  const reportMsg = (vo: ChatItem) => {
    reportModalRef.current?.handleOpen(vo)
  }

  /** Switch to the specified message version (local optimistic update) */
  const handleVersionChange = (page: number, item: ChatItem) => {
    if (!shareToken || shareToken === '' || !item.id) return
    switchMessageVersion(shareToken, item.id, page)
      .then(() => {
        setChatList(prev => {
          const lastList = [...prev]
          const filterIndex = lastList.findIndex(vo => Array.isArray(vo) && vo.filter(msg => msg.id === item.id).length > 0)

          if (filterIndex < 0) return lastList

          const currentItem: ChatItem[] = lastList[filterIndex] as ChatItem[]
          lastList[filterIndex] = [...currentItem.map(msg => {
            return {
              ...msg,
              is_current: msg.id === item.id,
            }
          })]

          return [...lastList]
        })
        messageApi.success(t('common.operateSuccess'))
      })
  }

  const handleShare = () => {
    if (!conversation_id) return
    shareModalRef.current?.handleOpen()
  }

  const handleFeedback = (feedbackType: 'like' | 'dislike', id?: string) => {
    if (!shareToken || shareToken === '' || !conversation_id || !id) return
    feedbackMessage(shareToken, id, { feedback_type: feedbackType })
      .then((res) => {
        const { feedback_type } = res as { feedback_type: 'like' | 'dislike' | null; }
        messageApi.success(feedback_type === 'dislike'
          ? t('memoryConversation.dislikeMsg')
          : feedback_type === 'like'
            ? t('memoryConversation.likeMsg')
            : t('memoryConversation.cancelMsg')
        )
        setChatList(prev => applyMessagePatchById(prev, id, { feedback_type }))
      })
  }

  const handleFavorite = (id?: string) => {
    if (!shareToken || shareToken === '' || !conversation_id || !id) return
    favoriteMessage(shareToken, id)
      .then((res) => {
        const { is_favorited } = res as { is_favorited: boolean; }
        messageApi.success(t('common.operateSuccess'))
        setChatList(prev => applyMessagePatchById(prev, id, { is_favorited }))
      })
  }

  return {
    validateVariables,
    handleSend,
    handleInterventionActionClick,
    regenerateMessages,
    handleChangeHistory,
    handleChangeMemory,
    handleChangeDeepThinking,
    handleChangeVariables,
    deleteMessage,
    reportMsg,
    handleVersionChange,
    handleShare,
    handleFeedback,
    handleFavorite,
  }
}
