/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:07 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:07 
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
                itemGap: 24,
                itemWidth: 40,
                itemHeight: 12,
                borderRadius: 2,
                orient: 'horizontal',
                textStyle: {
                  color: '#5B6167',
                  fontFamily: 'PingFangSC, PingFang SC',
                  lineHeight: 16,
                }
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
                axisLabel: {
                  color: '#A8A9AA',
                  fontFamily: 'PingFangSC, PingFang SC'
                },
                axisLine: {
                  show: true,
                  lineStyle: {
                    color: '#EBEBEB'
                  }
                },
                splitLine: {
                  show: true,
                  lineStyle: {
                    color: '#EBEBEB',
                    type: 'solid'
                  }
                },
                axisTick: {
                  show: true,
                  lineStyle: {
                    color: '#EBEBEB',
                    type: 'solid'
                  }
                }
              },
              yAxis: [
                {
                  type: 'value',
                  position: 'left',
                  axisLabel: {
                    color: Colors[0],
                    fontFamily: 'PingFangSC, PingFang SC'
                  },
                  axisLine: {
                    lineStyle: {
                      color: Colors[0]
                    }
                  },
                  splitLine: {
                    show: true,
                    lineStyle: {
                      color: '#EBEBEB',
                      type: 'solid'
                    }
                  },
                  axisTick: {
                    show: true,
                    lineStyle: {
                      color: '#EBEBEB',
                      type: 'solid'
                    }
                  },
                },
                {
                  type: 'value',
                  position: 'right',
                  axisLabel: {
                    color: Colors[1],
                    fontFamily: 'PingFangSC, PingFang SC',
                    formatter: '{value}'
                  },
                  axisLine: {
                    lineStyle: {
                      color: Colors[1]
                    }
                  },
                  splitLine: {
                    show: false,
                  },
                  axisTick: {
                    show: true,
                    lineStyle: {
                      color: '#EBEBEB',
                      type: 'solid'
                    }
                  },
                  max: 1,
                  min: 0
                }
              ],
              series: getSeries()
            }}
            style={{ height: '265px', width: '100%', minWidth: '100%' }}
            notMerge={true}
            lazyUpdate={true}
          />
      }
    </RbCard>
  )
}

export default RecentTrendsLineCard
