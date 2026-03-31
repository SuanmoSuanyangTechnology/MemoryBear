/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:41 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:11:46
 */
/**
 * Memory Insight Component
 * Displays memory insights including behavior patterns, key findings, and growth trajectory
 */

import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Divider } from 'antd';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getMemoryInsightReport,
} from '@/api/memory'
import type { MemoryInsightRef } from '../types'

/**
 * Insight data structure
 */
interface Data {
  memory_insight?: string;
  behavior_pattern?: string;
  key_findings?: string[];
  growth_trajectory?: string;
  updated_at?: number;
  is_cached: boolean;
}

const MemoryInsight = forwardRef<MemoryInsightRef, { className?: string; }>(({ className }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Data>({} as Data)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  /** Fetch memory insight data */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getMemoryInsightReport(id).then((res) => {
      setData((res as Data) || {})
      setLoading(false) 
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
      title={t('userMemory.memoryInsight')}
      headerClassName="rb:min-h-[46px]!! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-100 rb:top-29 rb:left-26", className)}
      bodyClassName="rb:px-5! rb:pb-5! rb:pt-3.75! rb:max-h-[calc(100vh-186px)] rb:overflow-auto"
    >
      {loading
        ? <Skeleton />
        : Object.keys(data).length > 0
        ? <div>
            {['memory_insight', 'key_findings', 'behavior_pattern', 'growth_trajectory'].map((key, index) => {
              const value = data[key as keyof Data];
              if (Array.isArray(value) && value.length > 0 || (!Array.isArray(value) && value)) {
                return (
                  <div key={key}>
                    {index > 0 && <Divider className="rb:my-4! rb:border-t-[0.5px]!" />}
                    <div className="rb:font-medium rb:leading-5">
                      {t(`userMemory.${key}`)}
                    </div>
                    <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167] rb:mt-2">
                      {Array.isArray(data[key as keyof Data])
                        ? <>
                          {(data[key as keyof Data] as string[])?.map((item: string, index: number) => (
                            <div key={index}>
                              - {item}
                            </div>
                          ))}
                        </>
                        : data[key as keyof Data] as string
                      }
                    </div>
                  </div>
                )
              }
              return null
            })}
            
          </div>
        : <Empty size={80} />
      }
    </RbCard>
  )
})
export default MemoryInsight