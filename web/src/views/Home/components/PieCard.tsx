import { type FC, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import ReactEcharts from 'echarts-for-react';
import Card from './Card'
import Loading from '@/components/Empty/Loading'
import Empty from '@/components/Empty'

interface PieCardProps {
  chartData: Array<Record<string, string | number>>;
  loading: boolean;
}
const Colors = ['#155EEF', '#31E8FF', '#AD88FF', '#FFB048', '#4DA8FF', '#03BDFF']

const PieCard: FC<PieCardProps> = ({ chartData, loading }) => {
  const { t } = useTranslation()
  const chartRef = useRef<ReactEcharts>(null);

  return (
    <Card
      title={t('dashboard.knowledgeBaseTypeDistribution')}
    >
      {loading
      ? <Loading size={249} />
      : !chartData || chartData.length === 0
      ? <Empty size={120} className="rb:mt-[48px] rb:mb-[81px]" />
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
          onEvents={{
            // 图表渲染完成后再次调整大小，确保宽度正确
            // 使用 setTimeout 避免在主渲染过程中调用 resize
            rendered: () => {
              if (chartRef.current) {
                setTimeout(() => {
                  chartRef.current?.getEchartsInstance().resize();
                }, 0);
              }
            }
          }}
        />
      }
    </Card>
  )
}

export default PieCard
