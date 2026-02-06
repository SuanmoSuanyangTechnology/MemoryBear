/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:34:23 
 */
/**
 * About Me Component
 * Displays user summary, personality, and core values
 */

import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import RbAlert from '@/components/RbAlert';
import {
  getUserSummary,
} from '@/api/memory'
import type { AboutMeRef } from '../types'


/**
 * User summary data
 */
interface Data {
  user_summary: string;
  personality: string;
  core_values: string;
  one_sentence: string;
  [key: string]: string;
}
const AboutMe = forwardRef<AboutMeRef>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Data>({} as Data)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  /** Fetch user summary data */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getUserSummary(id)
      .then((res) => {
        setData((res as Data) || null)
      })
      .finally(() => {
        setLoading(false)
      })
  }
  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    getData,
  }));

  return (
    <RbCard 
      title={t('userMemory.aboutMe')}
      headerClassName="rb:min-h-[46px]!"
    >
      {loading
        ? <Skeleton className="rb:mt-4" />
        : Object.keys(data).filter(key => data[key] !== null).length > 0
          ? <>
            {data.user_summary && 
              <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167]">
                {data.user_summary}
              </div>
            }
            {data.personality && <>
              <div className="rb:pt-4 rb:font-medium rb:leading-5 rb:mb-2">{t('userMemory.personality')}</div>
              <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167]">
                {data.personality}
              </div>
            </>}
            {data.core_values && <>
              <div className="rb:pt-4 rb:font-medium rb:leading-5 rb:mb-2">{t('userMemory.core_values')}</div>
              <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167]">
                {data.core_values}
              </div>
            </>}
            {data.one_sentence && 
              <RbAlert className="rb:mt-4">{data.one_sentence}</RbAlert>
            }
          </>
          : <Empty size={88} className="rb:mt-12 rb:mb-20.25" />
      }
    </RbCard>
  )
})
export default AboutMe