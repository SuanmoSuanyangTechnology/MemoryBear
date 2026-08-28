import { type FC } from 'react'
import { Flex } from 'antd'
import ReactEcharts from 'echarts-for-react'

import type {
  DayGroup,
} from './types'

const DonutChart: FC<{
  group: DayGroup
  emotionName: (type: string) => string
  tooltip: (name: string, count: number, ratio: string) => string
  topThree: string
  lowSample: string
  countLabel: (count: number) => string
}> = ({ group, emotionName, tooltip, topThree, lowSample, countLabel }) => {
  const topStats = group.stats.slice(0, 3)
  const topRatio = topStats.reduce((sum, item) => sum + item.ratio, 0)
  const isTooFew = group.dataQuality === 'too_few'
  const centerValue = isTooFew ? String(group.dialogueCount) : `${topRatio.toFixed(1)}%`
  const centerLabel = isTooFew ? lowSample : topThree
  return (
    <Flex align="center" justify="center" gap={4} className="rb:min-w-0">
      <ReactEcharts
        option={{
          animationDuration: 500,
          tooltip: { trigger: 'item', formatter: (params: { name: string; value: number; percent: number }) => isTooFew
            ? `${params.name}<br/>${countLabel(params.value)}`
            : tooltip(params.name, params.value, params.percent.toFixed(1)) },
          legend: {
            type: 'scroll',
            data: group.stats.map(item => emotionName(item.type)),
            orient: 'vertical',
            right: 0,
            top: 'center',
            height: 66,
            padding: 0,
            itemWidth: 6,
            itemHeight: 6,
            itemGap: 4,
            pageIconSize: 8,
            pageButtonGap: 4,
            pageButtonItemGap: 2,
            pageTextStyle: { color: '#858C96', fontSize: 9 },
            textStyle: { color: '#5B6167', fontSize: 10 },
            formatter: (name: string) => {
              const stat = group.stats.find(item => emotionName(item.type) === name)
              return stat ? `${name} ${isTooFew ? countLabel(stat.count) : `${stat.ratio.toFixed(1)}%`}` : name
            },
          },
          series: [{
            type: 'pie', radius: ['51%', '72%'], center: ['27%', '50%'], silent: false,
            label: { show: true, position: 'center', formatter: `{strong|${centerValue}}\n{small|${centerLabel}}`, rich: { strong: { fontSize: 15, fontWeight: 600, color: '#30343B', lineHeight: 20 }, small: { fontSize: 9, color: '#858C96' } } },
            labelLine: { show: false }, itemStyle: { borderWidth: 0 },
            data: group.stats.map(item => ({ value: item.count, name: emotionName(item.type), itemStyle: { color: item.color } })),
          }],
        }}
        style={{ width: 220, height: 116 }}
      />
    </Flex>
  )
}

export default DonutChart
