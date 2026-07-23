/*
 * @Author: ZhaoYing
 * @Date: 2026-01-07 20:37:34
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 12:05:26
 */
import { useEffect, useState, useMemo, forwardRef, useImperativeHandle, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, App, Flex } from 'antd'
import type { ColumnsType } from 'antd/es/table';
import clsx from 'clsx'

import RbCard from '@/components/RbCard/Card'
import {
  getForgetMemoryQuota,
  getForgetMemoryTrend,
  getForgetMemoryCandidatesUrl,
  getForgetMemoryLogsUrl,
  refreshForgetMemoryCache,
} from '@/api/memory'
import type { ForgetData, ForgetTrendData, ForgetCandidate } from '../types'
import ActivationMetricsPieCard from '../components/ActivationMetricsPieCard'
import RecentTrendsLineCard from '../components/RecentTrendsLineCard'
import { formatDateTime } from '@/utils/format'
import StatusTag from '@/components/StatusTag'
import ForgetRefreshModal from '../components/ForgetRefreshModal';
import RbTable from '@/components/Table'
import Tag from '@/components/Tag'
import { formatQuotaStatus, StatusProgress, quotaColorClass } from '@/views/UserMemory/components/StatusProgress'

/** Maps node type keys to StatusTag colour presets for the pending-nodes table. */
const statusTagColors: Record<string, 'success' | 'purple' | 'default' | 'warning' | 'error' | 'lightBlue'> = {
  statement: 'success',
  entity: 'purple',
  summary: 'default',
  chunk: 'warning',
}

/** Colour palette for the quota water-level donut: safe / buffer / over-limit zones. */
const quotaZoneColors = ['#155EEF', '#02AFD5', '#FF5D34']

/** Imperative handle exposed by ForgetRefreshModal for triggering the refresh dialog. */
export interface ForgetRefreshModalRef {
  handleOpen: () => void;
}

