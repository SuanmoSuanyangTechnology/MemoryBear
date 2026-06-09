/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-06 21:10:56 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-06-05 19:57:15
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
import { draftRun, appInterventionsSubmit } from '@/api/application';
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
import { replaceVariables } from '@/views/ApplicationConfig/Agent';
import { useWorkflowStore } from '@/store/workflow';
import VariableConfigModal from '@/views/Workflow/components/Chat/VariableConfigModal'
import type { VariableConfigModalRef } from '@/views/Workflow/types'
import type { Application } from '@/views/ApplicationManagement/types'
import { triggerParams } from '../Properties/hooks/useVariableList'
import RbCard from '@/components/RbCard/Card'


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
  const { message: messageApi } = App.useApp()
  const { setChatHistory } = useWorkflowStore()
  const toolbarRef = useRef<ChatToolbarRef>(null)
  const abortRef = useRef<(() => void) | null>(null)
  const [toolbarReady, setToolbarReady] = useState(false)
  const toolbarCallbackRef = useCallback((node: ChatToolbarRef | null) => {
    (toolbarRef as React.MutableRefObject<ChatToolbarRef | null>).current = node
    setToolbarReady(!!node)
  }, [])
  const [loading, setLoading] = useState(false)
  const [chatList, setChatList] = useState<ChatItem[]>([])
  const [variables, setVariables] = useState<Variable[]>([])
  const [streamLoading, setStreamLoading] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [fileList, setFileList] = useState<any[]>([])
  const [message, setMessage] = useState<string | undefined>(undefined)
  const variableConfigModalRef = useRef<VariableConfigModalRef>(null)
  const [executionId, setExecutionId] = useState<string | null>(null)
  const executionIdRef = useRef<string>('draft')

  /**
   * Initializes chat with opening statement if configured
   */
  useEffect(() => {
    if (open && features?.opening_statement?.enabled && features?.opening_statement?.statement && features?.opening_statement?.statement.trim() !== '') {
      setChatList([{
        role: 'assistant',
        created_at: Date.now(),
        content: features?.opening_statement?.statement,
        meta_data: {
          suggested_questions: features?.opening_statement?.suggested_questions || []
        }
      }])
    } else {
      handleClose(false)
    }
    getVariables()
  }, [open])

  useEffect(() => {
    if (toolbarReady || appType === 'pure_workflow') {
      getVariables()
    }
  }, [toolbarReady, appType])
  /**
   * Extracts variables from the workflow's start node and merges with previous values
   */
  const getVariables = () => {
    const nodes = graphRef.current?.getNodes()
    const list = nodes?.map(node => node.getData()) || []
    const startNodes = list.filter(vo => vo.type === 'start')
    const webhookTriggerNodes = list.filter(vo => vo.type === 'trigger' && (vo.config?.trigger_type === 'webhook' || vo.config?.trigger_type?.defaultValue === 'webhook'))
    const allVariables: Variable[] = []
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
      allVariables.push(...curVariables)
    }
    if (webhookTriggerNodes.length) {
      webhookTriggerNodes.forEach(webhookTrigger => {
        const webhookVariables: Variable[] = []
        Object.keys(triggerParams).forEach(key => {
          const params = Array.isArray(webhookTrigger.config?.[key])
              ? webhookTrigger.config?.[key]
              : Array.isArray(webhookTrigger.config?.[key].defaultValue)
              ? webhookTrigger.config?.[key].defaultValue
              : []
          params.forEach((param: any) => {
            if (param?.name) {
              webhookVariables.push({
                name: [triggerParams[key], param.name].join('.'),
                // description: param.name,
                ui_type: 'paragraph',
                type: param.type || 'string',
                required: param.required || false,
                nodeType: 'webhook',
              })
            }
          })
        })
        if (webhookVariables.length) {
          webhookVariables.forEach((vo: Variable) => {
            const lastVo = variables.find(item => item.name === vo.name)
            if (lastVo?.value) {
              vo.value = lastVo.value
            }
          })
          allVariables.push(...webhookVariables)
        }
      })
    }
    console.log('startNodes', allVariables)
    setVariables([...allVariables])
    toolbarRef.current?.setVariables([...allVariables])
  }
  /**
   * Closes the drawer and resets all state
   */
  const handleClose = (flag: boolean = true) => {
    setChatHistory(executionIdRef.current, chatList.map((item: ChatItem) => ({
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

    if (flag) {
      onOpenChange?.(false)
    }
  }

  const handleSend = (msg?: string) => {
    handleSave(false)
      .then(() => {
        handleSendMsg(msg)
      })
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
  const handleSendMsg = async (msg?: string) => {
    if (loading || !appId) return
    // Validate required variables before sending
    let isCanSend = true
    const params: Record<string, any> = {}
    const trigger_payload: Record<string, any> = {}
    if (variables.length > 0) {
      const needRequired: string[] = []
      const normalVariables = variables.filter(item => !item.nodeType)
      const webhookTriggerVariables = variables.filter(item => item.nodeType === 'webhook')
      normalVariables.forEach(vo => {
        params[vo.name] = vo.value ?? vo.defaultValue

        if (vo.required && (params[vo.name] === null || params[vo.name] === undefined || params[vo.name] === '')) {
          isCanSend = false
          needRequired.push(vo.name)
        }
      })
      webhookTriggerVariables.forEach(vo => {
        const nameList = vo.name.split('.')
        if (!trigger_payload[nameList[0]]) {
          trigger_payload[nameList[0]] = {}
        }
        trigger_payload[nameList[0]][nameList[1]] = vo.value
      })

      if (needRequired.length) {
        messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
      }
    }
    if (!isCanSend) {
      return
    }

    const message = msg
    const files = (toolbarRef.current?.getFiles() || []).filter(item => !['uploading', 'error'].includes(item.status))

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
        const {
          execution_id, content, conversation_id, node_id, node_name, cycle_id, cycle_idx,
          input, output, process, error, elapsed_time, status, citations,
          rendered_content, form_fields, actions, timeout_at,
          agent_log
        } = item.data as {
          content: string;
          execution_id?: string;
          conversation_id: string | null;
          cycle_id: string;
          cycle_idx: number;
          node_id: string;
          node_name?: string;
          node_type?: string;
          process?: any;
          input?: any;
          output?: any;
          elapsed_time?: string;
          error?: any;
          state: Record<string, any>;
          status?: 'completed' | 'failed' | 'running' | 'waiting_human',
          citations?: {
            document_id: string;
            file_name: string;
            knowledge_id: string;
            score: string;
          }[];
          rendered_content?: string;
          form_fields?: {
            id: string;
            default_value?: string;
          }[]
          actions?: {
            id: string;
            label: string;
            variant: string;
          }[];
          timeout_at?: number;
          agent_log?: Record<string, any>;
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
          case 'message_replace':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  content: content
                }
              }
              return newList
            })
            break;
          case 'intervention_required':
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
                    status: 'waiting_human',
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    node_type: type,
                    icon,
                    status: 'waiting_human',
                    content: {},
                    meta_data: {
                      waiting_human: true,
                    },
                  })
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  status: 'waiting_human',
                  subContent: newSubContent,
                  meta_data: {
                    ...newList[lastIndex].meta_data,
                    waiting_human: true
                  },
                  interventions: [
                    ...(newList[lastIndex].interventions || []),
                    {
                      execution_id,
                      node_id: node_id,
                      node_name: node_name || name,
                      rendered_content,
                      form_fields: form_fields || [],
                      actions: actions || [],
                      timeout_at,
                    }
                  ]
                }
              }
              return newList
            })
            break;
          case 'intervention_timeout':
            setChatList(prev => {
              const lastMsg = prev[prev.length - 1]
              if (!lastMsg?.interventions || lastMsg.interventions.length === 0) {
                return prev
              }

              const filterIndex = lastMsg.interventions.findIndex(item => item.node_id === node_id)
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
                    execution_id,
                    node_id: node_id,
                    node_name: name,
                    node_type: type,
                    icon,
                    status: 'running',
                    content: {},
                  }
                } else {
                  newSubContent.push({
                    execution_id,
                    id: node_id,
                    node_id: node_id,
                    node_name: name,
                    node_type: type,
                    icon,
                    status: 'running',
                    content: {},
                  })
                }
                newList[lastIndex] = {
                  ...newList[lastIndex],
                  status: 'running',
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
                      process,
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
                    node_name: type === 'cycle-start' ? t('workflow.cycle-start') : name,
                    node_type: type,
                    icon,
                    content: {
                      cycle_idx,
                      input,
                      output,
                      process,
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
          case 'agent_log':
            setChatList(prev => {
              const newList = [...prev]
              const lastIndex = newList.length - 1
              if (lastIndex >= 0) {
                const newSubContent = newList[lastIndex].subContent || []
                const filterIndex = newSubContent.findIndex(vo => vo.node_id === node_id)
                if (filterIndex > -1) {
                  const lastAgentLog = newSubContent[filterIndex].agent_log || {}
                  newSubContent[filterIndex].agent_log = {
                    ...lastAgentLog,
                    meta: agent_log?.meta || {},
                    iterations: [
                      ...(lastAgentLog?.iterations || []),
                      ...agent_log?.iterations || []
                    ],
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
                  meta_data: {
                    ...newList[lastIndex].meta_data || {},
                    citations
                  }
                }
              }
              return newList
            })
            setStreamLoading(false)
            setLoading(false)
            console.log('execution_id', execution_id, executionId)
            if (execution_id && executionId !== execution_id) {
              executionIdRef.current = execution_id
              setExecutionId(execution_id)
            }
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
    if (appType === 'pure_workflow') {
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
        }
      ])
    }
    setLoading(true)
    setStreamLoading(true)
    draftRun(appId, data, handleStreamMessage, abort => { abortRef.current = abort })
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
        refreshCache()
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
        setChatList(prev => {
          const lastMsg = prev[prev.length - 1]
          if (!lastMsg?.interventions || lastMsg.interventions.length === 0) {
            return prev
          }

          const filterIndex = lastMsg.interventions.findIndex(item => item.node_id === node_id)
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
        })
      })
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  useEffect(() => {
    const opening_statement = features?.opening_statement

    if (opening_statement?.enabled && opening_statement?.statement && opening_statement?.statement.trim() !== '') {
      const assistantMsg: ChatItem = {
        role: 'assistant',
        content: replaceVariables(opening_statement.statement, variables as any),
        meta_data: {
          suggested_questions: opening_statement?.suggested_questions
        }
      }
      setChatList(prev => {
        if (prev[0]?.role === 'assistant') {
          prev[0] = assistantMsg
        }
        return [...prev]
      })
    }
  }, [chatList.length, features?.opening_statement, variables])

  useEffect(() => {
    if (chatList.length < 1) return
    setChatHistory(executionIdRef.current, chatList)
  }, [chatList])

  // True when any required variable is missing a value, used to highlight the config button
  const isNeedVariableConfig = variables?.some(
    vo => vo.required && (vo.value === null || vo.value === undefined || vo.value === '')
  )

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
      />

        {appType === 'workflow' &&
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
        {appType === 'pure_workflow' &&
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
      </RbCard>
    </div>
  )
})

export default Chat
