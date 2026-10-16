import type { FC } from 'react'
import { Flex } from 'antd'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'

import Tag from '@/components/Tag'
import type { MemoryStageKey } from '../constants'
import type { LogItem } from '../types'
import StageContent from './StageContent'

interface StageCardProps {
  stageKey: MemoryStageKey
  index: number
  log?: LogItem
  loading: boolean
  expanded?: boolean
  onToggle: () => void
}

const ContentWrapper: FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="rb-border-t rb:bg-white rb:px-3 rb:py-2.5 rb:text-[11px] rb:leading-[1.65] [&>p]:rb:m-0">
    {children}
  </div>
)

const StageCard: FC<StageCardProps> = ({
  stageKey,
  index,
  log,
  loading,
  expanded,
  onToggle,
}) => {
  const { t } = useTranslation()
  const canExpand = stageKey !== 'hybridRetrieval' && log?.status !== 'failed'
  const statusKey = !log
    ? loading ? 'running' : 'waiting'
    : log.status === 'failed'
      ? 'failed'
      : log.status === 'completed'
        ? 'completed'
        : 'running'
  const isOpen = canExpand && (expanded ?? Boolean(log))

  return (
    <Flex gap={12}
      className="rb:relative rb:after:absolute rb:after:top-9 rb:after:-bottom-4 rb:after:left-2.5 rb:after:content-[''] rb:after:w-px rb:after:bg-gray-500 rb:last:after:hidden"
    >
      <Flex
        align="center"
        justify="center"
        className={clsx('rb:size-5 rb:rounded-full rb:text-[12px] rb:mt-3!', {
          'rb:bg-gray-100 rb:text-gray-600': statusKey === 'waiting',
          'rb:bg-gray-900 rb:text-white': statusKey === 'running',
          'rb:bg-[rgba(54,159,33)] rb:text-white': statusKey === 'completed',
          'rb:bg-[rgba(255,138,76)] rb:text-white': statusKey === 'failed',
        })}
      >
        {index + 1}
      </Flex>
      <div className="rb:flex-1 rb:overflow-hidden rb:rounded-xl rb-border rb:bg-gray-100">
        <Flex
          align="center"
          gap={8}
          className={clsx('rb:py-2.5! rb:w-full rb:border-0 rb:bg-transparent rb:px-3! rb:text-left', {
            'rb:cursor-pointer': canExpand,
            'rb:cursor-default': !canExpand,
          })}
          onClick={() => {
            if (canExpand) onToggle()
          }}
        >
          <b className="rb:flex-1 rb:text-xs rb:font-semibold">
            {t(`memoryConversation.stages.${stageKey}`)}
          </b>
          <Tag
            color={
              statusKey === 'completed'
                ? 'success'
                : statusKey === 'failed'
                  ? 'error'
                  : statusKey === 'waiting'
                    ? 'default'
                    : 'processing'
            }
            size="small"
            className="rb:shrink-0"
          >
            {t(`memoryConversation.${statusKey}`)}
          </Tag>
          {canExpand && (
            <div
              className={clsx("rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')] rb:transition-transform", {
                'rb:rotate-180': !isOpen,
                'rb:rotate-0': isOpen,
              })}
            />
          )}
        </Flex>
        {canExpand && isOpen && log && (
          <ContentWrapper>
            <StageContent stage={stageKey} log={log} />
          </ContentWrapper>
        )}
      </div>
    </Flex>
  )
}

export default StageCard
