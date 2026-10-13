import type { FC, ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Flex } from 'antd'

import type { LogItem } from '../types'
import type { MemoryStageKey } from '../constants'
import CodeBlock from '@/components/Markdown/CodeBlock'
import ScoreMergeContent from './ScoreMergeContent'
import UserMetadataContent from './UserMetadataContent'

interface StageContentProps {
  stage: MemoryStageKey
  log: LogItem
}

type RecordValue = Record<string, unknown>

const asRecord = (value: unknown): RecordValue => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return {}
  }
  return value as RecordValue
}

const getResultStages = (value: unknown): string[] => {
  const results = Array.isArray(value) ? value : [value]

  return results.flatMap(result => {
    const stage = asRecord(result).stage
    const stages = Array.isArray(stage) ? stage : [stage]
    return stages.filter((item): item is string => (
      typeof item === 'string' && Boolean(item.trim())
    ))
  })
}

const getData = (log: LogItem) => ({
  ...asRecord(log.data),
  ...asRecord(log.result),
  ...asRecord(log.input),
  ...log,
})

const firstValue = (source: RecordValue, keys: string[]) => {
  for (const key of keys) {
    const value = source[key]
    if (value !== undefined && value !== null && value !== '') {
      return value
    }
  }
  return undefined
}

const textValue = (value: unknown, fallback = '—'): string => {
  if (value === undefined || value === null || value === '') {
    return fallback
  }
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value)) {
    return value.map(item => textValue(item)).join('、')
  }
  if (typeof value === 'object') {
    return JSON.stringify(value, null, 2)
  }
  return String(value)
}

const RequestModeContent: FC<{ log: LogItem }> = ({ log }) => {
  const { t } = useTranslation()
  const data = getData(log)
  const searchSwitch = textValue(
    firstValue(data, ['search_switch', 'searchSwitch', 'mode']),
    '2',
  )
  const modeKey = searchSwitch === '2'
    ? 'quick'
    : searchSwitch === '5'
      ? 'express'
      : searchSwitch === '0'
        ? 'deep'
        : 'normal'
  const request = asRecord(log.input)
  const requestFields = Object.keys(request).length
    ? request
    : Object.fromEntries(
        ['message', 'end_user_id', 'search_switch', 'session_id']
          .filter(key => data[key] !== undefined)
          .map(key => [key, data[key]]),
      )

  return (
    <Flex vertical gap={10}>
      <div className="rb:rounded-lg rb:bg-[#F6F6F6] rb:p-2.5">
        <Flex align="center" justify="space-between" gap={8}>
          <div>
            <p className="rb:text-[12px] rb:font-medium rb:text-[#171719]">
              {t(`memoryConversation.requestMode.${modeKey}`)}
            </p>
            <p className="rb:mt-1! rb:text-[10px] rb:text-[#5B6167]">
              {t(`memoryConversation.requestMode.${modeKey}Desc`)}
            </p>
          </div>
          <div className="rb:shrink-0 rb:rounded-md rb:bg-[#171719] rb:px-2 rb:py-1 rb:text-[10px] rb:text-white">
            search_switch={searchSwitch}
          </div>
        </Flex>
      </div>
      <div>
        <p className="rb:mb-1 rb:text-[10px] rb:text-[#5B6167]">
          {t('memoryConversation.requestMode.requestFields')}
        </p>
        <CodeBlock
          size="small"
          background="#F6F6F6"
          needCopy={false}
          value={JSON.stringify(requestFields, null, 2)}
        />
      </div>
    </Flex>
  )
}

