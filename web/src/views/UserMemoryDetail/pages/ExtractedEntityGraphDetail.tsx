import { type FC, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { Row, Col, Flex, Skeleton, Pagination } from 'antd'
import clsx from 'clsx'

import { getRelationshipEvolution, getEntityEventTimeline } from '@/api/memory'
import type { Node } from '../types'
import RbCard from '@/components/RbCard/Card'
import EmotionLine from '../components/EmotionLine'
import { formatDateTime } from '@/utils/format'
import Tag, { type TagProps } from '@/components/Tag'
import Empty from '@/components/Empty'
import BtnTabs from '@/components/BtnTabs'


interface PaginationConfig { pagesize?: number; page?: number; }

export interface Emotion {
  emotion_intensity: number;
  emotion_type: string;
  created_at: string | number;
}
interface ExtractedEntityMemory {
  type: string;
  category: string;
  title: string;
  text: string;
  created_at: string | number;
}
interface TypeStats {
  type: string;
  count: number;
}

const PAGE_SIZE = 10

const tagColors: Record<string, TagProps['color']> = {
  'education_learning': 'processing',
  'career_work': 'success',
  'project_milestone': 'warning',

  'residence_relocation': 'success',
  'relationship_family': 'success',
  'pet_care': 'success',
  'health_medical': 'success',
  'travel_visit': 'success',

  'purchase_asset': 'warning',
  'creation_publication': 'warning',
  'achievement_award': 'warning',
  'finance_legal_admin': 'warning',

  'other_life_event': 'purple'
}
const tabs = ['all', 'key_node', 'statement', 'memory_summary']

const ExtractedEntityGraphDetail: FC = () => {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const [vo, setVo] = useState<Node | null>(null)
  const [loading, setLoading] = useState(false)
  const [emotionData, setEmotionData] = useState<Emotion[]>([])
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [timelineMemories, setTimelineMemories] = useState<ExtractedEntityMemory[]>([])
  const [typeStats, setTypeStats] = useState<TypeStats[]>([])
  const [activeTab, setActiveTab] = useState('all')
  const [currentPagination, setCurrentPagination] = useState<PaginationConfig>({
    page: 1,
    pagesize: PAGE_SIZE,
  });
  const [total, setTotal] = useState(0);
  const nodeId = searchParams.get('nodeId')
  const nodeLabel = searchParams.get('nodeLabel')
  const nodeName = searchParams.get('nodeName')

  useEffect(() => {
    if (nodeId && nodeLabel) {
      const nodeFromUrl = {
        id: nodeId,
        label: nodeLabel,
        name: nodeName || nodeLabel
      } as Node
      setVo(nodeFromUrl)
      getRelationshipEvolutionData(nodeFromUrl)
    }
  }, [searchParams])

  useEffect(() => {
    getTimelineMemoriesData(vo, {
      page: 1,
      pagesize: currentPagination.pagesize,
    })
  }, [vo, activeTab])

  const getRelationshipEvolutionData = (vo: Node) => {
    if (!vo.id || !vo.label) return
    setLoading(true)
    getRelationshipEvolution({ id: vo.id as string, label: vo.label })
      .then(res => {
        const { emotion } = res as { emotion: Emotion[]; } || {}
        setEmotionData(emotion)
      })
      .finally(() => setLoading(false))
  }
  const getTimelineMemoriesData = (vo: Node | null, pagination?: PaginationConfig) => {
    if (!vo || !vo?.id || !vo?.label) return
    setTimelineLoading(true)
    if (pagination) {
      setCurrentPagination({
        ...currentPagination,
        ...pagination,
      })
    }
    getEntityEventTimeline({
      type: activeTab === 'all' ? undefined : activeTab,
      id: vo.id as string,
      ...currentPagination,
      ...(pagination || {}),
    })
      .then(res => {
        const response = res as {
          total_count: number;
          items: ExtractedEntityMemory[];
          type_stats: TypeStats[]
          page: { hasnext: boolean; pagesize: number; total: number; }
        }
        setTypeStats(response.type_stats || [])
        setTotal(response.total_count)
        setTimelineMemories(response.items)
      })
      .finally(() => setTimelineLoading(false))
  }
  const handlePageChange = (page: number, pagesize: number) => {
    if (!vo) return
    getTimelineMemoriesData(vo, {
      page: page,
      pagesize
    })
  }
  const [isHasMore, setIsHasMore] = useState(true)

  return (
    <>
      <Row gutter={12} wrap={false} className="rb:p-3! rb:pr-0! rb:h-[calc(100vh-64px)] rb:w-full! rb:flex-nowrap! rb:overflow-hidden!">
        <Col flex="480px" className="rb:h-full! rb:overflow-auto">
          <Flex vertical gap={12}>
            <RbCard
              headerType="borderless"
              headerClassName="rb:min-h-0!"
              bodyClassName="rb:px-4! rb:pb-3! rb:pt-0! rb:h-full"
              className="rb:h-full!"
            >
              <Flex vertical justify="center" gap={2} className="rb:h-18">
                <div className="rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px]">{nodeName}</div>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4 rb:font-regular">{t('userMemory.extractedEntityTip')}</div>
              </Flex>
              <div className="rb:h-[calc(100%-72px)] rb:overflow-y-auto!">
                <div className='rb:w-full rb:mb-3 rb:h-15.5 rb:px-4 rb:pt-2.5 rb:pb-2 rb:bg-cover rb:bg-[url("@/assets/images/userMemory/extracted_bg.png")] rb:rounded-xl rb:overflow-hidden'>
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4">
                    {t('userMemory.totalCategoryStats')}
                  </div>
                  <div className="rb:mt-1 rb:font-[MiSans-Bold] rb:font-bold rb:text-[18px] rb:text-[#171719] rb:leading-6.5">
                    {total || 0}
                  </div>
                </div>
                <div className="rb:grid rb:grid-cols-2 rb:gap-3">
                  {(isHasMore ? typeStats.slice(0, 6) : typeStats).map((item, index) => (
                    <div key={index} className="rb:px-4 rb:pt-2.5 rb:pb-2 rb-border rb:rounded-xl rb:text-[#5B6167] rb:text-[12px]">
                      {t(`userMemory.${item.type}`)}
                      <div className="rb:mt-1 rb:font-[MiSans-Bold] rb:font-bold rb:text-[16px] rb:text-[#171719] rb:leading-5.5">{item.count || 0}</div>
                    </div>
                  ))}
                </div>
              </div>
              {typeStats.length > 6 &&
                <Flex align="center" justify="center" className="rb:mt-3!">
                  <Flex
                    align="center"
                    justify="center"
                    gap={4}
                    className="rb:cursor-pointer rb:text-[12px] rb:text-[#5B6167]"
                    onClick={() => setIsHasMore(prev => !prev)}
                  >
                    {isHasMore ? t('userMemory.more') : t('common.foldUp')}
                    <div className={clsx("rb:size-3.5 rb:bg-cover rb:bg-[url('@/assets/images/common/arrow_up.svg')]", {
                      'rb:rotate-180': isHasMore,
                      'rb:rotate-0': !isHasMore,
                    })}></div>
                  </Flex>
                </Flex>
              }
            </RbCard>
            <EmotionLine
              chartData={emotionData}
              loading={loading}
              className="rb:bg-white"
              headerClassName="rb:font-[MiSans-Bold] rb:font-bold rb:h-[54px] rb:text-[16px]"
            />
          </Flex>
        </Col>
        <Col flex="1" className="rb:h-full!">
          <RbCard
            title={t('userMemory.timelineMemories')}
            headerType="borderless"
            headerClassName="rb:min-h-[53px]! rb:font-[MiSans-Bold] rb:font-bold"
            bodyClassName="rb:px-4! rb:py-0! rb:h-[calc(100%-53px)]!"
            className="rb:w-full! rb:h-full!"
          >
            <BtnTabs
              className="rb:mb-4!"
              activeKey={activeTab}
              items={tabs.map(key => ({
                label: t(`userMemory.${key}`),
                key
              }))}
              onChange={(key: string) => setActiveTab(key)}
            />
            <div className="rb:h-[calc(100%-42px)] rb:overflow-y-auto">
              {timelineLoading
                ? <Skeleton active />
                : !timelineMemories || timelineMemories.length === 0
                  ? <Empty size={120} className="rb:h-full!" />
                  : <>
                    <Flex vertical gap={16} className="rb:overflow-y-auto rb:h-[calc(100%-54px)]!">
                      {timelineMemories.map((vo, index) => (
                        <Row key={index} wrap={false} className="rb:flex rb:gap-5">
                          <Col flex="74px" className="rb:leading-4.5">
                            <Flex vertical gap={8} align="end"  className="rb:h-full! rb:text-[12px]">
                              <span className="rb:text-center rb:text-[#5B6167]">{formatDateTime(vo.created_at, 'YYYY-MM-DD')}</span>
                              <div className={clsx("rb:flex-1 rb:w-px", {
                                'rb:bg-[#A8A9AA]!': index !== timelineMemories.length - 1
                              })} />
                            </Flex>
                          </Col>
                          <Col flex="1" className="rb:bg-[#F6F6F6] rb:rounded-xl rb:py-3! rb:px-4!">
                            <Flex vertical gap={8}>
                              {(vo.title || vo.category) &&
                                <div>
                                  {vo.title && <span className="rb:font-medium rb:leading-5">{vo.title} </span>}
                                  {vo.category &&
                                    <Tag circle={true} color={tagColors[vo.category]} className="rb:ml-2! rb:py-px!">
                                      {t(`userMemory.${vo.category}`)}
                                    </Tag>
                                  }
                                </div>
                              }
                              
                              <div className="rb:leading-5">{vo.text}</div>
                            </Flex>
                          </Col>
                        </Row>
                      ))}
                    </Flex>
                    <Flex justify="end" align="center" className="rb:h-13.5">
                      <Pagination
                        current={currentPagination.page}
                        pageSize={currentPagination.pagesize}
                        total={total}
                        onChange={handlePageChange}
                        size="small"
                        showSizeChanger={true}
                      />
                    </Flex>
                  </>
              }
            </div>
          </RbCard>
        </Col>
      </Row>
    </>
  )
}
export default ExtractedEntityGraphDetail