import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Select, Form, Space, Skeleton, Input } from 'antd'
import RbCard from '@/components/RbCard/Card'
import {
  getEpisodicOverview,
  getEpisodicDetail,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'
import RbAlert from '@/components/RbAlert'
import Empty from '@/components/Empty'

interface EpisodicMemory {
  id: string;
  title: string;
  type: string;
  created_at: number;
}
interface EpisodicOverviewData {
  total: number;
  total_all: number;
  episodic_memories: EpisodicMemory[]
}
interface EpisodicMemoryDetail {
  id: string;
  created_at: number;
  involved_objects: string[];
  episodic_type: string;
  content_records: string[];
  emotion: string;
}

const TAG_COLORS: Record<string, "processing" | "success" | "warning" | "error" | "default"> = {
  conversation: "processing",
  project_work: "success",
  learning: "warning",
  decision: "warning",
  important_event: "error",
}
const BG_COLORS: Record<string, string> = {
  conversation: "rb:bg-[#155EEF]",
  project_work: "rb:bg-[#369F21]",
  learning: "rb:bg-[#FF5D34]",
  decision: "rb:bg-[#FF5D34]",
  important_event: "rb:bg-[#5B6167]",
}

// Map display types to internal keys
const getTypeKey = (type: string): string => {
  const typeMap: Record<string, string> = {
    'Learning': 'learning',
    'Project/Work': 'project_work',
    'Conversation': 'conversation',
    'Decision': 'decision',
    'Important Event': 'important_event',
  }
  return typeMap[type] || type.toLowerCase().replace(/[^a-z0-9]/g, '_')
}
const EpisodicDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<EpisodicOverviewData>({} as EpisodicOverviewData)
  const values = Form.useWatch([], form)
  const [detailLoading, setDetailLoading] = useState<boolean>(false)
  const [detail, setDetail] = useState<EpisodicMemoryDetail | null>(null)
  const [selected, setSelected] = useState<EpisodicMemory | null>(null)

  useEffect(() => {
    if (!id) return
    // getData()
  }, [id])
  
  // 记忆洞察
  const getData = () => {
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
  }

  useEffect(() => {
    getData()
  }, [values])

  useEffect(() => {
    getDetail()
  }, [selected])

  const getDetail = () => {
    if (!selected || !selected.id) return

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
  }

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:flex rb:justify-between rb:items-center rb:text-[#FFFFFF] rb:leading-5 rb:h-30 rb:p-5 rb:bg-[url('@/assets/images/userMemory/shortTerm.png')] rb:bg-cover rb:mb-6">
        <div className="rb:max-w-135">{t('episodicDetail.title')}</div>

        <div className="rb:grid rb:grid-cols-1 rb:gap-4">
          <div className="rb:bg-[rgba(255,255,255,0.2)] rb:rounded-lg rb:p-3.5 rb:text-[12px] rb:text-center">
            <div className="rb:text-[24px] rb:leading-8 rb:mb-1">{data.total_all ?? 0}</div>
            {t(`episodicDetail.total_all`)}
          </div>
        </div>
      </div>

      <Form form={form} initialValues={{ time_range: 'all', episodic_type: 'all' }}>
        <Row gutter={16}>
          <Col span={6}>
            <Form.Item name="time_range">
              <Select
                placeholder={t('common.pleaseSelect')}
                options={[
                  { value: 'all', label: t('episodicDetail.all') },
                  { value: 'today', label: t('episodicDetail.today') },
                  { value: 'this_week', label: t('episodicDetail.this_week') },
                  { value: 'this_month', label: t('episodicDetail.this_month') },
                ]}
              />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="episodic_type">
              <Select
                placeholder={t('common.pleaseSelect')}
                options={[
                  { value: 'all', label: t('episodicDetail.all') },
                  { value: 'conversation', label: t('episodicDetail.conversation') },
                  { value: 'project_work', label: t('episodicDetail.project_work') },
                  { value: 'learning', label: t('episodicDetail.learning') },
                  { value: 'decision', label: t('episodicDetail.decision') },
                  { value: 'important_event', label: t('episodicDetail.important_event') },
                ]}
              />
            </Form.Item>
          </Col>
          <Col span={6}>
            <Form.Item name="title_keyword">
              <Input placeholder={t('episodicDetail.titleKeywordPlaceholder')} />
            </Form.Item>
          </Col>
        </Row>
      </Form>

      <Row gutter={16}>
        <Col span={8}>
          <RbCard
            title={<>{t('episodicDetail.curResult')}<span className="rb:text-[#5B6167] rb:font-regular!"> ({data.total || 0}{t('episodicDetail.unix')})</span></>}
            headerType="borderless"
          >
            {loading
              ? <Skeleton active />
              : !data.episodic_memories || data.episodic_memories.length === 0
                ? <Empty />
                : (
                  <Space size={8} direction="vertical" className="rb:w-full">
                    {data.episodic_memories.map((vo, index) => (
                      <div
                        key={vo.id}
                        className={clsx("rb:cursor-pointer rb:flex rb:items-center rb:bg-[#FFFFFF] rb:border  rb:rounded-lg rb:px-3 rb:py-2 rb:leading-5", {
                          'rb:border-[#DFE4ED] rb:shadow-[0px_2px_4px_0px_rgba(33,35,50,0.16)]': selected?.id !== vo.id,
                          'rb:border-[#155EEF]': selected?.id === vo.id,
                        })}
                        onClick={() => setSelected(vo)}
                      >
                        <div className={clsx("rb:bg-[#369F21] rb:rounded-lg rb:text-[#FFFFFF] rb:size-6 rb:text-[12px] rb:leading-6 rb:text-center rb:mr-3", BG_COLORS[getTypeKey(vo.type)])}>{index + 1}</div>
                        <div className="rb:flex-1">
                          <div className="rb:flex rb:items-center rb:justify-between">{vo.title} <Tag color={TAG_COLORS[getTypeKey(vo.type)]}>{t(`episodicDetail.${getTypeKey(vo.type)}`)}</Tag></div>
                          <div className="rb:text-[#5B6167] rb:text-[12px]">{formatDateTime(vo.created_at)}</div>
                        </div>
                      </div>
                    ))}
                  </Space>
                )
            }

          </RbCard>
        </Col>
        <Col span={16}>
          <RbCard
            title={selected?.title}
            headerType="borderless"
          >
            {detailLoading
              ? <Skeleton active />
              : !selected || !detail
                ? <Empty className="rb:mt-14" />
                : (
                  <Space size={12} direction="vertical" className="rb:w-full">
                    <div className="rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-3 rb:py-2 rb:leading-5">
                      <Row gutter={[12, 16]}>
                        <Col span={12}>
                          <div className="rb:text-[#5B6167]">{t('episodicDetail.created')}<br />{formatDateTime(detail.created_at)}</div>
                        </Col>
                        <Col span={12}>
                          <div className="rb:text-[#5B6167]">{t('episodicDetail.episodic_type')}<br />{detail.episodic_type}</div>
                        </Col>
                        {detail.involved_objects.length > 0 && <Col span={24}>
                          <div className="rb:font-medium rb:leading-5 rb:mb-1">{t('episodicDetail.involved_objects')}</div>
                          <Space size={8}>{detail.involved_objects.map((vo, index) => <Tag key={index}>{vo}</Tag>)}</Space>
                        </Col>}
                      </Row>
                    </div>
                    <div className="rb:bg-[#FFFFFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-3 rb:py-2 rb:leading-5">
                      <div className="rb:font-medium rb:leading-5 rb:mb-1">{t('episodicDetail.content_records')}</div>
                      {detail.content_records.map((vo, index) => <div key={index} className="rb:text-[#5B6167] rb:leading-5">- {vo}</div>)}
                    </div>
                    <RbAlert>
                      {t('episodicDetail.emotion')}: {t(`statementDetail.${detail.emotion}`)}
                    </RbAlert>
                  </Space>
                )
            }
          </RbCard>
        </Col>
      </Row>
    </div>
  )
}
export default EpisodicDetail