const QueryPreprocessContent: FC<{ log: LogItem }> = ({ log }) => {
  const { t } = useTranslation()
  const data = getData(log)
  const original = firstValue(
    data,
    ['original_query', 'raw_query', 'query', 'message', 'input'],
  )
  const result = firstValue(
    data,
    ['processed_query', 'rewritten_query', 'normalized_query', 'result'],
  )
  const results = Array.isArray(result) ? result : [result ?? original]

  return (
    <Flex vertical gap={10}>
      <div>
        <p className="rb:mb-1 rb:text-[10px] rb:text-[#5B6167]">
          {t('memoryConversation.queryPreprocess.original')}
        </p>
        <p className="rb:text-xs rb:text-[#171719]">
          {textValue(original)}
        </p>
      </div>
      <div>
        <p className="rb:mb-1 rb:text-[10px] rb:text-[#5B6167]">
          {t('memoryConversation.queryPreprocess.result')}
        </p>
        <Flex wrap gap={6}>
          {results.map((item, index) => (
            <span
              className="rb:inline-block rb:rounded-md rb:bg-[#F2F5FA] rb:px-2 rb:py-1 rb:text-[12px] rb:text-[#5B6167]"
              key={`${textValue(item)}-${index}`}
            >
              {textValue(item, textValue(original))}
            </span>
          ))}
        </Flex>
      </div>
    </Flex>
  )
}

const ProblemSplitContent: FC<{ log: LogItem }> = ({ log }) => {
  const { t } = useTranslation()
  const data = getData(log)
  const rawQuestions = firstValue(data, ['questions', 'result'])
  const values = Array.isArray(rawQuestions) ? rawQuestions : [rawQuestions]
  const questions = values
    .map(item => (
      typeof item === 'string'
        ? item
        : firstValue(asRecord(item), ['question', 'content'])
    ))
    .filter((item): item is string => (
      typeof item === 'string' && Boolean(item.trim())
    ))

  const original = firstValue(
    data,
    ['original_query', 'raw_query', 'query', 'message', 'input'],
  )

  return (
    <Flex vertical gap={10}>
      <div>
        <p className="rb:mb-1 rb:text-[10px] rb:text-[#5B6167]">
          {t('memoryConversation.queryPreprocess.original')}
        </p>
        <p className="rb:text-xs rb:text-[#171719]">
          {textValue(original)}
        </p>
      </div>
      <div>
        <p className="rb:mb-1 rb:text-[10px] rb:text-[#5B6167]">
          {t('memoryConversation.queryPreprocess.result')}
        </p>
        <Flex wrap gap={6}>
          {(questions.length ? questions : ['—']).map((question, index) => (
            <span
              className="rb:inline-block rb:rounded-md rb:bg-[#F2F5FA] rb:px-2 rb:py-1 rb:text-[12px] rb:text-[#5B6167]"
              key={`${question}-${index}`}
            >
              {question}
            </span>
          ))}
        </Flex>
      </div>
    </Flex>
  )
}

const FinalAnswerContent: FC<{ log: LogItem }> = ({ log }) => {
  const { t } = useTranslation()
  const data = getData(log)
  const resultStages = getResultStages(data.result)
  const stages = resultStages.length ? resultStages : getResultStages(log.data)

  return (
    <Flex vertical gap={10}>
      {stages.length > 0 && (
        <div>
          <p className="rb:mb-1 rb:text-[12px] rb:text-[#5B6167]">
            {t('memoryConversation.finalAnswer.intermediate')}
          </p>
          <Flex gap={8} wrap>
            {stages.map((stage, index) => (
              <span
                className="rb:rounded-md rb:border rb:border-dashed rb:border-[#EBEBEB] rb:px-2 rb:py-1 rb:text-[12px] rb:text-[#5B6167]"
                key={`${stage}-${index}`}
              >
                {stage}
              </span>
            ))}
          </Flex>
        </div>
      )}
      <div>
        <p className="rb:mb-1 rb:text-[12px] rb:text-[#5B6167]">
          {t('memoryConversation.finalAnswer.answer')}
        </p>
        <p className="rb:text-xs rb:leading-5 rb:text-[#171719]">
          {textValue(firstValue(data, ['answer', 'content']))}
        </p>
      </div>
    </Flex>
  )
}

const StageContent: FC<StageContentProps> = ({ stage, log }) => {
  if (stage === 'requestMode') return <RequestModeContent log={log} />
  if (stage === 'queryPreprocess') return <QueryPreprocessContent log={log} />
  if (stage === 'problemSplit') return <ProblemSplitContent log={log} />
  if (stage === 'userMetadata') return <UserMetadataContent log={log} />
  if (stage === 'hybridRetrieval') return null
  if (stage === 'scoreMerge') return <ScoreMergeContent log={log} />
  if (stage === 'finalAnswer') return <FinalAnswerContent log={log} />
  return null
}

export default StageContent
