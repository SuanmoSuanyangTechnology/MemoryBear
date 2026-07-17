/*
 * Shared display helper functions for the Result module
 */
import type { TFunction } from 'i18next'
import { LoadingOutlined } from '@ant-design/icons'
import Tag from '@/components/Tag'
import { tagColors } from './constants'
import type { ModuleItem } from './types'

/** Format status tag */
export const formatTag = (status: string, t: TFunction) => {
  return (
    <Tag color={tagColors[status]} className="rb:flex! rb:items-center rb:gap-1 rb:bg-white! rb:border-white!">
      {status === 'pending' && <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/memory/clock_orange.svg')]"></div>}
      {status === 'processing' && <LoadingOutlined spin className="rb:mr-1" />}
      {status === 'completed' && <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/check_green.svg')]"></div>}
      {t(`memoryExtractionEngine.status.${status}`)}
    </Tag>
  )
}

/** Format processing time */
export const formatTime = (data: ModuleItem, t: TFunction, color?: string) => {
  if (typeof data.end_at === 'number' && typeof data.start_at === 'number') {
    return <div className={`rb:text-[${color ?? '#155EEF'}] rb:mb-0.5`}>{t('memoryExtractionEngine.time')}{data.end_at - data.start_at}ms</div>
  }
  return null
}

/** Convert first character to lowercase */
export const lowercaseFirst = (str: string) => str.charAt(0).toLowerCase() + str.slice(1)
