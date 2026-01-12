import { type FC, useEffect, useState, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react'
import { Progress, Row, Col } from 'antd'

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
      headerClassName="rb:leading-[24px] rb:bg-[#F6F8FC]! rb:min-h-[46px]! rb:border-b! rb:border-b-[#DFE4ED]!"
      bodyClassName="rb:px-[28px]! rb:py-[16px]!"
    >
      {wordCloud?.total_count && wordCloud?.total_count > 0
        ? <Row gutter={50}>
          <Col span={12}>
            <ReactEcharts ref={chartRef} option={radarOption} style={{ width: '100%', height: 'calc(100% - 100px)' }} />
            <div className="rb:mb-4 rb:text-center rb:bg-[#F5F7FC] rb:rounded-lg rb:p-2.5 rb:mt-4">
              <span className="rb:text-[#155EEF] rb:text-[28px] rb:font-bold rb:leading-8">{wordCloud.total_count}</span><br />
              <span className="rb:text-[#5B6167] rb:leading-5">{t('statementDetail.totalCount')}</span>
            </div>
          </Col>
          <Col span={12}>
            <div className="rb:space-y-5">
              {wordCloud.tags.map(item => (
                <div key={item.emotion_type}>
                  <div className="rb:flex rb:items-center rb:justify-between">
                    <div>
                      <span className="rb:font-medium">{t(`statementDetail.${item.emotion_type}`)}</span>
                      <span className="rb:font-regular rb:text-[#5B6167]"> ( {item.count} {t('statementDetail.pieces')} )</span>
                    </div>
                    <div className="rb:text-[12px] rb:text-[#155EEF] rb:font-medium">{item.percentage.toFixed(1)}%</div>
                  </div>
                  <Progress strokeColor="#155EEF" percent={item.percentage} showInfo={false} />
                </div>
              ))}
            </div>
          </Col>
        </Row>
        : <Empty size={88} />
      }
    </RbCard>
  )
}

export default WordCloud