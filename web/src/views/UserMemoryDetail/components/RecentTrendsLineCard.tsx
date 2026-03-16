/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:07 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 11:49:29
 */
import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';

import Empty from '@/components/Empty'
import Loading from '@/components/Empty/Loading'
import RbCard from '@/components/RbCard/Card'

/**
 * Props for RecentTrendsLineCard component
 * @property {Array<Record<string, string | number>>} chartData - Trend data over time
 * @property {string[]} seriesList - List of series keys to display
 * @property {boolean} [loading] - Loading state
 */
interface RecentTrendsLineCardProps {
  chartData: Array<Record<string, string | number>>;
  seriesList: string[];
  loading?: boolean;
}

const Colors = ['#155EEF', '#FF5D34']

const axisLabelConfig = {
  color: '#5B6167',
  fontSize: 10,
  lineHeight: 14,
  fontFamily: 'PingFangSC, PingFang SC',
  formatter: '{value}'
}
/**
 * RecentTrendsLineCard Component
 * Displays forgetting trends with dual Y-axis line chart
 * Shows merged count and average activation over time
 */
const RecentTrendsLineCard: FC<RecentTrendsLineCardProps> = ({ chartData, seriesList, loading }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);

  const getSeries = () => {
    return seriesList.map((key, index) => ({
      name: key === 'merged_count' ? t('forgetDetail.merged_count') : t('forgetDetail.average_activation'),
      type: 'line',
      yAxisIndex: key === 'merged_count' ? 0 : 1,
      smooth: true,
      lineStyle: {
        width: 3,
        color: Colors[index]
      },
      itemStyle: {
        color: Colors[index]
      },
      areaStyle: {
        color: Colors[index],
        opacity: 0.08
      },
      data: chartData.map(item => item[key])
    }))
  }

  return (
    <RbCard
      title={t('forgetDetail.forgettingTrend')}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-46px)]"
      className="rb:h-full!"
    >
      {loading
        ? <Loading size={249} />
        : !chartData || chartData.length === 0
        ? <Empty size={120} className="rb:mt-12 rb:mb-20.25" />
        : <ReactEcharts
            ref={chartRef}
            option={{
              color: Colors,
              tooltip: {
                trigger: 'axis',
                extraCssText: 'box-shadow: 0px 2px 6px 0px rgba(33,35,50,0.16); border-radius: 8px;',
                axisPointer: {
                  type: 'line',
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
                data: seriesList.map((key, index) => ({
                  name: key === 'merged_count' ? t('forgetDetail.merged_count') : t('forgetDetail.average_activation'),
                  itemStyle: {
                    color: Colors[index] + '14',
                    borderColor: Colors[index],
                    borderWidth: 1,
                  }
                }))
              },
              grid: {
                top: 16,
                left: 30,
                right: 36,
                bottom: 48,
                // containLabel: false
              },
              xAxis: {
                type: 'category',
                data: chartData.map(item => item.date),
                boundaryGap: false,
                axisLabel: axisLabelConfig,
                axisLine: {
                  show: true,
                  lineStyle: {
                    color: '#DFE4ED'
                  }
                },
                splitLine: {
                  show: true,
                  lineStyle: {
                    color: '#DFE4ED',
                    type: 'solid'
                  }
                },
                axisTick: {
                  show: false,
                }
              },
              yAxis: [
                {
                  type: 'value',
                  position: 'left',
                  axisLabel: axisLabelConfig,
                  axisLine: {
                    lineStyle: {
                      color: Colors[0]
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
                {
                  type: 'value',
                  position: 'right',
                  axisLabel: axisLabelConfig,
                  axisLine: {
                    lineStyle: {
                      color: Colors[1]
                    }
                  },
                  splitLine: {
                    show: false,
                  },
                  max: 1,
                  min: 0
                }
              ],
              series: getSeries()
            }}
            style={{ height: '214px', width: '100%', minWidth: '100%' }}
            notMerge={true}
            lazyUpdate={true}
          />
      }
    </RbCard>
  )
}

export default RecentTrendsLineCard
