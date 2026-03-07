/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:47 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 18:29:29
 */
/**
 * Interest Distribution Component
 * Displays user interest distribution as pie chart with tag list
 */

import { type FC, useRef, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react';
import clsx from 'clsx'

import { getInterestDistributionByUser } from '@/api/memory';
import Empty from '@/components/Empty';
import Loading from '@/components/Empty/Loading';
import RbCard from '@/components/RbCard/Card';

/** Chart color palette */
const Colors = ['#171719', '#155EEF', '#4DA8FF', '#9C6FFF', '#ABEBFF', '#DFE4ED']

const InterestDistribution: FC<{ className?: string; }> = ({ className }) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null);
  const resizeScheduledRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<Array<Record<string, string | number>>>([])

  useEffect(() => {
    getData()
  }, [id])
  /** Fetch interest distribution data */
  const getData = () => {
    setLoading(true)
    getInterestDistributionByUser(id as string).then(res => {
      const response = res as { name: string; frequency: number }[]
      setData(response.map(item => ({
        ...item,
        value: item.frequency,
      })))
    })
    .finally(() => {
      setLoading(false)
    })
  }

  
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
  }, [data])

  return (
    <RbCard
      title={t('userMemory.interestDistribution')}
      headerClassName="rb:min-h-[46px]!! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-100 rb:top-29 rb:left-26", className)}
      bodyClassName="rb:px-5! rb:pb-5! rb:pt-3.75! rb:max-h-[calc(100vh-176px)] rb:overflow-auto"
    >
      {loading
      ? <Loading size={249} />
      : !data || data.length === 0
      ? <Empty size={88} className="rb:mt-12 rb:mb-20.25" />
      : data && data.length > 0 && <>
        <ReactEcharts
          option={{
            color: Colors,
            tooltip: {
              show: false,
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
              bottom: 0,
              padding: 0,
              itemWidth: 12,
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
                type: 'pie',
                radius: ['60%', '100%'],
                avoidLabelOverlap: false,
                percentPrecision: 0,
                padAngle: 1,
                width: 180,
                height: 180,
                left: 'center',
                top: 24,
                itemStyle: {
                  borderRadius: 2,
                  shadowBlur: 4,
                  shadowOffsetX: 0,
                  shadowOffsetY: 2,
                  shadowColor: 'rgba(0,0,0,0.25)',
                },
                label: {
                  fontWeight: 'bold',
                  color: '#171719',
                  formatter: '{d}%',
                  fontFamily: 'MiSans-Demibold',
                },
                labelLine: {
                  lineStyle: {
                    color: '#DFE4ED'
                  }
                },
                data: data
              }
            ]
          }}
          style={{ height: '320px', width: '100%' }}
          notMerge={true}
          lazyUpdate={true}
        />
      </>}
    </RbCard>
  )
}

export default InterestDistribution
