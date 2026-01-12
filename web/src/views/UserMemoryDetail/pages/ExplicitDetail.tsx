import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { List, Skeleton, Row, Col } from 'antd'
import RbCard from '@/components/RbCard/Card'
import {
  getExplicitMemory,
} from '@/api/memory'
import { formatDateTime } from '@/utils/format'
import Empty from '@/components/Empty'
import ExplicitDetailModal from '../components/ExplicitDetailModal'

export interface EpisodicMemory {
  id: string;
  title: string;
  content: string;
  created_at: number;
}
export interface SemanticMemory {
  id: string;
  name: string;
  entity_type: string;
  core_definition: string;
  created_at: number;
}
interface Data {
  episodic_memories: EpisodicMemory[];
  semantic_memories: SemanticMemory[]
}

export interface ExplicitDetailModalRef {
  handleOpen: (vo: EpisodicMemory | SemanticMemory) => void;
}

const ExplicitDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const explicitDetailModalRef = useRef<ExplicitDetailModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Data>({ episodic_memories: [], semantic_memories: [] })

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
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
  const handleView = (item: EpisodicMemory | SemanticMemory) => {
    explicitDetailModalRef.current?.handleOpen(item)
  }
  return (
    <div className="rb:h-full rb:w-full">
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mt-3 rb:rounded-md rb:mb-4">{t('explicitDetail.episodic_memories')}</div>
      {loading ?
        <Skeleton active />
        : data.episodic_memories?.length > 0 ? (
          <Row gutter={16}>
            {data.episodic_memories.map(item => (
              <Col key={item.id} span={6}>
                <RbCard
                  title={item.title}
                  className="rb:h-full! rb:cursor-pointer"
                  onClick={() => handleView(item)}
                >
                  <div>{formatDateTime(item.created_at)}</div>
                  <div>{item.content}</div>
                </RbCard>
              </Col>
            ))}
          </Row>
        ) : <Empty />}

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-3 rb:py-2.5 rb:font-medium rb:leading-5 rb:mt-6 rb:rounded-md rb:mb-4">{t('explicitDetail.semantic_memories')}</div>
      {loading ?
        <Skeleton active />
        : data.semantic_memories?.length > 0 ? (
          <Row gutter={16}>
            {data.semantic_memories.map(item => (
              <Col key={item.id} span={6}>
                <RbCard
                  title={item.name}
                  className="rb:h-full! rb:cursor-pointer"
                  onClick={() => handleView(item)}
                >
                  <div>{item.core_definition}</div>
                </RbCard>
              </Col>
            ))}
          </Row>
        ) : <Empty />}

      <ExplicitDetailModal
        ref={explicitDetailModalRef}
      />
    </div>
  )
}
export default ExplicitDetail