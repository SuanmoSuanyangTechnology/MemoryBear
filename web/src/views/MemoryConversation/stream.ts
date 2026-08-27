import type { SSEMessage } from '@/utils/stream'
import { MEMORY_STAGE_ALIASES, STREAM_EVENTS } from './constants'
import type { MemoryStageKey } from './constants'
import type { LogItem, StreamPayload, StreamUpdate } from './types'

const asRecord = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
)

const parseJson = (value: unknown): unknown => {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value) as unknown
  } catch {
    return value
  }
}

const asPayload = (event: SSEMessage): StreamPayload => {
  const parsed = parseJson(event.data)
  if (typeof parsed === 'string') return { content: parsed }
  return asRecord(parsed) as StreamPayload
}

const normalizeKey = (value: unknown) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/[\s-]+/g, '_')

const normalizeStatus = (value: unknown, fallback = 'running') => {
  const status = normalizeKey(value)
  if (['completed', 'complete', 'success', 'succeeded', 'done'].includes(status)) return 'completed'
  if (['failed', 'failure', 'error', 'cancelled', 'canceled', 'timeout'].includes(status)) return 'failed'
  return status || fallback
}

const asLog = (payload: StreamPayload, eventName?: string): LogItem => {
  const record = asRecord(payload)
  const nestedRecord = asRecord(parseJson(record.log || record.output || record.stage_data))
  const data = parseJson(record.data)
  const dataRecord = asRecord(data)

  return {
    ...record,
    ...nestedRecord,
    data: Object.keys(dataRecord).length ? dataRecord : data,
    type: String(nestedRecord.type || record.stage || dataRecord.stage || eventName || ''),
    stage: String(nestedRecord.stage || record.stage || dataRecord.stage || ''),
  }
}

const readText = (payload: StreamPayload) => {
  if (typeof payload.answer === 'string') return payload.answer
  if (typeof payload.content === 'string') return payload.content
  return undefined
}

const normalizeTraceLog = (payload: StreamPayload): LogItem => {
  const record = asRecord(payload)
  const trace = asRecord(parseJson(record.trace))
  return {
    ...record,
    type: STREAM_EVENTS.RETRIEVAL_TRACE,
    stage: STREAM_EVENTS.RETRIEVAL_TRACE,
    status: normalizeStatus(trace.status || record.status),
    data: trace,
  }
}

const normalizeEndOutput = (output: unknown): LogItem => {
  const record = asRecord(output)
  const type = normalizeKey(record.type)

  if (type === 'problem_split') {
    const data = asRecord(parseJson(record.data))
    const rawQuestions = Array.isArray(record.data)
      ? record.data
      : [data.questions, data.items, data.results]
        .find(value => Array.isArray(value)) || []
    const questions = rawQuestions
      .map(item => (
        typeof item === 'string'
          ? item
          : asRecord(item).question
      ))
      .filter((item): item is string => (
        typeof item === 'string' && Boolean(item.trim())
      ))
    const originalQuery = [
      data.original_query,
      data.raw_query,
      data.query,
      data.message,
    ].find(value => typeof value === 'string' && Boolean(value.trim()))

    return {
      ...record,
      type,
      stage: type,
      status: 'completed',
      data: {
        ...data,
        original_query: originalQuery,
        questions,
        count: questions.length,
      },
    }
  }

  if (type === 'search_result') {
    return {
      ...record,
      type: 'final_answer',
      stage: 'final_answer',
      status: 'completed',
      data: {
        search_result: record.title,
        summary: record.title,
        result: record.result,
        raw_result: record.raw_result,
        total: record.total,
      },
    }
  }

  return {
    ...record,
    type,
    stage: type,
    status: normalizeStatus(record.status, 'completed'),
  }
}

export const getStageIndex = (
  log: LogItem,
  stages: readonly MemoryStageKey[],
) => {
  const candidates = [log.stage, log.type, log.name, log.title]
    .map(normalizeKey)
    .filter(Boolean)

  for (const candidate of candidates) {
    if (candidate in MEMORY_STAGE_ALIASES) {
      const stage = MEMORY_STAGE_ALIASES[candidate as keyof typeof MEMORY_STAGE_ALIASES]
      const index = stages.indexOf(stage)
      if (index >= 0) return index
    }
  }
  return undefined
}

export const parseStreamEvents = (events: SSEMessage[]): StreamUpdate[] => {
  const updates: StreamUpdate[] = []

  events.forEach(event => {
    if (event.comment && !event.event && event.data === undefined) return

    const payload = asPayload(event)
    const eventName = normalizeKey(event.event)
    const record = asRecord(payload)
    const pushLog = (log: LogItem, status?: string) => {
      updates.push({
        log: {
          ...log,
          status: normalizeStatus(status || log.status),
          type: log.type || eventName,
        },
      })
    }

    const sessionId = record.session_id || record.conversation_id
    if (typeof sessionId === 'string') updates.push({ sessionId })

    switch (eventName) {
      case STREAM_EVENTS.START:
        pushLog(asLog(payload, eventName), 'completed')
        break
      case STREAM_EVENTS.MEMORY_STAGE:
        pushLog(asLog(payload, eventName), String(record.status || 'running'))
        break
      case STREAM_EVENTS.RETRIEVAL_TRACE:
        pushLog(normalizeTraceLog(payload))
        break
      case STREAM_EVENTS.MESSAGE: {
        const answer = readText(payload)
        if (answer !== undefined) {
          updates.push({
            answer,
            appendAnswer: true,
            log: {
              type: 'final_answer',
              stage: 'final_answer',
              status: 'running',
              append_answer: true,
              data: { answer },
            },
          })
        }
        break
      }
      case STREAM_EVENTS.END: {
        const outputs = Array.isArray(record.intermediate_outputs)
          ? record.intermediate_outputs
          : []
        outputs.forEach(output => pushLog(normalizeEndOutput(output), 'completed'))
        pushLog({
          type: 'final_answer',
          stage: 'final_answer',
          status: 'completed',
          data: {},
        }, 'completed')
        updates.push({ completed: true })
        break
      }
      default: {
        const answer = readText(payload)
        if (answer !== undefined) updates.push({ answer, appendAnswer: true })
        if (eventName.includes('error') || normalizeStatus(record.status) === 'failed') {
          pushLog({
            ...asLog(payload, eventName),
            type: record.stage || 'final_answer',
            stage: record.stage || 'final_answer',
          }, 'failed')
        } else if (eventName || record.stage || record.log || record.output || record.stage_data) {
          pushLog(asLog(payload, eventName), String(record.status || 'running'))
        }
        break
      }
    }

    if (eventName !== STREAM_EVENTS.END && Array.isArray(record.intermediate_outputs)) {
      record.intermediate_outputs.forEach(output => pushLog(normalizeEndOutput(output), 'completed'))
    }
  })

  return updates
}
