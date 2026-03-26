/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 13:35:55 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-10 13:35:55 
 */
/*
 * LineChart Component
 * 
 * A reusable line chart component built with ECharts for displaying time-series data
 * with multiple data series. Supports customizable colors, responsive behavior,
 * and interactive tooltips.
 * 
 * Features:
 * - Multiple line series with different colors
 * - Date-based x-axis with formatted labels (DD/MM)
 * - Responsive resizing using ResizeObserver
 * - Interactive tooltips on hover
 * - Customizable grid layout and colors
 * - Legend at the bottom for series identification
 * - Empty state when no data is available
 * - Smooth rendering with requestAnimationFrame
 */
import { type FC, useEffect, useRef, useMemo } from 'react'
import ReactEcharts from 'echarts-for-react';

import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'

/** Base configuration for all line series */
const SeriesConfig = {
  type: 'line',
  stack: 'Total',
  symbol: 'circle',
  symbolSize: 5,
  showSymbol: true,
  label: {
    show: false,
    position: 'top'
  },
  emphasis: {
    focus: 'series'
  },
}

/** Default color palette for line series */
const Colors = ['#171719', '#155EEF', '#FF5D34']

/**
 * Data structure for chart data points
 * 
 * @interface ChartData
 * @property {string | number} date - Date value for x-axis (timestamp or date string)
 * @property {string | number} [key: string] - Dynamic properties for different data series
 */
export interface ChartData {
  date: string | number;
  [key: string]: string | number;
}

/**
 * Props for the LineChart component
 * 
 * @interface LineChartProps
 * @property {ChartData[]} chartData - Array of data points with date and series values
 * @property {Record<string, string>} seriesList - Map of data keys to display names
 * @property {string} [className] - Additional CSS classes for the container
 * @property {number} [height] - Height of the chart in pixels
 * @property {string[]} [colors] - Custom color array for line series
 * @property {any} [grid] - ECharts grid configuration for chart positioning
 */
interface LineChartProps {
  chartData: ChartData[];
  seriesList: Record<string, string>;
  className?: string;
  height?: number;
  colors?: string[];
  grid?: any;
}

/**
 * LineChart Component
 * 
 * Renders a multi-series line chart with date-based x-axis.
 * Automatically resizes when container dimensions change.
 * 
 * @param {LineChartProps} props - Component props
 * @returns {JSX.Element} Rendered line chart or empty state
 * 
 * @example
 * ```tsx
 * <LineChart 
 *   chartData={[
 *     { date: '2024-01-01', users: 100, sessions: 200 },
 *     { date: '2024-01-02', users: 150, sessions: 250 }
 *   ]}
 *   seriesList={{ users: 'Active Users', sessions: 'Sessions' }}
 *   height={300}
 * />
 * ```
 */
const LineChart: FC<LineChartProps> = ({
  chartData,
  seriesList,
  height,
  colors = Colors,
  grid = {
    top: 7,
    right: 16,
  }
}) => {
  /** Reference to the ECharts instance for programmatic control */
  const chartRef = useRef<ReactEcharts>(null);
  /** Flag to prevent multiple simultaneous resize operations */
  const resizeScheduledRef = useRef(false)

  /**
   * Generate series configuration for each data series
   * Maps seriesList keys to chart series with corresponding data and colors
   * 
   * @returns {Array} Array of ECharts series configurations
   */
  const getSeries = () => {
    return Object.entries(seriesList).map(([key, name], index) => ({
      ...SeriesConfig,
      name: name,
      data: chartData.map(vo => vo[key as keyof ChartData]),
      lineStyle: {
        width: 2,
        color: colors[index]
      },
    }))
  }
  /**
   * Memoized legend data to prevent unnecessary recalculations
   * Formats series list for display in chart legend
   */
  const formatSeriesList = useMemo(() => {
    return Object.entries(seriesList).map(([_key, name]) => ({
      ...SeriesConfig,
      name: name,
    }))
  }, [seriesList])

  /**
   * Set up responsive behavior using ResizeObserver
   * Resizes chart when parent container dimensions change
   */
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
    <div style={{ height: `${height}px` }}>
      {chartData && chartData.length > 0
        ? <ReactEcharts
          ref={chartRef}
          option={{
            color: colors,
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
              data: formatSeriesList,
              textStyle: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 16,
              },
              itemGap: 8,
              padding: 0,
              itemWidth: 26,
              itemHeight: 10,
              bottom: 0,
              lineStyle: {
                width: 3,
              },
            },
            grid: grid,
            xAxis: {
              type: 'category',
              data: chartData.map(item => formatDateTime(item.date, 'DD/MM')),
              boundaryGap: false,
              axisLabel: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 17,
              },
              axisLine: {
                show: false,
                lineStyle: {
                  color: '#EBEBEB',
                }
              },
              splitLine: {
                show: false,
              },
              axisTick: {
                show: false
              }
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
          style={{ height: '100%', width: '100%', minWidth: '100%', boxSizing: 'border-box' }}
          opts={{ renderer: 'canvas' }}
          notMerge={true}
          lazyUpdate={true}
        />
        : <Empty size={120} className="rb:h-full!" />
      }
    </div>
  )
}

export default LineChart
