/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:41 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:41 
 */
/**
 * Memory Insight Component
 * Displays memory insights including behavior patterns, key findings, and growth trajectory
 */

import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Space } from 'antd';

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

const MemoryInsight = forwardRef<MemoryInsightRef>((_props, ref) => {
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
      headerType="borderless"
      headerClassName="rb:min-h-[46px]!"
    >
      {loading
        ? <Skeleton />
        : Object.keys(data).length > 0
        ? <Space size={16} direction="vertical" className="rb:w-full">
            {['memory_insight', 'key_findings', 'behavior_pattern', 'growth_trajectory'].map(key => {
              const value = data[key as keyof Data];
              if (Array.isArray(value) && value.length > 0 || (!Array.isArray(value) && value)) {
                return (
                  <div key={key} className="rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:py-3 rb:text-[#5B6167] rb:leading-5">
                    <div className={clsx(`rb:relative rb:before:content-[''] rb:before:block rb:before:h-4 rb:before:absolute rb:before:top-0.5 rb:before:left-0 rb:before:w-1 rb:pl-4 rb:mb-2 rb:font-medium rb:leading-5`, {
                      'rb:before:bg-[#155EEF]': key === 'memory_insight',
                      'rb:before:bg-[#369F21]': key !== 'memory_insight'
                    })}>{t(`userMemory.${key}`)}</div>
                    <div className="rb:px-4">
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
            
        </Space>
        : <Empty size={80} />
      }
    </RbCard>
  )
})
export default MemoryInsight