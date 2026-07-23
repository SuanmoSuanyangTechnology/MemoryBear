/*
 * @Author: ZhaoYing
 * @Date: 2026-02-03 18:32:07
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:23:11
 */
import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';

import Empty from '@/components/Empty'
import Loading from '@/components/Empty/Loading'
import RbCard from '@/components/RbCard/Card'
import type { ForgetTrendData } from '../types'

/**
 * Props for RecentTrendsLineCard component
 * @property {ForgetTrendData[]} chartData - Daily forgetting trend data ({ date, count })
 * @property {boolean} [loading] - Loading state
 */
interface RecentTrendsLineCardProps {
  chartData: ForgetTrendData[];
  loading?: boolean;
}

const BarColor = '#155EEF'

const axisLabelConfig = {
  color: '#5B6167',
  fontSize: 10,
  lineHeight: 14,
  fontFamily: 'PingFangSC, PingFang SC',
  formatter: '{value}'
}

/**
 * RecentTrendsLineCard Component
 * Displays the 7-day forgetting trend as a single-series bar chart of daily forgetting counts.
 */
const RecentTrendsLineCard: FC<RecentTrendsLineCardProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);

  return (
    <RbCard
      title={t('forgetDetail.forgetTrend7Days')}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-46px)]"
      className="rb:h-full!"
    >
      {loading
        ? <Loading size={150} />
        : !chartData || chartData.length === 0
        ? <Empty size={120} className="rb:h-full!" />
        : <ReactEcharts
            ref={chartRef}
            option={{
              color: [BarColor],
              tooltip: {
                trigger: 'axis',
                extraCssText: 'box-shadow: 0px 2px 6px 0px rgba(33,35,50,0.16); border-radius: 8px;',
                axisPointer: {
                  type: 'shadow',
                  crossStyle: {
                    color: '#5F6266',
                  },
                  lineStyle: {
                    color: '#5F6266',
                  }
                },
                formatter: function(params: any) {
                  let result = `${params[0].axisValue}<br/>`
                  params.forEach((param: any) => {
                    result += `${param.marker}${param.seriesName}: ${param.value}<br/>`
                  })
                  return result
                }
              },
              legend: {
                bottom: 2,
                padding: 0,
                itemGap: 8,
                itemWidth: 12,
                itemHeight: 6,
                icon: 'roundRect',
                orient: 'horizontal',
                textStyle: axisLabelConfig,
                data: [{
                  name: t('forgetDetail.dailyForget'),
                  itemStyle: {
                    color: BarColor + '14',
                    borderColor: BarColor,
                    borderWidth: 1,
                  }
                }]
              },
              grid: {
                top: 16,
                left: 30,
                right: 20,
                bottom: 48,
              },
              xAxis: {
                type: 'category',
                data: chartData.map(item => item.date),
                boundaryGap: true,
                axisLabel: axisLabelConfig,
                axisLine: {
                  show: true,
                  lineStyle: {
                    color: '#DFE4ED'
                  }
                },
                splitLine: {
                  show: false,
                },
                axisTick: {
                  show: false,
                }
              },
              yAxis: {
                type: 'value',
                position: 'left',
                minInterval: 1,
                axisLabel: {
                  ...axisLabelConfig,
                  formatter: (value: number) => Math.round(value)
                },
                axisLine: {
                  lineStyle: {
                    color: BarColor
                  }
                },
                splitLine: {
                  show: true,
                  lineStyle: {
                    color: '#DFE4ED',
                    type: 'solid'
                  }
                },
              },
              series: [{
                name: t('forgetDetail.dailyForget'),
                type: 'bar',
                barWidth: 14,
                barMaxWidth: 18,
                itemStyle: {
                  color: BarColor,
                  borderRadius: [4, 4, 0, 0],
                },
                data: chartData.map(item => item.count)
              }]
            }}
            style={{ height: '254px', width: '100%', minWidth: '100%' }}
            notMerge={true}
            lazyUpdate={true}
          />
      }
    </RbCard>
  )
}

export default RecentTrendsLineCard
