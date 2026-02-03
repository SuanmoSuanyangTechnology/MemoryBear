/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 18:32:35 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 18:32:35 
 */
/**
 * Node Statistics Component
 * Displays memory node statistics by type with navigation to detail views
 */

import { type FC, useEffect, useState } from 'react'
import clsx from 'clsx'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Skeleton } from 'antd';

import RbCard from '@/components/RbCard/Card'
import {
  getNodeStatistics,
} from '@/api/memory'
import type { NodeStatisticsItem } from '../types'


/** Background gradient list */
const BG_LIST = [
  'rb:bg-[linear-gradient(316deg,rgba(21,94,239,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(316deg,rgba(54,159,33,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(314deg,rgba(156,111,255,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(332deg,rgba(255,93,52,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(313deg,rgba(156,111,255,0.06)_0%,rgba(251,253,255,0)_100%)]',
  'rb:bg-[linear-gradient(332deg,rgba(54,159,33,0.06)_0%,rgba(251,253,255,0)_100%)]',
]
/** Memory type configuration */
const typeList = [
  { key: 'PERCEPTUAL_MEMORY', bg: 0 },
  { key: 'WORKING_MEMORY', bg: 1 },
  { key: 'EMOTIONAL_MEMORY', bg: 2 },
  { key: 'SHORT_TERM_MEMORY', bg: 3 },
  { 
    key: 'LONG_TERM_MEMORY',
    bg: 4,
    children: [
      { key: 'IMPLICIT_MEMORY' },
      { key: 'EPISODIC_MEMORY' },
      { key: 'EXPLICIT_MEMORY' }
    ]
  },
  { key: 'FORGET_MEMORY', bg: 5 },
]

const NodeStatistics: FC = () => {
  const navigate = useNavigate();
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [total, setTotal] = useState<number>(0)
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
      // 计算count总计
      const totalCount = response.reduce((sum, item) => sum + (item.count || 0), 0)
      setTotal(totalCount)
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
  const renderCard = (key: string, bgIndex: number | null, isChild: boolean = false) => {
    const item = data.find((item) => item.type === key)
    return (
      <div
        className={clsx(
          "rb:flex rb:flex-col rb:justify-between rb:group rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:pt-3 rb:px-4 rb:pb-5 rb:cursor-pointer",
          {
            'rb:h-45': !isChild,
            'rb:h-31': isChild
          },
          typeof bgIndex === 'number' ? BG_LIST[bgIndex] : 'rb:bg-[#FBFDFF]'
        )}
        onClick={() => handleViewDetail(key)}
      >
        <div>
          <div className={clsx("rb:text-[#5B6167] rb:leading-5 rb:font-regular", {
            'rb:mb-2': !isChild,
            'rb:mb-1': isChild
          })}>
            {t(`userMemory.${key}`)}
          </div>
          <div className="rb:w-3 rb:h-3 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/userMemory/arrow_right.svg')] rb:group-hover:bg-[url('@/assets/images/userMemory/arrow_right_hover.svg')]"></div>
        </div>
        <div className="rb:text-[28px] rb:leading-8.75 rb:font-extrabold">{item?.count ?? 0}</div>
      </div>
    )
  }

  return (
    <RbCard 
      title={<>{t('userMemory.nodeStatistics')} <span className="rb:text-[#5B6167] rb:font-normal!">({t('userMemory.total')}: {total})</span></>}
      headerType="borderless"
      headerClassName="rb:min-h-[46px]!"
    >
      {loading
        ? <Skeleton active />
        : <div className="rb:w-full rb:grid rb:grid-cols-8 rb:gap-3">
          {typeList.map((vo) => {
            if (!vo.children) {
              return <div key={vo.key}>{renderCard(vo.key, vo.bg)}</div>
            }
            return (
              <div key={vo.key} className={clsx("rb:col-span-3 rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:p-3", BG_LIST[vo.bg])}>
                <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-3">{t(`userMemory.${vo.key}`)}</div>
                <div className="rb:grid rb:grid-cols-3 rb:gap-3">
                  {vo.children.map((child) => <div key={child.key}>{renderCard(child.key, null, true)}</div>)}
                </div>
              </div>
            )
          })}
          </div>
        }
    </RbCard>
  )
}
export default NodeStatistics