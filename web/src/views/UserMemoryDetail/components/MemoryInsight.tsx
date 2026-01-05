import { type FC, useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton } from 'antd';
import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getMemoryInsightReport,
} from '@/api/memory'
import type { MemoryInsightRef } from '../types'

const MemoryInsight = forwardRef<MemoryInsightRef>((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [report, setReport] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  // 记忆洞察
  const getData = () => {
    if (!id) return
    setLoading(true)
    getMemoryInsightReport(id).then((res) => {
      setReport((res as { report?: string }).report || null)
      setLoading(false) 
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
      title={t('userMemory.memoryInsight')} 
      headerType="borderless"
    >
      {loading
        ? <Skeleton />
        : report
          ? <div className="rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:py-3 rb:px-4 rb:text-[#5B6167] rb:leading-5">
            {report || '-'}
        </div>
        : <Empty size={80} />
      }
    </RbCard>
  )
})
export default MemoryInsight