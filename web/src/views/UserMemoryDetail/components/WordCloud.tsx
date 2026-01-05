import { type FC, useEffect, useState, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react'
import { Progress } from 'antd'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionTags } from '@/api/memory'

interface WordCloud {
  tags: Array<{
    emotion_type: string;
    count: number;
    percentage: number;
    avg_intensity: number;
  }>;
  total_count: number;
}
const WordCloud: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null);
  const resizeScheduledRef = useRef(false)
  const [wordCloud, setWordCloud] = useState<WordCloud | null>(null)

  useEffect(() => {
    getWordCloudData()
  }, [id])

  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && !resizeScheduledRef.current) {
        resizeScheduledRef.current = true
        requestAnimationFrame(() => {
          chartRef.current?.getEchartsInstance().resize();
          resizeScheduledRef.current = false
        });
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    const chartElement = chartRef.current?.getEchartsInstance().getDom().parentElement
    if (chartElement) {
      resizeObserver.observe(chartElement)
    }

    return () => {
      resizeObserver.disconnect()
    }
  }, [wordCloud])

  const getWordCloudData = () => {
    if (!id) {
      return
    }
    getEmotionTags(id)
      .then((res) => {
        setWordCloud(res as WordCloud)
      })
  }
  const radarOption = useMemo(() => {
    if (!wordCloud?.tags.length) return {}
    
    // 将avg_intensity转换为1-100范围
    const radarData = wordCloud.tags.map(item => ({
      name: item.emotion_type,
      value: Math.round(item.avg_intensity * 100),
      count: item.count,
      percentage: item.percentage
    }))
    
    return {
      tooltip: {
        trigger: 'item',
        formatter: (params: any) => {
          const dataIndex = params.dataIndex
          const item = radarData[dataIndex]
          return `${item.name}<br/>${item.percentage.toFixed(1)}%`
        }
      },
      radar: {
        indicator: radarData.map(item => ({
          name: t(`statementDetail.${item.name}`),
          max: 100,
          min: 1
        }))
      },
      series: [{
        type: 'radar',
        name: 'Emotion Intensity',
        data: [{
          value: radarData.map(item => item.value),
          name: 'Emotion Intensity'
        }]
      }]
    }
  }, [wordCloud])

  return (
    <RbCard
      title={t('statementDetail.wordCloud')}
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
      height="100%"
    >
      {wordCloud?.total_count && wordCloud?.total_count > 0
        ? <div className="rb:flex rb:h-100">
          <ReactEcharts ref={chartRef} option={radarOption} style={{ width: '50%', height: '100%' }} />
          <div className="rb:w-[50%] rb:pl-4 rb:flex rb:flex-col rb:justify-center">
            <div className="rb:text-[18px] rb:font-medium rb:mb-4">样本数：{wordCloud.total_count}</div>
            <div className="rb:space-y-3">
              {wordCloud.tags.map(item => (
                <div key={item.emotion_type}>
                  <div className="rb:flex rb:items-center rb:justify-between rb:font-medium">
                    {t(`statementDetail.${item.emotion_type}`)}
                    <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular">{item.count}{t('statementDetail.pieces')}</div>
                  </div>
                  <Progress size="small" percent={item.percentage} />
                </div>
              ))}
            </div>
          </div>
        </div>
        : <Empty size={88} />
      }
    </RbCard>
  )
}

export default WordCloud