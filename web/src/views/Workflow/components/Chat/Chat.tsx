/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:10:56 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-06 21:10:56 
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
import { forwardRef, useImperativeHandle, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { App, Space, Button, Collapse, Flex, Dropdown, type MenuProps } from 'antd'
import { CheckCircleFilled, CloseCircleFilled, LoadingOutlined } from '@ant-design/icons'
import CodeBlock from '@/components/Markdown/CodeBlock'

import ChatIcon from '@/assets/images/application/chat.png'
import RbDrawer from '@/components/RbDrawer';
import VariableConfigModal from './VariableConfigModal'
import { draftRun } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem } from '@/components/Chat/types'
import dayjs from 'dayjs'
import type { ChatRef, VariableConfigModalRef, GraphRef } from '../../types'
import { type SSEMessage } from '@/utils/stream'
import type { Variable } from '../Properties/VariableList/types'
import styles from './chat.module.css'
import Markdown from '@/components/Markdown'
import ChatInput from '@/components/Chat/ChatInput'
import UploadFiles from '@/views/Conversation/components/FileUpload'
// import AudioRecorder from '@/components/AudioRecorder'
import UploadFileListModal from '@/views/Conversation/components/UploadFileListModal'
import type { UploadFileListModalRef } from '@/views/Conversation/types'

