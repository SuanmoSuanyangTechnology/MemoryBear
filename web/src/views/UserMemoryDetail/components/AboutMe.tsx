/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:34:23 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-11 15:03:05
 */
/**
 * About Me Component
 * Displays user summary, personality, and core values
 */

import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Divider } from 'antd';
import clsx from 'clsx'

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
const AboutMe = forwardRef<AboutMeRef, { className?: string; }>(({ className }, ref) => {
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
      headerClassName="rb:min-h-[46px]!! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-100 rb:top-29 rb:left-26", className)}
      bodyClassName="rb:px-5! rb:pb-5! rb:pt-3.75! rb:max-h-[calc(100vh-176px)] rb:overflow-y-auto!"
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
              <Divider className="rb:my-4!" />
              <div className="rb:font-medium rb:leading-5">{t('userMemory.personality')}</div>
              <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167] rb:mt-2">
                {data.personality}
              </div>
            </>}
            {data.core_values && <>
              <Divider className="rb:my-4!" />
              <div className="rb:font-medium rb:leading-5">{t('userMemory.core_values')}</div>
              <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167] rb:mt-2">
                {data.core_values}
              </div>
            </>}
            {data.one_sentence && 
              <RbAlert className="rb:mt-4! rb:text-[14px]!">{data.one_sentence}</RbAlert>
            }
          </>
          : <Empty size={88} className="rb:mt-12 rb:mb-20.25" />
      }
    </RbCard>
  )
})
export default AboutMe