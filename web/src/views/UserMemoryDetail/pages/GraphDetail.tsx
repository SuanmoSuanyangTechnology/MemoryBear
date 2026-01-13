import {  useState, forwardRef, useImperativeHandle, useMemo, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { Row, Col, Tabs, Space, Skeleton } from 'antd'

import { getRelationshipEvolution, getTimelineMemories } from '@/api/memory'
import type { Node, GraphDetailRef } from '../types'
import RbCard from '@/components/RbCard/Card'
import EmotionLine from '../components/EmotionLine'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import InteractionBar from '../components/InteractionBar'
import Empty from '@/components/Empty'
import PageHeader from '../components/PageHeader'

export interface Emotion {
  emotion_intensity: number;
  emotion_type: string;
  created_at: string | number;
}
export interface Interaction {
  created_at: string | number;
  count: number;
}
interface TimelineMemory {
  text: string;
  type: string;
  created_at: number | string;
}
interface Timeline {
  MemorySummary: TimelineMemory[];
  Statement: TimelineMemory[];
  ExtractedEntity: TimelineMemory[];
  timelines_memory: TimelineMemory[];
}

const GraphDetail = forwardRef<GraphDetailRef>((_props, ref) => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const [vo, setVo] = useState<Node | null>(null)
  const [loading, setLoading] = useState(false)
  const [emotionData, setEmotionData] = useState<Emotion[]>([])
  const [interactionData, setInteractionData] = useState<Interaction[]>([])
  const [activeTab, setActiveTab] = useState('timelines_memory')
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [timelineMemories, setTimelineMemories] = useState<Timeline>({ timelines_memory: [], MemorySummary: [], Statement: [], ExtractedEntity: []})
  useEffect(() => {
    const nodeId = searchParams.get('nodeId')
    const nodeLabel = searchParams.get('nodeLabel')
    const nodeName = searchParams.get('nodeName')
    
    if (nodeId && nodeLabel) {
      const nodeFromUrl = {
        id: nodeId,
        label: nodeLabel,
        name: nodeName || nodeLabel
      }
      handleOpen(nodeFromUrl as Node)
    }
  }, [searchParams])

  const handleOpen = (vo: Node) => {
    setActiveTab('timelines_memory')
    setVo(vo)
    getRelationshipEvolutionData(vo)
    getTimelineMemoriesData(vo)
  }
  const getRelationshipEvolutionData = (vo: Node) => {
    if (!vo.id || !vo.label) return
    setLoading(true)
    getRelationshipEvolution({ id: vo.id as string, label: vo.label })
      .then(res => {
        const { emotion, interaction } = res as { emotion: Emotion[]; interaction: Interaction[] } || {}
        setEmotionData(emotion)
        setInteractionData(interaction)
      })
      .finally(() => setLoading(false))
  }
  const getTimelineMemoriesData = (vo: Node) => {
    if (!vo.id || !vo.label) return
    setTimelineLoading(true)
    getTimelineMemories({ id: vo.id as string, label: vo.label })
      .then(res => {
        setTimelineMemories(res as Timeline)
      })
      .finally(() => setTimelineLoading(false))
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
  }));

  const activeContent = useMemo(() => {
    return timelineMemories[activeTab as keyof Timeline] || []
  }, [activeTab, timelineMemories])

  return (
    <>
      <PageHeader
        name={vo?.name}
        source="node"
      />
      <div className="rb:h-full rb:max-w-266 rb:mx-auto">
        <div className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:mb-3">{t('userMemory.relationshipEvolution')}</div>
        <RbCard>
          <Row gutter={16}>
            <Col span={12}>
              <EmotionLine chartData={emotionData} loading={loading} />
            </Col>
            <Col span={12}>
              <InteractionBar chartData={interactionData} loading={loading} />
            </Col>
          </Row>
        </RbCard>

        <div className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:mb-3 rb:mt-6">{t('userMemory.timelineMemories')}</div>
        <RbCard>
          <Tabs
            activeKey={activeTab}
            items={['timelines_memory', 'ExtractedEntity', 'Statement', 'MemorySummary'].map(key => ({
              label: t(`userMemory.${key}`),
              key
            }))}
            onChange={(key: string) => setActiveTab(key)}
          />
          {timelineLoading
            ? <Skeleton active />
            : !activeContent || activeContent.length === 0
            ? <Empty size={120} className="rb:mt-12 rb:mb-20.25" />
            : <Space size={16} direction="vertical" className="rb:w-full">
              {activeContent.map((vo, index) => (
                <RbCard
                  key={index}
                  headerType="borderL"
                  headerClassName="rb:before:bg-[#155EEF]!"
                  title={vo.text}
                >
                  <div className="rb:text-[#A8A9AA] rb:text-[12px] rb:leading-4">{formatDateTime(vo.created_at)}</div>
                  <Tag className="rb:mt-2">{vo.type}</Tag>
                </RbCard>
              ))}
            </Space>
          }

          
        </RbCard>
      </div>
    </>
  )
})
export default GraphDetail