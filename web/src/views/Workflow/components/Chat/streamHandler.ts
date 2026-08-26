import type { TFunction } from 'i18next'

import type { ChatItem } from '@/components/Chat/types'
import type { SSEMessage } from '@/utils/stream'
import { mapLastVersion } from './helpers'
import type { NodeContext, StreamEventData } from './types'
import {
  appendOutputByNodeId,
  finalizeOutputs,
} from '@/components/Chat/utils/messageOutputs'

export interface WorkflowStreamDeps {
  conversationId: string | null;
  executionId: string | null;
  getNodeContext: (node_id: string) => NodeContext;
  appendStreamContent: (content: string) => void;
  replaceStreamContent: (content: string, nodeId?: string) => void;
  setChatList: React.Dispatch<React.SetStateAction<Array<ChatItem | ChatItem[]>>>;
  setStreamLoading: (loading: boolean) => void;
  setLoading: (loading: boolean) => void;
  setConversationId: (id: string) => void;
  setExecutionId: (id: string) => void;
  executionIdRef: React.MutableRefObject<string>;
  chatIsEnded: React.MutableRefObject<boolean>;
  t: TFunction;
}

/**
 * Builds the SSE message handler that routes each workflow event to the matching
 * per-event reducer.
 *
 * Events:
 * - workflow_start: capture message_id onto the last assistant message and
 *   backfill user_message_id onto the pending user message
 * - message / message_replace: streaming text for the final output
 * - intervention_required / intervention_timeout: human-in-the-loop lifecycle
 * - node_start / node_end / node_error: per-node execution tracking
 * - cycle_item: appends a finished item to its parent cycle node
 * - agent_log: merges agent iteration logs
 * - workflow_end: finalises the assistant message
 */
export const createWorkflowStreamHandler = (deps: WorkflowStreamDeps) => {
  const {
    conversationId, executionId, getNodeContext, appendStreamContent, replaceStreamContent,
    setChatList, setStreamLoading, setLoading, setConversationId, setExecutionId,
    executionIdRef, chatIsEnded, t,
  } = deps

  return (data: SSEMessage[]) => {
    data.forEach(item => {
      const payload = item.data as StreamEventData
      const { event } = item
      const { conversation_id, message_id, user_message_id } = payload
      const ctx = payload.node_id ? getNodeContext(payload.node_id) : {}

      switch (event) {
        case 'workflow_start': {
          if (!message_id) break
          setChatList(prev => {
            // Backfill the real id onto the pending user message when provided.
            let next = prev
            if (user_message_id) {
              for (let i = prev.length - 1; i >= 0; i--) {
                const entry = prev[i]
                if (!Array.isArray(entry) && entry.role === 'user') {
                  next = prev.map((it, idx) =>
                    idx === i && !Array.isArray(it) ? { ...it, id: user_message_id } : it
                  )
                  break
                }
              }
            }
            return mapLastVersion(next, (current) => ({
              ...current,
              id: message_id,
            }))
          })
          break
        }
        case 'message':
          appendStreamContent(payload.content)
          setChatList(prev => appendOutputByNodeId(prev, payload.node_id, payload.content))
          break
        case 'message_replace':
          replaceStreamContent(payload.content, payload.node_id)
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
            const filterIndex = interventions.findIndex((it) => it.node_id === payload.node_id)
            if (filterIndex < 0) return current
            return {
              ...current,
              interventions: interventions.map((it, idx) =>
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
          setChatList(prev => finalizeOutputs(prev))
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
  }
}
