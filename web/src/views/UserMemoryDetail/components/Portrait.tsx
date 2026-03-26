/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:18 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 14:19:15
 */
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Progress, Flex } from 'antd';

import {
  getImplicitPortrait,
} from '@/api/memory'

/**
 * Portrait dimension item structure
 * @property {string} dimension_name - Dimension name
 * @property {number} percentage - Percentage value
 * @property {string[]} evidence - Supporting evidence
 * @property {string} reasoning - Reasoning
 * @property {string} confidence_level - Confidence level
 */
interface Item {
  dimension_name: string;
  percentage: number;
  evidence: string[];
  reasoning: string;
  confidence_level: string;
}

/**
 * Portrait data structure
 * @property {string} user_id - User ID
 * @property {number | string} analysis_timestamp - Analysis timestamp
 * @property {number} total_summaries_analyzed - Total summaries analyzed
 * @property {null} historical_trends - Historical trends
 * @property {Item} creativity - Creativity dimension
 * @property {Item} aesthetic - Aesthetic dimension
 * @property {Item} technology - Technology dimension
 * @property {Item} literature - Literature dimension
 */
interface PortraitItem {
  user_id: string;
  analysis_timestamp: number | string;
  total_summaries_analyzed: number;
  historical_trends: null;
  creativity: Item;
  aesthetic: Item;
  technology: Item;
  literature: Item;
}

/**
 * Portrait Component
 * Displays user portrait analysis across multiple dimensions
 * Shows aesthetic, creativity, literature, and technology percentages
 */
const Portrait = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<PortraitItem>({} as PortraitItem)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  const getData = () => {
    if (!id) return
    setLoading(true)
    getImplicitPortrait(id).then((res) => {
      const response = res as PortraitItem
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
    <div className="rb-border rb:p-4 rb:pb-2.25 rb:rounded-xl">
      <div className="rb:text-[#212332] rb:font-medium rb:leading-5 rb:mb-4">{t('implicitDetail.portrait')}</div>
      {loading
        ? <Skeleton active />
        : <Flex vertical gap={14} className="rb:mt-1!">
          {(['aesthetic', 'creativity', 'literature', 'technology'] as const).map((key) => {
            const item = data[key] as Item
            return (
              <div key={key}>
                <Flex align="center" justify="space-between" className="rb:leading-5">
                  <div className="rb:text-[#5B6167]">{t(`implicitDetail.${key}`)}</div>
                  {item?.percentage ?? 0}%
                </Flex>
                <Progress percent={item?.percentage || 0} showInfo={false} strokeColor="#171719" />
              </div>
            )
          })}
        </Flex>
        }
    </div>
  )
})
export default Portrait