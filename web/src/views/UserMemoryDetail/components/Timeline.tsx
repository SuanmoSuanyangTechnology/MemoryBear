import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Progress, Space, Tooltip, Divider } from 'antd';
import RbCard from '@/components/RbCard/Card'
import {
  getPerceptualTimeline
} from '@/api/memory'
import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'

interface TimelineItem {
  id: string;
  perceptual_type: number;
  file_path: string;
  file_name: string;
  summary: string;
  storage_type: number;
  created_time: string | number;
}

const KEYS = {
  last_visual: ['summary', 'keywords', 'topic', 'domain', 'scene'],
  last_listen: ['summary', 'keywords', 'topic', 'domain', 'speaker_count'],
  last_text: ['summary', 'keywords', 'topic', 'domain', 'section_count'],
}

const perceptual_type: Record<number, string> = {
  1: 'last_visual',
  2: 'last_listen',
  3: 'last_text',
}

const Timeline: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<TimelineItem[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  const getData = () => {
    if (!id) return
    setLoading(true)
    getPerceptualTimeline(id).then((res) => {
      const response = res as { memories: TimelineItem[] }
      setData(response.memories || [])
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }

  return (
    <RbCard>
      {loading
        ? <Skeleton active />
        : data.length === 0
        ? <Empty />
        : <Space size={8} direction="vertical" className="rb:w-full">
            {data.map((vo, index) => (
              <div key={vo.id} className="rb:flex rb:gap-6 rb:min-h-16">
                <div className="rb:text-[#155EEF] rb:leading-5 rb:font-medium rb:flex rb:flex-col rb:gap-2 rb:items-center">
                  {formatDateTime(vo.created_time)}
                  {index !== data.length - 1 && <Divider type="vertical" className="rb:flex-1 rb:w-px rb:border-[#155EEF]!" />}
                </div>
                <div className="rb:flex rb:justify-between rb:flex-1 rb:mb-4">
                  <div className="rb:w-150 rb:leading-5">{vo.summary}</div>
                  <div className="rb:text-[#5B6167] rb:font-medium">{t(`perceptualDetail.${perceptual_type[vo.perceptual_type]}`)}</div>
                </div>
              </div>
            ))}
        </Space>
        }
    </RbCard>
  )
}
export default Timeline