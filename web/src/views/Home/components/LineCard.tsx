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
import { DatePicker } from 'antd'
import type { Dayjs } from 'dayjs'

import Card from './Card'
import AreaLineChart, { type ChartData } from '@/components/Charts/AreaLineChart';

/**
 * Component props
 */
interface LineCardProps {
  chartData: ChartData[];
  dateRange: [Dayjs, Dayjs];
  onChange: (value: [string, string], type: string) => void;
  type: string;
  className?: string;
  seriesList: string[];
}

const LineCard: FC<LineCardProps> = ({ chartData, dateRange, onChange, type, className, seriesList }) => {
  const { t } = useTranslation()
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
        <DatePicker.RangePicker
          value={dateRange}
          onChange={(dates) => {
            if (dates?.[0] && dates[1]) {
              onChange([dates[0].format('YYYY-MM-DD'), dates[1].format('YYYY-MM-DD')], type)
            }
          }}
          size="small"
          className="rb:w-52!"
        />
      }
      className={`rb:pb-6 ${className}`}
    >
      <AreaLineChart
        xAxisKey="date"
        chartData={chartData}
        seriesList={formatSeriesList()}
        height={127}
        showLegend={false}
        grid={{
          top: 4,
          left: 4,
          right: 4,
          bottom: 0,
          containLabel: true
        }}
        emptySize={88}
      />
    </Card>
  )
}

export default LineCard
