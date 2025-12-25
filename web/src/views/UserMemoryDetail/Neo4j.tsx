import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { Row, Col, Skeleton, Flex, Button } from 'antd'
import { useParams } from 'react-router-dom'
import aboutUs from '@/assets/images/userMemory/aboutUs.svg'
import down from '@/assets/images/userMemory/down.svg'
import interestDistribution from '@/assets/images/userMemory/interestDistribution.svg'
import PieCard from './components/PieCard'
import RbCard from '@/components/RbCard/Card'
import {
  getUserSummary,
  analyticsRefresh
} from '@/api/memory'
import type { MemoryInsightRef } from './types'
import RelationshipNetwork from './components/RelationshipNetwork'
import MemoryInsight from './components/MemoryInsight'
import Empty from '@/components/Empty'

import NodeStatistics from './components/NodeStatistics'
import EndUserProfile from './components/EndUserProfile'

interface TitleProps {
  type: string;
  title: string
  icon: string
  t: (key: string) => string;
  expanded: boolean;
  onClick: (type: string) => void;
}
const Title: FC<TitleProps> = ({ type, title, icon, t, expanded, onClick }) => (
  <div className="rb:flex rb:items-center rb:justify-between rb:py-4.25 rb:border-b rb:border-[#DFE4ED] rb:text-[16px] rb:font-semibold rb:leading-5.5">
    <span className="rb:flex rb:items-center">
      <img src={icon} className="rb:w-5 rb:h-5 rb:mr-2" />
      {title}
    </span>

    <span className="rb:flex rb:items-center rb:cursor-pointer rb:text-[#5B6167] rb:text-[14px] rb:font-regular rb:leading-5" onClick={() => onClick(type)}>
      {t(`userMemory.${expanded ? 'foldUp' : 'expanded'}`)}
      <img src={down} className={clsx("rb:w-4 rb:h-4 rb:ml-1", {
        'rb:rotate-180': !expanded,
      })} />
    </span>
  </div>
)

const Neo4j: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const memoryInsightRef = useRef<MemoryInsightRef>(null)
  const [expanded, setExpanded] = useState<string[]>(['aboutUs', 'interestDistribution', 'importantRelationships', 'importantMomentsInLife'])
  const [summary, setSummary] = useState<string | null>(null)
  const [loading, setLoading] = useState<Record<string, boolean>>({
    summary: false,
    refresh: false
  })

  useEffect(() => {
    if (!id) return
    getSummary()
  }, [id])

  const handleTitleClick = (key: string) => {
    setExpanded(expanded.includes(key) ? expanded.filter((item) => item !== key) : [...expanded, key])
  }
  // 用户摘要
  const getSummary = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, summary: true }))
    getUserSummary(id).then((res) => {
      setSummary((res as { summary?: string }).summary || null)
    })
    .finally(() => {
      setLoading(prev => ({ ...prev, summary: false }))
    })
  }
  const handleRefresh = () => {
    setLoading(prev => ({ ...prev, refresh: true }))
    analyticsRefresh(id as string)
      .then(res => {
        const response = res as { insight_success: boolean; summary_success: boolean; }
        if (response.insight_success) {
          memoryInsightRef.current?.getInsightReport()
        }
        if (response.summary_success) {
          getSummary()
        }
      })
      .finally(() => {
        setLoading(prev => ({ ...prev, refresh: false }))
      })
  }

  return (
    <div>
      <Flex justify="flex-end">
        <Button type="primary" loading={loading.refresh} className="rb:mb-3" onClick={handleRefresh}>
          {t('common.refresh')}
        </Button>
      </Flex>
      <Row gutter={[16, 16]} className="rb:pb-6">
        <Col span={8}>
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <EndUserProfile />
            </Col>
            <Col span={24}>
              <RbCard>
                {/* 关于我 */}
                <>
                  <Title
                    type="aboutUs"
                    title={t('userMemory.aboutMe')}
                    icon={aboutUs}
                    t={t}
                    expanded={expanded.includes('aboutUs')}
                    onClick={handleTitleClick}
                  />
                  {expanded.includes('aboutUs') && (
                    <>
                      {loading.summary
                        ? <Skeleton className="rb:mt-4" />
                        : summary 
                        ? <div className="rb:font-regular rb:leading-5.5 rb:pt-4">
                          {summary || '-'}
                        </div>
                        : <Empty size={88} className="rb:mt-12 rb:mb-20.25" />
                      }
                    </>
                  )}
                </>

                {/* 兴趣分布 */}
                <>
                  <Title
                    type="interestDistribution"
                    title={t('userMemory.interestDistribution')}
                    icon={interestDistribution}
                    t={t}
                    expanded={expanded.includes('interestDistribution')}
                    onClick={handleTitleClick}
                  />

                  {expanded.includes('interestDistribution') && (
                    <PieCard />
                  )}
                </>
              </RbCard>
            </Col>
          </Row>
        </Col>
        <Col span={16}>
          <Row gutter={[16, 16]}>
            <Col span={24}>
              <NodeStatistics />
            </Col>
            {/* 记忆洞察 */}
            <Col span={24}>
              <MemoryInsight ref={memoryInsightRef} />
            </Col>
            {/* 关系网络 + 记忆详情 */}
            <RelationshipNetwork />
          </Row>
        </Col>
        </Row>
    </div>
  )
}
export default Neo4j