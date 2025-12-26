import { type FC, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Row, Col, Space, Button } from 'antd'
import { useTranslation } from 'react-i18next';

import PageHeader from './components/PageHeader'
import EndUserProfile from './components/EndUserProfile'
import AboutMe from './components/AboutMe'
import InterestDistribution from './components/InterestDistribution'
import NodeStatistics from './components/NodeStatistics'
import RelationshipNetwork from './components/RelationshipNetwork'
import MemoryInsight from './components/MemoryInsight'
import type { EndUserProfileRef, MemoryInsightRef, AboutMeRef } from './types'
import {
  analyticsRefresh,
} from '@/api/memory'

const Neo4j: FC = () => {
  const { t } = useTranslation();
  const { id } = useParams()
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const ref = useRef<EndUserProfileRef>(null)
  const memoryInsightRef = useRef<MemoryInsightRef>(null)
  const aboutMeRef = useRef<AboutMeRef>(null)

  const handleNameUpdate = (data: { other_name?: string; id: string }) => {
    setName(data.other_name ?? data.id)
  }

  const handleRefresh = () => {
    setLoading(true)
    analyticsRefresh(id as string)
      .then(res => {
        const response = res as { insight_success: boolean; summary_success: boolean; }
        if (response.insight_success) {
          memoryInsightRef.current?.getData()
        }
        if (response.summary_success) {
          memoryInsightRef.current?.getData()
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }



  return (
    <div className="rb:h-full rb:w-full">
      <PageHeader 
        name={name}
        operation={(
          <Button
            loading={loading}
            className="rb:group rb:h-7! rb:bg-transparent! rb:border-[#5B6167] rb:text-[#5B6167] rb:ml-3"
            onClick={handleRefresh}
          >
            {!loading && <div
              className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/refresh.svg')] rb:group-hover:bg-[url('@/assets/images/refresh_hover.svg')]"
            ></div>}
            {t('common.refresh')}
          </Button>
        )}
      />
      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
        <Row gutter={16}>
          <Col span={8}>
            <Space size={16} direction="vertical" className="rb:w-full">
              <EndUserProfile ref={ref} onDataLoaded={handleNameUpdate} />
              <AboutMe ref={aboutMeRef} />
              <InterestDistribution />
            </Space>
          </Col>
          <Col span={16}>
            <Space size={16} direction="vertical" className="rb:w-full">
              <NodeStatistics />
              <RelationshipNetwork />
              <MemoryInsight ref={memoryInsightRef} />
            </Space>
          </Col>
        </Row>
      </div>
    </div>
  )
}
export default Neo4j