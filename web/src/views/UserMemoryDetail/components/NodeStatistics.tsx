/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:35 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-02-05 19:07:07
 */
/**
 * Node Statistics Component
 * Displays memory node statistics by type with navigation to detail views
 */

import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Skeleton, Flex, Divider } from 'antd';

import {
  getNodeStatistics,
} from '@/api/memory'
import type { NodeStatisticsItem } from '../types'

/** Memory type configuration */
const typeList = [
  { key: 'PERCEPTUAL_MEMORY', bg: 0 },
  { key: 'WORKING_MEMORY', bg: 1 },
  { key: 'EMOTIONAL_MEMORY', bg: 2 },
  { key: 'SHORT_TERM_MEMORY', bg: 3 },
  { key: 'FORGET_MEMORY', bg: 5 },
  { 
    key: 'LONG_TERM_MEMORY',
    bg: 4,
    children: [
      { key: 'IMPLICIT_MEMORY' },
      { key: 'EPISODIC_MEMORY' },
      { key: 'EXPLICIT_MEMORY' }
    ]
  },
]

const NodeStatistics: FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<NodeStatisticsItem[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /** Fetch node statistics */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getNodeStatistics(id).then((res) => {
      const response = res as NodeStatisticsItem[]
      setData(response)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })
  }
  /** Navigate to detail page */
  const handleViewDetail = (type: string) => {
    navigate(`/user-memory/detail/${id}/${type}`)
  }
  /** Render statistics card */
  const renderCard = (key: string, isChild: boolean = false) => {
    const item = data.find((item) => item.type === key)
    return (
      <Flex
        vertical
        justify="space-between"
        className={clsx(
          "rb:h-full rb:group rb:cursor-pointer rb:bg-[#FFFFFF]",{
            'rb:rounded-xl rb:shaodow-[0px_2px_6px_0px_rgba(33,35,50,0.08)] rb:p-3!': !isChild,
            'rb:px-3! rb:pt-2! rb:pb-2.5! rb:w-full': isChild
          }
        )}
        onClick={() => handleViewDetail(key)}
      >
        <div>
          <div className={clsx("rb:text-[#5B6167] rb:leading-5 rb:font-regular")}>
            {t(`userMemory.${key}`)}
          </div>
        </div>
        <Flex justify="space-between" align="center">
          <div className="rb:text-[24px] rb:leading-8 rb:font-extrabold rb:font-[MiSans-Heavy]">{item?.count ?? 0}</div>
          <div className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/arrow_right.svg')] rb:group-hover:bg-[url('@/assets/images/userMemory/arrow_right_hover.svg')]"></div>
        </Flex>
      </Flex>
    )
  }

  return (
    <div className="rb:h-22">
      {loading
        ? <Skeleton active />
        : <div className="rb:w-full rb:grid rb:grid-cols-8 rb:gap-3 rb:h-full">
          {typeList.map((vo) => {
            if (!vo.children) {
              return <div key={vo.key} className="rb:h-full">{renderCard(vo.key)}</div>
            }
            return (
              <div key={vo.key} className={clsx("rb:col-span-3 rb:shaodow-[0px_2px_6px_0px_rgba(33,35,50,0.08)] rb:rounded-xl rb:bg-[#FFFFFF] rb:overflow-hidden")}>
                <div className="rb:bg-[#171719] rb:text-[12px] rb:text-[#FFFFFF] rb:font-medium rb:text-center rb:leading-4 rb:py-px rb:rounded-tl-xl  rb:rounded-tr-xl">{t(`userMemory.${vo.key}`)}</div>
                <div className="rb:grid rb:grid-cols-3">
                  {vo.children.map((child, index) => <Flex key={child.key} align="center">
                    {index > 0 && <Divider type="vertical" className="rb:h-12! rb:mx-0!" />}
                    {renderCard(child.key, true)}
                  </Flex>)}
                </div>
              </div>
            )
          })}
          </div>
        }
    </div>
  )
}
export default NodeStatistics