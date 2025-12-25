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
    getInsightReport()
  }, [id])
  
  // 记忆洞察
  const getInsightReport = () => {
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
    getInsightReport,
  }));
  return (
    <RbCard 
      title={t('userMemory.memoryInsight')} 
      headerType="borderless"
      headerClassName="rb:text-[18px]! rb:leading-[24px]"
      bgColor="linear-gradient(180deg,#F1F9FE 0%, #FBFCFF 100%)"
      height="100%"
    >
      {loading
        ? <Skeleton />
        : report
        ? <div className="rb:flex rb:flex-wrap rb:justify-between rb:h-full">
          <div className="rb:leading-5.5">
            {report|| '-'}
          </div>
        </div>
        : <Empty size={80} />
      }
    </RbCard>
  )
})
export default MemoryInsight