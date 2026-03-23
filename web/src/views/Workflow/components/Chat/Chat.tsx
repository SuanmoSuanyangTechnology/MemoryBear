/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:10:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 11:25:51
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
import { App, Flex } from 'antd'

import ChatIcon from '@/assets/images/application/chat.png'
import RbDrawer from '@/components/RbDrawer';
import { draftRun } from '@/api/application';
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

const Chat = forwardRef<ChatRef, { appId: string; graphRef: GraphRef; data: WorkflowConfig | null }>(({ appId, graphRef, data }, ref) => {
  const { t } = useTranslation()
  const { message: messageApi } = App.useApp()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const toolbarCallbackRef = useCallback((node: ChatToolbarRef | null) => {
    (toolbarRef as React.MutableRefObject<ChatToolbarRef | null>).current = node
  }, [])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<Variable[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const [features, setFeatures] = useState<FeaturesConfigForm>({} as FeaturesConfigForm)

  /**
   * Opens the chat drawer and loads workflow variables from the start node
   */
  const handleOpen = () => {
    setOpen(true)
  }

  useEffect(() => {
    if (data?.features && open) setFeatures(data.features)
  }, [open, data?.features])

  useEffect(() => {
    if (open && graphRef.current && toolbarRef.current) {
      getVariables()
    }
  }, [open])
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
      console.log('curVariables', curVariables)
      setVariables([...curVariables])
      toolbarRef.current?.setVariables([...curVariables])
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
    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    toolbarRef.current?.setVariables([])
    setFileList([])
    setLoading(false)
    setStreamLoading(false)
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

    setLoading(true)
    const message = msg
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))
    setChatList(prev => [...prev, {
      role: 'user',
      content: message,
      created_at: Date.now(),
      meta_data: {
        files
      },
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
        const { content, conversation_id, node_id, cycle_id, cycle_idx, input, output, error, elapsed_time, status } = item.data as {
          content: string;
          conversation_id: string | null;
          cycle_id: string;
          cycle_idx: number;
          node_id: string;
          node_name?: string;
          node_type?: string;
          input?: any;
          output?: any;
          elapsed_time?: string;
          error?: any;
          state: Record<string, any>;
          status?: 'completed' | 'failed'
        };

        const node = graphRef.current?.getNodes().find(n => n.id === node_id);
        const { name, icon, type } = node?.getData() || {}

        switch(item.event) {
          // Append streaming text chunks to assistant message
          case 'message':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: newList[lastIndex].content + content
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
                    node_type: type,
                    icon,
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    node_type: type,
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
          // Update node with subContent
          case 'cycle_item':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.id === cycle_id)
                if (filterIndex > -1) {
                  const items = newSubContent[filterIndex].subContent || []
                  items.push({
                    cycle_id,
                    cycle_idx,
                    node_id,
                    node_name: name,
                    node_type: type,
                    icon,
                    content: {
                      cycle_idx,
                      input,
                      output,
                      error,
                    },
                    status: status || 'completed',
                    elapsed_time
                  })
                  newSubContent[filterIndex] = {
                    ...newSubContent[filterIndex],
                    subContent: [...items]
                  }
                  newList[lastIndex] = {
                    ...newList[lastIndex],
                    subContent: newSubContent
                  }
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
                  error,
                  content: newList[lastIndex].content === '' ? null : newList[lastIndex].content,
                }
              }
              return newList
            })
            setStreamLoading(false)
            setLoading(false)
            break
        }

        if (conversation_id && conversationId !== conversation_id) {
          setConversationId(conversation_id)
        }
      })
    }

    setMessage(undefined)
    toolbarRef.current?.setFiles([])
    setFileList([])
    const data = {
      message: message,
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
    setStreamLoading(true)
    draftRun(appId, data, handleStreamMessage)
      .catch((error) => {
        const errorInfo = JSON.parse(error.message)
        setChatList(prev => {
          const newList = [...prev]
          const lastIndex = newList.length - 1
          if (lastIndex >= 0) {
            newList[lastIndex] = {
              ...newList[lastIndex],
              status: 'failed',
              content: null,
              subContent: errorInfo.error
            }
          }
          return newList
        })
      }).finally(() => {
        setLoading(false)
        setStreamLoading(false)
      })
  }

  const updateFileList = (list?: any[]) => {
    setFileList([...list || []])
    toolbarRef.current?.setFiles([...list || []])
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  return (
    <RbDrawer
      title={<Flex align="center" gap={10}>
        {t('workflow.run')}
      </Flex>}
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
          return <Runtime item={item} index={index} />
        }}
      />
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
            features={features}
            onFilesChange={setFileList}
            onVariablesChange={setVariables}
          />
        </ChatInput>
      </Flex>
    </RbDrawer>
  )
})

export default Chat
