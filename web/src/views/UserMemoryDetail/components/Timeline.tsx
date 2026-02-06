/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:36 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:31:36 
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Space, Divider } from 'antd';

import RbCard from '@/components/RbCard/Card'
import {
  getPerceptualTimeline
} from '@/api/memory'
import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'

/**
 * Timeline item structure
 * @property {string} id - Item ID
 * @property {number} perceptual_type - Perceptual type (1: visual, 2: audio, 3: text)
 * @property {string} file_path - File path
 * @property {string} file_name - File name
 * @property {string} summary - Content summary
 * @property {number} storage_type - Storage type
 * @property {string | number} created_time - Creation time
 * @property {string} domain - Domain
 * @property {string} topic - Topic
 * @property {string[]} keywords - Keywords
 */
interface TimelineItem {
  id: string;
  perceptual_type: number;
  file_path: string;
  file_name: string;
  summary: string;
  storage_type: number;
  created_time: string | number;
  domain: string;
  topic: string;
  keywords: string[]
}

/**
 * Perceptual type mapping
 */
const perceptual_type: Record<number, string> = {
  1: 'last_visual',
  2: 'last_listen',
  3: 'last_text',
}

/**
 * Timeline Component
 * Displays chronological timeline of perceptual memories
 * Shows visual, audio, and text memories with metadata
 */
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
                <div className="rb:flex-1 rb:pb-4">
                  <div className="rb:flex rb:justify-between">
                    <div className="rb:w-150 rb:leading-5 rb:font-medium">{vo.summary}</div>
                    <div className="rb:text-[#5B6167] rb:font-medium rb:flex-1 rb:text-right">{t(`perceptualDetail.${perceptual_type[vo.perceptual_type]}`)}</div>
                  </div>
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:mt-2">{[vo.domain, vo.topic].join(' | ')}</div>
                  
                  <Space size={8} className="rb:mt-2">{vo.keywords.map(tag => <Tag>{tag}</Tag>)}</Space>
                </div>
              </div>
            ))}
        </Space>
        }
    </RbCard>
  )
}
export default Timeline