/*
 * @Author: ZhaoYing
 * @Date: 2026-03-13 17:27:52
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-08-13 11:55:09
 */
import { type FC, useState, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { App } from 'antd'
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
import RbCard from '@/components/RbCard/Card'
import ChatToolbar, { type ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import Runtime from '@/views/Workflow/components/Chat/Runtime'
import ReportModal from '@/components/Chat/ReportModal'

import type { ChatItem } from '@/components/Chat/types'
import type { ReportModalRef } from '@/views/Conversation/types'
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
import { createAgentStreamHandler, createWorkflowStreamHandler } from './streamHandlers'

const TestChat: FC<TestChatProps> = ({
  application,
  config
}) => {
  const { t } = useTranslation()
  const { message: messageApi, modal } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const reportModalRef = useRef<ReportModalRef>(null)

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
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    const vars = toolbarRef.current?.getVariables() || []
    const { isCanSend, params, needRequired } = collectVariableParams(vars)
    if (needRequired.length) {
      messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
    }
    return { files, isCanSend, params }
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

  const handleSend = (msg?: string) => {
    if (loading || !application || !((message && message?.trim() !== '') || (msg && msg?.trim() !== ''))) return
    const { files, isCanSend, params } = resolveSendParams()
    if (!isCanSend) return

    setChatList(prev => addUserMessage(prev, (msg || message) as string, files))
    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    setFileList([])
    setChatList(prev => addAssistantMessage(prev, application?.type))
    streamLoadingRef.current = true
    setStreamLoading(true)
    setLoading(true)

    draftRun(
      application.id,
      formatParams((msg || message) as string, conversationId, files, params),
      handleStreamMessage,
      (abort) => { abortRef.current = abort }
    )
      .catch(() => {
        setChatList(prev => applyErrorMessage(prev, 0))
        setLoading(false)
      })
      .finally(() => {
        setLoading(false)
        streamLoadingRef.current = false
        setStreamLoading(false)
      })
  }

  const handleWorkflowSend = (msg?: string) => {
    if (loading || !application || !((message && message?.trim() !== '') || (msg && msg?.trim() !== ''))) return
    const { files, isCanSend, params } = resolveSendParams()
    if (!isCanSend) return

    setLoading(true)
    setChatList(prev => addUserMessage(prev, (msg || message) as string, files))
    setChatList(prev => addAssistantMessage(prev, application?.type))
    toolbarRef.current?.setFiles([])
    setFileList([])
    setMessage(undefined)
    setStreamLoading(true)
    streamLoadingRef.current = true

    draftRun(
      application.id,
      formatParams((msg || message) as string, conversationId, files, params),
      handleWorkflowStreamMessage,
      (abort) => { abortRef.current = abort }
    )
      .catch((error) => {
        const errorInfo = JSON.parse(error.message)
        setChatList(prev => applyWorkflowSendError(prev, errorInfo.error))
      })
      .finally(() => {
        setLoading(false)
        setStreamLoading(false)
        streamLoadingRef.current = false
      })
  }

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

    const handler = isWorkflow ? handleWorkflowStreamMessage : handleStreamMessage
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
  console.log('chatList', chatList)

  const isSupportTools = application?.type && ['workflow', 'agent'].includes(application?.type)

  return (
    <div className="rb:w-250 rb:mx-auto rb:h-full">
      <RbCard
        title={t('application.test')}
        headerClassName="rb:min-h-[56px]!"
        className="rb:h-full!"
        bodyClassName="rb:h-[calc(100%-56px)]! rb:overflow-y-auto rb:px-3! rb:py-0!"
      >
        <Chat
          empty={<Empty url={ChatIcon} title={t('application.testChatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full!" />}
          contentClassName={clsx(`rb:mx-[16px] rb:pt-[24px]`, {
            'rb:h-[calc(100%-140px)]': !fileList.length,
            'rb:h-[calc(100%-208px)]': !!fileList.length,
          })}
          data={chatList}
          streamLoading={streamLoading}
          loading={loading}
          onChange={setMessage}
          onSend={isWorkflow ? handleWorkflowSend : handleSend}
          fileList={fileList}
          fileChange={(list) => {
            setFileList(list || [])
            toolbarRef.current?.setFiles(list || [])
          }}
          labelFormat={(item) => item.role === 'user' ? t('application.you') : dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
          // errorDesc={t('application.ReplyException')}
          renderRuntime={isSupportTools ? (item, index) => <Runtime item={item} index={index} source={application.type} /> : undefined}
          handleInterventionActionClick={handleInterventionActionClick}
          isSupportTools={isSupportTools}
          isAlwaysShowAssistantTools={isSupportTools}
          isEnded={!loading}
          handleFeedback={isSupportTools ? handleFeedback : undefined}
          handleFavorite={isSupportTools ? handleFavorite : undefined}
          deleteMsg={isSupportTools ? deleteMsg : undefined}
          reportMsg={isSupportTools ? reportMsg : undefined}
          regenerateMaxCount={5}
          regenerateMessages={isSupportTools ? regenerateMessages : undefined}
          handleVersionChange={isSupportTools ? handleVersionChange : undefined}
        >
          <ChatToolbar
            ref={toolbarRef}
            features={features}
            onFilesChange={setFileList}
            onVariablesChange={setVariables}
          />
        </Chat>
      </RbCard>
      {application?.id && <ReportModal ref={reportModalRef} appId={application.id} />}
    </div>
  )
}

export default TestChat
