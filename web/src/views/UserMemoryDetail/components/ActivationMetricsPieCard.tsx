/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:16 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:34:16 
 */
import { type FC, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';

import Loading from '@/components/Empty/Loading'
import Empty from '@/components/Empty'
import RbCard from '@/components/RbCard/Card'

/**
 * Props for ActivationMetricsPieCard component
 * @property {Array<Record<string, string | number>>} chartData - Activation value distribution data
 * @property {boolean} loading - Loading state
 */
interface ActivationMetricsPieCardProps {
  chartData: Array<Record<string, string | number>>;
  loading: boolean;
}
const Colors = ['#155EEF', '#FFB048', '#FF5D34']

/**
 * ActivationMetricsPieCard Component
 * Displays activation value distribution as a donut chart with legend
 * Shows percentage distribution of different activation levels
 */
const ActivationMetricsPieCard: FC<ActivationMetricsPieCardProps> = ({ chartData, loading }) => {
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
    <RbCard
      title={t('forgetDetail.activationValueDistribution')}
      headerType="borderless"
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
              bottom: 14 ,
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
            series: [
              {
                name: 'Access From',
                type: 'pie',
                radius: ['50%', '90%'],
                avoidLabelOverlap: false,
                percentPrecision: 0,
                padAngle: 4,
                width: 200,
                height: 200,
                left: 143,
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
    </RbCard>
  )
}

export default ActivationMetricsPieCard
