/**
 * useConversation
 * 会话页面的核心状态与逻辑：历史记录、配置加载、消息发送/重新生成、人工干预、反馈、收藏、删除等。
 * 所有接口均使用分享态 token（shareToken）调用。
 */
import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { App } from 'antd'

import {
  getConversationHistory, sendConversation, getConversationDetail, getShareToken, getExperienceConfig,
  feedbackMessage, deleteConversationMessage, regenerateMessage, switchMessageVersion, accessShareConversation,
  interventionsSubmit, interventionsResumeSubmit, favoriteMessage,
} from '@/api/application'
import { formatDateTime } from '@/utils/format'
import { randomString, updateMetaIcon } from '@/utils/common'
import type { ChatItem } from '@/components/Chat/types'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { Variable as AppVariable } from '@/views/ApplicationConfig/components/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import { replaceVariables, buildOpeningStatementMessage } from '@/components/Chat/openingStatement'

import type { HistoryItem, ShareModalRef, ReportModalRef } from '../types'
import { useChatMessages } from './useChatMessages'
import { createSendStreamHandler, createRegenerateStreamHandler } from '../utils/streamHandlers'
import { applyInterventionSubmit, createResumeStreamHandler } from '../utils/interventionHandlers'
import { applyMessagePatchById } from '../utils/messageMutations'

