import type { ChatItem } from '@/components/Chat/types'
import type { SSEMessage } from '@/utils/stream'

export interface AgentTrace {
  meta?: Record<string, unknown>;
  iterations: Array<{
    llm?: { output?: unknown; [key: string]: unknown };
    [key: string]: unknown;
  }>;
  [key: string]: unknown;
}

export interface ClusterEventData {
  conversation_id?: string;
  message_id?: string;
  user_message_id?: string;
  content?: string;
  message_length?: number;
  execution_id?: string | null;
  parent_execution_id?: string | null;
  orchestration_mode?: string | null;
  agent_id?: string | null;
  agent_name?: string | null;
  task?: unknown;
  status?: string;
  output?: unknown;
  elapsed_time?: number;
  token_usage?: Record<string, number>;
  error?: unknown;
  data?: unknown;
  [key: string]: unknown;
}

export interface ClusterAgentBlock extends Record<string, unknown> {
  node_id: string;
  node_type: 'agent';
  node_name?: string;
  status: string;
  elapsed_time?: number;
  token_usage?: Record<string, number>;
  agent_id?: string | null;
  execution_id?: string | null;
  parent_execution_id?: string | null;
  depth: number;
  orchestration_mode?: string | null;
  content: { input?: unknown; output?: unknown; error?: string };
  agent_log: AgentTrace;
}

export interface ClusterStreamAdapter {
  updateAssistant: (updater: (message: ChatItem) => ChatItem) => void;
  appendAssistantContent: (content?: string) => void;
  applyMessageId: (id?: string) => void;
  applyUserMessageId: (id?: string) => void;
  syncConversationId: (id?: string) => void;
  stopInitialLoading?: () => void;
  finishStreaming: () => void;
  applyLegacyEmpty?: (messageLength: number) => void;
}

const AGENT_OWNER_KEYS: Array<keyof ClusterEventData> = [
  'execution_id', 'parent_execution_id', 'orchestration_mode', 'agent_id', 'agent_name',
]

const EMPTY_AGENT_TRACE: AgentTrace = { meta: {}, iterations: [] }

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null

const hasAgentOwner = (payload: ClusterEventData) =>
  AGENT_OWNER_KEYS.some(key => Object.prototype.hasOwnProperty.call(payload, key))

const normalizeTrace = (payload: ClusterEventData): AgentTrace => {
  const trace = isRecord(payload.data) ? payload.data : payload
  return Array.isArray(trace.iterations)
    ? { ...trace, iterations: trace.iterations as AgentTrace['iterations'] }
    : { ...EMPTY_AGENT_TRACE }
}

const getErrorText = (error: unknown): string | undefined => {
  if (typeof error === 'string') return error
  if (isRecord(error) && typeof error.message === 'string') return error.message
  return undefined
}

const getTraceOutput = (trace: AgentTrace): unknown => {
  for (let index = trace.iterations.length - 1; index >= 0; index--) {
    const output = trace.iterations[index]?.llm?.output
    if (output !== undefined && output !== null && output !== '') return output
  }
  return undefined
}

const normalizeAgentStatus = (status?: string, error?: unknown) => {
  if (error || status === 'failed' || status === 'error') return 'failed'
  if (!status || status === 'success') return 'completed'
  return status
}

const findAgentBlockIndex = (
  blocks: ClusterAgentBlock[],
  payload: ClusterEventData,
) => {
  if (payload.execution_id) {
    const exactIndex = blocks.findIndex(block =>
      block.execution_id === payload.execution_id || block.node_id === payload.execution_id,
    )
    if (exactIndex !== -1) return exactIndex
  }

  const canClaim = (block: ClusterAgentBlock) => {
    const hasExecutionConflict = Boolean(
      payload.execution_id && block.execution_id && payload.execution_id !== block.execution_id,
    )
    return !hasExecutionConflict && block.status !== 'completed' && block.status !== 'failed'
  }

  if (payload.agent_id) {
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index]
      if (canClaim(block) && block.agent_id === payload.agent_id) return index
    }
  }

  if (payload.agent_name) {
    for (let index = blocks.length - 1; index >= 0; index--) {
      const block = blocks[index]
      const hasAgentConflict = Boolean(
        payload.agent_id && block.agent_id && payload.agent_id !== block.agent_id,
      )
      if (canClaim(block) && !hasAgentConflict && block.node_name === payload.agent_name) return index
    }
  }
  return -1
}

