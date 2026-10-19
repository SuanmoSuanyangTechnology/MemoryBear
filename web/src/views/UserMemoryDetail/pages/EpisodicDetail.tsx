import { type FC, useEffect, useState, useCallback } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Select, Form, Skeleton, Input, Flex } from 'antd'
import RbCard from '@/components/RbCard/Card'
import {
  getEpisodicOverview,
  getEpisodicDetail,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import { TAG_COLORS } from '../constant'
import Empty from '@/components/Empty'

/** Single episodic memory item returned by the overview API. */
interface EpisodicMemory {
  id: string;
  title: string;
  type: string;
  created_at: number;
}

/** Response shape of the episodic overview API. */
interface EpisodicOverviewData {
  /** Count of memories matching the current filter. */
  total: number;
  /** Total count of all episodic memories (unfiltered). */
  total_all: number;
  episodic_memories: EpisodicMemory[]
}

/** Full detail of a single episodic memory entry. */
interface EpisodicMemoryDetail {
  id: string;
  created_at: number;
  /** People or entities involved in this episode. */
  involved_objects: string[];
  /** Category such as conversation, learning, decision, etc. */
  episodic_type: string;
  /** Ordered list of content paragraphs describing the episode. */
  content_records: string[];
  /** Emotion label associated with this episode (e.g. "joy", "neutral"). */
  emotion: string;
}

/** Normalise a display-friendly type string (e.g. "Project/Work") to its internal key (e.g. "project_work"). */
const getTypeKey = (type: string): string => {
  if (!type) return 'default'
  const typeMap: Record<string, string> = {
    'Learning': 'learning',
    'Project/Work': 'project_work',
    'Conversation': 'conversation',
    'Decision': 'decision',
    'Important Event': 'important_event',
  }
  return typeMap[type] || type.toLowerCase().replace(/[^a-z0-9]/g, '_')
}
/**
 * EpisodicDetail – Displays a user's episodic memories in a master-detail layout.
 *
 * Left panel: filterable & searchable list of episodic memory cards.
 * Right panel: full detail view of the selected episode including metadata,
 * content records and emotion label.
 *
 * Route param `id` is the end-user ID whose memories are being viewed.
 */
const EpisodicDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<EpisodicOverviewData>({} as EpisodicOverviewData)
  /** Reactive form values used as filter params (time_range, episodic_type, title_keyword). */
  const values = Form.useWatch([], form)
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const [detail, setDetail] = useState<EpisodicMemoryDetail | null>(null)
  const [selected, setSelected] = useState<EpisodicMemory | null>(null)

  /** Fetch the episodic memory overview list with current filter values. */
  const getData = useCallback(() => {
    if (!id) return
    setLoading(true)
    setSelected(null)
    setDetail(null)
    getEpisodicOverview({
      end_user_id: id,
      ...values
    }).then((res) => {
      const response = res as EpisodicOverviewData
      setData(response)
      if (response.episodic_memories.length > 0) {
        setSelected(response.episodic_memories[0])
      }
      setLoading(false)
    })
    .finally(() => {
      setLoading(false)
    })
  }, [id, values])


  /** Fetch full detail for the currently selected episodic memory. */
  const getDetail = useCallback(() => {
    if (!id || !selected || !selected.id) return

    setDetailLoading(true)
    getEpisodicDetail({
      end_user_id: id as string,
      summary_id: selected.id
    })
      .then(res => {
        setDetail(res as EpisodicMemoryDetail)
      })
      .finally(() => {
        setDetailLoading(false)
      })
  }, [id, selected])
  /* Re-fetch overview whenever filter form values change. */
  useEffect(() => {
    getData()
  }, [id, values, getData])

  /* Load detail whenever a different memory card is selected. */
  useEffect(() => {
    getDetail()
  }, [selected, getDetail])

  return (
    <Row gutter={16} wrap={false} className="rb:h-full!">
      <Col flex="400px" className="rb:h-full!">
        <RbCard
          title={<div className="rb:leading-5.5!">
            <span className="rb:font-[MiSans-Bold] rb:font-bold">{t('episodicDetail.curResult')}</span>
            <span className="rb:text-gray-600 rb:font-regular!"> ({data.total || 0}{t('episodicDetail.unix')})</span>
          </div>}
          headerType="borderless"
          className="rb:h-full!"
          headerClassName="rb:min-h-[38px]! rb:pt-3! rb:mb-0!"
          bodyClassName="rb:p-3! rb:pb-0! rb:h-[calc(100%-38px)]!"
        >
          <Form form={form} initialValues={{ time_range: 'all', episodic_type: 'all' }}>
            <Row gutter={[8, 8]} className="rb:mb-3">
              <Col span={12}>
                <Form.Item name="time_range" noStyle>
                  <Select
                    placeholder={t('common.pleaseSelect')}
                    options={[
                      { value: 'all', label: t('episodicDetail.all') },
                      { value: 'today', label: t('episodicDetail.today') },
                      { value: 'this_week', label: t('episodicDetail.this_week') },
                      { value: 'this_month', label: t('episodicDetail.this_month') },
                    ]}
                    className="rb:w-full"
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="episodic_type" noStyle>
                  <Select
                    placeholder={t('common.pleaseSelect')}
                    options={[
                      { value: 'all', label: t('episodicDetail.allType') },
                      ...(Object.keys(TAG_COLORS).filter(key => key !== 'default').map(key => ({
                        value: key,
                        label: t(`episodicDetail.${key}`)
                      }))),
                    ]}
                    className="rb:w-full"
                  />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item name="title_keyword" noStyle>
                  <Input placeholder={t('episodicDetail.titleKeywordPlaceholder')} />
                </Form.Item>
              </Col>
            </Row>
          </Form>
          {loading
            ? <Skeleton active />
            : !data.episodic_memories || data.episodic_memories.length === 0
              ? <Empty />
              : (
                <Flex gap={12} vertical className="rb:overflow-y-auto rb:h-[calc(100%-84px)] rb:pb-3!">
                  {data.episodic_memories.map((vo, index) => (
                    <Flex
                      key={vo.id}
                      gap={12}
                      align="center"
                      className={clsx("rb:cursor-pointer rb:rounded-xl rb:px-3! rb:py-2!", {
                        'rb-border': selected?.id !== vo.id,
                        'rb:border rb:border-[#171719]': selected?.id === vo.id,
                      })}
                      onClick={() => setSelected(vo)}
                    >
                      <div className="rb:rounded-md rb:text-[#FFFFFF] rb:size-5 rb:text-[10px] rb:leading-3.5 rb:text-center rb:py-0.75 rb:bg-[#171719]">{index + 1}</div>
                      <div className="rb:flex-1 rb:w-[calc(100%-36px)]">
                        <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1 rb:text-gray-800 rb:font-medium rb:leading-5 rb:mb-1">{vo.title}</div>
                        <Flex align="center" justify="space-between" className="rb:text-gray-600 rb:text-[12px]">
                          {formatDateTime(vo.created_at)}
                          {vo.type && <Tag color={TAG_COLORS[getTypeKey(vo.type)]}>{t(`episodicDetail.${getTypeKey(vo.type)}`)}</Tag>}
                        </Flex>
                      </div>
                    </Flex>
                  ))}
                </Flex>
              )
          }
        </RbCard>
      </Col>
      <Col flex="1" className="rb:h-full!">
        <RbCard
          title={selected?.title}
          headerType="borderless"
          className="rb:h-full!"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:h-[calc(100%-54px)]! rb:overflow-y-auto"
        >
          {detailLoading
            ? <Skeleton active />
            : !selected || !detail
              ? <Empty className="rb:mt-14" />
              : (
                <Flex gap={16} vertical>
                  <div className="rb-border rb:rounded-xl rb:px-4 rb:py-3">
                    <Row gutter={12}>
                      <Col span={8}>
                        <div className="rb:text-gray-600 rb:leading-5">
                          {t('episodicDetail.created')}
                          <div className="rb:font-medium rb:mt-1 rb:text-[#171719]">{formatDateTime(detail.created_at)}</div>
                        </div>
                      </Col>
                      <Col span={8}>
                        <div className="rb:text-gray-600 rb:leading-5">
                          {t('episodicDetail.episodic_type')}
                          <div className="rb:font-medium rb:mt-1 rb:text-[#171719]">{detail.episodic_type}</div>
                        </div>
                      </Col>
                      {detail.involved_objects.length > 0 && <Col span={8}>
                        <div className="rb:text-gray-600 rb:leading-5">
                          {t('episodicDetail.involved_objects')}
                          <Flex gap={8} className="rb:mt-1!">{detail.involved_objects.map((vo, index) => <Tag key={index}>{vo}</Tag>)}</Flex>
                        </div>
                      </Col>}
                    </Row>
                  </div>
                  <div>
                    <div className="rb:font-medium rb:leading-5 rb:mb-2 rb:pl-1">{t('episodicDetail.content_records')}</div>

                    <ul className="rb:leading-5.5 rb:list-disc rb-border rb:rounded-xl rb:pl-8 rb:pr-4 rb:py-3">
                      {detail.content_records?.length > 0
                        ? detail.content_records.map((vo, index) => <li key={index}>{vo}</li>)
                        : <Empty size={96} className="rb:pt-1! rb:pb-3.5!" />
                      }
                    </ul>
                  </div>

                  <div>
                    <div className="rb:font-medium rb:leading-5 rb:mb-2 rb:pl-1">{t('episodicDetail.emotion')}</div>
                    <div className="rb-border rb:rounded-xl rb:px-4 rb:py-3">
                      {detail.emotion
                        ? t(`statementDetail.${detail.emotion || 'none'}`)
                        : <Empty size={96} className="rb:pt-1! rb:pb-3.5!" />
                      }
                    </div>
                  </div>
                </Flex>
              )
          }
        </RbCard>
      </Col>
    </Row>
  )
}
export default EpisodicDetail