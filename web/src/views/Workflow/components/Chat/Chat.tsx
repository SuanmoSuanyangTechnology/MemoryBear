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
import { App, Flex, Button, type ButtonProps } from 'antd'
import clsx from 'clsx'

import ChatIcon from '@/assets/images/application/chat.png'
import { draftRun, appInterventionsSubmit, draftRunRegenerate, draftRunSwitchMessageVersion, draftRunFavoriteMessage } from '@/api/application';
import Empty from '@/components/Empty'
import ChatContent from '@/components/Chat/ChatContent'
import type { ChatItem, Intervention } from '@/components/Chat/types'
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
import type { LogItem } from '@/views/ApplicationConfig/types'

type Data = LogItem & {
  messages: Array<ChatItem | ChatItem[]>;
}

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

/**
 * Typed payload carried by a workflow SSE message
 */
type StreamEventData = {
  content: string;
  message_id?: string;
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
  status?: 'completed' | 'failed' | 'running' | 'waiting_human';
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
  }[];
  actions?: {
    id: string;
    label: string;
    variant: ButtonProps['type'];
  }[];
  timeout_at?: number;
  agent_log?: Record<string, any>;
};

/**
 * Apply an updater to the latest version of the last chat entry, preserving the
 * single-message / version-array structure.
 */
const mapLastVersion = (
  list: Array<ChatItem | ChatItem[]>,
  updater: (item: ChatItem) => ChatItem
): Array<ChatItem | ChatItem[]> => {
  if (list.length === 0) return list
  const lastIndex = list.length - 1
  const lastEntry = list[lastIndex]
  if (Array.isArray(lastEntry)) {
    const newVersions = [...lastEntry]
    newVersions[newVersions.length - 1] = updater(lastEntry[lastEntry.length - 1])
    return [...list.slice(0, lastIndex), newVersions]
  }
  return [...list.slice(0, lastIndex), updater(lastEntry)]
}

/**
 * Flatten a chat list into a single array of messages, expanding version arrays.
 */
