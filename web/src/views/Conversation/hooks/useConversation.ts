/**
 * useConversation
 * Core state and logic for the conversation page: history, config loading,
 * message send/regenerate, human-in-the-loop interventions, feedback, favorite, delete, etc.
 * All API calls use the share-mode token (shareToken).
 *
 * Action handlers are extracted to utils/conversationActions.ts;
 * SSE stream handlers live in utils/streamHandlers.ts and utils/interventionHandlers.ts.
 */
import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useParams, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { App } from 'antd'

import {
  getConversationHistory, getConversationDetail, getShareToken, getExperienceConfig,
  accessShareConversation,
} from '@/api/application'
import { formatDateTime } from '@/utils/format'
import { randomString, updateMetaIcon } from '@/utils/common'
import type { ChatItem } from '@/components/Chat/types'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import { buildOpeningStatementMessage } from '@/components/Chat/openingStatement'

import type { HistoryItem, ShareModalRef, ReportModalRef } from '../types'
import { useChatMessages } from './useChatMessages'
import { createConversationActions } from '../utils/conversationActions'
/** Group conversation history by date (pure function) */
const groupHistoryByDate = (items: HistoryItem[]): Record<string, HistoryItem[]> => {
  return items.reduce((groups: Record<string, HistoryItem[]>, item) => {
    const date = formatDateTime(item.updated_at, 'YYYY-MM-DD')
    if (!groups[date]) {
      groups[date] = []
    }
    groups[date].push(item)
    return groups
  }, {})
}

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
  const [showMemoryRecall, setShowMemoryRecall] = useState(true)
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
  /** Set to true when a new conversation is created via streaming; skips getChatDetail in useEffect */
  const skipChatDetailRef = useRef(false)
  const [disabled, setDisabled] = useState(false)

  const {
    chatList,
    setChatList,
    streamLoadingRef,
    startAudioPolling,
    addUserMessage,
    addAssistantMessage,
    updateAssistantMessage,
    updateAssistantReasoningMessage,
    updateAssistantMemoryRetrieval,
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

  /** Grouped by date (derived from historyList to avoid keeping two states in sync) */
  const groupHistoryList = useMemo(() => groupHistoryByDate(historyList), [historyList])

  /**
   * After send / regenerate / resume succeeds, update the history list locally instead of
   * re-fetching getConversationHistory: insert a new entry if the conversation ID doesn't
   * exist (title taken from the first user message), or refresh updated_at if it already exists.
   */
  const upsertHistory = useCallback((conversationId: string, title?: string) => {
    const now = Date.now()
    setHistoryList(prev => {
      const existingIndex = prev.findIndex(item => item.id === conversationId)
      if (existingIndex === -1) {
        const rawTitle = title?.trim() || t('memoryConversation.newConversation')
        const newTitle = rawTitle.length > 50 ? `${rawTitle.slice(0, 50)}…` : rawTitle
        const newItem: HistoryItem = {
          id: conversationId,
          app_id: '',
          workspace_id: '',
          user_id: null,
          title: newTitle,
          is_draft: false,
          message_count: 1,
          is_active: true,
          created_at: now,
          updated_at: now,
        }
        return [newItem, ...prev]
      }
      return prev.map(item => item.id === conversationId ? { ...item, updated_at: now } : item)
    })
  }, [t])

  /** Paginated fetch of conversation history */
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

  const getChatDetail = () => {
    if (!conversation_id || !shareToken || shareToken === '') return
    getConversationDetail(shareToken, conversation_id)
      .then(res => {
        applyChatDetail(res as { messages: ChatItem[]; pending_intervention?: Record<string, any> })
      })
  }

  useEffect(() => {
    if (conversation_id) {
      if (skipChatDetailRef.current) {
        skipChatDetailRef.current = false
      } else {
        getChatDetail()
      }
    } else {
      skipChatDetailRef.current = false
      const variables = toolbarRef.current?.getVariables() || []
      const openingMsg = buildOpeningStatementMessage(features?.opening_statement, {
        variables,
        withTimestamp: true,
        extra: { is_hidden_refresh: true },
      })
      setChatList(openingMsg ? [openingMsg] : [])
    }
  }, [conversation_id, features?.opening_statement?.statement])

  const actions = createConversationActions({
    t, messageApi, modal, shareToken, conversation_id, message, webSearch, memory, thinking,
    features, config, loading, disabled, setConversationId, setLoading, setMemory, setThinking,
    setFileList, setMessage, setChatList, chatIsEnded, streamLoadingRef, toolbarRef, abortRef,
    skipChatDetailRef, shareModalRef, reportModalRef, addUserMessage, addAssistantMessage,
    updateAssistantMessage, updateAssistantReasoningMessage, updateAssistantMemoryRetrieval,
    startAudioPolling, upsertHistory,
    getHistory, getChatDetail,
  })

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
    showMemoryRecall,
    setShowMemoryRecall,
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
    disabled,
    ...actions,
  }
}

export type ConversationCtx = ReturnType<typeof useConversation>