const Chat = forwardRef<ChatRef, { appId: string; graphRef: GraphRef }>(({ appId, graphRef }, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  // State management
  const [open, setOpen] = useState(false) // Drawer visibility
  const [loading, setLoading] = useState(false) // Send button loading state
  const [chatList, setChatList] = useState<ChatItem[]>([]) // Chat message history
  const [variables, setVariables] = useState<Variable[]>([]) // Workflow input variables
  const [streamLoading, setStreamLoading] = useState(false) // SSE streaming state
  const [conversationId, setConversationId] = useState<string | null>(null) // Current conversation ID
  const [fileList, setFileList] = useState<any[]>([]) // Uploaded files
  const [message, setMessage] = useState<string | undefined>(undefined) // Current input message
  const uploadFileListModalRef = useRef<UploadFileListModalRef>(null)

  /**
   * Opens the chat drawer and loads workflow variables from the start node
   */
  const handleOpen = () => {
    setOpen(true)
    getVariables()
  }
  /**
   * Extracts variables from the workflow's start node and merges with previous values
   */
  const getVariables = () => {
    const nodes = graphRef.current?.getNodes()
    const list = nodes?.map(node => node.getData()) || []
    const startNodes = list.filter(vo => vo.type === 'start')
    if (startNodes.length) {
      const curVariables = startNodes[0].config.variables?.defaultValue

      curVariables.forEach((vo: Variable) => {
        if (typeof vo.default !== 'undefined') {
          vo.value = vo.default
        }
        const lastVo = variables.find(item => item.name === vo.name)
        if (lastVo?.value) {
          vo.value = lastVo.value
        }
      })
      setVariables(curVariables)
    }
  }
  /**
   * Closes the drawer and resets all state
   */
  const handleClose = () => {
    setOpen(false)
    setChatList([])
    setVariables([])
    setConversationId(null)
  }
  /**
   * Opens the variable configuration modal
   */
  const handleEditVariables = () => {
    variableConfigModalRef.current?.handleOpen(variables)
  }
  /**
   * Saves updated variable values from the modal
   */
  const handleSave = (values: Variable[]) => {
    setVariables([...values])
  }
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
  const handleSend = async (msg?: string) => {
    if (loading || !appId) return
    // Validate required variables before sending
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
    if (!isCanSend) {
      return
    }

    // setLoading(true)
    const message = msg
    setChatList(prev => [...prev, {
      role: 'user',
      content: message,
      created_at: Date.now(),
    }])
    setChatList(prev => [...prev, {
      role: 'assistant',
      content: '',
      created_at: Date.now(),
      subContent: [],
    }])

    /**
     * Handles SSE stream messages from workflow execution
     * 
     * Events:
     * - message: Streaming text chunks for final output
     * - node_start: Node execution begins
     * - node_end: Node execution completes successfully
     * - node_error: Node execution fails
     * - workflow_end: Entire workflow completes
     */
    const handleStreamMessage = (data: SSEMessage[]) => {
      data.forEach(item => {
        const { chunk, conversation_id, node_id, input, output, error, elapsed_time, status } = item.data as {
          chunk: string;
          conversation_id: string | null;
          node_id: string;
          node_name?: string;
          input?: any;
          output?: any;
          elapsed_time?: string;
          error?: any;
          state: Record<string, any>;
          status?: 'completed' | 'failed'
        };

        const node = graphRef.current?.getNodes().find(n => n.id === node_id);
        const { name, icon } = node?.getData() || {}

        console.log('node', node?.getData())

        switch(item.event) {
          // Append streaming text chunks to assistant message
          case 'message':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: newList[lastIndex].content + chunk
                }
              }
              return newList
            })
            break
          // Track node execution start
          case 'node_start':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.id === node_id)
                if (filterIndex > -1) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    icon,
                    content: {},
                  })
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          // Update node with execution results or errors
          case 'node_end':
          case 'node_error':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
                if (filterIndex > -1 && newSubContent[filterIndex].content) {
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    content: {
                      input,
                      output,
                      error,
                    },
                    status: status || 'completed',
                    elapsed_time
                  }
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  subContent: newSubContent
                }
              }
              return newList
            })
            break
          // Mark workflow as complete
          case 'workflow_end':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  status,
                  content: newList[lastIndex].content === '' ? null : newList[lastIndex].content
                }
              }
              return newList
            })
            setStreamLoading(false)
            break
        }

        if (conversation_id && conversationId !== conversation_id) {
          setConversationId(conversation_id)
        }
      })
    }

    setMessage(undefined)
    setFileList([])
    const data = {
      message: message,
      variables: params,
      stream: true,
      conversation_id: conversationId,
      files: fileList.map(file => {
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
    setStreamLoading(true)
    draftRun(appId, data, handleStreamMessage)
      .catch((error) => {
        setChatList(prev => {
          const newList = [...prev]
          const lastIndex = newList.length - 1
          if (lastIndex >= 0) {
            newList[lastIndex] = {
              ...newList[lastIndex],
              status: 'failed',
              content: null,
              subContent: error.error
            }
          }
          return newList
        })
      }).finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }

  /**
   * Updates the current input message
   */
  const handleMessageChange = (message: string) => {
    setMessage(message)
  }
  const [update, setUpdate] = useState(false)
  /**
   * Handles file upload from local device
   */
  const fileChange = (file?: any) => {
    setFileList([...fileList, file])
    setUpdate(prev => !prev)
  }
  // const handleRecordingComplete = async (file: any) => {
  //   console.log('file', file)
  // }

  /**
   * Handles dropdown menu actions for file upload
   */
  const handleShowUpload: MenuProps['onClick'] = ({ key }) => {
    switch(key) {
      case 'define':
        uploadFileListModalRef.current?.handleOpen()
        break
    }
  }
  /**
   * Adds files from remote URL modal
   */
  const addFileList = (list?: any[]) => {
    if (!list || list.length <= 0) return
    setFileList([...fileList, ...(list || [])])
  }
  /**
   * Updates the entire file list (used when removing files)
   */
  const updateFileList = (list?: any[]) => {
    setFileList([...list || []])
  }

  // Expose methods to parent component via ref
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  /**
   * Returns CSS class for status-based text color
   */
  const getStatus = (status?: string) => {
    return status === 'completed' ? 'rb:text-[#369F21]' : status === 'failed' ? 'rb:text-[#FF5D34]' : 'rb:text-[#5B6167]'
  }
  return (
    <RbDrawer
      title={<div className="rb:flex rb:items-center rb:gap-2.5">
        {t('workflow.run')}
        {variables.length > 0 && <Space>
          <Button size="small" onClick={handleEditVariables}>{t('application.variable')}</Button>
        </Space>}
      </div>}
      classNames={{
        body: 'rb:p-0!'
      }}
      open={open}
      onClose={handleClose}
    >
      <ChatContent
        classNames="rb:mx-[16px] rb:pt-[24px] rb:h-[calc(100%-86px)]"
        contentClassNames="rb:max-w-[400px]!'"
        empty={<Empty url={ChatIcon} title={t('application.chatEmpty')} isNeedSubTitle={false} size={[240, 200]} className="rb:h-full" />}
        data={chatList}
        streamLoading={streamLoading}
        labelPosition="bottom"
        labelFormat={(item) => dayjs(item.created_at).locale('en').format('MMMM D, YYYY [at] h:mm A')}
        errorDesc={t('application.ReplyException')}
        renderRuntime={(item, index) => {
          return (
            <div key={index} className="rb:w-100 rb:mb-2">
              <Collapse
                className={styles[item.status || 'default']}
                items={[{
                  key: 0,
                  label: <div className={getStatus(item.status)}>
                    {item.status === 'completed' ? <CheckCircleFilled className="rb:mr-1" /> : item.status === 'failed' ? <CloseCircleFilled className="rb:mr-1" /> : <LoadingOutlined className="rb:mr-1" />}
                    {t('application.workflow')}
                  </div>,
                  className: styles.collapseItem,
                  children: (
                    Array.isArray(item.subContent)
                    ? <Space size={8} direction="vertical" className="rb:w-full!">
                      {item.subContent?.map(vo => (
                        <Collapse
                          key={vo.node_id}
                          items={[{
                            key: vo.node_id,
                            label: <div className={clsx("rb:flex rb:justify-between rb:items-center", getStatus(vo.status))}>
                              <div className="rb:flex rb:items-center rb:gap-1 rb:flex-1">
                                {vo.icon && <img src={vo.icon} className="rb:size-4" />}
                                <div className="rb:wrap-break-word rb:line-clamp-1">{vo.node_name || vo.node_id}</div>
                              </div>
                              <span>
                                {typeof vo.elapsed_time == 'number' && <>{vo.elapsed_time?.toFixed(3)}ms</>}
                                {vo.status === 'completed' ? <CheckCircleFilled className="rb:ml-1" /> : vo.status === 'failed' ? <CloseCircleFilled className="rb:ml-1" /> : <LoadingOutlined className="rb:ml-1" />}
                              </span>
                            </div>,
                            className: styles.collapseItem,
                            children: (
                              <Space size={8} direction="vertical" className="rb:w-full!">
                                {vo.status === 'failed' &&
                                  <div className={clsx("rb:bg-[#F0F3F8] rb:rounded-md", getStatus(vo.status))}>
                                    <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                                      {t(`workflow.error`)}
                                      <Button
                                        className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                        size="small"
                                      >{t('common.copy')}</Button>
                                    </div>
                                    <div className="rb:pb-2 rb:px-3 rb:max-h-40 rb:overflow-auto">
                                      <Markdown content={vo.content?.error || ''} />
                                    </div>
                                  </div>
                                }
                                {['input', 'output'].map(key => (
                                  <div key={key} className="rb:bg-[#F0F3F8] rb:rounded-md">
                                    <div className="rb:py-2 rb:px-3 rb:flex rb:justify-between rb:items-center rb:text-[12px]">
                                      {t(`workflow.${key}`)}
                                      <Button
                                        className="rb:py-0! rb:px-1! rb:text-[12px]!"
                                        size="small"
                                      >{t('common.copy')}</Button>
                                    </div>
                                    <div className="rb:max-h-40 rb:overflow-auto">
                                      <CodeBlock
                                        size="small"
                                        value={typeof vo.content === 'object' && vo.content?.[key] ? JSON.stringify(vo.content[key], null, 2) : '{}'}
                                        needCopy={false}
                                        showLineNumbers={true}
                                      />
                                    </div>
                                  </div>
                                ))}
                              </Space>
                            )
                          }]}
                        />
                      ))}
                    </Space>
                      : <div className={clsx("rb:bg-[#FBFDFF] rb:rounded-md rb:py-2 rb:px-3 ", getStatus('failed'))}>
                      <Markdown content={item.subContent || ''}  />
                    </div>
                  )
                }]}
              />
            </div>
          )
        }}
      />
      <div className="rb:relative rb:flex rb:items-center rb:gap-2.5 rb:m-4 rb:mb-1">
        <ChatInput
          message={message}
          className="rb:relative!"
          loading={loading}
          fileChange={updateFileList}
          fileList={fileList}
          onSend={handleSend}
          onChange={handleMessageChange}
        >
          <Flex justify="space-between" className="rb:flex-1">
            <Flex gap={8} align="center">
              <Dropdown
                menu={{
                  items: [
                    { key: 'define', label: t('memoryConversation.addRemoteFile') },
                    {
                      key: 'upload', label: (
                        <UploadFiles
                          fileType={['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'svg']}
                          onChange={fileChange}
                          fileList={[]}
                          update={update}
                        />
                      )
                    },
                  ],
                  onClick: handleShowUpload
                }}
              >
                <div
                  className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('src/assets/images/conversation/link.svg')] rb:hover:bg-[url('src/assets/images/conversation/link_hover.svg')]"
                ></div>
              </Dropdown>
            </Flex>
            {/* <Flex align="center">
              <AudioRecorder onRecordingComplete={handleRecordingComplete} />
              <Divider type="vertical" className="rb:ml-1.5! rb:mr-3!" />
            </Flex> */}
          </Flex>
        </ChatInput>
      </div>

      <VariableConfigModal
        ref={variableConfigModalRef}
        refresh={handleSave}
        variables={variables}
      />

      <UploadFileListModal
        ref={uploadFileListModalRef}
        refresh={addFileList}
      />
    </RbDrawer>
  )
})

export default Chat
