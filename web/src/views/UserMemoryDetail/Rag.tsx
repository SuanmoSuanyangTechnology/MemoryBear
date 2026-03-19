/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:57:11 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-19 11:38:17
 */
/**
 * RAG User Memory Detail View
 * Displays user memory details using RAG storage
 * Shows profile, tags, summary, memory count, and conversation history
 */

import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Row, Col, Skeleton, Flex } from 'antd'
import { useParams } from 'react-router-dom'

import aboutUs from '@/assets/images/userMemory/aboutUs.svg'
import memoryInsight from '@/assets/images/userMemory/memoryInsight.svg'
import RbCard from '@/components/RbCard/Card'
import type { Data } from './types'
import {
  getChunkSummaryTag,
  getUserProfile,
  getChunkInsight,
} from '@/api/memory'
import Empty from '@/components/Empty'
import ConversationMemory from './components/ConversationMemory'

/**
 * Title component props
 */
interface TitleProps {
  title: string
  icon: string
}
/** Collapsible section title */
const Title: FC<TitleProps> = ({ title, icon }) => (
  <Flex align="center" gap={4} className="rb:font-medium rb:leading-5 rb:mb-2.25!">
    <img src={icon} className="rb:size-4.5 rb:ml-0.5" />
    {title}
  </Flex>
)

const Rag: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [data, setData] = useState<Data | null>(null)
  const [summary, setSummary] = useState<string | null>('')
  const [loading, setLoading] = useState<Record<string, boolean>>({
    detail: true,
    summary: true,
    insight: true,
  })
  const [insight, setInsight] = useState<string | null>('')

  useEffect(() => {
    if (!id) return
    getSummary()
    getDetail()
    getInsightReport()
  }, [id])

  /** Fetch user memory detail */
  const getDetail = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, detail: true }))
    getUserProfile(id).then((res) => {
      setData((res as Data))
    })
    .finally(() => {
    setLoading(prev => ({ ...prev, detail: false }))
    })
  }
  /** Fetch user summary */
  const getSummary = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, summary: true }))
    getChunkSummaryTag(id).then((res) => {
      const response = res as { summary?: string; tags?: { tag: string; frequency: number }[]; personas?: string[] }
      setSummary(response.summary || null)
    })
    .finally(() => {
      setLoading(prev => ({ ...prev, summary: false }))
    })
  }
  /** Fetch memory insight */
  const getInsightReport = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, insight: true }))
    getChunkInsight(id).then((res) => {
      setInsight((res as { insight?: string }).insight || null)
    })
    .finally(() => {
      setLoading(prev => ({ ...prev, insight: false }))
    })
  }
  const name = loading.detail ? '' : data?.name && data?.name !== '' ? data.name : id
  return (
    <Row gutter={[16, 16]}>
      <Col span={8}>
        <RbCard
          bodyClassName="rb:p-3! rb:pt-4! rb:h-[calc(100vh-76px)]"
        >
          <Flex align="center" gap={12} className="rb:mb-6!">
            <div className="rb:size-12 rb:text-center rb:font-semibold rb:text-[28px] rb:leading-12 rb:rounded-xl rb:text-white rb:bg-[#155EEF]">{name?.[0]}</div>
            <div className="rb:text-[16px] rb:font-semibold rb:leading-6 rb:line-clamp-2 rb:flex-1">
              {name}
            </div>
          </Flex>

          {/* About Me */}
          <>
            <Title
              title={t('userMemory.aboutMe')}
              icon={aboutUs}
            />
            <div className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2.5 rb:px-3 rb:mb-4">
              {loading.summary
                ? <Skeleton />
                : summary 
                ? <div className="rb:leading-5 rb:text-[#5B6167]">
                  {summary || '-'}
                </div>
                : <Empty size={88} />
              }
            </div>
          </>
          {/* Memory Insights */}
          <>
            <Title
              title={t('userMemory.memoryInsight')}
              icon={memoryInsight}
            />
            <div className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2.5 rb:px-3">
              {loading.insight
                ? <Skeleton />
                : insight
                ? <div className="rb:leading-5 rb:text-[#5B6167]">
                  {insight || '-'}
                </div>
                : <Empty size={88} />
              }
            </div>
          </>
        </RbCard>
      </Col>
      <Col span={16}>
        <ConversationMemory />
      </Col>
    </Row>
  )
}
export default Rag