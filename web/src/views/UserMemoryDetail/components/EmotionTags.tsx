import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import * as echarts from 'echarts'
import 'echarts-wordcloud'
import { Flex, Divider } from 'antd';

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getWordCloud } from '@/api/memory'
import { getEmotionColor } from '../constant'

/**
 * Tag list data structure
 * @property {Array} keywords - List of keywords with emotion data
 * @property {number} total_keywords - Total number of keywords
 */
interface TagList {
  keywords: Array<{ keyword: string; frequency: number; emotion_type: string; avg_intensity: number; }>;
  total_keywords: number;
}

/**
 * EmotionTags Component
 * Displays emotion-tagged keywords as a word cloud
 * Each keyword is colored based on its associated emotion type
 * Shows emotion statistics summary at the bottom
 */
const EmotionTags: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstance = useRef<echarts.ECharts | null>(null)
  const [data, setData] = useState<TagList | null>(null)

  useEffect(() => {
    if (!id) return
    getEmotionTagData()
  }, [id])

  const getEmotionTagData = () => {
    if (!id) return
    getWordCloud(id)
      .then((res) => {
        setData(res as TagList)
      })
  }

  useEffect(() => {
    if (!chartRef.current || !data?.keywords.length) return

    if (chartInstance.current) {
      chartInstance.current.dispose()
    }

    chartInstance.current = echarts.init(chartRef.current)

    const wordCloudData = data.keywords.map((item) => ({
      name: item.keyword,
      value: item.frequency,
      textStyle: {
        color: getEmotionColor(item.emotion_type)
      }
    }))

    const option = {
      series: [{
        type: 'wordCloud',
        gridSize: 8,
        sizeRange: [14, 60],
        rotationRange: [-45, 45],
        shape: 'pentagon',
        width: '100%',
        height: '100%',
        textStyle: {
          fontFamily: 'sans-serif',
          fontWeight: 'bold'
        },
        emphasis: {
          textStyle: {
            shadowBlur: 10,
            shadowColor: '#333'
          }
        },
        data: wordCloudData
      }]
    }

    chartInstance.current.setOption(option)

    return () => {
      if (chartInstance.current) {
        chartInstance.current.dispose()
        chartInstance.current = null
      }
    }
  }, [data])

  const emotionStats = data?.keywords.reduce((acc, item) => {
    acc[item.emotion_type] = (acc[item.emotion_type] || 0) + item.frequency
    return acc
  }, {} as Record<string, number>) ?? {}

  return (
    <RbCard
      title={t('statementDetail.emotionTags')}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-0! rb:pt-4! rb:h-[calc(100%-46px)]!"
      className="rb:h-full!"
    >
      {data?.keywords && data?.keywords.length > 0
        ? <Flex vertical>
          <div ref={chartRef} className="rb:px-3 rb:min-h-53 rb:flex-1 rb:w-full" />
          
          <div className="rb:px-4 rb:pb-2.5 rb:shrink-0">
            <Divider className="rb:my-2.5!" />
            <Flex wrap gap={12} className="rb:px-1.5!">
              {Object.keys(emotionStats).map((type) => {
                return (
                  <Flex key={type} align="center" gap={4}>
                    <div className="rb:size-1 rb:rounded-full" style={{ backgroundColor: getEmotionColor(type) }}></div>
                    <span className="rb:leading-5">{t(`statementDetail.${type || 'neutral'}`)}</span>
                  </Flex>
                )
              })}
            </Flex>
          </div>
        </Flex>
        : <Empty size={88} className="rb:h-full! rb:mb-4" />
      }
    </RbCard>
  )
}

export default EmotionTags