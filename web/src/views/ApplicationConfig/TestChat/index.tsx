import { type FC, useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { App, Button, Flex } from 'antd'
import clsx from 'clsx'
import dayjs from 'dayjs'

import ChatIcon from '@/assets/images/application/chat.png'
import {
  draftRun,
  appInterventionsSubmit,
  draftRunRegenerate,
  draftRunSwitchMessageVersion,
  draftRunFavoriteMessage,
  draftRunFeedbackMessage,
  draftRunDeleteMessage,
} from '@/api/application'

import Empty from '@/components/Empty'
import Chat from '@/components/Chat'
import ChatContent from '@/components/Chat/ChatContent'
import RbCard from '@/components/RbCard/Card'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import Runtime from '@/views/Workflow/components/Chat/Runtime'
import VariableConfigModal from '@/views/Workflow/components/Chat/VariableConfigModal'
import ReportModal from '@/components/Chat/ReportModal'

import type { ChatItem } from '@/components/Chat/types'
import type { ReportModalRef } from '@/views/Conversation/types'
import type { VariableConfigModalRef } from '@/views/Workflow/types'
import type { Variable } from '@/views/Workflow/components/Properties/VariableList/types'
import type { TestChatProps } from './type'
import type { SSEMessage } from '@/utils/stream'
import type { FeaturesConfigForm } from '@/views/ApplicationConfig/types'
import { buildOpeningStatementMessage } from '@/components/Chat/openingStatement'
import {
  formatParams,
  collectVariableParams,
  computeInitVariables,
  resolveWorkflowNode,
  addUserMessage,
  addAssistantMessage,
  applyErrorMessage,
  applyWorkflowSendError,
  applyInterventionResolved,
} from './helpers'
import {
  appendRegenerateVersion,
  applyFavorite,
  applyFeedback,
  removeMessageById,
  buildVersionMessages,
} from '@/components/Chat/utils/messageVersions'
import {
  createAgentStreamHandler,
  createTestClusterStreamHandler,
  createWorkflowStreamHandler,
} from './streamHandlers'
import { createTimelineAwareStreamHandler } from '@/components/Chat/utils/timelineStream'

const TestChat: FC<TestChatProps> = ({
  application,
  config
}) => {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const reportModalRef = useRef<ReportModalRef>(null)
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)

  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<Array<ChatItem | ChatItem[]>>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | undefined>(undefined)
  const [fileList, setFileList] = useState<any[]>([])
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)
  const [variables, setVariables] = useState<Variable[]>([])

  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const streamLoadingRef = useRef(false)
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})
  const abortRef = useRef<(() => void) | null>(null)

  const isWorkflow = !!application?.type.includes('workflow')
  const isCluster = application?.type === 'multi_agent'
  // Pure workflow has no user message: it is triggered by variables + a run button
  const isPureWorkflow = application?.type === 'pure_workflow'

  useEffect(() => {
    getVariables()
  }, [application, JSON.stringify(config)])

  useEffect(() => {
    return () => {
      abortRef.current?.()
      abortRef.current = null
      audioPollingRef.current.forEach(timer => clearInterval(timer))
      audioPollingRef.current.clear()
    }
  }, [])

  const getVariables = () => {
    if (!application || !config) return

    setFeatures(config?.features || {} as FeaturesConfigForm)

    const openingMsg = buildOpeningStatementMessage(config?.features?.opening_statement, { withTimestamp: true })
    if (openingMsg) {
      setChatList(prev => [...prev, openingMsg])
    }

    const initVariables = computeInitVariables(application.type, config)
    toolbarRef.current?.setVariables([...initVariables])
    setVariables([...initVariables])
  }

  /** Resolves a node's display icon from the workflow config (safe for agents). */
  const getNodeContext = (node_id: string) => {
    if (!(config as any)?.nodes) return {}
    return { icon: resolveWorkflowNode(config, node_id).icon }
  }

  /**
   * Collects attachments + validated variable params shared by both send flows,
   * surfacing an error toast when required variables are missing.
   */
  const resolveSendParams = () => {
    // Pure workflow runs from the variable panel only: no attachments / input box
    const files = isPureWorkflow
      ? []
      : (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const vars = isPureWorkflow ? variables : (toolbarRef.current?.getVariables() || [])
    const { isCanSend, params, trigger_payload, needRequired } = collectVariableParams(vars)
    if (needRequired.length) {
      messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
    }
    return { files, isCanSend, params, trigger_payload }
  }

  const handleStreamMessage = (data: SSEMessage[]) =>
    createAgentStreamHandler({
      conversationId,
      setConversationId,
      setChatList,
      setLoading,
      setStreamLoading,
      streamLoadingRef,
      audioStatusMap,
      setAudioStatusMap,
      audioPollingRef,
    })(data)

  const handleWorkflowStreamMessage = (data: SSEMessage[]) =>
    createWorkflowStreamHandler({
      conversationId,
      setConversationId,
      setChatList,
      setStreamLoading,
      setLoading,
      streamLoadingRef,
      config,
    })(data)

  /** Agent / cluster runs get a timeline-aware wrapper; workflows keep their own handler. */
  const createStreamHandler = (mode: 'agent' | 'cluster' | 'workflow' | 'pure_workflow') => {
    if (mode === 'workflow' || mode === 'pure_workflow') return handleWorkflowStreamMessage
    const baseHandler = mode === 'cluster'
      ? createTestClusterStreamHandler({
        conversationId,
        setConversationId,
        setChatList,
        setLoading,
        setStreamLoading,
        streamLoadingRef,
        audioPollingRef,
      })
      : handleStreamMessage
    return createTimelineAwareStreamHandler(baseHandler, {
      setChatList,
      setLoading,
      setStreamLoading,
      streamLoadingRef,
    })
  }

  /**
   * Clears the previous pure workflow runs and starts a fresh one: only the
   * opening statement (when configured) and a new assistant placeholder remain.
   */
  const resetRunChatList = () => {
    const openingMsg = buildOpeningStatementMessage(features?.opening_statement, { withTimestamp: true, variables })
    const placeholder: ChatItem = {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
      subContent: [],
    }
    setChatList(openingMsg ? [openingMsg, placeholder] : [placeholder])
    setConversationId(null)
    setMessage(undefined)
  }

  type SendMode = 'agent' | 'cluster' | 'workflow' | 'pure_workflow'

  const handleSendByMode = (mode: SendMode, msg?: string) => {
    // Pure workflow is run without a message: only its variables are submitted
    const isRunMode = mode === 'pure_workflow'
    const sendMessage = msg || message
    if (loading || !application || (!isRunMode && !sendMessage?.trim())) return

    const { files, isCanSend, params, trigger_payload } = resolveSendParams()
    if (!isCanSend) return

    if (isRunMode) {
      // Each pure workflow run is a standalone execution: clear the previous runs
      resetRunChatList()
    } else {
      setChatList(prev => addUserMessage(prev, sendMessage as string, files))
      setChatList(prev => addAssistantMessage(prev, application.type))
      setMessage(undefined)
      toolbarRef.current?.setFiles([])
      setFileList([])
    }
    streamLoadingRef.current = true
    setStreamLoading(true)
    setLoading(true)

    const streamHandler = createStreamHandler(mode)

    draftRun(
      application.id,
      // Every pure workflow run is an independent execution (no conversation history)
      formatParams(isRunMode ? undefined : sendMessage, isRunMode ? null : conversationId, files, params, trigger_payload),
      streamHandler,
      (abort) => { abortRef.current = abort },
    )
      .catch((error) => {
        if (mode === 'workflow' || isRunMode) {
          let errorInfo: { error?: any } = {}
          try {
            errorInfo = JSON.parse(error.message)
          } catch {
            errorInfo = { error: error?.message }
          }
          setChatList(prev => applyWorkflowSendError(prev, errorInfo.error))
          return
        }
        setChatList(prev => applyErrorMessage(prev, 0))
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        setStreamLoading(false)
      })
  }

  const handleSend = (msg?: string) => handleSendByMode('agent', msg)
  const handleClusterSend = (msg?: string) => handleSendByMode('cluster', msg)
  const handleWorkflowSend = (msg?: string) => handleSendByMode('workflow', msg)
  const handlePureWorkflowRun = () => handleSendByMode('pure_workflow')

  useEffect(() => {
    if (!Object.keys(audioStatusMap).length) return
    setChatList(prev => prev.map(entry => {
      const apply = (msg: ChatItem): ChatItem => {
        if (msg.role === 'assistant' && msg.meta_data?.audio_url && audioStatusMap[msg.meta_data.audio_url]) {
          return {
            ...msg,
            meta_data: {
              ...msg.meta_data,
              audio_status: audioStatusMap[msg.meta_data.audio_url]
            }
          }
        }
        return msg
      }
      return Array.isArray(entry) ? entry.map(apply) : apply(entry)
    }))
  }, [audioStatusMap, chatList.length])

  const handleInterventionActionClick = (actionId: string, fieldValues: Record<string, string>, execution_id?: string, node_id?: string) => {
    if (!application?.id || !execution_id || !node_id) {
      return
    }
    const data = {
      node_id,
      action_id: actionId,
      form_data: fieldValues,
    }
    appInterventionsSubmit(application.id, execution_id, data)
      .then(() => {
        setChatList(prev => applyInterventionResolved(prev, fieldValues, actionId))
      })
  }

  /** Re-runs the targeted assistant message, appending it as a new version. */
  const regenerateMessages = (vo: ChatItem) => {
    if (!vo.id || !application?.id) return
    const { isCanSend } = resolveSendParams()
    if (!isCanSend) return

    let snapshot: Array<ChatItem | ChatItem[]> = []
    setChatList(prev => {
      snapshot = prev
      return appendRegenerateVersion(prev, vo.id as string)
    })
    streamLoadingRef.current = true
    setStreamLoading(true)
    setLoading(true)

    const handler = isWorkflow
      ? handleWorkflowStreamMessage
      : createStreamHandler(isCluster ? 'cluster' : 'agent')
    draftRunRegenerate(application.id, vo.id, handler, (abort) => { abortRef.current = abort })
      .catch(() => {
        // Roll back to the state captured before appendRegenerateVersion.
        setChatList(snapshot)
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        setStreamLoading(false)
      })
  }

  /** Switches the visible version of a message and rebuilds its execution detail. */
  const handleVersionChange = (page: number, item: ChatItem) => {
    if (!page || !item.id || !application?.id) return
    draftRunSwitchMessageVersion(application.id, item.id, page)
      .then((res) => {
        const rebuilt = buildVersionMessages(res, getNodeContext)
        setChatList(rebuilt)
        messageApi.success(t('common.operateSuccess'))
      })
  }

  const handleFavorite = (id?: string) => {
    if (!application?.id || !id) return
    draftRunFavoriteMessage(application.id, id)
      .then((res) => {
        const { is_favorited } = res as { is_favorited: boolean; }
        messageApi.success(t('common.operateSuccess'))
        setChatList(prev => applyFavorite(prev, id, is_favorited))
      })
  }

  const handleFeedback = (feedbackType: 'like' | 'dislike', id?: string) => {
    if (!application?.id || !id) return
    draftRunFeedbackMessage(application.id, id, { feedback_type: feedbackType })
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
    if (!application?.id || !vo.id) return
    modal.confirm({
      title: t('common.confirmDelete'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        draftRunDeleteMessage(application.id, vo.id as string)
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
  const isSupportTools = !!application?.type

  // True when any required variable is missing a value, used to highlight the config button
  const isNeedVariableConfig = variables?.some(
    vo => vo.required && (vo.value === null || vo.value === undefined || vo.value === '')
  )

  /** Persists the values edited in the standalone variable panel (pure workflow) */
  const handleVariablesSave = (values: Variable[]) => {
    setVariables([...values])
    toolbarRef.current?.setVariables([...values])
  }

  const emptyNode = (
    <Empty
      url={ChatIcon}
      subTitle={t(isPureWorkflow ? 'application.testRunEmpty' : 'application.testChatEmpty')}
      size={[240, 200]}
      className="rb:h-full!"
    />
  )

  /** Message list props shared by the chat layout and the run-only (pure workflow) layout */
  const chatContentProps = {
    data: chatList,
    source: application?.type,
    streamLoading,
    labelFormat: (item: ChatItem) => item.role === 'user' ? t('application.you') : dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A'),
    // errorDesc={t('application.ReplyException')}
    renderRuntime: application?.type ? (item: ChatItem, index: number) => <Runtime item={item} index={index} source={application.type} /> : undefined,
    handleInterventionActionClick,
    isSupportTools,
    isAlwaysShowAssistantTools: isSupportTools,
    isEnded: !loading,
    handleFeedback: isSupportTools ? handleFeedback : undefined,
    handleFavorite: isSupportTools ? handleFavorite : undefined,
    deleteMsg: isSupportTools ? deleteMsg : undefined,
    reportMsg: isSupportTools ? reportMsg : undefined,
    regenerateMaxCount: 5,
    // pure workflow is a one-shot execution: regenerate is rejected by the backend
    regenerateMessages: isSupportTools && !isPureWorkflow ? regenerateMessages : undefined,
    handleVersionChange: isSupportTools ? handleVersionChange : undefined,
  }

  return (
    <div className="rb:w-250 rb:mx-auto rb:h-full">
      <RbCard
        title={t('application.test')}
        headerClassName="rb:min-h-[56px]!"
        className="rb:h-full!"
        bodyClassName="rb:h-[calc(100%-56px)]! rb:overflow-y-auto rb:px-3! rb:py-0!"
      >
        {application?.type && isPureWorkflow
          ? (
            <Flex vertical className="rb:h-full! rb:overflow-hidden">
              <ChatContent
                classNames="rb:mx-[16px] rb:pt-[24px] rb:flex-1! rb:min-h-0!"
                empty={emptyNode}
                {...chatContentProps}
              />
              {/* Pure workflow has no message input: variables + a run button instead */}
              <Flex align="center" justify="center" gap={10} className="rb:mx-4! rb:mt-3! rb:mb-0! rb:pb-3!">
                {variables.length > 0 &&
                  <Button
                    danger={isNeedVariableConfig}
                    icon={<div className={clsx("rb:size-4 rb:bg-cover", {
                      "rb:bg-[url('@/assets/images/conversation/variables_red.svg')]": isNeedVariableConfig,
                      "rb:bg-[url('@/assets/images/conversation/variables.svg')] rb:group-hover:bg-[url('@/assets/images/conversation/variables_blue.svg')]": !isNeedVariableConfig
                    })} />}
                    className="rb:group"
                    onClick={() => variableConfigModalRef.current?.handleOpen(variables)}
                  >
                    {t('memoryConversation.variableConfig')}
                  </Button>
                }
                <Button
                  type="primary"
                  onClick={handlePureWorkflowRun}
                  loading={loading}
                  disabled={loading || isNeedVariableConfig}
                >
                  {t('workflow.startRun')}
                </Button>
              </Flex>
            </Flex>
          )
          : application?.type
          ? (
            <Chat
              empty={emptyNode}
              contentClassName={clsx(`rb:mx-[16px] rb:pt-[24px]`, {
                'rb:h-[calc(100%-140px)]': !fileList.length,
                'rb:h-[calc(100%-208px)]': !!fileList.length,
              })}
              loading={loading}
              onChange={setMessage}
              onSend={isWorkflow ? handleWorkflowSend : isCluster ? handleClusterSend : handleSend}
              fileList={fileList}
              fileChange={(list) => {
                setFileList(list || [])
                toolbarRef.current?.setFiles(list || [])
              }}
              {...chatContentProps}
            >
              <ChatToolbar
                ref={toolbarRef}
                features={features}
                onFilesChange={setFileList}
                onVariablesChange={setVariables}
              />
            </Chat>
          )
          : null
        }
      </RbCard>
      <VariableConfigModal ref={variableConfigModalRef} refresh={handleVariablesSave} />
      {application?.id && <ReportModal ref={reportModalRef} appId={application.id} />}
    </div>
  )
}

export default TestChat
