import { nodeLibrary } from '@/views/Workflow/constant'
import type { MemoryRetrieval } from '@/components/Chat/types'
import type { Timeline, TimelineStep } from '@/components/Chat/utils/timelineStream'

// Timeline data model now lives in the shared Chat stream module; re-export for
// backward-compatible imports while keeping the rendering helpers here.
export type { Timeline, TimelineStep, TimelineChatItem } from '@/components/Chat/utils/timelineStream'

/**
 * A long_term_memory tool step carrying memory_stage frames should render the
 * recall panel in place of the plain tool execution record.
 */
export const isMemoryStageStep = (step: TimelineStep, retrieval?: MemoryRetrieval): boolean => {
  if (step.type === 'text') return false
  return Boolean(retrieval?.tool_calls?.some(call => {
    if (call.name !== 'long_term_memory' || call.stages.length === 0) return false
    // `seq` is the only key preserved in persisted records (step_id is dropped),
    // so prefer it; fall back to step_id for streaming, then name for single-call timelines.
    if (typeof call.seq === 'number') return call.seq === step.seq
    if (step.step_id && call.step_id) return call.step_id === step.step_id
    return step.name === 'long_term_memory' || step.node_name === 'long_term_memory'
  }))
}

export const nodeIconMap: Record<string, string> = Object.fromEntries(
  nodeLibrary.flatMap(category => category.nodes.map(node => [node.type, node.icon]))
)

const TOOL_NODE_TYPE_MAP: Record<string, string> = {
  knowledge_retrieval: 'knowledge-retrieval',
  skill: 'tool',
  web_search: 'tool',
}

const getNodeType = (step: TimelineStep) => {
  if (step.execution?.node_type) return step.execution.node_type
  if (step.type === 'agent') return 'agent'
  if (step.type === 'text') return undefined
  return TOOL_NODE_TYPE_MAP[step.name || ''] || 'tool'
}

const pickValue = (preferred: unknown, fallback: unknown) =>
  preferred === undefined || preferred === null || preferred === ''
    ? fallback
    : preferred

/** Index executions by node_id so a timeline step_id can find its matching node. */
const indexExecutions = (executions: Record<string, any>[]) => {
  const index = new Map<string, Record<string, any>>()
  executions.forEach(execution => {
    const nodeId = execution?.node_id
    if (nodeId && !index.has(nodeId)) index.set(nodeId, execution)
  })
  return index
}

/** Enrich timeline steps with matching node executions while keeping server order. */
export const buildTimelineSteps = (timeline: Timeline, executions: Record<string, any>[] = []): Timeline => {
  const executionIndex = indexExecutions(executions)
  const steps = (timeline.steps || []).map(step => {
    const execution = step.step_id ? executionIndex.get(step.step_id) : undefined
    const nodeType = getNodeType({ ...step, execution })
    return {
      ...step,
      execution,
      node_type: nodeType,
      node_name: pickValue(execution?.node_name, step.name) as string | undefined,
      icon: nodeType ? nodeIconMap[nodeType] : undefined,
    }
  })
  return { ...timeline, steps }
}

export const getStepContent = (step: TimelineStep) => {
  const executionContent = step.execution?.content || {}
  return {
    input: pickValue(executionContent.input, step.input),
    output: pickValue(executionContent.output, step.output),
    process: executionContent.process,
    error: pickValue(executionContent.error, step.error),
  }
}

export const getStepStatus = (step: TimelineStep) =>
  pickValue(step.execution?.status, step.status) as string | undefined
