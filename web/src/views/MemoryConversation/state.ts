import type { ChatItem } from '@/components/Chat/types'
import type { MemoryStageKey } from './constants'
import type { LogItem } from './types'

export const markLastAssistantMessageFailed = (
  messages: ChatItem[],
  errorMessage: string,
): ChatItem[] => {
  const last = messages[messages.length - 1]

  if (last?.role === 'assistant') {
    return [
      ...messages.slice(0, -1),
      {
        ...last,
        status: 'failed',
        meta_data: {
          ...last.meta_data,
          error: errorMessage,
        },
      },
    ]
  }

  return [
    ...messages,
    {
      role: 'assistant',
      content: null,
      status: 'failed',
      meta_data: { error: errorMessage },
      created_at: Date.now(),
    },
  ]
}

export const markIncompleteStagesFailed = (
  logs: LogItem[],
  stages: readonly MemoryStageKey[],
): LogItem[] => stages.map((stage, index) => {
  const current = logs[index]
  if (current?.status === 'completed') return current

  return {
    ...current,
    type: current?.type || stage,
    stage: current?.stage || stage,
    status: 'failed',
  }
})
