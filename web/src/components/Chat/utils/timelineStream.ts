import type { Dispatch, MutableRefObject, SetStateAction } from 'react'

import type { ChatItem } from '@/components/Chat/types'
import type { SSEMessage } from '@/utils/stream'
import { mapLastVersion } from './messageVersions'
import {
  getEndFailure,
  getTraceOutput,
  mergeTrace,
  normalizeAgentStatus,
  normalizeTrace,
  type AgentTrace,
} from './clusterStream'

/* ------------------------------------------------------------------ */
/* Timeline data model (moved from ApplicationConfig/utils/logTimeline) */
/* ------------------------------------------------------------------ */
export interface TimelineStep {
  seq: number
  type?: 'text' | 'agent' | 'tool'
  step_id?: string
  name?: string
  content?: string
  length?: number
  status?: string
  input?: unknown
  output?: unknown
  error?: any
  duration_ms?: number
  /** node execution matched by step_id === node_id */
  execution?: Record<string, any>
  /** sub-agent trace from agent_log / agent_log_final frames */
  agent_log?: any
  node_type?: string
  node_name?: string
  icon?: string
}

export interface Timeline {
  schema_version?: number
  steps: TimelineStep[]
  truncated?: boolean
}

export type TimelineChatItem = ChatItem & { timeline?: Timeline }

type ChatList = Array<ChatItem | ChatItem[]>
type SetChatList = Dispatch<SetStateAction<ChatList>>

const getTimeline = (item: ChatItem): Timeline | undefined => (item as TimelineChatItem).timeline

const sortSteps = (steps: TimelineStep[]) => [...steps].sort((a, b) => a.seq - b.seq)

const applyTimelineToItem = (
  current: ChatItem,
  updater: (timeline: Timeline | null) => Timeline | null,
): ChatItem => {
  if (current.role !== 'assistant') return current
  const next = updater(getTimeline(current) || null)
  if (!next) return current
  return {
    ...current,
    timeline: next,
    subContent: next.steps,
    content: next.steps.length ? '' : current.content,
  } as TimelineChatItem
}

/** Append an incremental text chunk to the step owning this seq (single item). */
export const appendTimelineChunkToItem = (current: ChatItem, content: string, seq: number): ChatItem =>
  applyTimelineToItem(current, timeline => {
    const steps = [...(timeline?.steps || [])]
    const index = steps.findIndex(step => step.seq === seq && step.type === 'text')
    if (index >= 0) {
      steps[index] = { ...steps[index], content: (steps[index].content || '') + content }
    } else {
      steps.push({ seq, type: 'text', content })
    }
    return { ...(timeline || { schema_version: 1 }), steps: sortSteps(steps) }
  })

/** Append an incremental text chunk to the step owning this seq. */
export const appendTimelineMessage = (prev: ChatList, content: string, seq: number): ChatList =>
  mapLastVersion(prev, current => appendTimelineChunkToItem(current, content, seq))

/** Create or update a tool step on a single item. */
export const updateTimelineToolOnItem = (current: ChatItem, payload: any, status: string): ChatItem => {
  const seq = payload?.seq
  if (typeof seq !== 'number') return current
  return applyTimelineToItem(current, timeline => {
    const steps = [...(timeline?.steps || [])]
    const stepId = payload.step_id
    const index = steps.findIndex(step => step.seq === seq || (stepId && step.step_id === stepId))
    const current = index >= 0 ? steps[index] : { seq, step_id: stepId, name: payload.name } as TimelineStep
    const next: TimelineStep = {
      ...current,
      seq,
      step_id: stepId || current.step_id,
      name: payload.name || current.name,
      status,
      input: payload.input !== undefined ? payload.input : current.input,
      output: payload.output !== undefined ? payload.output : current.output,
      error: payload.error !== undefined ? payload.error : current.error,
      duration_ms: payload.duration_ms !== undefined ? payload.duration_ms : current.duration_ms,
    }
    if (index >= 0) steps[index] = next
    else steps.push(next)
    return { ...(timeline || { schema_version: 1 }), steps: sortSteps(steps) }
  })
}

/** Create or update a tool step (tool_start / tool_end / tool_error). */
export const upsertTimelineTool = (prev: ChatList, payload: any, status: string): ChatList =>
  mapLastVersion(prev, current => updateTimelineToolOnItem(current, payload, status))

