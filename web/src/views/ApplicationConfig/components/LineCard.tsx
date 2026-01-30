import { type FC, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';
import * as echarts from 'echarts';
import Empty from '@/components/Empty'

import Card from './Card'
import type { StatisticsItem } from '../types'

interface LineCardProps {
  chartData: StatisticsItem[];
  type: string;
  total: number;
}

const SeriesConfig = {
  type: 'line',
  stack: 'Total',
  smooth: true,
  lineStyle: {
    width: 3
  },
  showSymbol: true,
  label: {
    show: false,
    position: 'top'
  },
  emphasis: {
    focus: 'series'
  },
}

const ColorObj: Record<string, string> = {
  daily_conversations: '#FFB048',
  daily_new_users: '#4DA8FF',
  daily_api_calls: '#155EEF',
  daily_tokens: '#AD88FF'
}

const LineCard: FC<LineCardProps> = ({ chartData, type, total }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);

  useEffect(() => {

  }, [chartData])

  const getSeries = () => {
    return [{
      ...SeriesConfig,
      name: t(`application.${type}`),
      data: chartData.map(vo => vo.count),
      areaStyle: {
        opacity: 0.8,
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: ColorObj[type] },
          { offset: 1, color: '#FFFFFF' }
        ])
      },
    }]
  }

  return (
    <Card
      title={<div>{t(`application.${type}`)} <span className="rb:text-[#155EEF] rb:font-medium rb:text-[18px]">{total}</span></div>}
    >
      {chartData && chartData.length > 0 ? (
        <ReactEcharts
          ref={chartRef}
          option={{
            color: [ColorObj[type]],
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
                },
                label: {
                  show: false
                }
              },
            },

            grid: {
              top: 10,
              left: 15,
              right: 40,
              bottom: 0,
              containLabel: true
            },
            xAxis: {
              type: 'category',
              data: chartData.map(item => item.date),
              boundaryGap: false,
            },
            yAxis: {
              type: 'value',
              axisLabel: {
                color: '#A8A9AA',
                fontFamily: 'PingFangSC, PingFang SC',
                align: 'right',
                lineHeight: 17,
              },
              axisLine: {
                lineStyle: {
                  color: '#EBEBEB',
                }
              },
            },
            series: getSeries()
          }}
          style={{ height: '265px', width: '100%', minWidth: '100%', boxSizing: 'border-box' }}
          opts={{ renderer: 'canvas' }}
          notMerge={true}
          lazyUpdate={true}
        />
      ) : <Empty size={120} className="rb:mt-12 rb:mb-20.25" />}
    </Card>
  )
}

export default LineCard
