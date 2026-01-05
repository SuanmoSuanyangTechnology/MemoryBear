import { type FC, useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getUserSummary,
} from '@/api/memory'
import type { AboutMeRef } from '../types'

const AboutMe = forwardRef<AboutMeRef>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  // 记忆洞察
  const getData = () => {
    if (!id) return
    setLoading(true)
    getUserSummary(id)
      .then((res) => {
        setData((res as { summary?: string }).summary || null)
      })
      .finally(() => {
        setLoading(false)
      })
  }
  // 暴露给父组件的方法
  useImperativeHandle(ref, () => ({
    getData,
  }));

  return (
    <RbCard 
      title={t('userMemory.aboutMe')} 
    >
      {loading
        ? <Skeleton className="rb:mt-4" />
        : data
          ? <div className="rb:font-regular rb:leading-5 rb:text-[#5B6167]">
            {data || '-'}
          </div>
          : <Empty size={88} className="rb:mt-12 rb:mb-20.25" />
      }
    </RbCard>
  )
})
export default AboutMe