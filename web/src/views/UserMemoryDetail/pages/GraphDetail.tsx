import {  useState, forwardRef, useImperativeHandle, useMemo, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Row, Col, Flex, Space, Skeleton, Button } from 'antd'

import { getRelationshipEvolution, getTimelineMemories } from '@/api/memory'
import type { Node, GraphDetailRef } from '../types'
import RbCard from '@/components/RbCard/Card'
import EmotionLine from '../components/EmotionLine'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import InteractionBar from '../components/InteractionBar'
import Empty from '@/components/Empty'
import PageHeader from '@/components/Layout/PageHeader'
import BtnTabs from '@/components/BtnTabs'

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
  const navigate = useNavigate()
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
        title={vo?.name}
        extra={
          <Space size={12}>
            <Button
              className="rb:px-2! rb:gap-0.5!"
              icon={<div className="rb:bg-[url('@/assets/images/workflow/return.svg')] rb:size-4 rb:bg-cover"></div>}
              onClick={() => navigate(-1)}
            >
              {t('common.return')}
            </Button>
          </Space>
        }
      />
      <Row gutter={12} className="rb:p-3! rb:pr-0! rb:h-[calc(100vh-64px)] rb:w-full! rb:flex-nowrap! rb:overflow-hidden!">
        <Col flex="480px" className="rb:h-full!">
          <RbCard
            title={t('userMemory.relationshipEvolution')}
            headerType="borderless"
            headerClassName="rb:min-h-[56px]! rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-56px)] rb:overflow-y-auto!"
            className="rb:h-full!"
          >
            <Flex vertical gap={16}>
              <EmotionLine chartData={emotionData} loading={loading} />
              <InteractionBar chartData={interactionData} loading={loading} />
            </Flex>
          </RbCard>
        </Col>
        <Col flex="1" className="rb:h-full!">
          <RbCard
            title={t('userMemory.timelineMemories')}
            headerType="borderless"
            headerClassName="rb:min-h-[53px]! rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-53px)]!"
            className="rb:w-full! rb:h-full!"
          >
            <BtnTabs
              className="rb:mb-4!"
              activeKey={activeTab}
              items={['timelines_memory', 'Statement', 'MemorySummary'].map(key => ({
                label: t(`userMemory.${key}`),
                key
              }))}
              onChange={(key: string) => setActiveTab(key)}
            />
            <div className="rb:h-[calc(100%-42px)] rb:overflow-y-auto">
              {timelineLoading
                ? <Skeleton active />
                : !activeContent || activeContent.length === 0
                  ? <Empty size={120} className="rb:mt-12 rb:mb-20.25" />
                  : <Flex gap={12} vertical>
                    {activeContent.map((vo, index) => (
                      <div
                        key={index}
                        className="rb-border rb:rounded-xl rb:p-3"
                      >
                        <Flex align="center" justify="space-between">
                          <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{formatDateTime(vo.created_at)}</div>
                          <Tag>{vo.type}</Tag>
                        </Flex>
                        <div className="rb:mt-3 rb:leading-5 rb:break-all">{vo.text}</div>
                      </div>
                    ))}
                  </Flex>
              }
            </div>
          </RbCard>
        </Col>
      </Row>
    </>
  )
})
export default GraphDetail