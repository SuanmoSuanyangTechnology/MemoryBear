/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:16 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:22:10
 */
import { type FC } from 'react'
import { useTranslation } from 'react-i18next'

import Loading from '@/components/Empty/Loading'
import RbCard from '@/components/RbCard/Card'
import PieChart from '@/components/Charts/PieChart'

/**
 * Props for ActivationMetricsPieCard component
 * @property {Array<Record<string, string | number>>} chartData - Activation value distribution data
 * @property {boolean} loading - Loading state
 */
interface ActivationMetricsPieCardProps {
  chartData: Array<Record<string, string | number>>;
  loading: boolean;
}
const ActivationMetricsPieCard: FC<ActivationMetricsPieCardProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()

  return (
    <RbCard
      title={t('forgetDetail.activationValueDistribution')}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-46px)]"
      className="rb:h-full!"
    >
      {loading
        ? <Loading size={150} />
        : <PieChart
          chartData={chartData as { name: string; value: number }[]}
          height={214}
          seriesWidth={150}
          seriesHeight={150}
          itemGap={14}
          seriesLabel={false}
          seriesTop={5}
        />
      }
    </RbCard>
  )
}

export default ActivationMetricsPieCard
