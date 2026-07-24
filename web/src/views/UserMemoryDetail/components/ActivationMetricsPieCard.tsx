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
 * @property {Array<Record<string, string | number>>} chartData - Distribution data for the donut chart
 * @property {boolean} loading - Loading state
 * @property {string} [title] - Optional card title (i18n key already resolved). Falls back to activation distribution.
 * @property {string[]} [colors] - Optional custom segment colours
 * @property {number|string} [centerValue] - Optional big number rendered in the donut centre
 * @property {string} [centerLabel] - Optional label rendered under the centre value
 */
interface ActivationMetricsPieCardProps {
  chartData: Array<Record<string, string | number>>;
  loading: boolean;
  title?: string;
  colors?: string[];
  centerValue?: number | string;
  centerLabel?: string;
}
const ActivationMetricsPieCard: FC<ActivationMetricsPieCardProps> = ({
  chartData,
  loading,
  title,
  colors,
  centerValue,
  centerLabel,
}) => {
  const { t } = useTranslation()

  const showCenter = centerValue !== undefined || centerLabel !== undefined

  return (
    <RbCard
      title={title ?? t('forgetDetail.activationValueDistribution')}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-46px)]"
      className="rb:h-full!"
    >
      {loading
        ? <Loading size={150} />
        : <div className="rb:relative">
          <PieChart
            chartData={chartData as { name: string; value: number }[]}
            height={254}
            seriesWidth={150}
            seriesHeight={150}
            itemGap={14}
            seriesLabel={false}
            seriesTop={20}
            colors={colors}
          />
          {chartData && chartData.length > 0 && showCenter && (
            <div className="rb:absolute rb:left-1/2 rb:-translate-x-1/2 rb:top-[70px] rb:text-center rb:pointer-events-none">
              <div className="rb:text-[24px] rb:leading-7 rb:font-[MiSans-Bold] rb:font-bold rb:text-[#171719]">{centerValue}</div>
              {centerLabel && <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167] rb:mt-1">{centerLabel}</div>}
            </div>
          )}
        </div>
      }
    </RbCard>
  )
}

export default ActivationMetricsPieCard
