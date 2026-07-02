/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 16:27:39
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-02 16:02:29
 */
/**
 * Chat debugging component for application testing
 * Supports both single agent and multi-agent cluster modes
 * Provides real-time streaming responses and conversation history
 */

import { type FC, useEffect, useState, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom'
import clsx from 'clsx'
import { App, Flex, Tooltip } from 'antd';

import ChatIcon from '@/assets/images/application/chat.png'
import DebuggingEmpty from '@/assets/images/application/debuggingEmpty.png'
import type { ChatData, Config, FeaturesConfigForm } from '../../types'
import type { ChatItem } from '@/components/Chat/types'
import type { ReportModalRef } from '@/views/Conversation/types'
import {
  runCompare,
  draftRun,
  draftRunRegenerate,
  draftRunSwitchMessageVersion,
  draftRunFavoriteMessage,
  draftRunFeedbackMessage,
  draftRunDeleteMessage,
} from '@/api/application'
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import ChatInput from '@/components/Chat/ChatInput'
import ChatToolbar from '@/components/Chat/ChatToolbar'
import type { ChatToolbarRef } from '@/components/Chat/ChatToolbar'
import type { Variable } from '../VariableList/types'
import Runtime from '@/views/Workflow/components/Chat/Runtime'
import ReportModal from '@/components/Chat/ReportModal'
import {
  addUserMessage,
  addAssistantMessage,
  applyAudioStatus,
  collectVariableParams,
  formatFiles,
  updateClusterErrorAssistantMessage,
} from './helpers'
import {
  appendRegenerateVersion,
  applyFavorite,
  applyFeedback,
  removeMessageById,
  applyVersionMessages,
} from './messageReducers'
import { createCompareStreamHandler, createClusterStreamHandler, createRegenerateStreamHandler } from './streamHandlers'

/**
 * Component props
 */
interface ChatProps {
  /** List of chat configurations for comparison */
  chatList: ChatData[];
  /** Application configuration data */
  data: Config;
  /** Update chat list state */
  updateChatList: React.Dispatch<React.SetStateAction<ChatData[]>>;
  /** Save configuration before running */
  handleSave: (flag?: boolean) => Promise<unknown>;
  /** Source type: multi-agent cluster or single agent */
  source?: 'multi_agent' | 'agent';
  /** chatVariables prop */
  chatVariables?: Variable[];
  handleEditVariables?: () => void;
}


/**
 * Chat debugging component
 * Allows testing application with different model configurations side-by-side
 */
const Chat: FC<ChatProps> = ({
  chatList, data, updateChatList, handleSave, source = 'agent', chatVariables,
  handleEditVariables
}) => {
  const { t } = useTranslation();
  const { id } = useParams()
  const { message: messageApi, modal } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const reportModalRef = useRef<ReportModalRef>(null)
  const audioPollingRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map())
  const [loading, setLoading] = useState(false)
  const [isCluster, setIsCluster] = useState(source === 'multi_agent')
  const [conversationId, setConversationId] = useState<string | null>(null)
  const compareLoadingRef = useRef(false)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)
  const [audioStatusMap, setAudioStatusMap] = useState<Record<string, string>>({})
  const abortRef = useRef<(() => void) | null>(null)

  const cleanup = () => {
    abortRef.current?.()
    abortRef.current = null
    audioPollingRef.current.forEach(timer => clearInterval(timer))
    audioPollingRef.current.clear()
  }

  useEffect(() => {
    compareLoadingRef.current = false
    setLoading(false)
    return cleanup
  }, [chatList.map(item => item.label).join(',')])

  useEffect(() => cleanup, [])

  useEffect(() => {
    if (data?.features) setFeatures(data.features)
  }, [data?.features])

  useEffect(() => {
    setIsCluster(source === 'multi_agent')
    toolbarRef.current?.setFiles([])
    setMessage(undefined)
  }, [source, toolbarRef.current])

  useEffect(() => {
    updateChatList(prev => applyAudioStatus(prev, audioStatusMap))
  }, [chatList.length, audioStatusMap])

  /** Send message for agent comparison mode */
  const handleSend = (msg?: string) => {
    if (loading || !id) return
    setLoading(true)
    compareLoadingRef.current = true
    const files = (fileList || []).filter(item => !['uploading', 'error'].includes(item.status))
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message?.trim()) return
        // Validate required variables before sending
        const { isCanSend, params, needRequired } = collectVariableParams(chatVariables)
        if (needRequired.length) {
          messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
        }
        if (!isCanSend) {
          setLoading(false)
          compareLoadingRef.current = false
          return
        }

        updateChatList(prev => addUserMessage(prev, message, files))
        setMessage(undefined)
        toolbarRef.current?.setFiles([])
        setFileList([])
        updateChatList(prev => addAssistantMessage(prev))

        const handleStreamMessage = createCompareStreamHandler({
          updateChatList,
          setLoading,
          compareLoadingRef,
          audioStatusMap,
          setAudioStatusMap,
          audioPollingRef,
        })

        setTimeout(() => {
          runCompare(id, {
            message,
            files: formatFiles(files),
            models: chatList.map(item => ({
              model_config_id: item.model_config_id,
              label: item.label,
              model_parameters: item.model_parameters,
              conversation_id: item.conversation_id
            })),
            variables: params,
            parallel: true,
            stream: true,
            timeout: 60,
          }, handleStreamMessage, (abort) => { abortRef.current = abort })
            .catch(() => {
              setLoading(false)
              compareLoadingRef.current = false
              updateChatList(prev => updateClusterErrorAssistantMessage(prev, 0))
            })
            .finally(() => {
              setLoading(false)
              compareLoadingRef.current = false
            })
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        compareLoadingRef.current = false
      })
  }

  /** Send message for cluster mode */
  const handleClusterSend = (msg?: string) => {
    if (loading || !id) return
    setLoading(true)
    compareLoadingRef.current = true
    const files = (fileList || []).filter(item => !['uploading', 'error'].includes(item.status))
    handleSave(false)
      .then(() => {
        const message = msg
        if (!message || message.trim() === '') return
        updateChatList(prev => addUserMessage(prev, message, files))
        setMessage(undefined)
        toolbarRef.current?.setFiles([])
        setFileList([])
        updateChatList(prev => addAssistantMessage(prev))

        const handleStreamMessage = createClusterStreamHandler({
          updateChatList,
          setLoading,
          compareLoadingRef,
          conversationId,
          setConversationId,
        })

        setTimeout(() => {
          draftRun(id,
            {
              message,
              conversation_id: conversationId,
              stream: true,
              files: formatFiles(files),
            },
            handleStreamMessage,
            (abort) => { abortRef.current = abort }
          )
            .catch(() => {
              setLoading(false)
              compareLoadingRef.current = false
              updateChatList(prev => updateClusterErrorAssistantMessage(prev, 0))
            })
            .finally(() => {
              setLoading(false)
              compareLoadingRef.current = false
            })
        }, 0)
      })
      .catch(() => {
        setLoading(false)
        compareLoadingRef.current = false
      })
  }

  /** Delete chat configuration from list */
  const handleDelete = (index: number) => {
    updateChatList(chatList.filter((_, voIndex) => voIndex !== index))
  }

  /** Compare mode has no workflow nodes, so version-switch needs no icon lookup. */
  const getNodeContext = () => ({})

  /** Locates the model column owning a message id. */
  const findColumn = (msgId?: string) => chatList.find(chat =>
    (chat.list || []).some(entry =>
      Array.isArray(entry) ? entry.some(m => m.id === msgId) : entry.id === msgId,
    ),
  )

  /** Re-runs the targeted assistant message, appending it as a new version. */
  const regenerateMessages = (vo: ChatItem) => {
    if (loading || !id || !vo.id) return
    const column = findColumn(vo.id)
    if (!column) return

    let snapshot: ChatData[] = []
    updateChatList(prev => {
      snapshot = prev
      return appendRegenerateVersion(prev, vo.id as string)
    })
    setLoading(true)
    compareLoadingRef.current = true

    const handleStreamMessage = createRegenerateStreamHandler({
      modelConfigId: column.model_config_id,
      updateChatList,
      setLoading,
      compareLoadingRef,
      audioStatusMap,
      setAudioStatusMap,
      audioPollingRef,
    })

    setTimeout(() => {
      draftRunRegenerate(id, vo.id as string, handleStreamMessage, (abort) => { abortRef.current = abort })
        .catch(() => {
          // Roll back to the state captured before appendRegenerateVersion.
          updateChatList(snapshot)
        })
        .finally(() => {
          setLoading(false)
          compareLoadingRef.current = false
        })
    }, 0)
  }

  /** Switches the visible version of a message and rebuilds its column detail. */
  const handleVersionChange = (page: number, item: ChatItem) => {
    if (!page || !item.id || !id) return
    draftRunSwitchMessageVersion(id, item.id, page)
      .then((res) => {
        updateChatList(prev => applyVersionMessages(prev, item.id as string, res, getNodeContext))
        messageApi.success(t('common.operateSuccess'))
      })
  }

  const handleFavorite = (messageId?: string) => {
    if (!id || !messageId) return
    draftRunFavoriteMessage(id, messageId)
      .then((res) => {
        const { is_favorited } = res as { is_favorited: boolean; }
        messageApi.success(t('common.operateSuccess'))
        updateChatList(prev => applyFavorite(prev, messageId, is_favorited))
      })
  }

  const handleFeedback = (feedbackType: 'like' | 'dislike', messageId?: string) => {
    if (!id || !messageId) return
    draftRunFeedbackMessage(id, messageId, { feedback_type: feedbackType })
      .then((res) => {
        const { feedback_type } = res as { feedback_type: 'like' | 'dislike' | null; }
        messageApi.success(feedback_type === 'dislike'
          ? t('memoryConversation.dislikeMsg')
          : feedback_type === 'like'
            ? t('memoryConversation.likeMsg')
            : t('memoryConversation.cancelMsg')
        )
        updateChatList(prev => applyFeedback(prev, messageId, feedback_type))
      })
  }

  const deleteMsg = (vo: ChatItem) => {
    if (!id || !vo.id) return
    modal.confirm({
      title: t('common.confirmDelete'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        draftRunDeleteMessage(id, vo.id as string)
          .then(() => {
            updateChatList(prev => removeMessageById(prev, vo.id as string))
            messageApi.success(t('common.deleteSuccess'))
          })
      }
    })
  }

  const reportMsg = (vo: ChatItem) => {
    reportModalRef.current?.handleOpen(vo)
  }
  console.log('chatList', chatList)
  const isHasLabel = useMemo(() => chatList.some(item => item.label), [chatList])
  const isNeedVariableConfig = useMemo(() => chatVariables?.some(vo => vo.required && !vo.value), [chatVariables])
  return (
    <Flex vertical className="rb:relative rb:h-full">
      {chatList.length === 0
        ? <Empty
          url={DebuggingEmpty}
          size={[300, 200]}
          title={t('application.debuggingEmpty')}
          subTitle={t('application.debuggingEmptyDesc')}
          className="rb:h-[calc(100vh-159px)]"
        />
        : <>
          <div className={clsx(`rb:relative rb:grid rb:grid-cols-${chatList.length} rb:overflow-hidden rb:w-full rb:flex-1 rb:min-h-0`)}>
            {chatList.map((chat, index) => (
              <Flex key={index} vertical className={clsx({
                "rb:border-r rb:border-[#DFE4ED]": index !== chatList.length - 1 && chatList.length > 1,
              })}>
                {chat.label &&
                  <div className={clsx(
                    "rb:grid rb:bg-[#F6F6F6] rb:text-center rb:flex-[0_0_auto]"
                  )}>
                    <div className='rb:relative rb:py-2.5 rb:px-3 rb:overflow-hidden'>
                      <div className="rb:text-[#212332] rb:font-medium rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:w-[calc(100%-24px)]">{chat.label}</div>
                      <div
                        className="rb:w-4 rb:h-4 rb:cursor-pointer rb:absolute rb:top-3 rb:right-3 rb:bg-cover rb:bg-[url('@/assets/images/close.svg')] rb:hover:bg-[url('@/assets/images/close_hover.svg')]"
                        onClick={() => handleDelete(index)}
                      ></div>
                    </div>
                  </div>
                }
                <ChatContent
                  classNames={{
                    'rb:mb-3 rb:mt-5': isHasLabel,
                    'rb:mb-0!': !isHasLabel,
                    'rb:h-[calc(100vh-297px)]': isCluster && (!fileList || fileList.length === 0),
                    'rb:h-[calc(100vh-365px)]': !isCluster && (!fileList || fileList.length === 0),
                    'rb:h-[calc(100vh-362px)]': isCluster && fileList?.length > 0,
                    'rb:h-[calc(100vh-433px)]': !isCluster && fileList?.length > 0,
                    "rb:pr-4": index !== chatList.length - 1 && chatList.length > 1,
                    "rb:pl-4": index !== 0 && chatList.length > 1,
                  }}
                  contentClassNames={{
                    'rb:max-w-100!': chatList.length === 1,
                    'rb:max-w-70!': chatList.length === 2,
                    'rb:max-w-45!': chatList.length === 3,
                    'rb:max-w-24!': chatList.length === 4,
                  }}
                  empty={<Empty
                    url={ChatIcon}
                    title={t('application.chatEmpty')}
                    isNeedSubTitle={false}
                    size={[240, 200]}
                    className={clsx({
                      "rb:h-[calc(100vh-353px)]": isHasLabel,
                      "rb:h-[calc(100vh-292px)]": !isHasLabel,
                    })}
                  />}
                  onSend={isCluster ? handleClusterSend : handleSend}
                  data={chat.list || []}
                  streamLoading={compareLoadingRef.current}
                  labelPosition="top"
                  labelFormat={(item) => item.role === 'user' ? t('application.you') : chat.label || t(`application.ai`)}
                  renderRuntime={(item, index) => <Runtime source={source} item={item} index={index} />}
                  isSupportTools={!isCluster}
                  isAlwaysShowAssistantTools={!isCluster}
                  isEnded={!loading}
                  handleFeedback={isCluster ? undefined : handleFeedback}
                  handleFavorite={isCluster ? undefined : handleFavorite}
                  deleteMsg={isCluster ? undefined : deleteMsg}
                  reportMsg={isCluster ? undefined : reportMsg}
                  regenerateMaxCount={5}
                  regenerateMessages={isCluster ? undefined : regenerateMessages}
                  handleVersionChange={isCluster ? undefined : handleVersionChange}
                />
              </Flex>
            ))}
          </div>
          <div className="rb:relative rb:flex rb:items-center rb:gap-2.5 rb:mt-4">
            <ChatInput
              message={message}
              className="rb:relative! rb:bottom-0!"
              loading={loading}
              fileChange={(list) => {
                setFileList(list || [])
                toolbarRef.current?.setFiles(list || [])
              }}
              fileList={fileList}
              onSend={isCluster ? handleClusterSend : handleSend}
            >
              <ChatToolbar
                ref={toolbarRef}
                features={features}
                onFilesChange={setFileList}
                leftExtra={
                  chatVariables && chatVariables.length > 0 ?(
                    <Tooltip title={t('memoryConversation.variableConfig')}>
                      <Flex justify="center" align="center"
                        className={clsx("rb:size-7 rb:border rb:cursor-pointer rb:hover:bg-[#F6F6F6] rb:rounded-full rb:shadow-[0px_2px_12px_0px_rgba(23,23,25,0.12)]", {
                          'rb:border-[#FF5D34]': isNeedVariableConfig,
                          'rb:border-[#EBEBEB]': !isNeedVariableConfig,
                        })}
                        onClick={handleEditVariables}
                      >
                        <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/conversation/variables.svg')]" />
                      </Flex>
                    </Tooltip>
                  ): null
                }
              />
            </ChatInput>
          </div>
        </>
      }
      {id && <ReportModal ref={reportModalRef} appId={id} />}
    </Flex>
  )
}

export default Chat;
