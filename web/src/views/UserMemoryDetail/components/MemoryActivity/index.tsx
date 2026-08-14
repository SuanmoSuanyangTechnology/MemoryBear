import { type FC, useState } from 'react'
import { Flex, Tooltip, type SegmentedProps } from 'antd'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import clsx from 'clsx'

import PageScrollList from '@/components/PageScrollList'
import Empty from '@/components/Empty'
import { useI18n } from '@/store/locale'
import { formatDateTime } from '@/utils/format'
import { TAG_COLORS } from '../../pages/EpisodicDetail'
import Tag from '@/components/Tag'
import PageTabs from '@/components/PageTabs'
import {
  activityDateGroups,
  activityDateLabelKeys,
  activityIcons,
  activityUrls,
  filterKeys,
  filterLabelKeys,
  memoryTypeIcons,
  searchModeIcons,
} from './constants'
import { getActivityDateGroup } from './utils'
import type {
  ActivityFilter,
  ActivityQuery,
  ActivityRecord,
  ActivityType,
  MemoryActivityProps,
} from './types'

const MemoryActivity: FC<MemoryActivityProps> = ({ className }) => {
  const { id } = useParams()
  const { t } = useTranslation()
  const { language, changeLanguage, timeZone } = useI18n()
  const [filter, setFilter] = useState<ActivityFilter>(filterKeys[0])
  const [total, setTotal] = useState(0)

  const handleFilterChange = (nextFilter: SegmentedProps['value']) => {
    if (nextFilter === filter) return
    setTotal(0)
    setFilter(nextFilter as ActivityFilter)
  }

  const renderActivity = (record: ActivityRecord) => {
    const activityType: Exclude<ActivityType, 'all'> = record.engine_type
      ? 'engine'
      : 'write'
    const memoryIconComponent = record.memory_type
      ? memoryTypeIcons[record.memory_type]
      : undefined
    const searchIcon = record.search_mode
      ? searchModeIcons[record.search_mode]
      : undefined
    const activityIconComponent = !memoryIconComponent && !searchIcon
      ? activityIcons[activityType]
      : undefined

    return (
      <Flex gap={12} align="center" className="rb:rounded-xl rb:bg-white rb:p-3! rb:shadow-[0_1px_2px_rgba(0,0,0,0.05)] rb:transition-[background-color,box-shadow] rb:hover:bg-[#FAFAFC] rb:hover:shadow-[0_2px_8px_rgba(0,0,0,0.06)]">
        <div
          className={clsx('rb:flex rb:size-7.5 rb:items-center rb:justify-center rb:rounded-lg rb:text-[14px]', {
            'rb:bg-gray-900 rb:text-white': activityType === 'engine',
            'rb:bg-gray-50 rb:text-gray-400': filter === 'read',
            'rb:bg-gray-200 rb:text-gray-600': activityType === 'write',
          })}
          title={record.memory_type
            ? t(`episodicDetail.${record.memory_type}`)
            : t(`userMemory.${filterLabelKeys[activityType]}`)}
        >
          {memoryIconComponent
            ? (() => {
                const MemIcon = memoryIconComponent
                return <MemIcon />
              })()
            : searchIcon
              ? <div className={`rb:size-4 rb:bg-cover ${searchIcon}`} />
              : activityIconComponent
                ? (() => {
                    const ActIcon = activityIconComponent
                    return <ActIcon />
                  })()
                : null}
        </div>

        <Flex vertical gap={4} className="rb:min-w-0 rb:flex-1 rb:shrink-0">
          <Tooltip title={record.name || record.query || '-'} placement="topLeft">
            <div className="rb:min-w-0 rb:truncate rb:text-[12px] rb:leading-4.5 rb:font-medium">
              {record.name || record.query || '-'}
            </div>
          </Tooltip>

          <Tooltip title={record.content || '-'} placement="topLeft">
            <div className="rb:line-clamp-2 rb:text-[10px] rb:leading-4.25 rb:text-gray-600">
              {record.content || '-'}
            </div>
          </Tooltip>

          <Flex align="center" justify="space-between" gap={10} className="rb:text-[10px] rb:leading-4 rb:text-gray-400">
            <span className="rb:shrink-0">
              {formatDateTime(record.occurred_at, 'HH:mm') || '-'}
            </span>
            {record.memory_type &&
              <Tag size="small" color={TAG_COLORS[record.memory_type]} className="rb:shrink-0!">{t(`episodicDetail.${record.memory_type}`)}</Tag>
            }
            {record.engine_type &&
              <Tag size="small" color="default" className="rb:shrink-0!">{t(`userMemory.${record.engine_type}`)}</Tag>
            }
            {record.search_mode &&
              <Tag size="small" color="default" className="rb:shrink-0!">{t(`userMemory.${record.search_mode}_mode`)}</Tag>
            }
          </Flex>
        </Flex>
      </Flex>
    )
  }

  const renderGroupedActivities = (records: ActivityRecord[]) => {
    const groups = activityDateGroups
      .map(group => ({
        group,
        records: records.filter(record => getActivityDateGroup(record.occurred_at, timeZone) === group),
      }))
      .filter(({ records: groupRecords }) => groupRecords.length > 0)

    return (
      <Flex vertical gap={16}>
        {groups.map(({ group, records: groupRecords }) => (
          <section key={group}>
            <Flex align="center" justify="space-between" className="rb:px-2! rb:pb-1.5!">
              <span className="rb:text-[11px] rb:font-medium rb:text-gray-600">
                {t(`userMemory.${activityDateLabelKeys[group]}`)}
              </span>
              <span className="rb:text-[10px] rb:tabular-nums rb:text-gray-400">
                {groupRecords.length}
              </span>
            </Flex>
            <Flex vertical gap={8}>
              {groupRecords.map(record => (
                <div key={record.id}>{renderActivity(record)}</div>
              ))}
            </Flex>
          </section>
        ))}
      </Flex>
    )
  }

  return (
    <Flex vertical className={clsx('rb:h-full rb:min-h-0 rb:overflow-hidden rb:bg-[#F5F6F6] rb:rounded-xl rb:p-3!', className)}>
      <header className="rb:pt-2 rb:pb-3">
        <Flex align="center" justify="space-between" gap={16} className="rb:pl-1! rb:pr-2!">
          <div className="rb:text-[16px] rb:leading-6 rb:font-bold rb:font-[MiSans-Bold] rb:tracking-normal">{t('userMemory.memoryActivity')}</div>

          <Flex align="center" gap={8} className="rb:mt-0.5 rb:shrink-0">
            <div className="rb:inline-flex rb:items-center rb:rounded-full rb:bg-gray-200 rb:p-0.5!">
              {(['zh', 'en'] as const).map(lng => (
                <button
                  key={lng}
                  type="button"
                  className={clsx('rb:h-6 rb:cursor-pointer rb:rounded-full rb:border-0 rb:px-2.5! rb:text-[11px] rb:font-medium rb:transition-colors', language === lng
                    ? 'rb:bg-white rb:shadow-[0_1px_2px_rgba(0,0,0,0.12)]'
                    : 'rb:bg-transparent rb:text-gray-400 rb:hover:text-gray-900')}
                  onClick={() => changeLanguage(lng)}
                >
                  {lng === 'zh' ? '中' : 'EN'}
                </button>
              ))}
            </div>
            <span className="rb:text-[12px] rb:font-bold rb:font-[MiSans-Bold] rb:tabular-nums rb:text-gray-600">{total}</span>
          </Flex>
        </Flex>
        <div className="rb:pl-1 rb:mt-0.5 rb:text-[12px] rb:leading-4 rb:text-gray-600">{t('userMemory.memoryActivitySubtitle')}</div>

        {filterKeys.length > 1 
          ? <PageTabs
            value={filter}
            options={filterKeys
              .map(value => ({
                value,
                label: t(`userMemory.${filterLabelKeys[value]}`)
              }))}
            onChange={handleFilterChange}
            size="small"
            block
            className="rb:mt-2!"
          />
          : null
        }
      </header>

      <div className="rb:min-h-0 rb:flex-1">
        {id && (
          <PageScrollList<ActivityRecord, ActivityQuery>
            key={`${id}-${filter}`}
            url={activityUrls[filter]}
            query={{ end_user_id: id, include_engines: filterKeys.includes('engine'), language }}
            column={1}
            gutter={[0, 8]}
            heightClass="rb:h-full!"
            onTotalChange={setTotal}
            renderItem={renderActivity}
            renderItems={renderGroupedActivities}
            empty={<Empty size={88} className="rb:h-[calc(100vh-166px)]!" />}
            needLoading={false}
          />
        )}
      </div>
    </Flex>
  )
}

export default MemoryActivity
