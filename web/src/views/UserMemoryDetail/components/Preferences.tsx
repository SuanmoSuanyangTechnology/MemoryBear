/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 14:30:25
 */
import { useEffect, useState, useRef, useMemo, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

import Empty from '@/components/Empty'
import { getImplicitPreferences } from '@/api/memory'
import RbAlert from '@/components/RbAlert'

/**
 * Preference item structure
 * @property {string} tag_name - Tag name
 * @property {number} confidence_score - Confidence score (0-1)
 * @property {string[]} supporting_evidence - Supporting evidence
 * @property {string} context_details - Context details
 * @property {number | string} created_at - Creation timestamp
 * @property {number | string} updated_at - Update timestamp
 * @property {string[]} conversation_references - Conversation references
 * @property {string} category - Category
 */
interface PreferenceItem {
  tag_name: string;
  confidence_score: number;
  supporting_evidence: string[];
  context_details: string;
  created_at: number | string;
  updated_at: number | string;
  conversation_references: string[];
  category: string;
}

/**
 * Default color palette for categories
 */
const DEFAULT_COLORS = ['#FF8A4C', '#FF5D34', '#155EEF', '#9C6FFF', '#4DA8FF', '#369F21']

/**
 * Generate color mapping for categories
 * @param {string[]} categories - List of categories
 * @returns {Record<string, string>} Category to color mapping
 */
const generateCategoryColors = (categories: string[]) => {
  const colors: Record<string, string> = {}
  categories.forEach((category, index) => {
    colors[category] = DEFAULT_COLORS[index % DEFAULT_COLORS.length]
  })
  return colors
}

/**
 * Preferences Component
 * Displays user preferences as an interactive word cloud
 * Shows detailed context and evidence when a word is selected
 */
const Preferences = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<HTMLDivElement>(null)
  const chartInstance = useRef<echarts.ECharts | null>(null)
  const [selectedWord, setSelectedWord] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<PreferenceItem[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) {
      return
    }
    setLoading(true)
    setSelectedWord(null)
    getImplicitPreferences(id)
      .then((res) => {
        setData(res as PreferenceItem[])
      })
      .finally(() => {
        setLoading(false)
      })
  }

  const uniqueCategories = [...new Set(data.map(item => item.category).filter(Boolean))]
  const categoryColors = generateCategoryColors(uniqueCategories)
  
  const getCategoryColor = (category: string) => {
    return categoryColors[category] || '#4DA8FF'
  }

  useEffect(() => {
    if (!chartRef.current || !data.length) return

    if (chartInstance.current) {
      chartInstance.current.dispose()
    }

    chartInstance.current = echarts.init(chartRef.current)

    const wordCloudData = data.map((item, index) => ({
      name: item.tag_name,
      value: Math.round(item.confidence_score * 100),
      itemIndex: index,
      textStyle: {
        color: getCategoryColor(item.category)
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

    chartInstance.current.on('click', (params) => {
      const clickedIndex = (params.data as any).itemIndex
      if (selectedWord !== clickedIndex) {
        setSelectedWord(clickedIndex)
      }
      
      // Highlight selected word without redrawing
      chartInstance.current?.dispatchAction({
        type: 'highlight',
        dataIndex: clickedIndex
      })
    })

    return () => {
      if (chartInstance.current) {
        chartInstance.current.dispose()
        chartInstance.current = null
      }
    }
  }, [data])

  const detailTitle = useMemo(() => {
    return selectedWord !== null && data[selectedWord].tag_name ? <>{data[selectedWord].tag_name}{t('implicitDetail.preferencesDetail')}</> : ''
  }, [selectedWord, data, t])

  useImperativeHandle(ref, () => ({
    handleRefresh: getData
  }));
  return (
    <>
      <RbAlert color="orange">{t('implicitDetail.preferencesTip')}</RbAlert>
      <div className="rb-border rb:rounded-xl rb:h-60 rb:my-3">
        {loading
          ? <Skeleton active className="rb:px-4" />
          : data && data.length > 0
            ? <div ref={chartRef} className="rb:px-3 rb:h-full" />
            : <Empty size={88} className="rb:h-full" />
        }
      </div>
      <div className="rb:h-[calc(100%-296px)] rb:overflow-y-auto">
        {selectedWord === null
          ? <Empty
            subTitle={t('implicitDetail.wordEmpty')}
            size={96}
            className="rb:h-full"
          />
          : <>
            <div className="rb:px-1 rb:pt-1 rb:pb-3 rb:font-medium rb:leading-5">{detailTitle}</div>
            <div className="rb:bg-[#F6F6F6] rb:rounded-lg rb:px-3 rb:py-2.5">
              <div className="rb:leading-5 rb:mb-2 rb:font-medium">{t('implicitDetail.context_details')}</div>
              <div className="rb:leading-5">{data[selectedWord].context_details}</div>
            </div>

            <div className="rb:bg-[#F6F6F6] rb:rounded-lg rb:px-3 rb:py-2.5 rb:mt-3">
              <div className="rb:leading-5 rb:mb-2 rb:font-medium">{t('implicitDetail.supporting_evidence')}</div>
              <ul className="rb:list-disc rb:ml-4">
                {data[selectedWord].supporting_evidence.map((vo, index) => (
                  <li key={index} className="rb:text-[#5B6167]">{vo}</li>
                ))}
              </ul>
            </div>
          </>
        }
      </div>
    </>
  )
})

export default Preferences