/** Create or update a sub-agent step on a single item. */
export const upsertTimelineAgentOnItem = (current: ChatItem, payload: any): ChatItem => {
  const seq = payload?.seq
  if (typeof seq !== 'number') return current
  const executionId = payload.execution_id
  return applyTimelineToItem(current, timeline => {
    const steps = [...(timeline?.steps || [])]
    const index = steps.findIndex(step => step.seq === seq || (executionId && step.step_id === executionId))
    const current = index >= 0 ? steps[index] : { seq, type: 'agent' as const } as TimelineStep
    const next: TimelineStep = {
      ...current,
      seq,
      type: 'agent',
      node_type: 'agent',
      step_id: executionId || current.step_id,
      name: payload.agent_name || current.name,
      input: payload.task !== undefined ? payload.task : current.input,
      status: current.status === 'completed' || current.status === 'failed' ? current.status : 'running',
      execution: {
        ...(current.execution || {}),
        ...(payload.agent_id ? { agent_id: payload.agent_id } : {}),
        ...(payload.parent_execution_id ? { parent_execution_id: payload.parent_execution_id } : {}),
        ...(payload.orchestration_mode ? { orchestration_mode: payload.orchestration_mode } : {}),
      },
    }
    if (index >= 0) steps[index] = next
    else steps.push(next)
    return { ...(timeline || { schema_version: 1 }), steps: sortSteps(steps) }
  })
}

/** Create or update a sub-agent step (agent_dispatch). */
export const upsertTimelineAgent = (prev: ChatList, payload: any): ChatList =>
  mapLastVersion(prev, current => upsertTimelineAgentOnItem(current, payload))

/** Attach and merge agent_log / agent_log_final trace into its agent step (single item). */
export const mergeTimelineAgentLogOnItem = (current: ChatItem, payload: any): ChatItem => {
  const trace = payload?.data || payload
  if (!trace) return current
  const incoming = normalizeTrace(payload)
  const executionId = payload?.execution_id || trace.execution_id
  const agentId = payload?.agent_id || trace.agent_id
  const agentName = payload?.agent_name || trace.agent_name

  return applyTimelineToItem(current, timeline => {
    if (!timeline) return null
    const steps = [...timeline.steps]
    const index = steps.findIndex(step => step.type === 'agent' && (
      (executionId && step.step_id === executionId)
      || (agentId && step.execution?.agent_id === agentId)
      || (agentName && (step.name === agentName || step.execution?.agent_name === agentName))
    ))
    let target = index
    if (target < 0) {
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].type === 'agent') {
          target = i
          break
        }
      }
    }
    if (target < 0) return timeline
    const current = steps[target]
    const currentTrace: AgentTrace = current.agent_log || { meta: {}, iterations: [] }
    steps[target] = {
      ...current,
      node_type: current.node_type || 'agent',
      name: agentName || current.name,
      agent_log: mergeTrace(currentTrace, incoming),
      execution: {
        ...(current.execution || {}),
        ...(agentId ? { agent_id: agentId } : {}),
        ...(agentName ? { agent_name: agentName } : {}),
        ...(incoming.meta ? { meta: incoming.meta } : {}),
      },
    }
    return { ...timeline, steps }
  })
}

/** Attach and merge agent_log / agent_log_final trace into its agent step. */
export const mergeTimelineAgentLog = (prev: ChatList, payload: any): ChatList =>
  mapLastVersion(prev, current => mergeTimelineAgentLogOnItem(current, payload))

/** Finalize an agent step on a single item (agent_complete). */
export const completeTimelineAgentOnItem = (current: ChatItem, payload: any): ChatItem => {
  const seq = payload?.seq
  if (typeof seq !== 'number') return current
  const executionId = payload.execution_id
  return applyTimelineToItem(current, timeline => {
    if (!timeline) return null
    const steps = [...timeline.steps]
    const index = steps.findIndex(step => step.seq === seq || (executionId && step.step_id === executionId))
    if (index < 0) return timeline
    const current = steps[index]
    const currentTrace: AgentTrace = current.agent_log || { meta: {}, iterations: [] }
    const failure = getEndFailure(payload)
    const duration = payload.duration_ms !== undefined
      ? payload.duration_ms
      : typeof payload.elapsed_time === 'number' ? Math.round(payload.elapsed_time * 1000) : undefined
    steps[index] = {
      ...current,
      name: payload.agent_name || current.name,
      status: normalizeAgentStatus(payload.status, failure || payload.error),
      output: payload.output !== undefined ? payload.output : getTraceOutput(currentTrace),
      error: failure || payload.error || current.error,
      duration_ms: duration !== undefined ? duration : current.duration_ms,
      execution: { ...(current.execution || {}), ...(payload.token_usage ? { token_usage: payload.token_usage } : {}) },
    }
    return { ...timeline, steps }
  })
}

/** Finalize an agent step (agent_complete). */
export const upsertTimelineAgentComplete = (prev: ChatList, payload: any): ChatList =>
  mapLastVersion(prev, current => completeTimelineAgentOnItem(current, payload))

