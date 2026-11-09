import { Button, DatePicker, Flex, Skeleton, Tooltip } from 'antd'
import { RetweetOutlined } from '@ant-design/icons'
import { useEffect, useState } from 'react'
import dayjs, { type Dayjs } from 'dayjs'
import { useTranslation } from 'react-i18next'

import { ACTIVITY_START } from '../constants'
import { getActivityStatistics, getActivityComposition } from '@/api/memory'
import type { ActivityDay, ActivityStatisticsResponse, ActivityCompositionResponse } from '../types'
import Empty from '@/components/Empty'
import Card from './Card'
import ActivityChart from './ActivityChart'

/** Presents the memory activity calendar and daily composition with period filtering. */
export default function RecentActivity() {
  const { t } = useTranslation()
  const [view, setView] = useState<'calendar' | 'composition'>('calendar')
  const [year, setYear] = useState<Dayjs>()
  const [month, setMonth] = useState<Dayjs>()
  const [statistics, setStatistics] = useState<ActivityStatisticsResponse | null>(null)
  const [composition, setComposition] = useState<ActivityCompositionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const selected = view === 'calendar' ? year : month
  const unit = view === 'calendar' ? 'year' : 'month'
  const now = dayjs()
  const available = !now.isBefore(ACTIVITY_START, 'day')
  const periodStart = (selected ?? now).startOf(unit)
  const selectedYear = selected?.year()
  const selectedMonth = selected ? selected.month() + 1 : undefined

  useEffect(() => {
    let cancelled = false
    setStatistics(null)
    setComposition(null)
    setLoading(true)
    const pending = view === 'calendar'
      ? getActivityStatistics({ year: selectedYear }).then(result => {
        if (!cancelled) setStatistics(result)
      })
      : getActivityComposition(selectedYear !== undefined && selectedMonth !== undefined
        ? { year: selectedYear, month: selectedMonth }
        : {}).then(result => {
        if (!cancelled) setComposition(result)
      })
    pending
      .catch(() => {
        if (!cancelled) {
          setStatistics(null)
          setComposition(null)
        }
      })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [view, selectedYear, selectedMonth, available])

  const calendar = view === 'calendar'
  const days: ActivityDay[] = calendar
    ? (statistics?.daily_activities ?? []).map(day => ({
      date: day.date,
      total: day.total_activities,
      counts: { extraction: 0, forgetting: 0, reflection: 0, retrieval: 0, other: 0 },
    }))
    : (composition?.daily_activities ?? []).map(day => ({
      date: day.date,
      total: day.total_activities,
      categories: day.categories,
      counts: {
        extraction: day.categories.extraction.count,
        forgetting: day.categories.forgetting.count,
        reflection: day.categories.reflection.count,
        retrieval: day.categories.retrieval.count,
        other: day.categories.other.count,
      },
    }))
  const total = (calendar ? statistics?.total_activities : composition?.summary.total_activities) ?? 0
  const changeDate = (value: Dayjs | null) => {
    if (view === 'calendar') setYear(value ?? undefined)
    else setMonth(value ?? undefined)
  }

  const activeDays = statistics?.active_days ?? 0
  const average = statistics?.daily_average ?? 0
  const peak = statistics?.peak_daily_activities ?? 0
  const switchLabel = t(calendar ? 'dashboard.switchComposition' : 'dashboard.switchCalendar')
  const noData = !available || total === 0
  // Presentation-only samples: never feed mock counts into the real summary.
  const chartDays: ActivityDay[] = noData
    ? Array.from({ length: calendar ? periodStart.endOf('year').diff(periodStart, 'day') + 1 : periodStart.daysInMonth() }, (_, index) => ({
      date: periodStart.add(index, 'day').format('YYYY-MM-DD'),
      counts: {
        extraction: index % 7 === 0 ? 0 : (index * 13 + 17) % 80,
        forgetting: index % 7 === 0 ? 0 : (index * 7 + 3) % 19,
        reflection: index % 7 === 0 ? 0 : (index * 3 + 5) % 24,
        retrieval: index % 7 === 0 ? 0 : (index * 17 + 29) % 105,
        other: index % 7 === 0 ? 0 : (index + 2) % 11,
      },
    }))
    : days

  return (
    <Card
      title={t('dashboard.activityTotal')}
      bodyClassName="rb:pt-0! rb:pb-4! rb:px-4! rb:h-[326px]!"
      headerOperate={
        <Flex align="center" gap={8} wrap>
          <Tooltip title={switchLabel}>
            <Button size="small" aria-label={switchLabel} icon={<RetweetOutlined />}
              onClick={() => setView(calendar ? 'composition' : 'calendar')}
            ></Button>
          </Tooltip>
          <DatePicker
            picker={calendar ? 'year' : 'month'}
            value={selected}
            allowClear={true}
            minDate={dayjs(ACTIVITY_START)}
            maxDate={dayjs()}
            disabledDate={date => date.isBefore(ACTIVITY_START, calendar ? 'year' : 'month') || date.isAfter(dayjs(), calendar ? 'year' : 'month')}
            onChange={changeDate}
            placeholder={t(calendar ? 'dashboard.activityYear' : 'dashboard.activityMonth')}
          />
        </Flex>
      }
    >
      {loading
        ? <Skeleton active paragraph={{ rows: 6 }} />
        : (
          <>
            <div className="rb:mb-3">
              <span className="rb:text-[24px] rb:font-semibold">{total.toLocaleString()}</span>
              <span className="rb:ml-2 rb:text-gray-600 rb:text-[12px]">{t('dashboard.activityUnit')}</span>
            </div>
            {calendar && (
              <div className="rb:mb-4! rb:grid rb:grid-cols-4 rb:gap-3">
                {[
                  { key: 'activityDays', value: activeDays, unit: 'activityDayUnit' },
                  { key: 'activityAverage', value: average, unit: 'activityUnit' },
                  { key: 'activityPeak', value: peak, unit: 'activityUnit' },
                  { key: 'activityUsers', value: statistics?.involved_users ?? 0, unit: 'activityUserUnit' },
                ].map(({ key, value, unit }) => (
                  <div key={key} className="rb:bg-gray-100 rb:rounded-xl rb:p-3">
                    <div className="rb:font-semibold">
                      {value.toLocaleString()}
                      <span className="rb:ml-1 rb:text-xs">{t(`dashboard.${unit}`)}</span>
                    </div>
                    <div className="rb:mt-1 rb:text-gray-600 rb:text-[12px]">{t(`dashboard.${key}`)}</div>
                  </div>
                ))}
              </div>
            )}
            <div className="rb:relative">
              <div className={noData ? 'rb:pointer-events-none rb:select-none rb:opacity-35' : undefined}>
                <ActivityChart
                  days={chartDays}
                  calendar={calendar}
                  year={selected?.year()}
                  month={selected?.month()}
                  categories={noData ? undefined : composition?.summary.categories}
                />
              </div>
              {noData && (
                <Flex align="center" justify="center" className="rb:absolute rb:inset-0 rb:z-10 rb:rounded-lg rb:bg-black/55" role="status">
                  <Empty size={88} subTitle={t('dashboard.activityNoData')} subClassName="rb:text-white!" />
                </Flex>
              )}
            </div>
          </>
        )
      }
    </Card>
  )
}