const calcActiveCount = (breakdown: ForgetData['breakdown']) => {
  const keys: (keyof ForgetData['breakdown'])[] = ['Statement', 'Chunk', 'ExtractedEntity']
  return keys.reduce((acc, key) => acc + (breakdown[key] || 0), 0)
}
const calcPercent = (activeCount: number, limit: number) => {
  return (activeCount / limit) * 100
}
const ForgetDetail = forwardRef((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const { message } = App.useApp()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ForgetData>({} as ForgetData)
  const [trendLoading, setTrendLoading] = useState<boolean>(false)
  const [trendData, setTrendData] = useState<ForgetTrendData[]>([])
  const [activeTab, setActiveTab] = useState<'pending' | 'forgotten'>('pending')
  const forgetRefreshModalRef = useRef<ForgetRefreshModalRef>(null)

  /* Fetch stats and trend whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    updateData()
  }, [id])

  const updateData = (flag: boolean = false) => {
    getData(flag)
    getTrend()
  }

  /** Load the 7-day forgetting trend (daily forgetting counts) for the current user. */
  const getTrend = () => {
    if (!id) return
    setTrendLoading(true)
    getForgetMemoryTrend(id).then((res) => {
      setTrendData(((res as ForgetTrendData[]) || []).map(item => ({
        ...item,
        date: formatDateTime(item.date, 'MM-DD')
      })))
    })
    .finally(() => {
      setTrendLoading(false)
    })
  }

  /**
   * Load forgetting-engine statistics for the current user.
   * @param flag - When true, shows a success toast after loading (used after manual refresh).
   */
  const getData = (flag: boolean = false) => {
    if (!id) return
    setLoading(true)
    getForgetMemoryQuota(id).then((res) => {
      const response = res as ForgetData
      const { breakdown = {}, memory_limit = 0, target_count = 0 } = response || {}
      setData(response || {})

      const activeCount = calcActiveCount(breakdown as ForgetData['breakdown'])
      const activePercent = calcPercent(activeCount, memory_limit)
      const overLimitCount = activeCount - memory_limit
      const pendingForgetCount = Math.max(0, activeCount - target_count)
      const quotaStatus = formatQuotaStatus(activeCount, memory_limit)
      const quotaColorClassObj = quotaColorClass(quotaStatus)

      setData({
        ...data,
        activeCount,
        activePercent,
        overLimitCount,
        pendingForgetCount,
        quotaStatus,
        quotaColorClassObj,
      })

      setLoading(false)
      if (flag) {
        message.success(t('forgetDetail.refreshSuccess'))
      }
    })
    .finally(() => {
      setLoading(false)
    })
  }

  /**
   * Derive donut data for the quota water-level distribution from the raw quota data:
   * - safe zone:      target_count (capacity from 0 → target water level)
   * - buffer zone:    memory_limit - target_count (capacity from target → limit)
   * - over-limit zone: active total - memory_limit (the portion exceeding the quota limit)
   */
  const quotaChartData = useMemo(() => {
    const safe = data.target_count || 0
    const buffer = (data.memory_limit || 0) - (data.target_count || 0)
    const over = calcActiveCount(data.breakdown || {} as ForgetData['breakdown']) - (data.memory_limit || 0)
    return [
      { name: t('forgetDetail.safeZone'), value: Math.max(0, safe) },
      { name: t('forgetDetail.bufferZone'), value: Math.max(0, buffer) },
      { name: t('forgetDetail.overLimitZone'), value: Math.max(0, over) },
    ].filter(({ value }) => value > 0)
  }, [data, t])

  /** Open the forgetting-refresh confirmation modal. */
  const handleRefresh = () => {
    if (!id) return
    refreshForgetMemoryCache(id)
      .then(res => {
        if ((res as { refreshed: boolean }).refreshed) {
          updateData(true)
        }
      })
  }

  /* Expose handleRefresh to parent components via ref. */
  useImperativeHandle(ref, () => ({
    handleRefresh
  }));

  /** Column definitions shared by the pending-nodes table. */
  const columns: ColumnsType<ForgetCandidate> = [
    {
      title: t('forgetDetail.content_summary'),
      dataIndex: 'content',
      key: 'content',
      width: '500px',
    },
    {
      title: t('forgetDetail.node_type'),
      dataIndex: 'node_type',
      key: 'node_type',
      width: '180px',
      render: (node_type: string) => <StatusTag status={statusTagColors[node_type] || 'default'} text={t(`userMemory.${node_type}`)} />
    },
    {
      title: activeTab === 'pending' ? t('forgetDetail.createOrAccessTime') : t('userMemory.created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: '180px',
      render: (created_at, record) => activeTab === 'pending'
        ? formatDateTime(created_at, 'YYYY-MM-DD HH:mm')
        : record.delete_at ? formatDateTime(record.delete_at, 'YYYY-MM-DD HH:mm') : ''
    },
  ]

  /** Tab items for the bottom table card. */
  const tabItems: { key: 'pending' | 'forgotten'; label: string }[] = [
    { key: 'pending', label: t('forgetDetail.nextBatchForget') },
    { key: 'forgotten', label: t('forgetDetail.alreadyForgotten') },
  ]

  return (
    <Row gutter={[12, 12]}>
      <Col span={12}>
        <RbCard
          title={t('forgetDetail.quotaOverviewTitle')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:overflow-visible!"
          className="rb:h-full!"
        >
          <div className="rb:grid rb:grid-cols-3 rb:gap-3">
            {/* Active quota memory total */}
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.activeQuotaTotal')}</div>
              <div className="rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6 rb:mb-2">{data.activeCount}</div>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-2">{t('forgetDetail.activeQuotaTotalTip')}</div>
              <div className="rb:bg-white rb:rounded-lg rb:p-3 rb:grid rb:grid-cols-2 rb:gap-x-2 rb:gap-y-3">
                {['Statement', 'ExtractedEntity', 'MemorySummary', 'Chunk'].map((key, index) => (
                  <div key={index}>
                    <div className="rb:font-[MiSans-Bold] rb:font-bold rb:leading-4.75">{data?.breakdown?.[key as keyof typeof data.breakdown] ?? 0}</div>
                    <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mt-1">
                      {t(`userMemory.${key}`)}
                      {key === 'MemorySummary' &&
                        <Tag color="default" className="rb:text-[10px]! rb:leading-3!">{t('forgetDetail.not_contain')}</Tag>
                      }
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Quota usage rate */}
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.quotaUsageRate')}</div>
              <Flex align="baseline" gap={6}>
                <div className={clsx("rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6", data.quotaColorClassObj)}>
                  {data.activePercent?.toFixed(2)}%
                </div>
              </Flex>
              <div className="rb:mb-2">
                <StatusProgress
                  status={data.quotaStatus}
                  percent={data.activePercent}
                />
              </div>

              <div className="rb:bg-white rb:rounded-lg rb:p-3 rb:pt-2">
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.currentStatus')}</div>
                <div className={clsx('rb:font-medium rb:text-[18px]', data.quotaColorClassObj)}>{t(`userMemory.${data.quotaStatus}`)}</div>
                {data.quotaStatus !== 'normal' &&
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">{t(`forgetDetail.${data.quotaStatus}StatusTip`, { num: data.overLimitCount })}</div>
                }
              </div>
            </div>

            {/* Pending forget count */}
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.pendingForgetCount')}</div>
              <div className="rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6 rb:mb-1">{data.pendingForgetCount}</div>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-3.5">{t('forgetDetail.low_nodes')}</div>

              <div className="rb:bg-white rb:rounded-lg rb:mt-2 rb:p-3 rb:space-y-3">
                <div>
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.triggerLine')}</div>
                  <div className="rb:text-[16px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6">{data.memory_limit}</div>
                </div>
                <div>
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.targetWaterLevel')}</div>
                  <div className="rb:text-[16px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6">{data.target_count}</div>
                </div>
              </div>
            </div>
          </div>
        </RbCard>
      </Col>
      <Col span={6}>
        <ActivationMetricsPieCard
          title={t('forgetDetail.quotaLevelDistribution')}
          chartData={quotaChartData}
          colors={quotaZoneColors}
          centerValue={data.activeCount}
          centerLabel={t('forgetDetail.activeTotalCenter')}
          loading={loading}
        />
      </Col>
      <Col span={6}>
        <RecentTrendsLineCard
          chartData={trendData}
          loading={trendLoading}
        />
      </Col>
      <Col span={24}>
        <div
          className="rb:p-3 rb:bg-white rb:rounded-xl"
        >
          <Flex align="center" gap={8} className="rb:mb-3!">
            {tabItems.map(item => (
              <span
                key={item.key}
                onClick={() => setActiveTab(item.key)}
                className={clsx(
                  'rb:cursor-pointer rb:px-3 rb:py-1 rb:rounded-lg rb:text-[14px] rb:leading-5 rb:transition-colors',
                  activeTab === item.key
                    ? 'rb:bg-[#171719] rb:text-white rb:font-[MiSans-Bold] rb:font-bold'
                    : 'rb:bg-[#F6F6F6] rb:text-[#5B6167]'
                )}
              >
                {item.label}
              </span>
            ))}
          </Flex>
          {activeTab === 'pending' &&
            <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:px-1 rb:mb-3">
              {t('forgetDetail.forgetRuleNote')}
            </div>
          }
          {activeTab === 'pending' &&
            <RbTable<ForgetCandidate>
              key="pending"
              apiUrl={getForgetMemoryCandidatesUrl(id as string)}
              columns={columns}
              rowKey="node_id"
            />
          }
          {activeTab === 'forgotten' &&
            <RbTable<ForgetCandidate>
              key="forgotten"
              apiUrl={getForgetMemoryLogsUrl(id as string)}
              columns={columns}
              rowKey="node_id"
            />
          }
        </div>
      </Col>

      <ForgetRefreshModal
        ref={forgetRefreshModalRef}
        refresh={getData}
      />
    </Row>
  )
})
export default ForgetDetail
