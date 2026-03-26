/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:33:06 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-16 14:05:10
 */
import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Space, Progress, Tooltip, Flex } from 'antd';

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty'
import {
  getImplicitHabits,
} from '@/api/memory'
import styles from '../pages/index.module.css'

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
    <RbCard
      title={() => (<Space size={4}>
        {t('implicitDetail.habits')}
        <Tooltip title={t('implicitDetail.habitsSubTitle')}>
          <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/userMemory/question.svg')]"></div>
        </Tooltip>
      </Space>)}
      headerType="borderless"
      headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)] rb:overflow-y-auto!"
      className="rb:h-[calc(100vh-88px)]!"
    >
      {loading
        ? <Skeleton active />
        : data.length === 0
          ? <Empty size={88} />
          : <Flex gap={12} vertical>
            {data.map((vo, voIdx) => (
              <div key={voIdx} className="rb:leading-5 rb-border rb:rounded-xl rb:p-3">
                <Flex gap={30} align="center" justify="space-between">
                  <div className="rb:flex-1">
                    <div className="rb:mb-2.5 rb:font-medium rb:text-[#212332]">{vo.habit_description}</div>
                    <div className="rb:text-[#5B6167]">{vo.time_context}</div>
                  </div>
                  <Progress type="circle" strokeWidth={10} percent={vo.confidence_level} className={styles.progressCustom} />
                </Flex>

                {vo.specific_examples.length > 0 && <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-2.5 rb:px-3 rb:mt-2.5">
                  <div className="rb:font-medium rb:mb-1">{t('implicitDetail.specific_examples')}</div>
                  <ul className="rb:list-disc rb:ml-4">
                    {vo.specific_examples.map((item, index) => (
                      <li key={index} className="rb:text-[#5B6167]">{item}</li>
                    ))}
                  </ul>
                </div>}
              </div>
            ))}
          </Flex>
      }
    </RbCard>
  )
})
export default Habits