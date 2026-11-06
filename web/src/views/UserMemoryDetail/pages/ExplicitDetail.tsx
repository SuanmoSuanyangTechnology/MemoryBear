import { type FC, useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Row, Col, Flex } from 'antd'
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getSemanticsMemory,
  type EpisodicMemoryType,
} from '@/api/memory'
import Empty from '@/components/Empty'
import ExplicitDetailModal from '../components/ExplicitDetailModal'
import ExplicitNodePanel from '../components/ExplicitNodePanel'
import PermanentMemoryPanel from '../components/PermanentMemoryPanel'

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
  /** Entity name displayed in the semantic memory list. */
  name: string;
  /** Classification of the entity (e.g. person, location, concept). */
  entity_type: string;
  /** Brief definition or description of the entity. */
  core_definition: string;
}

/** Imperative handle exposed by ExplicitDetailModal for opening the detail drawer. */
export interface ExplicitDetailModalRef {
  handleOpen: (vo: EpisodicMemory | SemanticMemory) => void;
}

/** Original rotating colour palette for semantic memory terms. */
const DEFAULT_COLORS = ['#FF8A4C', '#FF5D34', '#155EEF', '#9C6FFF', '#4DA8FF', '#369F21']
const SEMANTIC_SIZES = ['rb:text-sm', 'rb:text-lg', 'rb:text-sm', 'rb:text-base', 'rb:text-xl']

/** Permanent, node and semantic memories for the current user. */
const ExplicitDetail: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const explicitDetailModalRef = useRef<ExplicitDetailModalRef>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [permanentMemoryVisible, setPermanentMemoryVisible] = useState(false)
  const [semanticsMemory, setSemanticsMemory] = useState<SemanticMemory[]>([])

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

  return (
    <Row gutter={12} className="rb:h-full! rb:overflow-hidden!">
      <Col span={permanentMemoryVisible ? 6 : 0} className="rb:h-full! rb:overflow-hidden!">
        <PermanentMemoryPanel key={id} userId={id || ''} onVisibilityChange={setPermanentMemoryVisible} />
      </Col>
      <Col span={permanentMemoryVisible ? 12 : 16} className="rb:h-full! rb:overflow-hidden!">
        <ExplicitNodePanel key={id} userId={id || ''} />
      </Col>
      <Col span={permanentMemoryVisible ? 6 : 8} className="rb:h-full! rb:overflow-hidden!">
        <RbCard
          title={<>
            <span className="rb:font-[MiSans-Bold] rb:font-bold">{t('explicitDetail.semantic_memories')}</span>
            <span className="rb:font-normal! rb:text-gray-600 rb:ml-1 rb:text-[13px]">{semanticsMemory?.length ?? 0}</span>
          </>}
          headerType="borderless"
          headerClassName="rb:min-h-[53px]!"
          bodyClassName="rb:px-4! rb:py-0! rb:h-[calc(100%-53px)]!"
          className="rb:w-full! rb:h-full!"
        >
          {loading
            ? <Skeleton active />
            : semanticsMemory?.length > 0
            ? (
              <div className="rb:h-full rb:min-h-0 rb:overflow-y-auto rb:overflow-x-hidden rb:overscroll-contain">
                <Flex wrap justify="center" align="center" gap="12px 10px" className="rb:pb-4! rb:w-full">
                  {semanticsMemory.map((item, index) => (
                    <div
                      key={item.id}
                      onClick={() => handleView(item)}
                      style={{ color: DEFAULT_COLORS[index % DEFAULT_COLORS.length] }}
                      className={clsx(
                        'rb:max-w-full rb:min-w-0 rb:shrink-0 rb:cursor-pointer rb:text-center rb:font-medium rb:leading-6 rb:whitespace-normal rb:wrap-anywhere rb:hover:opacity-75 rb:focus-visible:outline-2 rb:focus-visible:outline-primary-600',
                        SEMANTIC_SIZES[index % SEMANTIC_SIZES.length],
                      )}
                    >
                      {item.name}
                    </div>
                  ))}
                </Flex>
              </div>
            )
            : <Empty size={88} className="rb:h-full!" />
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