/*
 * @Author: ZhaoYing 
 * @Date: 2026-01-07 20:37:34 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 12:05:26
 */
import { useEffect, useState, useMemo, forwardRef, useImperativeHandle, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Progress, App, Table } from 'antd'

import RbCard from '@/components/RbCard/Card'
import {
  getForgetStats,
  getForgetPendingNodesUrl,
} from '@/api/memory'
import type { ForgetData } from '../types'
import ActivationMetricsPieCard from '../components/ActivationMetricsPieCard'
import RecentTrendsLineCard from '../components/RecentTrendsLineCard'
import { formatDateTime } from '@/utils/format'
import StatusTag from '@/components/StatusTag'
import ForgetRefreshModal from '../components/ForgetRefreshModal';
import RbTable from '@/components/Table'

/** Maps node type keys to StatusTag colour presets for the pending-nodes table. */
const statusTagColors: Record<string, 'success' | 'purple' | 'default' | 'warning' | 'error' | 'lightBlue'> = {
  statement: 'success',
  entity: 'purple',
  summary: 'default',
  chunk: 'warning',
}

/** Imperative handle exposed by ForgetRefreshModal for triggering the refresh dialog. */
export interface ForgetRefreshModalRef {
  handleOpen: () => void;
}

/**
 * ForgetDetail – Dashboard for the forgetting engine of a single user.
 *
 * Layout (top → bottom):
 * 1. Overview row (3 metric cards): total memory nodes, memory health
 *    (average activation vs threshold), and forgetting-risk count.
 * 2. Pie chart (activation distribution) + line chart (7-day trends).
 * 3. Pending-nodes table listing low-activation memories at risk of being forgotten.
 *
 * The parent can trigger a manual forgetting refresh via the imperative
 * `handleRefresh` method exposed through `forwardRef`.
 *
 * Route param `id` is the end-user ID.
 */
