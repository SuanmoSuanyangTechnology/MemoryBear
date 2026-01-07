import { type FC, useEffect, useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { Row, Col, Progress } from 'antd'
import RbCard from '@/components/RbCard/Card'
import {
  getForgetStats,
} from '@/api/memory'
import type { ForgetData } from '../types'
import ActivationMetricsPieCard from '../components/ActivationMetricsPieCard'
import RecentTrendsLineCard from '../components/RecentTrendsLineCard'
import Table from '@/components/Table'
import { formatDateTime } from '@/utils/format'
import StatusTag from '@/components/StatusTag'

const statusTagColors: Record<string, 'success' | 'purple' | 'default' | 'warning' | 'error' | 'lightBlue'> = {
  statement: 'success',
  entity: 'purple',
  summary: 'default',
  chunk: 'warning',
}

const ForgetOverview: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const [loading, setLoading] = useState<boolean>(false)
  const [data, setData] = useState<ForgetData>({} as ForgetData)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id])
  
  // 记忆洞察
  const getData = () => {
    if (!id) return
    setLoading(true)
    getForgetStats(id).then((res) => {
      const response = res as ForgetData
      setData(response)
      setLoading(false)
    })
    .finally(() => {
      setLoading(false)
    })
  }
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

  const seriesList = useMemo(() => {
    const { recent_trends = [] } = data
    if (!recent_trends || recent_trends.length === 0) return { chartData: [], seriesList: [] }
    
    return {
      chartData: recent_trends,
      seriesList: ['merged_count', 'average_activation']
    }
  }, [data.recent_trends])

  return (
    <div className="rb:h-full rb:max-w-266 rb:mx-auto">
      <div className="rb:text-[#5B6167] rb:leading-5 rb:mt-3">{t('forgetDetail.title')}</div>
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-4 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('forgetDetail.overviewTitle')}</div>
      <Row gutter={16}>
        <Col span={8}>
          <RbCard>
            <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-2">{t('forgetDetail.totalMemory')}</div>
            <div className="rb:text-[26px] rb:font-bold rb:leading-8.5">{data?.activation_metrics?.total_nodes ?? 0}</div>
            <div className="rb:mt-4 rb:grid rb:grid-cols-2 rb:gap-x-2 rb:gap-y-5 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-3 rb:py-2">
              {['statement_count', 'entity_count', 'summary_count', 'chunk_count'].map((key, index) => (
                <div key={index}>
                  <div className="rb:text-[16px] rb:font-bold rb:leading-5.5">{data?.node_distribution?.[key as keyof typeof data.node_distribution] ?? 0}</div>
                  <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mt-1">{t(`forgetDetail.${key}`)}</div>
                </div>
              ))}
            </div>
          </RbCard>
        </Col>
        <Col span={8}>
          <RbCard>
            <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-2">{t('forgetDetail.MemoryHealth')}</div>
            <div className="rb:text-[26px] rb:font-bold rb:leading-8.5">{data?.activation_metrics?.average_activation_value ?? 0}</div>
            <Progress className="rb:mt-px" showInfo={false} percent={data?.activation_metrics?.average_activation_value ?? 0} />
            <div className="rb:mt-4 rb:bg-[#F6F8FC] rb:border rb:border-[#DFE4ED] rb:rounded-lg rb:px-3 rb:py-2">
              <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular">{t('forgetDetail.healthStatus')}</div>
              <div className="rb:text-[20px] rb:font-semibold rb:leading-7">{data?.activation_metrics?.average_activation_value > data.activation_metrics?.forgetting_threshold ? t('forgetDetail.healthy') : t('forgetDetail.unhealthy')}</div>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:mt-2">
                {t('forgetDetail.average')}<br />
                {t('forgetDetail.threshold')}{data.activation_metrics?.forgetting_threshold ?? 0}
              </div>
            </div>
          </RbCard>
        </Col>
        <Col span={8}>
          <RbCard>
            <div className="rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:mb-2">{t('forgetDetail.riskOfForgetting')}</div>
            <div className="rb:text-[26px] rb:font-bold rb:leading-8.5">{data.activation_metrics?.low_activation_nodes ?? 0}</div>
            <div className="rb:mb-31.5 rb:text-[#A8A9AA] rb:text-[12px] rb:leading-4 rb:font-regular rb:mt-1">{t('forgetDetail.low_nodes')}</div>
          </RbCard>
        </Col>
      </Row>

      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-4 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('forgetDetail.memoryHealthVisualization')}</div>
      <Row gutter={16}>
        <Col span={12}>
          <ActivationMetricsPieCard chartData={chartData} loading={loading} />
        </Col>
        <Col span={12}>
          <RecentTrendsLineCard chartData={seriesList.chartData} seriesList={seriesList.seriesList} loading={loading} />
        </Col>
      </Row>
      <div className="rb:bg-[rgba(21,94,239,0.12)] rb:px-4 rb:py-2.5 rb:font-medium rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">{t('forgetDetail.pending_nodes')}</div>
      <Table
        rowKey='node_id'
        initialData={data.pending_nodes ?? []}
        columns={[
          {
            title: t('forgetDetail.content_summary'),
            dataIndex: 'content_summary',
            key: 'content_summary',
            width: 340,
            render: (content_summary) => <div className="rb:wrap-break-word rb:line-clamp-2">{content_summary}</div>
          },
          {
            title: t('forgetDetail.node_type'),
            dataIndex: 'node_type',
            key: 'node_type',
            render: (node_type: string) => {
              return <StatusTag status={statusTagColors[node_type] || 'default'} text={node_type} />}
          },
          {
            title: t('forgetDetail.last_access_time'),
            dataIndex: 'last_access_time',
            key: 'last_access_time',
            render: (last_access_time) => formatDateTime(last_access_time, 'YYYY-MM-DD HH:mm')
          },
          {
            title: t('forgetDetail.activation_value'),
            dataIndex: 'activation_value',
            key: 'activation_value',
          },
        ]}
        pagination={false}
      />
    </div>
  )
}
export default ForgetOverview