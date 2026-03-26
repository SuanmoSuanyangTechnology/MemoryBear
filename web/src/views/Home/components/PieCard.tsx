/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:16:45 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-10 11:57:35
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
        : <PieChart chartData={chartData} />
      }
    </Card>
  )
}

export default PieCard
