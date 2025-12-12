import { type FC, useRef, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import ReactEcharts from 'echarts-for-react';
import { getHotMemoryTagsByUser } from '@/api/memory';
import Empty from '@/components/Empty';
import Loading from '@/components/Empty/Loading';

const Colors = ['#155EEF', '#4DA8FF', '#03BDFF', '#31E8FF', '#AD88FF', '#FFB048']

const PieCard: FC = () => {
  const { id } = useParams()
  const chartRef = useRef<ReactEcharts>(null);
  const resizeScheduledRef = useRef(false)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<Array<Record<string, string | number>>>([])

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
    <>
      {loading
      ? <Loading size={249} />
      : !data || data.length === 0
      ? <Empty size={88} className="rb:mt-[48px] rb:mb-[81px]" />
      : data && data.length > 0 &&
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
              type: data.length > 8 ? 'scroll' : 'plain',
              bottom: 0,
              left: 16,
              padding: 0,
              itemWidth: 12,
              itemHeight: 12,
              borderRadius: 2,
              // orient: 'horizontal',
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
                padAngle: 0,
                width: 220,
                height: 220,
                top: 32,
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
          style={{ height: '340px', width: '100%' }}
          notMerge={true}
          lazyUpdate={true}
        />
      }
    </>
  )
}

export default PieCard
