import { type FC, useRef, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react';
import { Space } from 'antd'

import { getHotMemoryTagsByUser } from '@/api/memory';
import Empty from '@/components/Empty';
import Loading from '@/components/Empty/Loading';
import RbCard from '@/components/RbCard/Card';

const Colors = ['#155EEF', '#4DA8FF', '#03BDFF', '#31E8FF', '#AD88FF', '#FFB048']

const InterestDistribution: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null);
  const resizeScheduledRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<Array<Record<string, string | number>>>([])
  const totalValue = data.reduce((sum, item) => sum + Number(item.value), 0)

  useEffect(() => {
    getData()
  }, [id])
  const getData = () => {
    setLoading(true)
    getHotMemoryTagsByUser(id as string).then(res => {
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
              show: false
            },
            series: [
              {
                name: 'Access From',
                type: 'pie',
                radius: ['60%', '100%'],
                avoidLabelOverlap: false,
                percentPrecision: 0,
                padAngle: 0,
                width: 200,
                height: 200,
                top: 18,
                left: 'center',
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
                data: data
              }
            ]
          }}
          style={{ height: '250px', width: '100%' }}
          notMerge={true}
          lazyUpdate={true}
        />
        <Space size={12} direction="vertical" className="rb:w-full">
          {data.map((item, index) => (
            <div key={index} className="rb:relative rb:flex rb:items-center rb:justify-between rb:px-4 rb:py-2.5 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:font-regular rb:leading-5 rb:rounded-md">
              <div className="rb:pl-3.5 rb:relative">
                <span 
                  className="rb:absolute rb:left-0 rb:top-[calc(50%-4px)] rb:w-2 rb:h-2 rb:rounded-full"
                  style={{ backgroundColor: Colors[index % Colors.length] }}
                />
                {item.name}
              </div>
              <div className="rb:font-medium">{totalValue > 0 ? Math.round((Number(item.value) / totalValue) * 100) : 0}%</div>
            </div>
          ))}
        </Space>
      </>}
    </RbCard>
  )
}

export default InterestDistribution
