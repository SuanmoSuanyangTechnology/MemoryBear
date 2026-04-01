/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:16:45 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-26 14:30:27
 */
/**
 * Pie Chart Card Component
 * Displays knowledge base type distribution with ECharts donut chart
 */

import { type FC } from 'react'
import { useTranslation } from 'react-i18next'

import Card from './Card'
import Loading from '@/components/Empty/Loading'
import PieChart, { type ChartData } from '@/components/Charts/PieChart'

/**
 * Component props
 */
interface PieCardProps {
  chartData: ChartData[];
  loading: boolean;
}

const PieCard: FC<PieCardProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()

  return (
    <Card
      title={t('dashboard.knowledgeBaseTypeDistribution')}
    >
      {loading
        ? <Loading size={249} />
        : <PieChart chartData={chartData} itemGap={24} />
      }
    </Card>
  )
}

export default PieCard
