/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:10:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-02 15:45:38
 */
/**
 * Workflow Chat Component
 * 
 * A drawer-based chat interface for testing and debugging workflow executions.
 * Provides real-time streaming of workflow node execution status, input/output data,
 * and error messages. Supports variable configuration and file attachments.
 * 
 * Key Features:
 * - Real-time workflow execution monitoring with SSE streaming
 * - Node-level execution tracking (start, end, error states)
 * - Variable configuration for workflow inputs
 * - File upload support (images and documents)
 * - Collapsible node execution details with input/output inspection
 * - Error handling and display
 * 
 * @component
 */
import { forwardRef, useImperativeHandle, useState, useRef, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { App, Flex, Button } from 'antd'
import clsx from 'clsx'

import ChatIcon from '@/assets/images/application/chat.png'
import { draftRun, appInterventionsSubmit, draftRunRegenerate, draftRunSwitchMessageVersion, draftRunFavoriteMessage, draftRunFeedbackMessage, draftRunDeleteMessage } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import dayjs from 'dayjs'
import type { ChatRef, GraphRef, WorkflowConfig } from '../../types'
import { type SSEMessage } from '@/utils/stream'
import type { Variable } from '../Properties/VariableList/types'
import ChatInput from '@/components/Chat/ChatInput'
import ChatToolbar from '@/components/Chat/ChatToolbar'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import Runtime from './Runtime';
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types';
import { buildOpeningStatementMessage } from '@/components/Chat/openingStatement';
import { useWorkflowStore } from '@/store/workflow';
import VariableConfigModal from '@/views/Workflow/components/Chat/VariableConfigModal'
import type { VariableConfigModalRef } from '@/views/Workflow/types'
import type { Application } from '@/views/ApplicationManagement/types'
import RbCard from '@/components/RbCard/Card'
import ReportModal from '@/components/Chat/ReportModal'
import type { ReportModalRef } from '@/views/Conversation/types'
import { createWorkflowStreamHandler } from './streamHandler'
import type { Data } from './types'
import {
  flattenChatList,
  mapLastVersion,
  collectVariableParams,
  computeVariables,
  appendRegenerateVersion,
  applyFavorite,
  applyFeedback,
  removeMessageById,
  applyInterventionResolved,
  buildVersionMessages,
} from './helpers'

interface ChatProps {
  appId: string;
  appType?: Application['type'];
  graphRef: GraphRef;
  data: WorkflowConfig | null;
  features?: FeaturesConfigForm;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Function to save workflow configuration */
  handleSave: (flag?: boolean) => Promise<unknown>;
  refreshCache: () => void;
}

const Chat = forwardRef<ChatRef, ChatProps>(({
  appId, graphRef, features, appType, open, onOpenChange, handleSave, refreshCache
}, ref) => {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const { setChatHistory } = useWorkflowStore()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const reportModalRef = useRef<ReportModalRef>(null)
  const [toolbarReady, setToolbarReady] = useState(false)
  const toolbarCallbackRef = useCallback((node: ChatToolbarRef | null) => {
    (toolbarRef as React.MutableRefObject<ChatToolbarRef | null>).current = node
    setToolbarReady(!!node)
  }, [])
  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<Array<ChatItem | ChatItem[]>>([])
  const [variables, setVariables] = useState<Variable[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const [executionId, setExecutionId] = useState<string | null>(null)
  const executionIdRef = useRef<string>('draft')
  const chatIsEnded = useRef(true)
  // Synchronous in-flight guard: blocks duplicate sends while handleSave (slow on
  // poor networks) is still pending, before the async `loading` state flips.
  const sendingRef = useRef(false)

  /**
   * Initializes chat with opening statement if configured
   */
  useEffect(() => {
    const openingMsg = open
      ? buildOpeningStatementMessage(features?.opening_statement, { withTimestamp: true })
      : null
    if (openingMsg) {
      setChatList([openingMsg])
    } else {
      handleClose(false)
    }
    getVariables()
  }, [open])

  useEffect(() => {
    if (toolbarReady || !isWorkflow) {
      getVariables()
    }
  }, [toolbarReady, appType])
  /**
   * Extracts variables from the workflow's start node and merges with previous values
   */
  const getVariables = () => {
    const allVariables = computeVariables(graphRef, variables)
    setVariables([...allVariables])
    toolbarRef.current?.setVariables([...allVariables])
  }
  /**
   * Closes the drawer and resets all state
   */
  const handleClose = (flag: boolean = true) => {
    setChatHistory(executionIdRef.current, flattenChatList(chatList).map((item: ChatItem) => ({
      ...item,
      subContent: item.subContent?.map(sub => ({
        ...sub,
        status: sub.status === 'running' ? undefined : sub.status
      }))
    })))
    abortRef.current?.()
    abortRef.current = null;
    setToolbarReady(false)
    setChatList([])
    setVariables([])
    setExecutionId(null)
    setConversationId(null)
    executionIdRef.current = 'draft'
    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    toolbarRef.current?.setVariables([])
    setFileList([])
    setLoading(false)
    setStreamLoading(false)
    sendingRef.current = false

    if (flag) {
      onOpenChange?.(false)
    }
  }

  const handleSend = (msg?: string) => {
    // Guard synchronously so rapid Enter presses can't queue multiple saves+sends
    // during the (possibly slow) handleSave round-trip.
    if (sendingRef.current || loading) return
    sendingRef.current = true
    handleSave(false)
      .then(() => {
        handleSendMsg(msg)
      })
      .catch(() => {
        sendingRef.current = false
      })
  }
  /**
   * Resolves a node's display metadata (name, icon, type) from the graph
   */
  const getNodeContext = useCallback((node_id: string) => {
    const node = graphRef.current?.getNodes().find(n => n.id === node_id)
    const { name, icon, type } = node?.getData() || {}
    return { name, icon, type }
  }, [graphRef])

  /**
   * Appends a streaming text chunk to the last assistant message
   */
  const appendStreamContent = useCallback((content: string) => {
    setChatList(prev => mapLastVersion(prev, (current) => ({
      ...current,
      content: (current.content || '') + content,
    })))
  }, [])

  /**
   * Replaces the last assistant message content
   */
  const replaceStreamContent = useCallback((content: string) => {
    setChatList(prev => mapLastVersion(prev, (current) => ({
      ...current,
      content,
    })))
  }, [])

  /**
   * Routes each SSE message to the matching per-event logic. The actual reducers
   * live in {@link createWorkflowStreamHandler}; this wrapper keeps the same
   * dependency list so the handler reflects current conversation / execution ids.
   */
  const handleStreamMessage = useCallback((data: SSEMessage[]) => {
    createWorkflowStreamHandler({
      conversationId,
      executionId,
      getNodeContext,
      appendStreamContent,
      replaceStreamContent,
      setChatList,
      setStreamLoading,
      setLoading,
      setConversationId,
      setExecutionId,
      executionIdRef,
      chatIsEnded,
      t,
    })(data)
  }, [
    conversationId,
    executionId,
    getNodeContext,
    appendStreamContent,
    replaceStreamContent,
    t,
  ])

  /**
   * Sends a message to execute the workflow
   * 
   * Process:
   * 1. Validates required variables
   * 2. Adds user message to chat
   * 3. Initiates SSE stream for workflow execution
   * 4. Handles real-time node execution updates
   * 5. Updates chat with results or errors
   * 
   * @param msg - Optional message to send (uses state if not provided)
   */
  const handleSendMsg = async (msg?: string) => {
    if (loading || !appId) {
      sendingRef.current = false
      return
    }
    // Validate required variables before sending
    const { params, trigger_payload, needRequired, isCanSend } = collectVariableParams(variables)
    if (needRequired.length) {
      messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
    }
    if (!isCanSend) {
      sendingRef.current = false
      return
    }

    const message = msg
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))

    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    setFileList([])
    const data = {
      message: message,
      trigger_payload,
      variables: params,
      stream: true,
      conversation_id: conversationId,
      files: files.map(file => {
        if (file.url) {
          return file
        } else {
          return {
            type: file.type,
            transfer_method: 'local_file',
            upload_file_id: file.response.data.file_id
          }
        }
      })
    }
    if (!isWorkflow) {
      setChatList([
        {
          role: 'assistant',
          content: '',
          created_at: Date.now(),
          subContent: [],
        }
      ])
    } else {
      setChatList(prev => [
        ...prev,
        {
          role: 'user',
          content: message,
          created_at: Date.now(),
          meta_data: {
            files
          },
        },
        {
          role: 'assistant',
          content: '',
          created_at: Date.now(),
          subContent: [],
          is_current: true,
          version: 1,
        }
      ])
    }
    setLoading(true)
    setStreamLoading(true)
    chatIsEnded.current = false
    draftRun(appId, data, handleStreamMessage, abort => { abortRef.current = abort })
      .catch((error) => {
        const errorInfo = JSON.parse(error.message)
        setChatList(prev => mapLastVersion(prev, (current) => ({
          ...current,
          status: 'failed',
          content: null,
          subContent: errorInfo.error,
        })))
        chatIsEnded.current = true
      }).finally(() => {
        setLoading(false)
        setStreamLoading(false)
        refreshCache()
        chatIsEnded.current = true
        sendingRef.current = false
      })
  }

  const updateFileList = (list?: any[]) => {
    setFileList([...list || []])
    toolbarRef.current?.setFiles([...list || []])
  }

  /**
   * Ref methods for external control
   */
  const handleOpen = () => {
    // Chat panel is now always visible, just refresh variables
    getVariables()
  }
  const handleInterventionActionClick = async (actionId: string, fieldValues: Record<string, string>, execution_id?: string, node_id?: string) => {
    if (!execution_id || !node_id) {
      return
    }
    const data = {
      node_id,
      action_id: actionId,
      form_data: fieldValues,
    }
    appInterventionsSubmit(appId, execution_id, data)
      .then(() => {
        setChatList(prev => applyInterventionResolved(prev, node_id, fieldValues, actionId))
      })
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    const assistantMsg = buildOpeningStatementMessage(features?.opening_statement, { variables })
    if (assistantMsg) {
      setChatList(prev => {
        const first = prev[0]
        if (first && !Array.isArray(first) && first.role === 'assistant') {
          prev[0] = assistantMsg
        }
        return [...prev]
      })
    }
  }, [features?.opening_statement, variables])

  useEffect(() => {
    if (chatList.length < 1) return
    setChatHistory(executionIdRef.current, flattenChatList(chatList))
  }, [chatList])

  // True when any required variable is missing a value, used to highlight the config button
  const isNeedVariableConfig = variables?.some(
    vo => vo.required && (vo.value === null || vo.value === undefined || vo.value === '')
  )
  const regenerateMessages = (vo: ChatItem) => {
    if (!vo.id || !appId) return
    // Validate required variables before sending
    const { needRequired, isCanSend } = collectVariableParams(variables)
    if (needRequired.length) {
      messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
    }
    if (!isCanSend) return

    // Append a new version of the assistant message to the chat list
    // and drop everything that originally followed it. Snapshot the list
    // beforehand so a request failure can restore the exact prior state.
    let snapshot: Array<ChatItem | ChatItem[]> = []
    setChatList(prev => {
      snapshot = prev
      return appendRegenerateVersion(prev, vo.id as string)
    })

    setLoading(true)
    setStreamLoading(true)
    chatIsEnded.current = false
    draftRunRegenerate(appId, vo.id, handleStreamMessage, abort => { abortRef.current = abort })
      .catch(() => {
        // Roll back to the state captured before appendRegenerateVersion.
        setChatList(snapshot)
        chatIsEnded.current = true
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
        refreshCache()
        chatIsEnded.current = true
      })
  }
  const handleVersionChange = (page: number, item: ChatItem) => {
    if (!page || !item.id || !appId) return
    draftRunSwitchMessageVersion(appId, item.id, page)
      .then((res) => {
        const rebuilt = buildVersionMessages(res as Data, getNodeContext)
        setChatList(rebuilt)
        messageApi.success(t('common.operateSuccess'))
      })
  }
  const handleFavorite = (id?: string) => {
    if (!appId || !id) return
    draftRunFavoriteMessage(appId, id)
      .then((res) => {
        const { is_favorited } = res as { is_favorited: boolean; }
        messageApi.success(t('common.operateSuccess'))
        setChatList(prev => applyFavorite(prev, id, is_favorited))
      })
  }
  const handleFeedback = (feedbackType: 'like' | 'dislike', id?: string) => {
    if (!appId || !id) return
    draftRunFeedbackMessage(appId, id, { feedback_type: feedbackType })
      .then((res) => {
        const { feedback_type } = res as { feedback_type: 'like' | 'dislike' | null; }
        messageApi.success(feedback_type === 'dislike'
          ? t('memoryConversation.dislikeMsg')
          : feedback_type === 'like'
            ? t('memoryConversation.likeMsg')
            : t('memoryConversation.cancelMsg')
        )
        setChatList(prev => applyFeedback(prev, id, feedback_type))
      })
  }
  const deleteMsg = (vo: ChatItem) => {
    console.log('deleteMsg', vo, appId)
    if (!appId || !vo.id) return
    modal.confirm({
      title: t('common.confirmDelete'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        draftRunDeleteMessage(appId, vo.id as string)
          .then(() => {
            setChatList(prev => removeMessageById(prev, vo.id as string))
            messageApi.success(t('common.deleteSuccess'))
          })
      }
    })
  }
  const reportMsg = (vo: ChatItem) => {
    reportModalRef.current?.handleOpen(vo)
  }
  console.log('chatList', chatList)
  const isWorkflow = appType === 'workflow'
  if (!open) return null

  return (
    <div className="rb:w-150 rb:fixed rb:right-2.5 rb:top-18.5 rb:bottom-2.5">
      <RbCard
        title={t('workflow.run')}
        extra={<div className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/close.svg')]" onClick={() => handleClose()}></div>}
        headerType="borderless"
        headerClassName={clsx("rb:font-[MiSans-Bold] rb:font-bold rb:min-h-[48px]!")}
        className="rb:h-full!"
        bodyClassName={clsx('rb:overflow-hidden! rb:h-[calc(100%-48px)]! rb:px-0! rb:pt-0! rb:pb-3!')}
    >
      <ChatContent
        classNames="rb:mx-[16px] rb:pt-[24px] rb:h-[calc(100%-134px)]"
        contentClassNames="rb:max-w-[400px]!'"
        empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
        data={chatList}
        streamLoading={streamLoading}
        labelPosition="bottom"
        labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        // errorDesc={t('application.ReplyException')}
        renderRuntime={(item, index) => {
          return <Runtime item={item} index={index} source="workflow" />
        }}
        onSend={handleSend}
        handleInterventionActionClick={handleInterventionActionClick}
        isEnded={chatIsEnded.current}
        isSupportTools={isWorkflow}
        isAlwaysShowAssistantTools={isWorkflow}
        handleFavorite={isWorkflow ? handleFavorite : undefined}
        handleFeedback={isWorkflow ? handleFeedback : undefined}
        deleteMsg={isWorkflow ? deleteMsg : undefined}
        reportMsg={isWorkflow ? reportMsg : undefined}
        regenerateMaxCount={5}
        regenerateMessages={isWorkflow ? regenerateMessages : undefined}
        handleVersionChange={isWorkflow ? handleVersionChange : undefined}
      />
        {isWorkflow &&
          <Flex align="center" gap={10} className="rb:relative rb:m-4! rb:mb-1!">
            <ChatInput
              message={message}
              className="rb:relative!"
              loading={loading}
              fileChange={updateFileList}
              fileList={fileList}
              onSend={handleSend}
              onChange={(msg) => setMessage(msg)}
            >
              <ChatToolbar
                ref={toolbarCallbackRef}
                features={features as FeaturesConfigForm}
                onFilesChange={setFileList}
                onVariablesChange={setVariables}
              />
            </ChatInput>
          </Flex>
        }
        {!isWorkflow &&
          <Flex align="center" justify="center" gap={10} className="rb:relative rb:m-4! rb:mb-1!">
            {variables.length > 0 &&
              <Button
                danger={isNeedVariableConfig}
                icon={<div className={clsx("rb:size-4 rb:bg-cover", {
                  "rb:bg-[url('@/assets/images/conversation/variables_red.svg')]": isNeedVariableConfig,
                  "rb:bg-[url('@/assets/images/conversation/variables.svg')]": !isNeedVariableConfig
                })} />}
                onClick={() => variableConfigModalRef.current?.handleOpen(variables)}
              >
                {t('memoryConversation.variableConfig')}
              </Button>
            }
            <Button
              type="primary"
              onClick={() => handleSend()}
              loading={loading}
            >
              {t('workflow.startRun')}
            </Button>
          </Flex>
        }
        <VariableConfigModal
          ref={variableConfigModalRef}
          refresh={setVariables}
        />
        <ReportModal ref={reportModalRef} appId={appId} />
      </RbCard>
    </div>
  )
})

export default Chat
