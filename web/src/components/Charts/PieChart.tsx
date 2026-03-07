/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 13:35:45 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-10 13:35:45 
 */
/*
 * PieChart Component
 * 
 * A reusable pie chart component built with ECharts that displays data distribution
 * in a donut chart format with customizable colors and responsive behavior.
 * 
 * Features:
 * - Donut-style pie chart with percentage labels
 * - Customizable color palette
 * - Responsive resizing using ResizeObserver
 * - Hover tooltips showing percentage values
 * - Legend at the bottom with horizontal layout
 * - Empty state when no data is available
 * - Shadow effects for better visual depth
 */
import { type FC, useEffect, useRef } from 'react'
import ReactEcharts from 'echarts-for-react';

import Empty from '@/components/Empty'

/** Default color palette for pie chart segments */
const Colors = ['#171719', '#155EEF', '#4DA8FF', '#9C6FFF', '#ABEBFF', '#DFE4ED']

/**
 * Data structure for each pie chart segment
 * 
 * @interface ChartData
 * @property {string} name - Label for the segment (displayed in legend)
 * @property {number} value - Numeric value for the segment (determines size)
 */
export interface ChartData {
  name: string;
  value: number;
}

/**
 * Props for the PieChart component
 * 
 * @interface PieChartProps
 * @property {ChartData[]} chartData - Array of data points to display in the chart
 * @property {number} [height=260] - Height of the chart in pixels
 * @property {string[]} [colors] - Custom color array for chart segments (defaults to Colors)
 */
interface PieChartProps {
  chartData: ChartData[];
  height?: number;
  colors?: string[];
}

/**
 * PieChart Component
 * 
 * Renders a donut-style pie chart with percentage labels and legend.
 * Automatically resizes when container dimensions change.
 * 
 * @param {PieChartProps} props - Component props
 * @returns {JSX.Element} Rendered pie chart or empty state
 * 
 * @example
 * ```tsx
 * <PieChart 
 *   chartData={[
 *     { name: 'Category A', value: 30 },
 *     { name: 'Category B', value: 70 }
 *   ]}
 *   height={300}
 * />
 * ```
 */
const PieChart: FC<PieChartProps> = ({
  chartData,
  height = 260,
  colors = Colors,
}) => {
  /** Reference to the ECharts instance for programmatic control */
  const chartRef = useRef<ReactEcharts>(null);
  /** Flag to prevent multiple simultaneous resize operations */
  const resizeScheduledRef = useRef(false)

  /**
   * Set up responsive behavior using ResizeObserver
   * Resizes chart when parent container dimensions change
   */
  useEffect(() => {
    const handleResize = () => {
      if (chartRef.current && !resizeScheduledRef.current) {
        resizeScheduledRef.current = true
        // Use requestAnimationFrame for smooth resize performance
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

    // Cleanup: disconnect observer when component unmounts
    return () => {
      resizeObserver.disconnect()
    }
  }, [chartData])

  return (
    <div style={{ height: `${height}px` }}>
      {chartData && chartData.length > 0
        ? <ReactEcharts
          ref={chartRef}
          option={{
            color: colors,
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
              bottom: 0,
              padding: 0,
              itemWidth: 12,
              itemHeight: 12,
              borderRadius: 2,
              orient: 'horizontal',
              itemGap: 48,
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
                width: 182,
                height: 182,
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
                data: chartData
              }
            ]
          }}
          style={{ height: `${height}px`, width: '100%', minWidth: '100%', boxSizing: 'border-box' }}
          opts={{ renderer: 'canvas' }}
          notMerge={true}
          lazyUpdate={true}
        />
        : <Empty size={120} className="rb:h-full!" />
      }
    </div>
  )
}

export default PieChart
