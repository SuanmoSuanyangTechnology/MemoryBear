/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:57 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:57 
 */
import { type FC, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react'

import Empty from '@/components/Empty'
import Loading from '@/components/Empty/Loading'
import type { Interaction } from '../pages/GraphDetail'

/**
 * Props for InteractionBar component
 * @property {Interaction[]} chartData - Interaction count data over time
 * @property {boolean} [loading] - Loading state
 */
interface InteractionBarProps {
  chartData: Interaction[];
  loading?: boolean;
}

const Colors = ['#155EEF', '#369F21', '#FF5D34']

/**
 * InteractionBar Component
 * Displays user interaction counts over time as a bar chart
 * Shows daily interaction frequency
 */
const InteractionBar: FC<InteractionBarProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()

  const series = useMemo(() => {
    return [{
      name: t('userMemory.interactionCountData'),
      type: 'bar',
      data: chartData.map(item => item.count)
    }]
  }, [chartData, t])

  return (
    <>
      <div>{t('userMemory.interaction')}</div>
      {loading
        ? <Loading size={249} />
        : !chartData || chartData.length === 0
          ? <Empty size={120} className="rb:mt-12 rb:mb-20.25" />
          : <ReactEcharts
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
                data: chartData.map(item => item.created_at),
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
              yAxis: {
                type: 'value',
                minInterval: 1,
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
                },
              },
              series
            }}
            style={{ height: '265px', width: '100%' }}
          />
      }
    </>
  )
}

export default InteractionBar