const flattenChatList = (list: Array<ChatItem | ChatItem[]>): ChatItem[] =>
  list.flatMap((item) => (Array.isArray(item) ? item : [item]))

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
    if (toolbarReady || !isWorkflow) {
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
   * Routes each SSE message to the matching per-event logic.
   *
   * Events:
   * - workflow_start: capture message_id onto the last assistant message
   * - message / message_replace: streaming text for the final output
   * - intervention_required / intervention_timeout: human-in-the-loop lifecycle
   * - node_start / node_end / node_error: per-node execution tracking
   * - cycle_item: appends a finished item to its parent cycle node
   * - agent_log: merges agent iteration logs
   * - workflow_end: finalises the assistant message
   */
  const handleStreamMessage = useCallback((data: SSEMessage[]) => {
    data.forEach(item => {
      const payload = item.data as StreamEventData
      const { event } = item
      const { conversation_id, message_id } = payload
      const ctx = getNodeContext(payload.node_id)

      switch (event) {
        case 'workflow_start':
          if (!message_id) break
          setChatList(prev => mapLastVersion(prev, (current) => ({
            ...current,
            id: message_id,
          })))
          break
        case 'message':
          appendStreamContent(payload.content)
          break
        case 'message_replace':
          replaceStreamContent(payload.content)
          break
        case 'intervention_required': {
          const { name, icon, type } = ctx
          const { node_id } = payload
          const {
            execution_id, node_name, rendered_content, form_fields, actions, timeout_at,
          } = payload
          setChatList(prev => mapLastVersion(prev, (current) => {
            const subContent = [...(current.subContent || [])]
            const filterIndex = subContent.findIndex(vo => vo.id === node_id)
            const baseNode = {
              node_id,
              node_name: name,
              node_type: type,
              icon,
              status: 'waiting_human' as const,
              content: {},
            }
            if (filterIndex > -1) {
              subContent[filterIndex] = { ...subContent[filterIndex], ...baseNode }
            } else {
              subContent.push({ ...baseNode, id: node_id, meta_data: { waiting_human: true } })
            }
            return {
              ...current,
              status: 'waiting_human',
              subContent,
              meta_data: { ...current.meta_data, waiting_human: true },
              interventions: [
                ...(current.interventions || []),
                {
                  execution_id,
                  node_id,
                  node_name: node_name || name,
                  rendered_content,
                  form_fields: form_fields || [],
                  actions: actions || [],
                  timeout_at,
                },
              ],
            }
          }))
          break
        }
        case 'intervention_timeout':
          setChatList(prev => mapLastVersion(prev, (current) => {
            const interventions = current.interventions || []
            const filterIndex = interventions.findIndex((it: Intervention) => it.node_id === payload.node_id)
            if (filterIndex < 0) return current
            return {
              ...current,
              interventions: interventions.map((it: Intervention, idx: number) =>
                idx === filterIndex
                  ? { ...it, resolved_action_id: '__timeout__', resolved_kind: 'timeout' }
                  : it
              ),
            }
          }))
          break
        case 'node_start': {
          const { name, icon, type } = ctx
          const { execution_id, node_id } = payload
          setChatList(prev => mapLastVersion(prev, (current) => {
            const subContent = [...(current.subContent || [])]
            const filterIndex = subContent.findIndex(vo => vo.id === node_id)
            const baseNode = {
              execution_id,
              node_id,
              node_name: name,
              node_type: type,
              icon,
              status: 'running' as const,
              content: {},
            }
            if (filterIndex > -1) {
              subContent[filterIndex] = { ...subContent[filterIndex], ...baseNode }
            } else {
              subContent.push({ ...baseNode, id: node_id })
            }
            return { ...current, status: 'running', subContent }
          }))
          break
        }
        case 'node_end':
        case 'node_error':
          setChatList(prev => mapLastVersion(prev, (current) => {
            const subContent = [...(current.subContent || [])]
            const filterIndex = subContent.findIndex(vo => vo.node_id === payload.node_id)
            if (filterIndex < 0 || !subContent[filterIndex].content) {
              return { ...current, subContent }
            }
            subContent[filterIndex] = {
              ...subContent[filterIndex],
              content: {
                input: payload.input,
                output: payload.output,
                process: payload.process,
                error: payload.error,
              },
              status: payload.status || 'completed',
              elapsed_time: payload.elapsed_time,
            }
            return { ...current, subContent }
          }))
          break
        case 'cycle_item': {
          const { name, icon, type } = ctx
          const {
            cycle_id, cycle_idx, node_id,
            input, output, process, error, status, elapsed_time,
          } = payload
          setChatList(prev => mapLastVersion(prev, (current) => {
            const subContent = [...(current.subContent || [])]
            const filterIndex = subContent.findIndex(vo => vo.id === cycle_id)
            if (filterIndex < 0) return current
            const items = [...(subContent[filterIndex].subContent || [])]
            items.push({
              cycle_id,
              cycle_idx,
              node_id,
              node_name: type === 'cycle-start' ? t('workflow.cycle-start') : name,
              node_type: type,
              icon,
              content: { cycle_idx, input, output, process, error },
              status: status || 'completed',
              elapsed_time,
            })
            subContent[filterIndex] = { ...subContent[filterIndex], subContent: items }
            return { ...current, subContent }
          }))
          break
        }
        case 'agent_log':
          setChatList(prev => mapLastVersion(prev, (current) => {
            const subContent = [...(current.subContent || [])]
            const filterIndex = subContent.findIndex(vo => vo.node_id === payload.node_id)
            if (filterIndex < 0) return { ...current, subContent }
            const lastAgentLog = subContent[filterIndex].agent_log || {}
            const lastIterations = lastAgentLog?.iterations || []
            const newIterations = payload.agent_log?.iterations || []
            const indexMap = new Map<number, any>()
            lastIterations.forEach((item: any) => indexMap.set(item.index, item))
            newIterations.forEach((item: any) => indexMap.set(item.index, item))
            const mergedIterations = Array.from(indexMap.values()).sort((a, b) => a.index - b.index)
            subContent[filterIndex] = {
              ...subContent[filterIndex],
              agent_log: {
                ...lastAgentLog,
                meta: payload.agent_log?.meta || {},
                iterations: mergedIterations,
              },
            }
            return { ...current, subContent }
          }))
          break
        case 'workflow_end': {
          const { execution_id, status, error, citations } = payload
          setChatList(prev => mapLastVersion(prev, (current) => ({
            ...current,
            status,
            error,
            content: current.content === '' ? null : current.content,
            meta_data: { ...(current.meta_data || {}), citations },
          })))
          setStreamLoading(false)
          setLoading(false)
          if (execution_id && executionId !== execution_id) {
            executionIdRef.current = execution_id
            setExecutionId(execution_id)
          }
          chatIsEnded.current = true
          break
        }
      }

      if (conversation_id && conversationId !== conversation_id) {
        setConversationId(conversation_id)
      }
    })
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
        setChatList(prev => mapLastVersion(prev, (current) => {
          const interventions = current.interventions || []
          if (interventions.length === 0) return current
          const filterIndex = interventions.findIndex((item: Intervention) => item.node_id === node_id)
          if (filterIndex < 0) return current
          return {
            ...current,
            interventions: interventions.map((it: Intervention, idx: number) =>
              idx === filterIndex
                ? { ...it, resolved_form_data: fieldValues, resolved_action_id: actionId }
                : it
            ),
          }
        }))
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
        const first = prev[0]
        if (first && !Array.isArray(first) && first.role === 'assistant') {
          prev[0] = assistantMsg
        }
        return [...prev]
      })
    }
  }, [chatList.length, features?.opening_statement, variables])

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
    let isCanSend = true
    const params: Record<string, any> = {}
    const trigger_payload: Record<string, any> = {}
    if (variables.length > 0) {
      const needRequired: string[] = []
      const normalVariables = variables.filter(item => !item.nodeType)
      const webhookTriggerVariables = variables.filter(item => item.nodeType === 'webhook')
      normalVariables.forEach(v => {
        params[v.name] = v.value ?? v.defaultValue
        if (v.required && (params[v.name] === null || params[v.name] === undefined || params[v.name] === '')) {
          isCanSend = false
          needRequired.push(v.name)
        }
      })
      webhookTriggerVariables.forEach(v => {
        const nameList = v.name.split('.')
        if (!trigger_payload[nameList[0]]) {
          trigger_payload[nameList[0]] = {}
        }
        trigger_payload[nameList[0]][nameList[1]] = v.value
      })
      if (needRequired.length) {
        messageApi.error(`${needRequired.join(',')} ${t('workflow.variableRequired')}`)
      }
    }
    if (!isCanSend) return

    // Append a new version of the assistant message to the chat list
    // and drop everything that originally followed it.
    setChatList(prev => {
      const filterIndex = prev.findIndex((item) =>
        Array.isArray(item)
          ? item.some((v) => v.id === vo.id)
          : item.id === vo.id
      )
      if (filterIndex === -1) return prev
      const newList = prev.slice(0, filterIndex + 1)
      const existingEntry = newList[filterIndex]
      const newVersion: ChatItem = {
        role: 'assistant',
        content: '',
        created_at: Date.now(),
        subContent: [],
        is_current: true,
      }
      if (Array.isArray(existingEntry)) {
        const nextVersion = existingEntry.length + 1
        newList[filterIndex] = [
          ...existingEntry.map((v) => ({ ...v, is_current: false })),
          { ...newVersion, version: nextVersion },
        ]
      } else {
        newList[filterIndex] = [
          { ...existingEntry, is_current: false, version: 1 },
          { ...newVersion, version: 2 },
        ]
      }
      return newList
    })

    setLoading(true)
    setStreamLoading(true)
    chatIsEnded.current = false
    draftRunRegenerate(appId, vo.id, handleStreamMessage, abort => { abortRef.current = abort })
      .catch(() => {
        setChatList(prev => {
          const lastEntry = prev[prev.length - 1]
          if (!lastEntry || !Array.isArray(lastEntry)) return prev
          return mapLastVersion(prev, (current) => ({
            ...current,
            status: 'failed',
            content: null,
          }))
        })
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
        const { node_executions_map, messages = [], pending_intervention } = res as Data
        const enrichMessage = (msg: any) => {
          const base = { ...msg, status: msg.role === 'user' ? null : msg.status }
          if (!msg.id || !(node_executions_map?.[msg.id] || pending_intervention?.[msg.id])) {
            return base
          }
          const executions = node_executions_map?.[msg.id] || []
          const interventions = pending_intervention?.[msg.id]?.interventions || []
          const lastExecution = executions?.[executions.length - 1]
          const meta = lastExecution?.meta
          let sourceList: any[] = []

          if (meta?.sources?.length > 0 && (meta?.tool_type === 'knowledge_retrieval' || meta?.tool_type === 'skill')) {
            const groupedSources = meta?.sources.reduce((acc: any, source: any) => {
              const key = source.knowledge_name || source.knowledge_id || source.name || source.id || 'default';
              if (!acc[key]) {
                acc[key] = { ...source, name: source.knowledge_name || source.name, contentList: [source.content] };
              } else {
                acc[key].contentList.push(source.content);
              }
              return acc;
            }, {});
            sourceList = Object.values(groupedSources).map((group: any) => ({
              ...lastExecution,
              icon: getNodeContext(lastExecution?.node_id).icon,
              node_name: group.name,
              content: {
                input: lastExecution?.input || '',
                output: group.contentList.join('\n') || lastExecution?.output,
                error: lastExecution?.error
              }
            }));
          } else if (meta?.sources?.length > 0) {
            sourceList = meta?.sources?.map((source: any) => ({
              ...lastExecution,
              icon: getNodeContext(lastExecution?.node_id).icon,
              name: source.name || 'default',
              content: {
                input: lastExecution?.input || '',
                output: source.content || lastExecution?.output,
                error: lastExecution?.error
              }
            }));
          } else {
            sourceList = executions.map(({ input, output, cycle_items = [], error, process, ...node }: any) => {
              const converted: any = { ...node, icon: getNodeContext(node?.node_id).icon, content: { input, output, process, error } }
              if (node.node_type === 'loop' && Array.isArray(cycle_items) && cycle_items.length > 0) {
                converted.subContent = cycle_items.map(({ input: cInput, output: cOutput, error: cError, process: cProcess, ...cNode }: any) => ({
                  ...cNode,
                  icon: getNodeContext(cNode?.node_id).icon,
                  content: { input: cInput, output: cOutput, process: cProcess, error: cError }
                }))
              }
              return converted
            })
          }
          return { ...base, subContent: sourceList, interventions }
        }
        const hasSubContentMessages = messages.map((item: any) =>
          Array.isArray(item) ? item.map(enrichMessage) : enrichMessage(item)
        )
        setChatList(hasSubContentMessages)
        messageApi.success(t('common.operateSuccess'))
      })
  }
  const handleFavorite = (id?: string) => {
    if (!appId || !id) return
    draftRunFavoriteMessage(appId, id)
      .then((res) => {
        const { is_favorited } = res as { is_favorited: boolean; }
        messageApi.success(t('common.operateSuccess'))
        setChatList(prev => {
          const lastList = [...prev]
          const filterIndex = lastList.findIndex(item => Array.isArray(item) ? item.filter(msg => msg.id === id).length > 0 : item.id === id)
          if (filterIndex === -1) return lastList
          const filterItem = lastList[filterIndex]
          if (Array.isArray(filterItem)) {
            filterItem.forEach(msg => {
              if (msg.id === id) {
                msg.is_favorited = is_favorited
              }
            })
          } else {
            filterItem.is_favorited = is_favorited
          }
          lastList[filterIndex] = filterItem
          return [...lastList]
        })
      })
  }

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
      </RbCard>
    </div>
  )
})

export default Chat