export const createClusterStreamProcessor = (adapter: ClusterStreamAdapter) => {
  let placeholderIndex = 0

  const updateAgentBlock = (
    payload: ClusterEventData,
    updater: (block: ClusterAgentBlock) => ClusterAgentBlock,
  ) => {
    adapter.updateAssistant(message => {
      const blocks = [...(message.subContent || [])] as ClusterAgentBlock[]
      let targetIndex = findAgentBlockIndex(blocks, payload)
      if (targetIndex === -1) {
        const identity = payload.agent_id || payload.agent_name || 'unknown'
        blocks.push({
          node_id: payload.execution_id || `cluster-agent-${identity}-${++placeholderIndex}`,
          node_type: 'agent',
          node_name: payload.agent_name || undefined,
          status: 'running',
          agent_id: payload.agent_id,
          execution_id: payload.execution_id,
          parent_execution_id: payload.parent_execution_id,
          depth: 1,
          orchestration_mode: payload.orchestration_mode,
          content: {},
          agent_log: { ...EMPTY_AGENT_TRACE },
        })
        targetIndex = blocks.length - 1
      }

      const current = blocks[targetIndex]
      const executionId = payload.execution_id || current.execution_id
      blocks[targetIndex] = updater({
        ...current,
        node_id: executionId || current.node_id,
        node_name: payload.agent_name || current.node_name,
        agent_id: payload.agent_id !== undefined ? payload.agent_id : current.agent_id,
        execution_id: executionId,
        parent_execution_id: payload.parent_execution_id !== undefined
          ? payload.parent_execution_id
          : current.parent_execution_id,
        orchestration_mode: payload.orchestration_mode !== undefined
          ? payload.orchestration_mode
          : current.orchestration_mode,
      })
      return { ...message, status: 'running', subContent: blocks }
    })
  }

  const finishCluster = (status: 'completed' | 'failed', error?: string) => {
    adapter.stopInitialLoading?.()
    adapter.updateAssistant(message => ({
      ...message,
      status,
      ...(error ? { error } : {}),
      subContent: message.subContent?.map(block =>
        block.status === 'running'
          ? { ...block, status: status === 'failed' ? 'failed' : 'completed' }
          : block,
      ),
    }))
    adapter.finishStreaming()
  }

  return (events: SSEMessage[]) => {
    events.forEach(item => {
      const payload = item.data as ClusterEventData
      const ownedByAgent = hasAgentOwner(payload)

      switch (item.event) {
        case 'start':
          if (ownedByAgent) break
          adapter.syncConversationId(payload.conversation_id)
          adapter.applyMessageId(payload.message_id)
          adapter.applyUserMessageId(payload.user_message_id)
          adapter.updateAssistant(message => ({
            ...message,
            status: 'running',
            subContent: message.subContent || [],
          }))
          break
        case 'message':
          adapter.stopInitialLoading?.()
          adapter.appendAssistantContent(payload.content)
          break
        case 'agent_dispatch':
          updateAgentBlock(payload, block => ({
            ...block,
            status: 'running',
            content: { ...block.content, input: payload.task },
          }))
          break
        case 'agent_log':
        case 'agent_log_final':
          updateAgentBlock(payload, block => ({ ...block, agent_log: normalizeTrace(payload) }))
          break
        case 'agent_complete': {
          const error = getErrorText(payload.error)
          updateAgentBlock(payload, block => ({
            ...block,
            status: normalizeAgentStatus(payload.status, payload.error),
            ...(typeof payload.elapsed_time === 'number' ? { elapsed_time: payload.elapsed_time } : {}),
            ...(payload.token_usage ? { token_usage: payload.token_usage } : {}),
            content: {
              ...block.content,
              output: payload.output ?? getTraceOutput(block.agent_log),
              ...(error ? { error } : {}),
            },
          }))
          break
        }
        case 'end':
          if (ownedByAgent) {
            updateAgentBlock(payload, block => ({
              ...block,
              status: block.status === 'running' ? 'completed' : block.status,
            }))
          } else {
            finishCluster('completed')
          }
          break
        case 'error': {
          const error = getErrorText(payload.error)
          if (ownedByAgent) {
            updateAgentBlock(payload, block => ({
              ...block,
              status: 'failed',
              content: { ...block.content, ...(error ? { error } : {}) },
            }))
          } else {
            finishCluster('failed', error)
          }
          break
        }
        case 'model_end':
          adapter.applyLegacyEmpty?.(payload.message_length || 0)
          break
        case 'compare_end':
          finishCluster('completed')
          break
      }
    })
  }
}
