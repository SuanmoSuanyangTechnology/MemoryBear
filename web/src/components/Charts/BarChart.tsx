/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-10 13:36:03 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-25 13:49:04
 */
/*
 * BarChart Component
 * 
 * A reusable area line chart component built with ECharts that displays time-series data
 * with gradient-filled areas under the lines. Supports multiple data series with
 * customizable colors and responsive behavior.
 * 
 * Features:
 * - Multiple line series with gradient area fills
 * - Gradient line colors (white to color to white)
 * - Customizable x-axis key for flexible data structures
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
import * as echarts from 'echarts';

import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'

/** Base configuration for all line series */
const SeriesConfig = {
  type: 'bar',
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
  showBackground: true,
}

/** Default color palette for area line series */
const Colors = ['#155EEF', '#FFB048', '#4DA8FF']

/**
 * Data structure for chart data points
 * Flexible structure allowing any string key with string or number values
 * 
 * @interface ChartData
 * @property {string | number} [key: string] - Dynamic properties for x-axis and data series
 */
export interface ChartData {
  [key: string]: string | number;
}

/**
 * Props for the BarChart component
 * 
 * @interface BarChartProps
 * @property {string} xAxisKey - Key name in chartData to use for x-axis values
 * @property {ChartData[]} chartData - Array of data points with dynamic properties
 * @property {Record<string, string>} seriesList - Map of data keys to display names
 * @property {string} [className] - Additional CSS classes for the container
 * @property {number} [height] - Height of the chart in pixels
 * @property {string[]} [colors] - Custom color array for line series and gradients
 * @property {any} [grid] - ECharts grid configuration for chart positioning
 */
interface BarChartProps {
  xAxisKey: string;
  chartData: ChartData[];
  seriesList: Record<string, string>;
  className?: string;
  height?: number;
  colors?: string[];
  grid?: any;
  itemStyle?: any;
  showLegend?: boolean;
  showBackground?: boolean;
}

/**
 * BarChart Component
 * 
 * Renders a multi-series area line chart with gradient fills.
 * The area gradient goes from the series color at the top to white at the bottom.
 * The line gradient goes from white to the series color and back to white.
 * Automatically resizes when container dimensions change.
 * 
 * @param {BarChartProps} props - Component props
 * @returns {JSX.Element} Rendered area line chart or empty state
 * 
 * @example
 * ```tsx
 * <BarChart 
 *   xAxisKey="date"
 *   chartData={[
 *     { date: '2024-01-01', revenue: 1000, profit: 200 },
 *     { date: '2024-01-02', revenue: 1500, profit: 300 }
 *   ]}
 *   seriesList={{ revenue: 'Revenue', profit: 'Profit' }}
 *   height={300}
 * />
 * ```
 */
const BarChart: FC<BarChartProps> = ({
  xAxisKey,
  chartData,
  seriesList,
  height,
  colors = Colors,
  grid = {
    top: 7,
    left: 4,
    right: 16,
    bottom: 32,
    containLabel: true
  },
  itemStyle,
  showLegend = true,
  showBackground = true,
}) => {
  /** Reference to the ECharts instance for programmatic control */
  const chartRef = useRef<ReactEcharts>(null);
  /** Flag to prevent multiple simultaneous resize operations */
  const resizeScheduledRef = useRef(false)

  /**
   * Generate series configuration for each data series with gradient effects
   * Creates area fills with vertical gradients (color to white)
   * and line colors with horizontal gradients (white to color to white)
   * 
   * @returns {Array} Array of ECharts series configurations with gradient styles
   */
  const getSeries = () => {
    return Object.entries(seriesList).map(([key, name], index) => ({
      ...SeriesConfig,
      name: name,
      data: chartData.map(vo => vo[key as keyof ChartData]),
      barWidth: 16,
      itemStyle: itemStyle || {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          {
            offset: 0,
            color: colors[index]
          },
          {
            offset: 1,
            color: '#FFFFFF'
          }
        ]),
      },
      emphasis: {
        itemStyle: {
        }
      },
      barGap: '-100%',
      showBackground: showBackground,
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
              show: showLegend,
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
              itemStyle: {
                width: 3,
              },
            },
            grid: grid,
            xAxis: {
              type: 'category',
              data: chartData.map(item => formatDateTime(item[xAxisKey], 'DD/MM')),
              boundaryGap: false,
              axisLabel: {
                color: '#5B6167',
                fontFamily: 'PingFangSC, PingFang SC',
                lineHeight: 17,
              },
              axisLine: {
                show: false,
                itemStyle: {
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
                itemStyle: {
                  color: '#EBEBEB',
                }
              },
            },
            series: getSeries()
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

export default BarChart
