/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-26 15:39:16 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-05-26 15:39:16 
 */
/**
 * Reflect Memory Component
 * Displays reflection logs including statistics and recent records
 */

import { useEffect, useState, forwardRef, useImperativeHandle } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { Skeleton, Flex } from 'antd';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import Empty from '@/components/Empty';
import {
  getReflectLogStats,
  getReflectLogs
} from '@/api/memory'
import type { ReflectMemoryRef } from '../types'
import Tag from '@/components/Tag'
import { formatDateTime } from '@/utils/format'

/**
 * Reflection log record
 */
export interface ReflectLog {
  id: string;
  sub_problem: SubProblem;
  trigger_type: TriggerType;
  baseline: string;
  strategy: string;
  confidence: number;
  status: Status;
  summary_text: string;
  created_at: number;
}
//  entity_dedup / description_merge / stale_detection / fac
// t_contradiction / metadata_validation / unresolved_entity
export type SubProblem = 'entity_dedup' | 'description_merge' | 'stale_detection' | 'fact_contradiction' | 'metadata_validation' | 'unresolved_entity';
export type Status = 'resolved' | 'recorded';
export type TriggerType = 'conversation' | 'scheduled';
/**
 * Reflection data structure
 */
export interface Data {
  total: number;
  sub_problem: Record<SubProblem, number>;
  status: Record<Status, number>;
  resolve_rate: number;
}

const ReflectMemory = forwardRef<ReflectMemoryRef, { className?: string; }>(({ className }, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const navigate = useNavigate()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<Data | null>(null)
  const [recentRecords, setRecentRecords] = useState<ReflectLog[]>([])

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  /** Fetch reflection logs data */
  const getData = () => {
    if (!id) return
    setLoading(true)
    getReflectLogStats(id).then((res) => {
      setData((res as Data) || null)
      setLoading(false) 
    })
    .finally(() => {
      setLoading(false)
    })

    getReflectLogs({
      end_user_id: id,
      page: 1,
      pagesize: 4
    })
      .then((res) => {
        setRecentRecords(((res as { items: ReflectLog[] }).items) || [])
      })
  }
  
  /** Navigate to full reflect view */
  const handleViewAll = () => {
    navigate(`/user-memory/detail/${id}/REFLECT_LOGS`)
  }
  
  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    getData,
  }));

  return (
    <RbCard 
      title={t('userMemory.reflectMemory')}
      headerClassName="rb:min-h-[46px]!! rb:font-medium!"
      className={clsx("rb:bg-[#FFFFFF]! rb:shadow-[0px_2px_6px_0px_rgba(33,35,50,0.13)]! rb:absolute! rb:w-100 rb:top-29 rb:left-26", className)}
      bodyClassName="rb:px-5! rb:pb-5! rb:pt-3.75! rb:max-h-[calc(100vh-186px)] rb:overflow-auto"
    >
      {loading
        ? <Skeleton />
        : data || recentRecords.length > 0
        ? <div>
            <div className="rb:max-h-[calc(100vh-280px)] rb:overflow-auto rb:mb-4">
              {/* Statistics Cards */}
              <div className="rb:flex rb:gap-3 rb:mb-5">
                <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-4 rb:text-center">
                  <div className="rb:text-[24px] rb:font-bold rb:text-[#212332]">{data?.total || 0}</div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1">{t('userMemory.total')}</div>
                </div>
                <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-4 rb:text-center">
                  <div className="rb:text-[24px] rb:font-bold rb:text-[#212332]">{data?.status?.['resolved'] || 0}</div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1">{t('userMemory.resolved')}</div>
                </div>
                <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-4 rb:text-center">
                  <div className="rb:text-[24px] rb:font-bold rb:text-[#212332]">{data?.status?.['recorded'] || 0}</div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1">{t('userMemory.recorded')}</div>
                </div>
                <div className="rb:flex-1 rb:bg-[#F6F6F6] rb:rounded-xl rb:p-4 rb:text-center">
                  <div className="rb:text-[24px] rb:font-bold rb:text-[#212332]">{((data?.resolve_rate || 0) * 100).toFixed(0)}%</div>
                  <div className="rb:text-[12px] rb:text-[#5B6167] rb:mt-1">{t('userMemory.resolve_rate')}</div>
                </div>
              </div>

              {/* Recent Records */}
              <div className="rb:font-medium rb:text-[#5B6167] rb:mb-3">{t('userMemory.recentRecords')}</div>
              <div className="rb:space-y-3">
                {recentRecords?.map(record => (
                  <div 
                    key={record.id}
                    className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-3"
                  >
                    <Flex justify="space-between" className="mb-2">
                      <Flex align="center" gap={8}>
                        <Tag>{t(`userMemory.${record.sub_problem}`)}</Tag>
                        <Tag color={record.status === 'resolved' ? 'success' : 'default'}>{t(`userMemory.${record.status}`)}</Tag>
                        <Tag color="dark">{record.strategy}</Tag>
                      </Flex>
                      <div className="rb:text-xs rb:text-[#9CA1A8] text-right">
                        {formatDateTime(record.created_at, 'MM-DD HH:mm')}
                      </div>
                    </Flex>
                    <Flex align="center" justify="space-between" className="rb:mt-3! rb:text-[12px]">
                      <div className="rb:text-[#5B6167]">
                        {record.summary_text}
                      </div>
                      {record.confidence}
                    </Flex>
                  </div>
                ))}
              </div>
            </div>
          </div>
        : <Empty size={80} className="rb:mb-4!" />
      }
      <Flex
        align="center"
        justify="center"
        className="rb:border rb:border-[#171719] rb:rounded-xl rb:h-11 rb:font-medium rb:leading-5 rb:cursor-pointer"
        onClick={handleViewAll}
      >
        {t('userMemory.completeReflectLogs')}
      </Flex>
    </RbCard>
  )
})
export default ReflectMemory