export function useConversation() {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const { token, shareUuid } = useParams()
  const location = useLocation()
  const searchParams = new URLSearchParams(location.search)
  const userId = searchParams.get('user_id')
  const windowType = searchParams.get('type')
  const isFloatBtn = windowType === 'floatBtn'

  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState<string>('')
  const [conversation_id, setConversationId] = useState<string | null>(null)
  const [historyList, setHistoryList] = useState<HistoryItem[]>([])
  const [groupHistoryList, setGroupHistoryList] = useState<Record<string, HistoryItem[]>>({})
  const [pageLoading, setPageLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const toolbarRef = useRef<ChatToolbarRef | null>(null)
  // Track toolbar mount so config variables can be (re)applied once its ref is
  // attached — getExperienceConfig often resolves before the toolbar mounts.
  const [toolbarReady, setToolbarReady] = useState(false)
  const toolbarCallbackRef = useCallback((node: ChatToolbarRef | null) => {
    toolbarRef.current = node
    setToolbarReady(!!node)
  }, [])
  const abortRef = useRef<(() => void) | null>(null)
  const [shareToken, setShareToken] = useState<string | null>(localStorage.getItem(`shareToken_${token}`))
  const [fileList, setFileList] = useState<any[]>([])
  const [webSearch, setWebSearch] = useState(false)
  const [isHasMemory, setIsHasMemory] = useState(false)
  const [memory, setMemory] = useState(true)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)
  const [config, setConfig] = useState<Record<string, any>>({})
  const [isDeepThinking, setIsDeepThinking] = useState<Record<string, any>>({})
  const [thinking, setThinking] = useState(false)
  const [isIframe, setIsIframe] = useState(false)
  const [isSmallScreen, setIsSmallScreen] = useState(false)
  const [isShare, setIsShare] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [configLoading, setConfigLoading] = useState(true)
  const chatIsEnded = useRef(true)
  const shareModalRef = useRef<ShareModalRef>(null)
  const reportModalRef = useRef<ReportModalRef>(null)

  const {
    chatList,
    setChatList,
    streamLoadingRef,
    startAudioPolling,
    addUserMessage,
    addAssistantMessage,
    updateAssistantMessage,
    updateAssistantReasoningMessage,
    applyChatDetail,
  } = useChatMessages()

  useEffect(() => {
    setIsIframe(location.pathname.includes('/chat-box/'))
  }, [location?.pathname])

  useEffect(() => {
    const handleResize = () => {
      setIsSmallScreen(window.innerWidth < 1080)
    }
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => {
      window.removeEventListener('resize', handleResize)
      abortRef.current?.()
      abortRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!shareUuid) return
    setIsShare(true)
    accessShareConversation(shareUuid)
      .then(res => {
        const response = res as { messages: ChatItem[] } || {}
        setChatList(response.messages ?? [])
      })
  }, [shareUuid])

  useEffect(() => {
    if (!token) return
    const localShareToken = localStorage.getItem(`shareToken_${token}`)
    setShareToken(localShareToken)
    if (localShareToken && localShareToken !== '') return
    getShareToken(token as string, userId || randomString(12, false))
      .then(res => {
        const response = res as { access_token: string } || {}
        localStorage.setItem(`shareToken_${token}`, response.access_token ?? '')
        setShareToken(response.access_token ?? '')
      })
  }, [token])

  useEffect(() => {
    if (page === 1 && hasMore && historyList.length === 0 && shareToken) {
      getHistory()
    }
  }, [shareToken, page, hasMore, historyList])

  useEffect(() => {
    if (shareToken && shareToken !== '') {
      setConfigLoading(true)
      getExperienceConfig(shareToken)
        .then(res => {
          const response = res as {
            variables: Variable[];
            features: FeaturesConfigForm;
            app_name?: string;
            model_parameters?: Record<string, any>;
            app_type: string;
            memory: boolean;
            app_icon?: string;
          }
          toolbarRef.current?.setVariables(response.variables || [])
          setConfig(response)
          setFeatures(response.features)
          setIsHasMemory((response.app_type === 'workflow' && response.memory) || response.memory)
          setIsDeepThinking(response.model_parameters?.deep_thinking || false)

          if (response.app_icon && !isFloatBtn && !isIframe) {
            updateMetaIcon(response.app_icon)
          }
          document.title = response.app_name || t('memoryConversation.chatTitle')
        })
        .finally(() => {
          setConfigLoading(false)
        })
    } else {
      setChatList([])
    }
  }, [shareToken])

  // Re-apply config variables whenever the toolbar becomes ready. Without this the
  // imperative setVariables in getExperienceConfig is silently dropped when the
  // toolbar ref is still null, leaving the variable-config entry permanently hidden.
  useEffect(() => {
    if (!toolbarReady) return
    const configVariables = (config?.variables as Variable[] | undefined) || []
    if (configVariables.length > 0) {
      toolbarRef.current?.setVariables(configVariables)
    }
  }, [toolbarReady, config])

  /** 按日期对会话历史分组 */
  const groupHistoryByDate = (items: HistoryItem[]): Record<string, HistoryItem[]> => {
    return items.reduce((groups: Record<string, HistoryItem[]>, item) => {
      const date = formatDateTime(item.created_at, 'YYYY-MM-DD')
      if (!groups[date]) {
        groups[date] = []
      }
      groups[date].push(item)
      return groups
    }, {})
  }

  /** 分页拉取会话历史 */
  const [disabled, setDisabled] = useState(false)
  const getHistory = (flag: boolean = false) => {
    if (!shareToken || shareToken === '' || (pageLoading || !hasMore) && !flag) return
    setPageLoading(true)
    getConversationHistory(shareToken, { page: flag ? 1 : page, pagesize: 20 })
      .then(res => {
        const response = res as { items: HistoryItem[], page: { hasnext: boolean; page: number; pagesize: number; total: number } }
        const results = response?.items || []
        let list = []
        if (flag) {
          setHistoryList(results)
          list = [...results]
        } else {
          setHistoryList(historyList.concat(results))
          list = [...historyList, ...results]
        }
        setHistoryList(list)
        setGroupHistoryList(groupHistoryByDate(list))
        if (page === 1 && !flag) {
          setConversationId(list[0]?.id || '')
        }
        setPage(response.page.page + 1)
        setHasMore(response.page.hasnext)
        setLoading(false)
      })
      .catch(err => {
        setDisabled(err?.response?.data?.error_code === 'QUOTA_EXCEEDED')
      })
      .finally(() => setPageLoading(false))
  }

  /** 切换会话或开启新会话 */
  const handleChangeHistory = (id: string | null) => {
    if (disabled && id === null) return
    if (id !== conversation_id) setConversationId(id)
    if (!id) setMessage('')
    abortRef.current?.()
    abortRef.current = null
  }

  const getChatDetail = () => {
    if (!conversation_id || !shareToken || shareToken === '') return
    getConversationDetail(shareToken, conversation_id)
      .then(res => {
        applyChatDetail(res as { messages: ChatItem[]; pending_intervention: Record<string, any> })
      })
  }

  useEffect(() => {
    if (conversation_id) {
      getChatDetail()
    } else {
      const variables = toolbarRef.current?.getVariables() || []
      const openingMsg = buildOpeningStatementMessage(features?.opening_statement, {
        variables,
        withTimestamp: true,
        extra: { is_hidden_refresh: true },
      })
      setChatList(openingMsg ? [openingMsg] : [])
    }
  }, [conversation_id, features?.opening_statement?.statement])

  /** 校验工具栏变量，返回是否可发送及参数 */
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

  /** 发送消息并处理流式响应 */
  const handleSend = (msg?: string) => {
    if (!shareToken || shareToken === '') return
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const { isCanSend, params } = validateVariables()
    if (!isCanSend) return

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
      getHistory,
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

  /** 人工干预动作点击：流式进行中直接提交，否则发起恢复执行 */
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
        getHistory,
        streamLoadingRef,
      })
      interventionsResumeSubmit(shareToken, execution_id, data, handleStreamMessage)
    }
  }

  /** 重新生成指定助手消息 */
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
      getHistory,
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

  /** 切换到指定版本的消息（本地乐观更新） */
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

  const chatTitle = useMemo(() => {
    const conversation = historyList.find(item => item.id === conversation_id)
    return conversation?.title
  }, [conversation_id, historyList])

  return {
    t,
    token,
    loading,
    conversation_id,
    historyList,
    groupHistoryList,
    chatList,
    hasMore,
    scrollRef,
    toolbarRef,
    toolbarCallbackRef,
    shareToken,
    fileList,
    setFileList,
    webSearch,
    setWebSearch,
    isHasMemory,
    memory,
    features,
    config,
    isDeepThinking,
    thinking,
    isIframe,
    isSmallScreen,
    isShare,
    showHistory,
    setShowHistory,
    isFloatBtn,
    chatTitle,
    configLoading,
    streamLoadingRef,
    chatIsEnded,
    shareModalRef,
    reportModalRef,
    setMessage,
    getHistory,
    handleChangeHistory,
    handleSend,
    handleInterventionActionClick,
    regenerateMessages,
    handleChangeMemory,
    handleChangeDeepThinking,
    handleChangeVariables,
    deleteMessage,
    reportMsg,
    handleVersionChange,
    handleShare,
    handleFeedback,
    handleFavorite,
    disabled,
  }
}

export type ConversationCtx = ReturnType<typeof useConversation>
