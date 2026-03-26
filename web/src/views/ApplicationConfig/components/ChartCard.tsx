/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:28:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 13:55:11
 */
/**
 * Line chart card component for displaying statistics
 * Uses ECharts to render time-series data with gradient area fill
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'

import type { StatisticsItem } from '../types'
import RbCard from '@/components/RbCard/Card';
import AreaLineChart from '@/components/Charts/AreaLineChart'
import BarChart from '@/components/Charts/BarChart'

/**
 * Component props
 */
interface ChartCardProps {
  /** Chart data points */
  chartData: StatisticsItem[];
  /** Statistics type key */
  type: string;
  /** Total count to display */
  total: number;
}

/**
 * Color mapping for different statistic types
 */
const ColorObj: Record<string, string> = {
  daily_conversations: '#155EEF',
  daily_new_users: '#9C6FFF',
  daily_api_calls: '#155EEF',
  daily_tokens: '#FF8A4C'
}

/**
 * Line chart card component
 * Displays time-series statistics with gradient area chart
 */
const ChartCard: FC<ChartCardProps> = ({ chartData, type, total }) => {
  const { t } = useTranslation()

  return (
    <RbCard
      title={t(`application.${type}`)}
      subTitle={<span className="rb:font-[MiSans-Bold] rb:text-[#171719] rb:font-bold rb:text-[28px] rb:leading-9.5">{total}</span>}
      headerType="borderless"
      headerClassName="rb:min-h-26!"
    >
      <div className="rb:h-50">
        {type === 'daily_conversations' || type === 'daily_tokens' ? (
          <AreaLineChart
            chartData={chartData}
            colors={[ColorObj[type]]}
            xAxisKey="date"
            seriesList={{ count: t(`application.${type}`) }}
            height={200}
            lineStyle={{width: 3}}
            showLegend={false}
            grid={{
              top: 7,
              left: 4,
              right: 18,
              bottom: 0,
              containLabel: true
            }}
            smooth={type === 'daily_conversations'}
          />
        )
        : <BarChart
            chartData={chartData}
            colors={[ColorObj[type]]}
            xAxisKey="date"
            seriesList={{ count: t(`application.${type}`) }}
            height={200}
            showLegend={false}
            grid={{
              top: 7,
              left: 4,
              right: 18,
              bottom: 0,
              containLabel: true
            }}
            itemStyle={type === 'daily_new_users' ? { color: ColorObj[type] } : null}
            showBackground={type === 'daily_api_calls'}
          />}
      </div>
    </RbCard>
  )
}

export default ChartCard