/** Use end.last_message to finalize the last text step and close the stream (single item). */
export const finishTimelineItem = (current: ChatItem, payload: any): ChatItem => {
  const timeline = getTimeline(current)
  if (current.role !== 'assistant' || !timeline) return current
  const steps = [...timeline.steps]
  const lastMessage = payload?.last_message
  if (lastMessage && typeof lastMessage.seq === 'number') {
    const index = steps.findIndex(step => step.seq === lastMessage.seq && step.type === 'text')
    if (index >= 0) steps[index] = { ...steps[index], content: lastMessage.content || '' }
    else steps.push({ seq: lastMessage.seq, type: 'text', content: lastMessage.content || '' })
  }
  return {
    ...current,
    status: 'completed',
    timeline: { ...timeline, steps: sortSteps(steps) },
    subContent: sortSteps(steps),
    content: '',
    meta_data: {
      ...(current.meta_data || {}),
      error: undefined,
      ...(payload?.audio_url ? { audio_url: payload.audio_url } : {}),
      ...(payload?.audio_status !== undefined ? { audio_status: payload.audio_status } : {}),
      ...(payload?.citations !== undefined ? { citations: payload.citations } : {}),
      ...(payload?.suggested_questions !== undefined ? { suggested_questions: payload.suggested_questions } : {}),
    },
  } as TimelineChatItem
}

/** Use end.last_message to finalize the last text step and close the stream. */
export const finalizeTimelineEnd = (prev: ChatList, payload: any): ChatList =>
  mapLastVersion(prev, current => finishTimelineItem(current, payload))

/** Keep the timeline intact when an error frame arrives (single item). */
export const failTimelineItem = (current: ChatItem, payload: any): ChatItem => {
  const errorText = payload?.msg || payload?.message || payload?.error?.msg || payload?.error?.message
  if (current.role !== 'assistant' || !getTimeline(current)) return current
  return {
    ...current,
    status: 'completed',
    content: '',
    error: typeof errorText === 'string' ? errorText : undefined,
    meta_data: { ...(current.meta_data || {}), error: undefined },
  } as TimelineChatItem
}

/** Keep the timeline intact when an error frame arrives; show the error separately. */
export const finalizeTimelineError = (prev: ChatList, payload: any): ChatList =>
  mapLastVersion(prev, current => failTimelineItem(current, payload))

/** A frame that starts the step timeline: text/tool/sub-agent frames carry `seq`, `end` carries `last_seq`/`last_message`. */
export const isTimelineStart = (item: SSEMessage): boolean => {
  const payload = item.data as any
  if (typeof payload?.seq === 'number') return true
  if (item.event === 'end' && (typeof payload?.last_seq === 'number' || payload?.last_message?.seq != null)) return true
  return false
}

export interface TimelineStreamDeps {
  setChatList: SetChatList
  setLoading: (loading: boolean) => void
  setStreamLoading: (loading: boolean) => void
  streamLoadingRef: MutableRefObject<boolean>
}

/**
 * Wraps an existing stream handler: events carrying seq are reduced into a
 * timeline, while legacy events keep their original handling.
 */
export const createTimelineAwareStreamHandler = (
  baseHandler: (data: SSEMessage[]) => void,
  deps: TimelineStreamDeps,
) => {
  const { setChatList, setLoading, setStreamLoading, streamLoadingRef } = deps
  let hasTimeline = false

  return (data: SSEMessage[]) => {
    data.forEach(item => {
      const payload = item.data as any
      if (!hasTimeline && isTimelineStart(item)) hasTimeline = true
      if (!hasTimeline) {
        baseHandler([item])
        return
      }

      switch (item.event) {
        case 'message':
          if (typeof payload?.seq === 'number') {
            setChatList(prev => appendTimelineMessage(prev, payload.content || '', payload.seq))
          } else {
            baseHandler([item])
          }
          break
        case 'tool_start':
          setChatList(prev => upsertTimelineTool(prev, payload, 'pending'))
          break
        case 'tool_end':
          setChatList(prev => upsertTimelineTool(prev, payload, 'completed'))
          break
        case 'tool_error':
          setChatList(prev => upsertTimelineTool(prev, payload, 'failed'))
          break
        case 'agent_dispatch':
          setChatList(prev => upsertTimelineAgent(prev, payload))
          break
        case 'agent_log':
        case 'agent_log_final':
          setChatList(prev => mergeTimelineAgentLog(prev, payload))
          break
        case 'agent_complete':
          setChatList(prev => upsertTimelineAgentComplete(prev, payload))
          break
        case 'error':
          baseHandler([item])
          setChatList(prev => finalizeTimelineError(prev, payload))
          break
        case 'end':
          // Keep base handling for ids, audio polling, citations and spinner cleanup.
          baseHandler([item])
          setChatList(prev => finalizeTimelineEnd(prev, payload))
          streamLoadingRef.current = false
          setStreamLoading(false)
          setLoading(false)
          break
        default:
          baseHandler([item])
      }
    })
  }
}
