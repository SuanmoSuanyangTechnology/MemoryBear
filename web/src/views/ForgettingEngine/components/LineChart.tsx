import { type FC, useRef, useEffect, useState, useMemo, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';
import type { ConfigForm } from '../types'

interface LineCardProps {
  config: ConfigForm
}

const SeriesConfig = {
  type: 'line',
  smooth: true,
  lineStyle: {
    width: 3
  },
  showSymbol: false,
  label: {
    show: true,
    position: 'top'
  },
  emphasis: {
    focus: 'series'
  },
}
const Colors = ['#155EEF', '#4DA8FF', '#FFB048']


// 快速遗忘：lambda_mem=0.3，lambda_time=1，offset=0.05； 慢速遗忘：lambda_mem=1，lambda_time=0.3，offset=0.2
const LineChart: FC<LineCardProps> = ({ config }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);
  const debounceRef = useRef()
  const resizeScheduledRef = useRef(false)
  const xAxisData = [1, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45, 48, 51, 54, 57, 60]
  const [initialData, setInitialData] = useState([])
  const [currentData, setCurrentData] = useState({
      ...SeriesConfig,
      name: `${t('forgettingEngine.currentConfig')}(λ_time=${config?.lambda_mem})`,
      data: [],
      config: {}
  })
  const seriesData = useMemo(() => [
    {
      ...SeriesConfig,
      name: `${t('forgettingEngine.quicklyForget')}(λ_time=0.3)`,
      data: [],
      config: {lambda_mem: 0.3, lambda_time: 1, offset: 0.05}
    },
    {
      ...SeriesConfig,
      name: `${t('forgettingEngine.slowForgetting')}(λ_time=1)`,
      data: [],
      config: {lambda_mem: 1, lambda_time: 0.3, offset: 0.2}
    }
  ], [t])

  useEffect(() => {
    getInitData()
  }, [])

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
  }, [initialData])
  useEffect(() => {
    if (config) {
      clearTimeout(debounceRef.current)
      debounceRef.current = setTimeout(() => {
        getCaculateData(config)
      }, 300)
    }
    return () => {
      clearTimeout(debounceRef.current)
    }
  }, [config])

  // 快速遗忘：lambda_mem=0.3，lambda_time=1，offset=0.05； 
  // 慢速遗忘：lambda_mem=1，lambda_time=0.3，offset=0.2
  const getInitData = useCallback(() => {
    const list = seriesData.map(item => ({
      ...item,
      data: formatData(item.config)
    }))
    setInitialData(list)
  }, [seriesData])
  
  const calculateSeriesData = useCallback((days: number, data: ConfigForm) => {
    const offset = Number(data.offset)
    const lambda_time = Number(data.lambda_time)
    const lambda_mem = Number(data.lambda_mem)
    // R = offset + (1 - offset) × e^(-λtime × t / (λmem × S))
    return +(offset + (1 - offset) * Math.exp(-lambda_time * days / lambda_mem)).toFixed(4)
  }, [])
  const formatData = useCallback((data: ConfigForm) => {
    return xAxisData.map(days => Number(calculateSeriesData(days, data)))
  }, [calculateSeriesData])

  const getCaculateData = useCallback((data: ConfigForm) => {
    if (!data) {
      return
    }
    setCurrentData(prev => ({
      ...prev,
      config: data,
      name: `${t('forgettingEngine.currentConfig')}(λ_time=${data.lambda_time})`,
      data: xAxisData.map(days => Number(calculateSeriesData(days, data)))
    }))
  }, [t, calculateSeriesData])

  return (
    <>
      {xAxisData.length > 0 && initialData.length > 0 && (
        <ReactEcharts
          ref={chartRef}
          option={{
            color: Colors,
            tooltip: {
              trigger: 'axis',
            },
            legend: {
              data: [currentData.name, ...seriesData.map(item => item.name)],
              textStyle: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 16,
                // width: 127,
                // overflow: 'break'
              },
              itemGap: 24,
              padding: 0,
              itemWidth: 26,
              itemHeight: 10,
              left: 'center',
              bottom: 0,
            },
            grid: {
              left: 4,
              right: '2%',
              bottom: 60,
              top: 32,
              containLabel: true
            },
            xAxis: {
              type: 'category',
              data: xAxisData,
              boundaryGap: false,
              axisLine: {
                lineStyle: {
                  color: '#EBEBEB',
                },
                show: true,
              },
              axisTick: {
                show: true
              },
              axisLabel: {
                color: '#5B6167'
              },
            },
            yAxis: {
              type: 'value',
              axisLabel: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                align: 'right',
                lineHeight: 17,
              },
              axisLine: {
                lineStyle: {
                  color: '#EBEBEB',
                },
              },
            },
            series: [
              currentData,
              ...initialData || []
            ]
          }}
          style={{ height: '450px', width: '100%' }}
          opts={{ renderer: 'canvas' }}
          notMerge={true}
          lazyUpdate={true}
        />
      )}
    </>
  )
}

export default LineChart
