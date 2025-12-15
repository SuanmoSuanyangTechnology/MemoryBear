import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Select } from 'antd'
import ReactEcharts from 'echarts-for-react';
import * as echarts from 'echarts';
import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'

import Card from './Card'

interface LineCardProps {
  chartData: Array<Record<string, string | number>>;
  limit: number;
  onChange: (value: string, type: string) => void;
  type: string;
  className?: string;
  seriesList: string[];
}

const SeriesConfig = {
  type: 'line',
  stack: 'Total',
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
  data: [220, 302, 181, 234, 210, 290, 150]
}
const Colors = ['#FFB048', '#4DA8FF', '#155EEF']

const LineCard: FC<LineCardProps> = ({ chartData, limit, onChange, type, className, seriesList }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);
  const options = [
    { label: t('dashboard.lastDays', { days: 7 }), value: 7 },
    { label: t('dashboard.lastDays', { days: 30 }), value: 30 },
    { label: t('dashboard.lastDays', { days: 90 }), value: 90 },
    { label: t('dashboard.lastHalfYear'), value: 180 },
    { label: t('dashboard.lastYear'), value: 365 },
  ]

  const getSeries = () => {
    const list = seriesList.map((key, index) => {
      return {
        ...SeriesConfig,
        name: t(`dashboard.${key}`),
        data: chartData.map(vo => vo[key]),
        areaStyle: {
          opacity: 0.8,
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            {
              offset: 0,
              color: Colors[index]
            },
            {
              offset: 1,
              color: '#FFFFFF'
            }
          ])
        },
      }
    })

    return list
  }
  const formatSeriesList = () => {
    return seriesList.map(key => ({
      ...SeriesConfig,
      name: t(`dashboard.${key}`),
    }))
  }

  return (
    <Card
      title={t(`dashboard.${type}`)}
      headerOperate={
        <Select 
          value={limit}
          options={options} 
          onChange={(value) => onChange(value, type)} 
          style={{ width: '150px' }} 
        />
      }
      className={`rb:pb-[24px] ${className}`}
    >
      {chartData && chartData.length > 0 ? (
        <ReactEcharts
          ref={chartRef}
          option={{
            color: Colors,
            tooltip: {
              trigger: 'axis',
              extraCssText: 'box-shadow: 0px 2px 6px 0px rgba(33,35,50,0.16); border-radius: 8px;',
              axisPointer: {
                type: 'line',
                crossStyle: {
                  color: '#5F6266',
                },
                lineStyle: {
                  color: '#5F6266',
                },
                label: {
                  show: false
                }
              },
            },
            legend: {
              data: formatSeriesList(),
              textStyle: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 16,
              },
              itemGap: 32,
              padding: 0,
              itemWidth: 26,
              itemHeight: 10,
              left: 'center'
            },
            grid: {
              left: 4,
              right: '3%',
              bottom: 0,
              containLabel: true
            },
            xAxis: {
              type: 'category',
              data: chartData.map(item => formatDateTime(item.created_at, 'DD/MM')),
              boundaryGap: false,
            },
            yAxis: {
              type: 'value',
              axisLabel: {
                color: '#A8A9AA',
                fontFamily: 'PingFangSC, PingFang SC',
                align: 'right',
                lineHeight: 17,
              },
              axisLine: {
                lineStyle: {
                  color: '#EBEBEB',
                }
              },
            },
            series: getSeries()
          }}
          style={{ height: '265px', width: '100%', minWidth: '100%', boxSizing: 'border-box' }}
          opts={{ renderer: 'canvas' }}
          notMerge={true}
          lazyUpdate={true}
        />
      ) : <Empty size={120} className="rb:mt-[48px] rb:mb-[81px]" />}
    </Card>
  )
}

export default LineCard
