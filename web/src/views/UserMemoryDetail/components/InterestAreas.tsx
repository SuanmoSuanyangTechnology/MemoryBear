/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:53 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:53 
 */
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Progress } from 'antd';

import RbCard from '@/components/RbCard/Card'
import {
  getImplicitInterestAreas,
} from '@/api/memory'

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
    <RbCard
      title={t('implicitDetail.interestAreas')}
      headerType="borderless"
    >
      {loading
        ? <Skeleton active />
        : <div>
          {(['art', 'music', 'tech', 'lifestyle'] as const).map((key) => {
            return (
              <div key={key} >
                <div className="rb:flex rb:justify-between rb:items-center">
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-1">{t(`implicitDetail.${key}`)}</div>
                  {data[key]?.percentage ?? 0}%
                </div>
                <Progress percent={data[key]?.percentage || 0} showInfo={false} />
              </div>
            )
          })}
          </div>
        }
    </RbCard>
  )
})
export default InterestAreas