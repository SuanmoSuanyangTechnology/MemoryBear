import { type FC, useMemo } from 'react';
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import dayjs from 'dayjs'
import { useTranslation } from 'react-i18next'

import { activityKinds, activityColors } from '../constants'
import type { ActivityDay, ActivityCategories } from '../types'

// Calendar heatmap color tiers based on activity count
const CALENDAR_COLORS = ['#E7EAF0', '#D4E2FF', '#9BBCFF', '#5C8EF5', '#155EEF']

const CELL_SIZE = 19

interface ActivityChartProps {
  days: ActivityDay[];
  calendar: boolean;
  year?: number;
  month?: number;
  categories?: ActivityCategories;
}

/** Displays daily activity intensity or normalized daily activity composition. */
const ActivityChart: FC<ActivityChartProps> = ({ days, calendar, year, month, categories }) => {
  const { t, i18n } = useTranslation()
  const dates = days.map(day => day.date).sort()
  const calendarRange = useMemo(() => {
    return year !== undefined
    ? String(year)
    : dates.length ? [dates[0], dates[dates.length - 1]] : String(dayjs().year())
  }, [dates, year])
  const fallbackDate = dates.length ? dayjs(dates[0]) : dayjs()
  const monthStart = dayjs(`${year ?? fallbackDate.year()}-${String((month ?? fallbackDate.month()) + 1).padStart(2, '0')}-01`)
  const daysByDate = useMemo(() => new Map(days.map(day => [day.date, day])), [days])
  const monthDays: ActivityDay[] = Array.from({ length: monthStart.daysInMonth() }, (_, index) => {
    const date = monthStart.add(index, 'day').format('YYYY-MM-DD')
    return daysByDate.get(date) ?? {
      date,
      counts: { extraction: 0, forgetting: 0, reflection: 0, retrieval: 0, other: 0 },
    }
  })
  const monthTotals = monthDays.map(day => day.total ?? activityKinds.reduce((sum, kind) => sum + day.counts[kind], 0))
  const kindTotals = activityKinds.map(kind => monthDays.reduce((sum, day) => sum + day.counts[kind], 0))
  const monthTotal = kindTotals.reduce((sum, value) => sum + value, 0)
  const names = activityKinds.map(kind => t(`dashboard.activityKinds.${kind}`))

  // Calculate calendar width based on weeks
  const calendarWidth = calendar
    ? (() => {
        const weeks = Array.isArray(calendarRange)
          ? Math.ceil(dayjs(calendarRange[1]).diff(dayjs(calendarRange[0]), 'day') / 7) + 2
          : 53
        return weeks * CELL_SIZE
      })()
    : undefined

  // Build full year data including zero-activity days
  const fullYearData = useMemo(() => {
    const start = Array.isArray(calendarRange) ? dayjs(calendarRange[0]) : dayjs(`${calendarRange}-01-01`)
    const end = Array.isArray(calendarRange) ? dayjs(calendarRange[1]) : dayjs(`${calendarRange}-12-31`)
    const totalDays = end.diff(start, 'day') + 1
    const result: [string, number][] = []
    for (let i = 0; i < totalDays; i++) {
      const date = start.add(i, 'day').format('YYYY-MM-DD')
      const data = daysByDate.get(date)
      result.push([date, data?.total ?? 0])
    }
    return result
  }, [calendarRange, daysByDate])

  const option: EChartsOption = calendar
    ? {
      tooltip: {
        trigger: 'item',
        renderMode: 'richText',
        confine: true,
        backgroundColor: 'rgba(0,0,0,0.8)',
        borderWidth: 0,
        textStyle: {
          color: '#FFFFFF',
          lineHeight: 24,
        },
        formatter: params => {
          const item = Array.isArray(params) ? params[0] : params
          const data = item.data as [string, number] | undefined
          if (!data) return ''
          const dateStr = data[0]
          const total = data[1]
          const dateLabel = new Intl.DateTimeFormat(i18n.language, {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            weekday: 'short',
          }).format(dayjs(dateStr).toDate())
          const countLabel = t('dashboard.activityTooltipCount', { count: total })
          return `${dateLabel}\n${countLabel}`
        },
      },
      visualMap: {
        show: false,
        type: 'piecewise',
        pieces: [
          { min: 0, max: 0, color: CALENDAR_COLORS[0] },
          { min: 1, max: 24, color: CALENDAR_COLORS[1] },
          { min: 25, max: 64, color: CALENDAR_COLORS[2] },
          { min: 65, max: 120, color: CALENDAR_COLORS[3] },
          { min: 121, color: CALENDAR_COLORS[4] },
        ],
      },
      calendar: {
        top: 0,
        left: 0,
        right: 0,
        range: calendarRange,
        cellSize: [CELL_SIZE, CELL_SIZE],
        yearLabel: { show: false },
        dayLabel: { show: false, firstDay: 1 },
        monthLabel: {
          show: true,
          position: 'end',
          color: '#A8A9AA',
          nameMap: t('dashboard.months', { returnObjects: true }) as string[],
        },
        itemStyle: { color: 'transparent', borderColor: '#FFFFFF', borderWidth: 2 },
        splitLine: { show: false },
      },
      series: [{
        name: t('dashboard.activityTotal'),
        type: 'heatmap',
        coordinateSystem: 'calendar',
        data: fullYearData,
        itemStyle: { borderRadius: 3, borderColor: '#FFFFFF', borderWidth: 2 },
        emphasis: { itemStyle: { borderColor: '#171719', borderWidth: 2 } },
      }],
    }
    : {
      color: activityColors,
      tooltip: {
        trigger: 'axis',
        backgroundColor: 'rgba(0,0,0,0.8)',
        textStyle: {
          color: '#FFFFFF',
          lineHeight: 24,
        },
        formatter: params => {
          const items = Array.isArray(params) ? params : [params]
          const dataIndex = items[0]?.dataIndex
          if (typeof dataIndex !== 'number') return ''
          const day = monthDays[dataIndex]
          if (!day) return ''
          const total = monthTotals[dataIndex]
          const dateLabel = new Intl.DateTimeFormat(i18n.language, {
            month: 'long',
            day: 'numeric',
          }).format(dayjs(day.date).toDate())
          const rows = activityKinds.map((kind, index) => {
            const category = day?.categories?.[kind]
            const count = category?.count ?? day?.counts[kind] ?? 0
            const percentage = category?.percentage ?? (total ? count / total * 100 : 0)
            return `<div style="display:flex;align-items:center;gap:8px;line-height:22px;">
              <span style="display:inline-block;width:7px;height:7px;border-radius:1px;background:${activityColors[index]};"></span>
              <span style="flex:1;">${t(`dashboard.activityKinds.${kind}`)}</span>
              <span style="min-width:24px;text-align:right;">${count.toLocaleString()}</span>
              <span style="min-width:40px;text-align:right;opacity:.65;">${percentage.toFixed(1)}%</span>
            </div>`
          }).join('')
          return `<div style="min-width:170px;font-size:11px;"><div style="display:flex;justify-content:space-between;gap:16px;margin-bottom:8px;"><span>${dateLabel}</span><span>${t('dashboard.activityTooltipCount', { count: total })}</span></div>${rows}</div>`
        },
      },
      legend: names.map((name, index) => ({
        data: [name],
        left: `${index * 21.5}%`,
        bottom: 0,
        padding: 0,
        icon: 'none',
        itemWidth: 0,
        itemHeight: 0,
        textStyle: {
          fontSize: 12,
          lineHeight: 22,
          rich: {
            marker: { width: 7, height: 7, backgroundColor: activityColors[index], borderRadius: 1 },
            label: { color: '#5B6167', fontSize: 12 },
            count: { color: '#212332', fontSize: 12 },
            percentage: { color: '#A8A9AA', fontSize: 10 },
          },
        },
        formatter: () => {
          const category = categories?.[activityKinds[index]]
          const value = category?.count ?? kindTotals[index]
          const percentage = category ? category.percentage.toFixed(1) : monthTotal ? (value / monthTotal * 100).toFixed(1) : '0.0'
          return `{marker|} {label|${name}}\n{count|${value.toLocaleString()}}  {percentage|${percentage}%}`
        },
      })),
      grid: { top: 0, left: 8, right: 0, bottom: 80 },
      xAxis: {
        type: 'category',
        data: monthDays.map(day => dayjs(day.date).format('MM/DD')),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { interval: 6 },
      },
      yAxis: { type: 'value', max: 100, show: false },
      series: [...activityKinds.map((kind, index) => ({
        name: t(`dashboard.activityKinds.${kind}`),
        type: 'bar' as const,
        stack: 'activity',
        emphasis: { focus: 'series' as const },
        blur: { itemStyle: { opacity: 0.2 } },
        barCategoryGap: '12%',
        data: monthDays.map((day, dayIndex) => ({
          value: day.categories?.[kind].percentage ?? (monthTotals[dayIndex] ? Number((day.counts[kind] / monthTotals[dayIndex] * 100).toFixed(2)) : 0),
          itemStyle: { color: activityColors[index] },
        })),
      })), {
        type: 'custom',
        silent: true,
        tooltip: { show: false },
        data: monthTotals.flatMap((total, index) => total === 0 ? [[index, 0]] : []),
        renderItem: (_params, api) => {
          const point = api.coord([api.value(0), 0])
          const width = api.getWidth() / monthDays.length * 0.88
          return {
            type: 'rect',
            shape: { x: point[0] - width / 2, y: point[1] - 2, width, height: 2 },
            style: { fill: activityColors[4] },
          }
        },
      }],
    }

  return (
    <div className={calendar ? "rb:overflow-x-auto rb:min-w-full" : ""}>
      <ReactECharts option={option} notMerge style={calendar ? { width: calendarWidth, height: 170, margin: '0 auto' } : { height: 260 }} />
    </div>
  )
}
export default ActivityChart;
