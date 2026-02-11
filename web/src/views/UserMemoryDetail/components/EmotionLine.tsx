/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:44 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:44 
 */
import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';

import Empty from '@/components/Empty'
import Loading from '@/components/Empty/Loading'
import type { Emotion } from '../pages/GraphDetail'

/**
 * Props for EmotionLine component
 * @property {Emotion[]} chartData - Emotion data over time
 * @property {boolean} [loading] - Loading state
 */
interface EmotionLineProps {
  chartData: Emotion[];
  loading?: boolean;
}

const Colors = ['#369F21', '#155EEF', '#FF5D34']

/**
 * EmotionLine Component
 * Displays emotion intensity trends over time as a multi-line chart
 * Shows different emotion types with smooth lines and area fills
 */
const EmotionLine: FC<EmotionLineProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);

  const getSeries = () => {
    const emotionTypes = [...new Set(chartData.map(item => item.emotion_type))]
    const timePoints = [...new Set(chartData.map(item => item.created_at))].sort()
    
    return emotionTypes.map((emotionType, index) => {
      const emotionData = chartData.filter(item => item.emotion_type === emotionType)
      const dataMap = new Map(emotionData.map(item => [item.created_at, item.emotion_intensity]))
      const seriesData = timePoints.map(time => dataMap.get(time) || 0)
      
      return {
        name: t(`userMemory.${emotionType}`),
        type: 'line',
        smooth: true,
        lineStyle: {
          width: 3,
          color: Colors[index % Colors.length]
        },
        itemStyle: {
          color: Colors[index % Colors.length]
        },
        areaStyle: {
          color: Colors[index % Colors.length],
          opacity: 0.08
        },
        data: seriesData
      }
    })
  }

  return (
    <>
      <div>{t('userMemory.emotionLine')}</div>
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
                    result += `${param.marker}${param.seriesName}: ${param.value}%<br/>`
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
                left: 40,
                right: 36,
                bottom: 48,
                // containLabel: false
              },
              xAxis: {
                type: 'category',
                data: [...new Set(chartData.map(item => item.created_at))].sort(),
                boundaryGap: false,
                axisLabel: {
                  color: '#A8A9AA',
                  fontFamily: 'PingFangSC, PingFang SC',
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
                axisLabel: {
                  color: '#A8A9AA',
                  fontFamily: 'PingFangSC, PingFang SC',
                  formatter: '{value}%'
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
                max: 100,
                min: 0
              },
              series: getSeries()
            }}
            style={{ height: '265px', width: '100%', minWidth: '100%' }}
            notMerge={true}
            lazyUpdate={true}
          />
      }
    </>
  )
}

export default EmotionLine
