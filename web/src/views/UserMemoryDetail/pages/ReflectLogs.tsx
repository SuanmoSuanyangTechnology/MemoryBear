/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-26 15:39:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-26 15:44:10
 */
import { useState, useEffect, useRef, type FC } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams, useNavigate, useParams } from 'react-router-dom'
import { Form, Button, Select, Flex } from 'antd'
import type { ColumnsType } from 'antd/es/table';

import Table from '@/components/Table'
import { reflectLogListUrl, getReflectLogStats } from '@/api/memory'
import PageHeader from '@/components/Layout/PageHeader'
import StatusTag, { type StatusTagProps } from '@/components/StatusTag';
import ReflectLogDetail, { type ReflectLogDetailRef } from '../components/ReflectLogDetail'
import type { ReflectLog, Data } from '../components/ReflectMemory'
import { formatDateTime } from '@/utils/format'
import Tag from '@/components/Tag'

const subTypeObj: Record<string, boolean> = {
  entity_dedup: true,
  description_merge: true,
  unresolved_entity: true,
  expired_detection: false,
  factual_contradiction: false,
  ontology_verification: false,
}

const queryInitialValues = {
  sub_problem: null,
  status: null,
  trigger_type: null,
}
const ReflectLogs: FC = () => {
  const { t } = useTranslation()
  const { id } = useParams()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [data, setData] = useState<Data | null>(null)
  const detailRef = useRef<ReflectLogDetailRef>(null)
  const [form] = Form.useForm<{
    sub_problem?: ReflectLog['sub_problem'];
    status?: ReflectLog['status'];
    trigger_type?: ReflectLog['trigger_type'];
  }>();
  const values = Form.useWatch([], form)

  useEffect(() => {
    if (!id) return
    getData()
  }, [id, searchParams])

  /** Fetch reflection logs data */
  const getData = () => {
    if (!id) return
    getReflectLogStats(id).then((res) => {
      setData((res as Data) || null)
    })
  }

  /** Get status color */
  const getStatusColor = (status: string) => {
    const colorMap: Record<string, StatusTagProps['status']> = {
      resolved: 'success',
      recorded: 'warning',
      pending: 'default',
    }
    return colorMap[status] || 'default'
  }

  const handleViewDetail = (record: ReflectLog) => {
    detailRef.current?.handleOpen(record)
  }

  /** Table columns */
  const columns: ColumnsType<ReflectLog> = [
    {
      title: t('userMemory.logId'),
      dataIndex: 'id',
      key: 'id',
      render: (id) => <>#{id.split('-')[0]}</>
    },
    {
      title: t('userMemory.time'),
      dataIndex: 'created_at',
      key: 'created_at',
      className: 'rb:text-[#5B6167]',
      render: (text: number) => formatDateTime(text, 'MM-DD HH:mm'),
    },
    {
      title: t('userMemory.sub_question'),
      dataIndex: 'sub_problem',
      key: 'sub_problem',
      className: 'rb:text-[#171719] rb:font-medium',
      render: (text) => <Tag>{t(`userMemory.${text}`)}</Tag>
    },
    {
      title: t('userMemory.summary_text'),
      key: 'summary_text',
      dataIndex: 'summary_text',
      width: 350,
    },
    {
      title: t('userMemory.trigger'),
      dataIndex: 'trigger_type',
      key: 'trigger_type',
      render: (text) => t(`userMemory.${text}`),
    },
    {
      title: t('userMemory.baseline'),
      dataIndex: 'baseline',
      key: 'baseline',
      className: 'rb:text-[#5B6167]',
    },
    {
      title: t('userMemory.strategy'),
      dataIndex: 'strategy',
      key: 'strategy',
      render: (text) => <Tag color="default">{text}</Tag>
    },
    {
      title: t('userMemory.confidence'),
      dataIndex: 'confidence',
      key: 'confidence',
      className: 'rb:text-[#5B6167]',
      render: (text: number) => text || '-',
    },
    {
      title: t('userMemory.status'),
      dataIndex: 'status',
      key: 'status',
      render: (text: string) => (
        <StatusTag status={getStatusColor(text)} text={t(`userMemory.${text}`)} />
      ),
    },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      render: (_, record) => (
        <Button
          type="link"
          onClick={() => handleViewDetail(record)}
        >
          {t('userMemory.detail')}
        </Button>
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title={t('userMemory.reflectMemory')}
        extra={
          <Form
            form={form}
            initialValues={queryInitialValues}
          >
            <Flex gap={12} align="center">
              <Form.Item name="sub_problem" noStyle>
                <Select
                  options={[
                    { value: null, label: t('userMemory.allSubTypes') },
                    ...Object.keys(subTypeObj).map(type => ({
                      value: type,
                      label: <>{t(`userMemory.${type}`)} {!subTypeObj[type] && `(${t('userMemory.to_be_implemented')})`}</>,
                      disabled: !subTypeObj[type],
                    }))
                  ]}
                  popupMatchSelectWidth={false}
                  className="rb:w-30!"
                />
              </Form.Item>
              <Form.Item name="status" noStyle>
                <Select
                  options={[
                    { value: null, label: t('userMemory.allStatus') },
                    { value: 'resolved', label: t('userMemory.resolved') },
                    { value: 'recorded', label: t('userMemory.recorded') },
                  ]}
                  className="rb:w-30!"
                />
              </Form.Item>
              <Form.Item name="trigger_type" noStyle>
                <Select
                  options={[
                    { value: null, label: t('userMemory.allTrigger') },
                    { value: 'scheduled', label: t('userMemory.scheduled') },
                    { value: 'conversation', label: t('userMemory.conversation') },
                    { value: 'manual', label: t('userMemory.manual') },
                  ]}
                  className="rb:w-30!"
                />
              </Form.Item>
              <Button
                className="rb:px-2! rb:gap-0.5!"
                icon={<div className="rb:bg-[url('@/assets/images/workflow/return.svg')] rb:size-4 rb:bg-cover"></div>}
                onClick={() => navigate(-1)}
              >
                {t('common.return')}
              </Button>
            </Flex>
          </Form>
        }
      />
      <div className="rb:p-3">
        {/* Statistics Cards */}
        <div className="rb:grid rb:grid-cols-5 rb:gap-3 rb:mb-4">
          <div className="rb:bg-white rb:rounded-xl rb:p-4">
            <div className="rb:text-3xl rb:font-bold rb:text-[#212332]">{data?.total || 0}</div>
            <div className="rb:text-sm rb:text-[#5B6167] mt-1">{t('userMemory.totalRecords')}</div>
          </div>
          <div className="rb:bg-white rb:rounded-xl rb:p-4">
            <div className="rb:text-3xl rb:font-bold rb:text-[#212332]">{data?.sub_problem?.entity_dedup || 0}</div>
            <div className="rb:text-sm rb:text-[#5B6167] mt-1">{t('userMemory.entity_dedup')}</div>
          </div>
          <div className="rb:bg-white rb:rounded-xl rb:p-4">
            <div className="rb:text-3xl rb:font-bold rb:text-[#212332]">{data?.sub_problem?.description_merge || 0}</div>
            <div className="rb:text-sm rb:text-[#5B6167] mt-1">{t('userMemory.description_merge')}</div>
          </div>
          <div className="rb:bg-white rb:rounded-xl rb:p-4">
            <div className="rb:text-3xl rb:font-bold rb:text-[#212332]">{data?.status?.resolved || 0}</div>
            <div className="rb:text-sm rb:text-[#5B6167] mt-1">{t('userMemory.resolved')}</div>
          </div>
          <div className="rb:bg-white rb:rounded-xl rb:p-4">
            <div className="rb:text-3xl rb:font-bold rb:text-[#212332]">{((data?.resolve_rate || 0) * 100).toFixed(0)}%</div>
            <div className="rb:text-sm rb:text-[#5B6167] mt-1">{t('userMemory.resolve_rate')}</div>
          </div>
        </div>

        {/* Table */}
        <div className="rb:bg-white rb:rounded-xl rb:overflow-hidden">
          <Table<ReflectLog>
            apiUrl={reflectLogListUrl}
            apiParams={{
              ...values,
              end_user_id: id,
            }}
            columns={columns}
            rowKey="id"
            isScroll={true}
            scrollY="calc(100vh - 289px)"
          />
        </div>
      </div>
      <ReflectLogDetail ref={detailRef} />
    </>
  )
}
export default ReflectLogs
