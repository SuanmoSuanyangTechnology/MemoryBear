/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:06 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:33:06 
 */
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Space, Progress } from 'antd';

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty'
import {
  getImplicitHabits,
} from '@/api/memory'

/**
 * Habits item data structure
 * @property {string} habit_description - Description of the habit
 * @property {string} frequency_pattern - Frequency pattern
 * @property {string} time_context - Time context
 * @property {number} confidence_level - Confidence level percentage
 * @property {string[]} supporting_summaries - Supporting summaries
 * @property {string} first_observed - First observation date
 * @property {string} last_observed - Last observation date
 * @property {boolean} is_current - Whether habit is current
 * @property {string[]} specific_examples - Specific examples
 */
interface HabitsItem {
  habit_description: string;
  frequency_pattern: string;
  time_context: string;
  confidence_level: number;
  supporting_summaries: string[];
  first_observed: string;
  last_observed: string;
  is_current: boolean;
  specific_examples: string[];
}

/**
 * Habits Component
 * Displays user habits with confidence levels and specific examples
 * Shows habit descriptions, time context, and supporting evidence
 */
const Habits = forwardRef<{ handleRefresh: () => void; }>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<HabitsItem[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  const getData = () => {
    if (!id) return
    setLoading(true)
    getImplicitHabits(id).then((res) => {
      const response = res as HabitsItem[]
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
    <>
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('implicitDetail.habits')}</div>
      <div className="rb:my-3 rb:text-[#5B6167] rb:leading-5">{t('implicitDetail.habitsSubTitle')}</div>
      <RbCard>
        {loading
          ? <Skeleton active />
          : data.length === 0
            ? <Empty size={88} />
            : <Space size={12} direction="vertical" className="rb:w-full!">
              {data.map((vo, voIdx) => (
                <div key={voIdx} className="rb:leading-5 rb:shadow-[inset_3px_0px_0px_0px_#155EEF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-3">
                  <div className="rb:flex rb:items-center rb:justify-between">
                    <div>
                      <div className="rb:mb-1">{vo.habit_description}</div>
                      <div className="rb:mb-1 rb:text-[#5B6167]">{vo.time_context}</div>
                    </div>
                    <div className="rb:text-[24px] rb:font-medium">{vo.confidence_level}%</div>
                  </div>

                  {vo.specific_examples.length > 0 && <>
                    <div className="rb:mt-3 rb:mb-2">{t('implicitDetail.specific_examples')}</div>
                    <div className="rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-3">
                      {vo.specific_examples.map((item, index) => (
                        <div key={index} className="rb:text-[#5B6167] rb:text-[12px] rb:mt-1">- {item}</div>
                      ))}
                    </div>
                  </>}
                  <Progress percent={vo.confidence_level} showInfo={false} className="rb:mt-3" />
                </div>
              ))}
            </Space>
        }
      </RbCard>
    </>
  )
})
export default Habits