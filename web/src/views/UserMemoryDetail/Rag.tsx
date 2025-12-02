import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import clsx from 'clsx'
import { Row, Col, Skeleton } from 'antd'
import { useParams } from 'react-router-dom'
import aboutUs from '@/assets/images/userMemory/aboutUs.svg'
import down from '@/assets/images/userMemory/down.svg'
import interestDistribution from '@/assets/images/userMemory/interestDistribution.svg'
import RbCard from '@/components/RbCard/Card'
import type { Data } from './types'
import {
  getChunkSummaryTag,
  getUserProfile,
  getTotalRagMemoryCountByUser,
  getChunkInsight,
} from '@/api/memory'
import Empty from '@/components/Empty'
import ConversationMemory from './components/ConversationMemory'

const tagColors = ['21, 94, 239', '156, 111, 255', '255, 93, 52', '54, 159, 33']

interface TitleProps {
  type: string;
  title: string
  icon: string
  t: (key: string) => string;
  expanded: boolean;
  onClick: (type: string) => void;
}
const Title: FC<TitleProps> = ({ type, title, icon, t, expanded, onClick }) => (
  <div className="rb:flex rb:items-center rb:justify-between rb:py-[17px] rb:border-b-[1px] rb:border-[#DFE4ED] rb:text-[16px] rb:font-semibold rb:leading-[22px]">
    <span className="rb:flex rb:items-center">
      <img src={icon} className="rb:w-[20px] rb:h-[20px] rb:mr-[8px]" />
      {title}
    </span>

    <span className="rb:flex rb:items-center rb:cursor-pointer rb:text-[#5B6167] rb:text-[14px] rb:font-regular rb:leading-[20px]" onClick={() => onClick(type)}>
      {t(`userMemory.${expanded ? 'foldUp' : 'expanded'}`)}
      <img src={down} className={clsx("rb:w-[16px] rb:h-[16px] rb:ml-[4px]", {
        'rb:rotate-180': !expanded,
      })} />
    </span>
  </div>
)

const Rag: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [data, setData] = useState<Data | null>(null)
  const [expanded, setExpanded] = useState<string[]>(['aboutUs', 'memoryInsight',])
  const [summary, setSummary] = useState<string | null>('')
  const [loading, setLoading] = useState<Record<string, boolean>>({
    detail: true,
    summary: true,
    insight: true,
  })
  const [memory, setMemory] = useState<number | null>(null)
  const [insight, setInsight] = useState<string | null>('')
  const [tags, setTags] = useState<{ tag: string; frequency: number }[]>([])
  const [personas, setPersonas] = useState<string[]>([])

  useEffect(() => {
    if (!id) return
    getMemory()
    getSummary()
    getDetail()
    getInsightReport()
  }, [id])

  const handleTitleClick = (key: string) => {
    setExpanded(expanded.includes(key) ? expanded.filter((item) => item !== key) : [...expanded, key])
  }
  // 用户记忆详情
  const getDetail = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, detail: true }))
    getUserProfile(id).then((res) => {
      setData((res as Data))
    })
    .finally(() => {
    setLoading(prev => ({ ...prev, detail: false }))
    })
  }
  // 记忆总览
  const getMemory = () => {
    if (!id) return
    getTotalRagMemoryCountByUser(id).then((res) => {
      setMemory(res as number || 0)
    })
  }
  // 用户摘要
  const getSummary = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, summary: true }))
    getChunkSummaryTag(id).then((res) => {
      const response = res as { summary?: string; tags?: { tag: string; frequency: number }[]; personas?: string[] }
      setSummary(response.summary || null)
      setTags(response.tags || [])
      setPersonas(response.personas || [])
    })
    .finally(() => {
      setLoading(prev => ({ ...prev, summary: false }))
    })
  }
  // 记忆洞察
  const getInsightReport = () => {
    if (!id) return
    setLoading(prev => ({ ...prev, insight: true }))
    getChunkInsight(id).then((res) => {
      setInsight((res as { insight?: string }).insight || null)
    })
    .finally(() => {
      setLoading(prev => ({ ...prev, insight: false }))
    })
  }
  const name = loading.detail ? '' : data?.name && data?.name !== '' ? data.name : id
  return (
    <Row gutter={[16, 16]} className="rb:pb-[24px]">
      <Col span={8}>
        <RbCard>
          <div className="rb:flex rb:items-center">
            <div className="rb:flex-[0_0_auto] rb:w-[80px] rb:h-[80px] rb:text-center rb:font-semibold rb:text-[28px] rb:leading-[80px] rb:rounded-[8px] rb:text-[#FBFDFF] rb:bg-[#155EEF]">{name?.[0]}</div>
            <div className="rb:text-[24px] rb:font-semibold rb:leading-[32px] rb:ml-[16px]">
              {name}<br/>
              <div className="rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-[16px] rb:mt-[8px]">{personas?.join(' | ')}</div>
            </div>
          </div>

          <div className="rb:flex rb:gap-[8px] rb:mb-[8px] rb:flex-wrap rb:mt-[25px]">
            {tags?.map((tag, tagIndex) => (
              <span key={tag.tag} className="rb:rounded-[11px] rb:p-[0_8px] rb:leading-[22px] rb:border"
                style={{
                  backgroundColor: `rgba(${tagColors[tagIndex % tagColors.length]}, 0.08)`,
                  borderColor: `rgba(${tagColors[tagIndex % tagColors.length]}, 0.3)`,
                  color: `rgba(${tagColors[tagIndex % tagColors.length]}, 1)`,
                }}
              >
                {tag.tag}({tag.frequency})
              </span>
            ))}
          </div>

          {/* 记忆总量 */}
          <div className="rb:font-regular rb:text-[12px] rb:text-[#5B6167] rb:leading-[16px] rb:mb-[25px]">
            {t('userMemory.totalNumOfMemories')}
            <div className="rb:font-extrabold rb:text-[24px] rb:text-[#212332] rb:leading-[30px] rb:mt-[8px]">{memory || 0}</div>
          </div>

          {/* 关于我 */}
          <>
            <Title
              type="aboutUs"
              title={t('userMemory.aboutMe')}
              icon={aboutUs}
              t={t}
              expanded={expanded.includes('aboutUs')}
              onClick={handleTitleClick}
            />
            {expanded.includes('aboutUs') && (
              <>
                {loading.summary
                  ? <Skeleton className="rb:mt-[16px]" />
                  : summary 
                  ? <div className="rb:font-regular rb:leading-[22px] rb:pt-[16px]">
                    {summary || '-'}
                  </div>
                  : <Empty size={88} className="rb:mt-[48px] rb:mb-[81px]" />
                }
              </>
            )}
          </>
          {/* 记忆洞察 */}
          <>
            <Title
              type="memoryInsight"
              title={t('userMemory.memoryInsight')}
              icon={interestDistribution}
              t={t}
              expanded={expanded.includes('memoryInsight')}
              onClick={handleTitleClick}
            />
            {expanded.includes('memoryInsight') && (
              <>
                {loading.insight
                  ? <Skeleton className="rb:mt-[16px]" />
                  : insight 
                  ? <div className="rb:font-regular rb:leading-[22px] rb:pt-[16px]">
                    {insight || '-'}
                  </div>
                  : <Empty size={88} className="rb:mt-[48px] rb:mb-[81px]" />
                }
              </>
            )}
          </>
        </RbCard>
      </Col>
      <Col span={16}>
        <ConversationMemory />
      </Col>
    </Row>
  )
}
export default Rag