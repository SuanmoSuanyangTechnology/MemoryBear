/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:31:36 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 11:13:44
 */
import { type FC, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Skeleton, Row, Col, Flex } from 'antd';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getPerceptualTimeline
} from '@/api/memory'
import { formatDateTime } from '@/utils/format';
import Empty from '@/components/Empty'

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
    <RbCard
      title={t('perceptualDetail.timeLine')}
      headerType="borderless"
      headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
      bodyClassName="rb:pl-5! rb:pt-0! rb:pr-3! rb:pb-4! rb:h-[calc(100%-54px)] rb:overflow-y-auto"
      className="rb:h-full!"
    >
      {loading
        ? <Skeleton active />
        : data.length === 0
        ? <Empty />
        : <Flex gap={12} vertical>
            {data.map((vo, index) => (
              <Row key={vo.id}className="rb:flex rb:gap-6 rb:min-h-16">
                <Col flex="90px" className="rb:leading-5 rb:font-semibold">
                  <Flex vertical gap={12} align="center" justify="center" className="rb:h-full!">
                    <span className="rb:text-center">{formatDateTime(vo.created_time)}</span>
                    <div className={clsx("rb:flex-1 rb:w-px", {
                      'rb:bg-[#5B6167]!': index !== data.length - 1
                    })} />
                  </Flex>
                </Col>
                <Col flex="1" className="rb:mb-1! rb:bg-[#F6F6F6] rb:rounded-xl rb:py-3 rb:px-4">
                  <div className="rb:leading-4.5 rb:font-bold rb:text-[12px] rb:font-[MiSans-Bold]">{t(`perceptualDetail.${perceptual_type[vo.perceptual_type]}`)}</div>
                  
                  <div className="rb:leading-5 rb:mt-2">{vo.summary}</div>
                  <div className="rb:leading-5 rb:text-[#5B6167] rb:mt-2">{[vo.domain, vo.topic].join(' | ')}</div>
                  
                  <Flex gap={8} wrap className="rb:mt-2!">
                    {vo.keywords.map((tag, index) => <div key={index} className="rb:bg-white rb:rounded-[13px] rb:py-1 rb:px-2 rb:font-medium rb:leading-4.5 rb:text-[12px]">{tag}</div>)}
                  </Flex>
                </Col>
              </Row>
            ))}
          </Flex>
        }
    </RbCard>
  )
}
export default Timeline