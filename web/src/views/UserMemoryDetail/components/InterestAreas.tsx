/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:53 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 14:27:12
 */
import { useEffect, useState, forwardRef, useImperativeHandle, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';
import ReactEcharts from 'echarts-for-react';

import {
  getImplicitInterestAreas,
} from '@/api/memory'

/** Default color palette for area line series */
const Colors = ['#9C6FFF', '#FFB048', '#4DA8FF', '#369F21']
const keys = ['art', 'music', 'tech', 'lifestyle'] as const
/**
 * Interest category item structure
 * @property {string} category_name - Category name
 * @property {number} percentage - Interest percentage
 * @property {string[]} evidence - Supporting evidence
 * @property {string | null} trending_direction - Trending direction
 */
interface Item {
  category_name: string;
  percentage: number;
  evidence: string[];
  trending_direction: string | null;
}

/**
 * Interest areas data structure
 * @property {string} user_id - User ID
 * @property {number | string} analysis_timestamp - Analysis timestamp
 * @property {number} total_summaries_analyzed - Total summaries analyzed
 * @property {Item} tech - Technology interest
 * @property {Item} lifestyle - Lifestyle interest
 * @property {Item} music - Music interest
 * @property {Item} art - Art interest
 */
interface InterestAreasItem {
  user_id: string;
  analysis_timestamp: number | string;
  total_summaries_analyzed: number;
  tech: Item;
  lifestyle: Item;
  music: Item;
  art: Item;
}

/**
 * InterestAreas Component
 * Displays user interest distribution across different categories
 * Shows percentage breakdown for art, music, tech, and lifestyle
 */
const InterestAreas = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<InterestAreasItem>({} as InterestAreasItem)
  const chartRef = useRef<ReactEcharts>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    getImplicitInterestAreas(id).then((res) => {
      const response = res as InterestAreasItem
      setData(response)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }

  useImperativeHandle(ref, () => ({
    handleRefresh: getData
  }));
  return (
    <div className="rb-border rb:p-4 rb:rounded-xl rb:mt-4">
      <div className="rb:text-[#212332] rb:font-medium rb:leading-5 rb:mb-4">{t('implicitDetail.interestAreas')}</div>
      {loading
        ? <Skeleton active />
        : <ReactEcharts
            ref={chartRef}
            option={{
              color: Colors,
              grid: { top: 8, left: 38, right: 8, bottom: 24 },
              xAxis: {
                type: 'category',
                data: keys.map(k => t(`implicitDetail.${k}`)),
                axisLabel: { color: '#5B6167', fontSize: 12, fontFamily: 'PingFangSC, PingFang SC', interval: 0, overflow: 'break-word', width: 60 },
                axisLine: { lineStyle: { color: '#EBEBEB' } },
                axisTick: { show: false },
              },
              yAxis: {
                type: 'value',
                min: 0,
                max: 100,
                axisLabel: { color: '#A8A9AA', fontSize: 12, fontFamily: 'PingFangSC, PingFang SC', formatter: '{value}%' },
                splitLine: { lineStyle: { color: '#EBEBEB' } },
              },
              series: [{
                type: 'bar',
                barMaxWidth: 40,
                borderRadius: [4, 4, 0, 0],
                data: keys.map((k, i) => ({
                  value: data[k]?.percentage ?? 0,
                  itemStyle: { color: Colors[i] }
                })),
                label: { show: true, position: 'top', formatter: '{c}%', color: '#5B6167', fontSize: 10 },
              }]
            }}
            style={{ height: '200px', width: '100%' }}
            notMerge={true}
            lazyUpdate={true}
          />
      }
    </div>
  )
})
export default InterestAreas