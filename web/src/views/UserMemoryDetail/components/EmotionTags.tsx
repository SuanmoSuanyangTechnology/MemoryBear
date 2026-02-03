/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:39 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:39 
 */
import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getWordCloud } from '@/api/memory'

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

  /**
   * Get color for emotion type
   * @param {string} emotionType - Emotion type (joy, anger, sadness, etc.)
   * @returns {string} Color hex code
   */
  const getEmotionColor = (emotionType: string) => {
    const colors: Record<string, string> = {
      joy: '#52c41a',
      anger: '#ff4d4f', 
      sadness: '#1890ff',
      fear: '#fa8c16',
      neutral: '#8c8c8c',
      surprise: '#722ed1'
    }
    return colors[emotionType] || '#8c8c8c'
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
      headerClassName="rb:leading-[24px] rb:bg-[#F6F8FC]! rb:min-h-[46px]! rb:border-b! rb:border-b-[#DFE4ED]!"
      bodyClassName="rb:p-0!"
    >
      {data?.keywords && data?.keywords.length > 0
        ? <div>
          <div ref={chartRef} className="rb:mt-6 rb:px-6" style={{ height: '320px', width: '100%' }} />
          <div className="rb:flex rb:flex-wrap rb:items-center rb:justify-center rb:gap-10 rb:text-sm rb:mt-3 rb:p-3 rb:bg-[#F0F3F8] rb:rounded-[0_0_8px_8px]">
            {Object.entries(emotionStats).map(([type, count]) => {
              console.log(type)
              return (
                <div key={type} className="rb:flex rb:items-center rb:gap-2">
                  <div className="rb:w-3 rb:h-3 rb:rounded-full" style={{ backgroundColor: getEmotionColor(type) }}></div>
                  <span className="rb:leading-5">{t(`statementDetail.${type || 'neutral'}`)} ({count}个)</span>
                </div>
              )
            })}
          </div>
        </div>
        : <Empty size={88} className="rb:h-full rb:mb-4" />
      }
    </RbCard>
  )
}

export default EmotionTags