const ForgetDetail = forwardRef((_props, ref) => {
  const { t } = useTranslation()
  const { id } = useParams()
  const { message } = App.useApp()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ForgetData>({} as ForgetData)
  const forgetRefreshModalRef = useRef<ForgetRefreshModalRef>(null)

  /* Fetch stats whenever the route user ID changes. */
  useEffect(() => {
    if (!id) return
    getData()
  }, [id])

  /**
   * Load forgetting-engine statistics for the current user.
   * @param flag - When true, shows a success toast after loading (used after manual refresh).
   */
  const getData = (flag: boolean = false) => {
    if (!id) return
    setLoading(true)
    getForgetStats(id).then((res) => {
      const response = res as ForgetData
      setData(response)
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
   * Derive pie-chart data from activation metrics.
   * Splits total nodes into three zones: healthy, observation (no activation), and forgetting (low activation).
   */
  const chartData = useMemo(() => {
    const { activation_metrics } = data
    if (!activation_metrics) return []
    
    let health_nodes = (activation_metrics.total_nodes || 0) - (activation_metrics.low_activation_nodes || 0) - (activation_metrics.nodes_without_activation || 0)

    return [
      { name: t('forgetDetail.health_nodes'), value: health_nodes },
      { name: t('forgetDetail.nodes_without_activation'), value: activation_metrics.nodes_without_activation || 0 },
      { name: t('forgetDetail.low_activation_nodes'), value: activation_metrics.low_activation_nodes || 0 },
    ]

  }, [data.activation_metrics, t])

  /**
   * Prepare line-chart series from the recent 7-day trend data.
   * Returns merged_count (daily merged nodes) and average_activation as two series.
   */
  const seriesList = useMemo(() => {
    const { recent_trends = [] } = data
    if (!recent_trends || recent_trends.length === 0) return { chartData: [], seriesList: [] }
    
    return {
      chartData: recent_trends,
      seriesList: ['merged_count', 'average_activation']
    }
  }, [data.recent_trends])

  /** Open the forgetting-refresh confirmation modal. */
  const handleRefresh = () => {
    forgetRefreshModalRef.current?.handleOpen()
  }

  /* Expose handleRefresh to parent components via ref. */
  useImperativeHandle(ref, () => ({
    handleRefresh
  }));

  return (
    <Row gutter={[12, 12]}>
      <Col span={12}>
        <RbCard
          title={t('forgetDetail.overviewTitle')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:pt-0! rb:overflow-visible!"
        >
          <div className="rb:grid rb:grid-cols-3 rb:gap-3">
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.totalMemory')}</div>
              <div className="rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6 rb:mb-2">{data?.activation_metrics?.total_nodes ?? 0}</div>
              <div className="rb:bg-white rb:rounded-lg rb:p-3 rb:grid rb:grid-cols-2 rb:gap-x-2 rb:gap-y-6">
                {['statement_count', 'entity_count', 'summary_count', 'chunk_count'].map((key, index) => (
                  <div key={index}>
                    <div className="rb:font-[MiSans-Bold] rb:font-bold rb:leading-4.75">{data?.node_distribution?.[key as keyof typeof data.node_distribution] ?? 0}</div>
                    <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mt-1">{t(`forgetDetail.${key}`)}</div>
                  </div>
                ))}
              </div>
            </div>
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.MemoryHealth')}</div>
              <div className="rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6">{data?.activation_metrics?.average_activation_value ?? 0}</div>
              <div className="rb:-mt-1 rb:mb-2">
                <Progress showInfo={false} percent={data?.activation_metrics?.average_activation_value ?? 0} />
              </div>

              <div className="rb:bg-white rb:rounded-lg rb:p-3 rb:pt-2">
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.healthStatus')}</div>
                <div className="rb:text-[16px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6 rb:mb-3">
                  {data?.activation_metrics?.average_activation_value > data.activation_metrics?.forgetting_threshold
                    ? t('forgetDetail.healthy')
                    : t('forgetDetail.unhealthy')
                  }
                </div>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5">
                  {t('forgetDetail.average')}<br />
                  {t('forgetDetail.threshold')}{data.activation_metrics?.forgetting_threshold ?? 0}
                </div>
              </div>
            </div>
            <div className="rb:bg-[#F6F6F6] rb:rounded-xl rb:p-2 rb:pt-3">
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mb-1">{t('forgetDetail.riskOfForgetting')}</div>
              <div className="rb:text-[18px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-6">{data.activation_metrics?.low_activation_nodes ?? 0}</div>
              <div className="rb:text-[#5B6167] rb:text-[10px] rb:leading-3.5">{t('forgetDetail.low_nodes')}</div>

              <div className="rb:bg-white rb:rounded-lg rb:mt-2 rb:h-29.5">
                <div className="rb:h-29.5 rb:w-full rb:bg-contain rb:bg-no-repeat rb:bg-center rb:bg-[url('@/assets/images/userMemory/forget.png')]"></div>
              </div>
            </div>
          </div>
        </RbCard>
      </Col>
      <Col span={6}>
        <ActivationMetricsPieCard chartData={chartData} loading={loading} />
      </Col>
      <Col span={6}>
        <RecentTrendsLineCard chartData={seriesList.chartData} seriesList={seriesList.seriesList} loading={loading} />
      </Col>
      <Col span={24}>
        <RbCard
          title={t('forgetDetail.pending_nodes')}
          headerType="borderless"
          headerClassName="rb:min-h-[54px]! rb:font-[MiSans-Bold] rb:font-bold"
          bodyClassName="rb:p-3! rb:py-0! rb:h-[calc(100%-54px)]"
          className="rb:h-full!"
        >
          <RbTable
            apiUrl={getForgetPendingNodesUrl}
            apiParams={{ end_user_id: id }}
            rowKey='node_id'
            dataSource={data.pending_nodes ?? []}
            columns={[
              {
                title: t('forgetDetail.content_summary'),
                dataIndex: 'content_summary',
                key: 'content_summary',
                render: (content_summary) => <div className="rb:wrap-break-word rb:line-clamp-2">{content_summary}</div>
              },
              {
                title: t('forgetDetail.node_type'),
                dataIndex: 'node_type',
                key: 'node_type',
                width: '20%',
                render: (node_type: string) => {
                  return <StatusTag status={statusTagColors[node_type] || 'default'} text={node_type} />
                }
              },
              {
                title: t('forgetDetail.last_access_time'),
                dataIndex: 'last_access_time',
                key: 'last_access_time',
                width: '20%',
                render: (last_access_time) => <span className="rb:text-[#5B6167]">{formatDateTime(last_access_time, 'YYYY-MM-DD HH:mm')}</span>
              },
              {
                title: t('forgetDetail.activation_value'),
                dataIndex: 'activation_value',
                key: 'activation_value',
                width: '20%',
                render: (activation_value) => <span className="rb:text-[#5B6167]">{activation_value}</span>
              },
            ]}
            className="table-header-has-bg"
          />
        </RbCard>
      </Col>

      <ForgetRefreshModal
        ref={forgetRefreshModalRef}
        refresh={getData}
      />
      {/* <div className="rb:h-full rb:max-w-266 rb:mx-auto">
        <div className="rb:text-[#5B6167] rb:leading-5 rb:mt-3">{t('forgetDetail.title')}</div>

      </div> */}
    </Row>
  )
})
export default ForgetDetail