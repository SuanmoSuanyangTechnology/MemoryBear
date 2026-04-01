/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-10 17:35:17 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:19:38
 */
import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Row, Col, Flex, DatePicker, Pagination } from 'antd'
import type { Dayjs } from 'dayjs'
import * as echarts from 'echarts'
import 'echarts-wordcloud'

import RbCard from '@/components/RbCard/Card'
import {
  getExplicitMemory,
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
  created_at: number;
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

/** Rotating colour palette used for word-cloud text. */
const DEFAULT_COLORS = ['#FF8A4C', '#FF5D34', '#155EEF', '#9C6FFF', '#4DA8FF', '#369F21']

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
  const [data, setData] = useState<Data>({ episodic_memories: [], semantic_memories: [], total: 0 })
  const [dateRange, setDateRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [page, setPage] = useState(1)
  const PAGE_SIZE = 10

  const filteredEpisodic = dateRange?.[0] && dateRange?.[1]
    ? data.episodic_memories.filter(item => {
        const ts = item.created_at
        return ts >= dateRange[0]!.startOf('day').valueOf() && ts <= dateRange[1]!.endOf('day').valueOf()
      })
    : data.episodic_memories

  const pagedEpisodic = filteredEpisodic.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)

  /* Fetch data whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /** Load both episodic and semantic memories for the current user. */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getExplicitMemory(id).then((res) => {
      const response = res as Data
      setData(response)
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
    if (!wordCloudRef.current || !data.semantic_memories?.length) return
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
        data: data.semantic_memories.map((item, index) => ({
          name: item.name,
          value: 50 + (index % 5) * 10,
          itemIndex: index,
          textStyle: { color: DEFAULT_COLORS[index % DEFAULT_COLORS.length] }
        }))
      }]
    })
    chartInstance.current.on('click', (params) => {
      const item = data.semantic_memories[(params.data as any).itemIndex]
      if (item) handleView(item)
    })
    return () => { chartInstance.current?.dispose(); chartInstance.current = null }
  }, [data.semantic_memories])

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
          extra={<span className="rb:text-[#5B6167]">{t('table.totalRecords', { total: data.total })}</span>}
          headerType="borderless"
          headerClassName="rb:min-h-[50px]!"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-50px)]"
          className="rb:h-full!"
        >
          {loading ?
            <Skeleton active />
            : (
              <Flex gap={12} vertical className="rb:h-full!">
                <Row gutter={12}>
                  <Col span={12}>
                    <DatePicker.RangePicker
                      value={dateRange}
                      onChange={(val) => { setDateRange(val); setPage(1) }}
                      allowClear
                    />
                  </Col>
                </Row>
                <div className="rb:max-h-[calc(100%-92px)] rb:overflow-y-auto">
                  {pagedEpisodic.length > 0 ? pagedEpisodic.map(item => (
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
                  )) : <Empty />}
                </div>
                {filteredEpisodic.length > PAGE_SIZE && (
                  <Pagination
                    current={page}
                    pageSize={PAGE_SIZE}
                    total={filteredEpisodic.length}
                    onChange={setPage}
                    size="small"
                    showSizeChanger={true}
                    showQuickJumper={true}
                    className="rb:mt-1!"
                  />
                )}
              </Flex>
            )
          }
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
            : data.semantic_memories?.length > 0
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