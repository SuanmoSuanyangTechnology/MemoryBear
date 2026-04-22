/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-10 17:35:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:19:38
 */
import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Row, Col, Flex, DatePicker, Pagination, Form, Select } from 'antd'
import type { Dayjs } from 'dayjs'
import * as echarts from 'echarts'
import 'echarts-wordcloud'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getSemanticsMemory,
  getEpisodicMemory,
  type EpisodicMemoryQuery,
  type EpisodicMemoryType,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Empty from '@/components/Empty'
import ExplicitDetailModal from '../components/ExplicitDetailModal'

/** An episodic (event-based) memory entry with a title and free-text content. */

export interface EpisodicMemory {
  id: string;
  title: string;
  content: string;
  created_at: number;
  memory_type: EpisodicMemoryType;
}

/** A semantic (concept-based) memory entry extracted as a named entity. */
export interface SemanticMemory {
  id: string;
  /** Entity name displayed in the word cloud. */
  name: string;
  /** Classification of the entity (e.g. person, location, concept). */
  entity_type: string;
  /** Brief definition or description of the entity. */
  core_definition: string;
}

/** Combined API response containing both memory categories. */
interface Data {
  total: number;
  episodic_memories: EpisodicMemory[];
  semantic_memories: SemanticMemory[]
}

/** Imperative handle exposed by ExplicitDetailModal for opening the detail drawer. */
export interface ExplicitDetailModalRef {
  handleOpen: (vo: EpisodicMemory | SemanticMemory) => void;
}
interface PaginationConfig { pagesize?: number; page?: number; }

/** Rotating colour palette used for word-cloud text. */
const DEFAULT_COLORS = ['#FF8A4C', '#FF5D34', '#155EEF', '#9C6FFF', '#4DA8FF', '#369F21']

const PAGE_SIZE = 10
/**
 * ExplicitDetail – Two-column view of a user's explicit memories.
 *
 * Left column: scrollable list of episodic memory cards (title + content).
 * Right column: ECharts word cloud built from semantic memory entity names;
 *              clicking a word opens the detail modal.
 *
 * Route param `id` is the end-user ID whose memories are displayed.
 */
const ExplicitDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const explicitDetailModalRef = useRef<ExplicitDetailModalRef>(null)
  /** Container element for the ECharts word-cloud instance. */
  const wordCloudRef = useRef<HTMLDivElement>(null)
  /** Keeps a stable reference to the ECharts instance for cleanup. */
  const chartInstance = useRef<echarts.ECharts | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [semanticsMemory, setSemanticsMemory] = useState<SemanticMemory[]>([])

  const [form] = Form.useForm<EpisodicMemoryQuery & { range?: [Dayjs, Dayjs] | null }>()
  const values = Form.useWatch([], form)
  const [episodicLoading, setEpisodicLoading] = useState(false)
  const [episodicMemories, setEpisodicMemories] = useState<Data['episodic_memories']>([])
  const [currentPagination, setCurrentPagination] = useState<PaginationConfig>({
    page: 1,
    pagesize: PAGE_SIZE,
  });
  const [total, setTotal] = useState(0);
  const [allEpisodicTotal, setAllEpisodicTotal] = useState(0)

  useEffect(() => {
    getEpisodicMemoryList({ page: 1 })
  }, [values])

  const getEpisodicMemoryList = (pagination?: PaginationConfig) => {
    if (!id) return
    if (pagination) {
      setCurrentPagination({
        ...currentPagination,
        ...pagination,
      })
    }

    const { range, ...rest } = values || {};
    const params = {
      end_user_id: id,
      ...currentPagination,
      ...pagination,
      ...rest
    }

    if (range && range.length === 2) {
      params.start_date = range[0]!.startOf('day').valueOf()
      params.end_date = range[1]!.endOf('day').valueOf()
    }
    setEpisodicLoading(true)
    getEpisodicMemory(params)
      .then(res => {
        const response = res as { total: number; items: EpisodicMemory[]; page: { hasnext: boolean; pagesize: number; total: number; } }
        setEpisodicMemories(response.items)
        setTotal(response.page.total)
        setAllEpisodicTotal(response.total)
      })
      .finally(() => {
        setEpisodicLoading(false)
      })
  }
  const handlePageChange = (page: number, pagesize: number) => {
    getEpisodicMemoryList({
      page: page,
      pagesize
    })
  }

  /* Fetch data whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /** Load both episodic and semantic memories for the current user. */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getSemanticsMemory(id).then((res) => {
      setSemanticsMemory(res as SemanticMemory[])
      setLoading(false)
    })
    .finally(() => {
      setLoading(false)
    })
  }
  /** Open the detail modal for a given memory item. */
  const handleView = (item: EpisodicMemory | SemanticMemory) => {
    explicitDetailModalRef.current?.handleOpen(item)
  }

  /**
   * Initialise / re-render the word cloud whenever semantic memories change.
   * Each word is clickable and opens the detail modal for that entity.
   * The chart instance is disposed on cleanup to prevent memory leaks.
   */
  useEffect(() => {
    if (!wordCloudRef.current || !semanticsMemory?.length) return
    if (chartInstance.current) chartInstance.current.dispose()
    chartInstance.current = echarts.init(wordCloudRef.current)
    chartInstance.current.setOption({
      series: [{
        type: 'wordCloud',
        gridSize: 8,
        sizeRange: [14, 56],
        rotationRange: [-45, 45],
        shape: 'pentagon',
        width: '100%',
        height: '100%',
        textStyle: { fontFamily: 'sans-serif', fontWeight: 'bold' },
        emphasis: { textStyle: { shadowBlur: 10, shadowColor: '#333' } },
        data: semanticsMemory.map((item, index) => ({
          name: item.name,
          value: 50 + (index % 5) * 10,
          itemIndex: index,
          textStyle: { color: DEFAULT_COLORS[index % DEFAULT_COLORS.length] }
        }))
      }]
    })
    chartInstance.current.on('click', (params) => {
      const item = semanticsMemory[(params.data as any).itemIndex]
      if (item) handleView(item)
    })
    return () => { chartInstance.current?.dispose(); chartInstance.current = null }
  }, [semanticsMemory])

  /* Redraw the word cloud when the container dimensions change. */
  useEffect(() => {
    const target = wordCloudRef.current?.parentElement
    if (!target) return
    const observer = new ResizeObserver(() => {
      if (!chartInstance.current) return
      chartInstance.current.resize()
      chartInstance.current.setOption({ series: [{ type: 'wordCloud' }] })
    })
    observer.observe(target)
    return () => {
      observer.disconnect()
      chartInstance.current?.dispose();
      chartInstance.current = null
    }
  }, [])

  return (
    <Row gutter={12} className="rb:h-full!">
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={() => <span className="rb:font-[MiSans-Bold] rb:font-bold">{t('explicitDetail.episodic_memories')}</span>}
          extra={<span className="rb:text-[#5B6167]">{t('table.totalRecords', { total: allEpisodicTotal })}</span>}
          headerType="borderless"
          headerClassName="rb:min-h-[50px]!"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-50px)]"
          className="rb:h-full!"
        >
          <Flex vertical gap={12} className="rb:h-full!">
            <Form form={form} initialValues={{ episodic_type: null }}>
              <Row gutter={12}>
                <Col span={12}>
                  <Form.Item name="range" noStyle>
                    <DatePicker.RangePicker
                      allowClear
                      className="rb:w-full!"
                    />
                  </Form.Item>
                </Col>
                <Col span={12}>
                  <Form.Item name="episodic_type" noStyle>
                    <Select
                      options={[
                        { value: null, label: t('common.all') },
                        ...["conversation", "project_work", "learning", "decision", "important_event"].map(type => ({
                          value: type, label: t(`explicitDetail.${type}`)
                        }))
                      ]}
                      placeholder={t('explicitDetail.episodic_type')}
                      className="rb:w-full!"
                    />
                  </Form.Item>
                </Col>
              </Row>
            </Form>
            {episodicLoading ?
              <Skeleton active />
              : (
                <>
                  <Flex vertical gap={12} className={clsx(" rb:overflow-y-auto", {
                    'rb:max-h-[calc(100%-92px)]!': total > PAGE_SIZE,
                    'rb:max-h-[calc(100%-36px)]!': total <= PAGE_SIZE && episodicMemories.length > 0,
                    'rb:h-full!': episodicMemories.length === 0
                  })}>
                    {episodicMemories.length > 0 ? episodicMemories.map(item => (
                      <div
                        key={item.id}
                        className="rb:cursor-pointer rb:bg-[#F6F6F6] rb:rounded-xl rb:pt-2.5 rb:px-3 rb:pb-3"
                        onClick={() => handleView(item)}
                      >
                        <Flex align="center" justify="space-between">
                          <span className="rb:font-medium rb:pl-1">{item.title}</span>
                          <div className="rb:text-[#5B6167] rb:leading-4.25 rb:text-[12px]">{formatDateTime(item.created_at)}</div>
                        </Flex>
                        <div className="rb:bg-white rb:rounded-lg rb:py-2.5 rb:px-3 rb:mt-2.5 rb:leading-5">{item.content}</div>
                      </div>
                    )) : <Empty className="rb:h-full!" />}
                  </Flex>
                  {total > PAGE_SIZE && (
                    <Pagination
                      current={currentPagination.page}
                      pageSize={PAGE_SIZE}
                      total={total}
                      onChange={handlePageChange}
                      size="small"
                      showSizeChanger={true}
                      showQuickJumper={true}
                      className="rb:mt-1!"
                    />
                  )}
                </>
              )
            }
          </Flex>
        </RbCard>
      </Col>
      <Col span={12} className="rb:h-full!">
        <RbCard
          title={t('explicitDetail.semantic_memories')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)] rb:overflow-y-auto!"
          className="rb:h-full!"
        >
          {loading ?
            <Skeleton active />
            : semanticsMemory?.length > 0
              ? <div ref={wordCloudRef} className="rb:h-full rb:w-full rb:cursor-pointer" />
              : <Empty />
          }
        </RbCard>
      </Col>

      <ExplicitDetailModal
        ref={explicitDetailModalRef}
      />
    </Row>
  )
}
export default ExplicitDetail