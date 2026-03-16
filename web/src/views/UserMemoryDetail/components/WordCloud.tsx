/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:24 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-03-16 15:02:21 
 */
import { type FC, useEffect, useState, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react'
import { Progress, Row, Col, Flex} from 'antd'

import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'
import { getEmotionTags } from '@/api/memory'

/**
 * Word cloud data structure
 * @property {Array} tags - Emotion tags with statistics
 * @property {number} total_count - Total count of tags
 */
interface WordCloud {
  tags: Array<{
    emotion_type: string;
    count: number;
    percentage: number;
    avg_intensity: number;
  }>;
  total_count: number;
}

/**
 * WordCloud Component
 * Displays emotion distribution as radar chart with statistics
 * Shows emotion types, counts, percentages, and average intensity
 */
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
    
    // Convert avg_intensity to 1-100 range
    const radarData = wordCloud.tags.map(item => ({
      name: item.emotion_type,
      value: Math.round(item.avg_intensity * 100),
      count: item.count,
      percentage: item.percentage
    }))
    
    return {
      color: ['#155EEF'],
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
          min: 1,
          color: '#5B6167',
          axisLine: {
            lineStyle: {
              color: '#EBEBEB'
            }
          },
          splitLine: {
            show: false,
          },
          axisLabel: {
            show: true,
            color: '#A8A9AA',
            fontSize: 10,
            customValues: [20, 40, 60, 80, 100],
            align: 'center',
            margin: 0,
          }
        }))
      },
      series: [{
        type: 'radar',
        name: 'Emotion Intensity',
        data: [{
          value: radarData.map(item => item.value),
          name: 'Emotion Intensity',
          symbol: 'circle'
        }]
      }]
    }
  }, [wordCloud])

  return (
    <RbCard
      title={t('statementDetail.wordCloud')}
      headerType="borderless"
      headerClassName="rb:min-h-[50px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName='rb:px-[22px]! rb:pb-[28px]! rb:pt-0! rb:h-[calc(100%-54px)]'
      className="rb:h-full!"
    >
      {wordCloud?.total_count && wordCloud?.total_count > 0
        ? <Row gutter={58}>
          <Col span={12}>
            <ReactEcharts
              ref={chartRef}
              option={radarOption}
              style={{ width: '100%', height: 'calc(100% - 88px)' }}
            />
            <div className="rb:text-center rb:bg-[#F6F6F6] rb:rounded-lg rb:p-2.5 rb:mt-4">
              <span className="rb:font-[MiSans-Heavy] rb:font-bold rb:text-[24px] rb:leading-8">{wordCloud.total_count}</span><br />
              <span className="rb:text-[#5B6167] rb:leading-5">{t('statementDetail.totalCount')}</span>
            </div>
          </Col>
          <Col span={12}>
            <Flex vertical gap={20} className="rb:pt-1!">
              {wordCloud.tags.map(item => (
                <div key={item.emotion_type}>
                  <div className="rb:flex rb:items-center rb:justify-between">
                    <div>
                      <span className="rb:font-medium rb:text-[#212332]">{t(`statementDetail.${item.emotion_type}`)}</span>
                      <span className="rb:font-regular rb:text-[#5B6167]">({item.count} {t('statementDetail.pieces')})</span>
                    </div>
                    <div className="rb:font-medium">{item.percentage.toFixed(1)}%</div>
                  </div>
                  <Progress strokeColor="#155EEF" percent={item.percentage} showInfo={false} />
                </div>
              ))}
            </Flex>
          </Col>
        </Row>
        : <Empty size={88} />
      }
    </RbCard>
  )
}

export default WordCloud