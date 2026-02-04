/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:16:45 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:16:45 
 */
/**
 * Pie Chart Card Component
 * Displays knowledge base type distribution with ECharts donut chart
 */

import { type FC, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';

import Card from './Card'
import Loading from '@/components/Empty/Loading'
import Empty from '@/components/Empty'

/**
 * Component props
 */
interface PieCardProps {
  chartData: Array<Record<string, string | number>>;
  loading: boolean;
}
/** Chart color palette */
const Colors = ['#155EEF', '#31E8FF', '#AD88FF', '#FFB048', '#4DA8FF', '#03BDFF']

const PieCard: FC<PieCardProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);
  const resizeScheduledRef = useRef(false)
  
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
  }, [chartData])

  return (
    <Card
      title={t('dashboard.knowledgeBaseTypeDistribution')}
    >
      {loading
      ? <Loading size={249} />
      : !chartData || chartData.length === 0
      ? <Empty size={120} className="rb:mt-12 rb:mb-20.25" />
      : <ReactEcharts
          option={{
            color: Colors,
            tooltip: {
              trigger: 'item',
              textStyle: {
                color: '#5B6167',
                fontSize: 12,
                width: 27,
                height: 16,
              },
              formatter: '{d}%',
              padding: [8, 5],
              backgroundColor: '#FFFFFF',
              borderColor: '#DFE4ED',
              extraCssText: 'width: 36px; height: 36px; box-shadow: 0px 2px 4px 0px rgba(33,35,50,0.12);border-radius: 36px;'
            },
            legend: {
              right: 20 ,
              top: 'middle',
              padding: 0,
              itemWidth: 12,
              itemHeight: 12,
              borderRadius: 2,
              orient: 'vertical',
              textStyle: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 16,
              }
            },
            series: [
              {
                name: 'Access From',
                type: 'pie',
                radius: ['60%', '100%'],
                avoidLabelOverlap: false,
                percentPrecision: 0,
                padAngle: 4,
                width: 200,
                height: 200,
                left: '10%',
                top: 'middle',
                itemStyle: {
                  borderRadius: 0
                },
                label: {
                  show: false,
                  position: 'center'
                },
                emphasis: {
                  label: {
                    show: true,
                    fontSize: 24,
                    fontWeight: 'bold',
                    color: '#212332',
                    formatter: '{d}%\n{b}',
                  }
                },
                labelLine: {
                  show: false
                },
                data: chartData
              }
            ]
          }}
          style={{ height: '265px', width: '100%', minWidth: '400px' }}
          notMerge={true}
          lazyUpdate={true}
        />
      }
    </Card>
  )
}

export default PieCard
