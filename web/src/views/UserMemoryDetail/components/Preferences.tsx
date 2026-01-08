import { type FC, useEffect, useState, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Skeleton } from 'antd'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getImplicitPreferences } from '@/api/memory'

interface PreferenceItem {
  tag_name: string;
  confidence_score: number;
  supporting_evidence: string[];
  context_details: string;
  created_at: number | string; // TODO
  updated_at: number | string; // TODO
  conversation_references: string[];
  category: string;
}

const DEFAULT_COLORS = ['#FF5D34', '#155EEF', '#9C6FFF', '#369F21', '#4DA8FF', '#FF8C00', '#32CD32', '#FF69B4', '#20B2AA', '#DDA0DD']

const generateCategoryColors = (categories: string[]) => {
  const colors: Record<string, string> = {}
  categories.forEach((category, index) => {
    colors[category] = DEFAULT_COLORS[index % DEFAULT_COLORS.length]
  })
  return colors
}

const Preferences: FC = () => {
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


  console.log(selectedWord, data)

  const detailTitle = useMemo(() => {
    return selectedWord !== null && data[selectedWord].tag_name ? <>{data[selectedWord].tag_name}{t('implicitDetail.preferencesDetail')}</> : ''
  }, [selectedWord, data, t])

  return (
    <>
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-4 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('forgetDetail.overviewTitle')}</div>
      <Row gutter={16}>
        <Col span={16}>
          <RbCard
            title={t('implicitDetail.preferences')}
            headerType="borderless"
            headerClassName="rb:text-[18px]! rb:leading-[24px]"
            bodyClassName='rb:p-0! rb:pb-3! rb:relative rb:h-[350px]'
          >
            {loading
              ? <Skeleton active className="rb:px-4" />
              : data && data.length > 0
              ? <div ref={chartRef} className="rb:mt-6 rb:px-6" style={{ height: '350px' }} />
              : <Empty size={88} className="rb:h-full" />
            }
          </RbCard>
        </Col>
        <Col span={8}>
          <RbCard
            title={detailTitle}
            headerType="borderless"
            height="100%"
            bodyClassName='rb:p-3! rb:h-[326px]'
          >
            {selectedWord === null
              ? <Empty size={88} className="rb:h-full!" />
              : <>
                <div className="rb:leading-5 rb:mb-1 rb:font-medium">{t('implicitDetail.context_details')}</div>
                <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular">{data[selectedWord].context_details}</div>

                <div className="rb:leading-5 rb:mt-3 rb:font-medium">{t('implicitDetail.supporting_evidence')}</div>
                {data[selectedWord].supporting_evidence.map((vo, index) => <div key={index} className="rb:text-[#5B6167] rb:leading-5 rb:font-regular">-{vo}</div>)}
              </>
            }
          </RbCard>
        </Col>
      </Row>
    </>
  )
}

export default Preferences