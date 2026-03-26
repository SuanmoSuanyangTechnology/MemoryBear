/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:17:05 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 11:59:10
 */
/**
 * Line Chart Card Component
 * Displays time-series data with ECharts line chart
 * Supports multiple series and date range selection
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { Select } from 'antd'

import Card from './Card'
import AreaLineChart, { type ChartData } from '@/components/Charts/AreaLineChart';

/**
 * Component props
 */
interface LineCardProps {
  chartData: ChartData[];
  limit: number;
  onChange: (value: string, type: string) => void;
  type: string;
  className?: string;
  seriesList: string[];
}

const LineCard: FC<LineCardProps> = ({ chartData, limit, onChange, type, className, seriesList }) => {
  const { t } = useTranslation()
  const options = [
    { label: t('dashboard.lastDays', { days: 7 }), value: 7 },
    { label: t('dashboard.lastDays', { days: 30 }), value: 30 },
    { label: t('dashboard.lastDays', { days: 90 }), value: 90 },
    { label: t('dashboard.lastHalfYear'), value: 180 },
    { label: t('dashboard.lastYear'), value: 365 },
  ]
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
      title={t(`dashboard.${type}`)}
      headerOperate={
        <Select 
          value={limit}
          options={options} 
          onChange={(value) => onChange(String(value), type)}
          className="rb:w-35!"
        />
      }
      className={`rb:pb-6 ${className}`}
    >
      <AreaLineChart
        xAxisKey="date"
        chartData={chartData}
        seriesList={formatSeriesList()}
        height={239}
      />
    </Card>
  )
}

export default LineCard
