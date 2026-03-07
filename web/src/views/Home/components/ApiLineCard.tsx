/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:17:05 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 11:35:52
 */
/**
 * Line Chart Card Component
 * Displays time-series data with ECharts line chart
 * Supports multiple series and date range selection
 */

import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Select } from 'antd'
import dayjs from 'dayjs'

import Card from './Card'
import { getWorkspaceApiStatistics } from '@/api/application'
import LineChart, { type ChartData } from '@/components/Charts/LineChart'

const seriesList = ['total_calls', 'app_calls', 'service_calls']
const ApiLineCard: FC = () => {
  const { t } = useTranslation()
  const options = [
    { label: t('dashboard.lastDays', { days: 7 }), value: 7 },
    { label: t('dashboard.lastDays', { days: 30 }), value: 30 },
    { label: t('dashboard.lastDays', { days: 90 }), value: 90 },
    { label: t('dashboard.lastHalfYear'), value: 180 },
    { label: t('dashboard.lastYear'), value: 365 },
  ]
  const [chartData, setChartData] = useState<ChartData[]>([])
  const [query, setQuery] = useState(7)

  useEffect(() => {
    getWorkspaceApiStatistics({
      start_date: dayjs().subtract(query - 1, 'd').startOf('d').valueOf(),
      end_date: dayjs().endOf('d').valueOf(),
    })
      .then(res => {
        setChartData(res as ChartData[])
      })
  }, [query])

  /** Format series list for legend */
  const formatSeriesList = () => {
    const list: Record<string, string> = {}
    seriesList.forEach(key => {
      list[key] = t(`dashboard.${key}`)
    })

    return list
  }

  return (
    <Card
      title={t(`dashboard.apiCallTrends`)}
      headerOperate={
        <Select 
          value={query}
          options={options} 
          onChange={(value) => setQuery(value)}
          className="rb:w-35!"
        />
      }
      className={`rb:pb-6`}
    >
      <LineChart
        chartData={chartData}
        seriesList={formatSeriesList()}
        height={239}
      />
    </Card>
  )
}

export default ApiLineCard
