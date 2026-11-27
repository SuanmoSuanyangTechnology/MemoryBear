import { type FC } from 'react'
import { Flex } from 'antd'
import { useTranslation } from 'react-i18next'

import Markdown from '@/components/Markdown'
import MemoryRecall from '@/components/Chat/MemoryRecall'
import RuntimeNodeList from '@/views/Workflow/components/Chat/RuntimeNodeList'
import {
  getStepContent,
  getStepStatus,
  isMemoryStageStep,
  nodeIconMap,
  type TimelineChatItem,
  type TimelineStep,
} from '../utils/logTimeline'

/** Convert a timeline step (and its matched execution) into a Runtime node card. */
const toRuntimeNode = (step: TimelineStep) => {
  const execution = step.execution || {}
  const content = getStepContent(step)
  const cycleItems = execution.cycle_items
  const nodeType = step.node_type || execution.node_type || (step.type === 'agent' ? 'agent' : 'tool')

  return {
    ...execution,
    node_id: step.step_id || execution.node_id || `timeline-${step.seq}`,
    node_type: nodeType,
    node_name: step.node_name || step.name || step.step_id,
    icon: step.icon || execution.icon || nodeIconMap[nodeType],
    status: getStepStatus(step) || execution.status || 'completed',
    elapsed_time: typeof execution.elapsed_time === 'number' ? execution.elapsed_time : step.duration_ms,
    agent_log: step.agent_log || execution.agent_log,
    content,
    ...(Array.isArray(cycleItems) && cycleItems.length > 0
      ? {
        subContent: cycleItems.map(({ input, output, error, process, ...node }: any) => ({
          ...node,
          content: { input, output, process, error },
        })),
      }
      : {}),
  }
}

interface LogDetailTimelineProps {
  item: TimelineChatItem
  source: string
  isStreaming?: boolean
}

/** Renders one assistant message as ordered text chunks and runtime tool / agent steps. */
const LogDetailTimeline: FC<LogDetailTimelineProps> = ({ item, source, isStreaming = false }) => {
  const { t } = useTranslation()
  const steps = item.timeline?.steps || []
  const retrieval = item.meta_data?.memory_retrieval

  return (
    <Flex vertical gap={4}>
      {steps.map(step => {
        const key = `step-${step.seq}`
        if (step.type === 'text') {
          return (
            <div key={key} className="rb:leading-6">
              <Markdown content={step.content || ''} />
            </div>
          )
        }
        // long_term_memory carrying memory_stage frames: show the recall panel
        // at the tool step's position instead of its execution record.
        const recall = isMemoryStageStep(step, retrieval) ? retrieval : undefined
        if (recall) {
          return (
            <MemoryRecall
              key={key}
              retrieval={recall}
              assistantContent={item.content}
              isStreaming={isStreaming}
              stepId={step.step_id}
              stepSeq={step.seq}
              className="rb:mb-0!"
            />
          )
        }
        return (
          <RuntimeNodeList
            key={key}
            source={source}
            list={[toRuntimeNode(step)]}
            showIcon
            outlined
            showLlmDetail={true}
          />
        )
      })}
      {item.error && typeof item.error === 'string' && (
        <div className="rb:text-red-500">
          {item.error}
        </div>
      )}
      {item.timeline?.truncated && (
        <div className="rb:text-[12px] rb:text-gray-500">
          {t('workflow.timelineTruncated', { defaultValue: '步骤过多，部分步骤已省略' })}
        </div>
      )}
    </Flex>
  )
}

export default LogDetailTimeline
