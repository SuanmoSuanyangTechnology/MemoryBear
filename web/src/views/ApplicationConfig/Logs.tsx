/*
 * @Author: ZhaoYing 
 * @Date: 2026-03-24 15:41:20 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-26 14:06:17
 */
import { type FC, useRef, useState, useMemo, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { Flex, Button, Form, Switch, Space, Divider, App, Dropdown, type SegmentedProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import { getAppLogsUrl, getAnnotationsListUrl, getAnnotationsSettings, updateAnnotationsSettings, deleteAnnotations, deleteAllAnnotations, exportAnnotation, } from '@/api/application';
import Table, { type TableRef } from '@/components/Table'
import { formatDateTime } from '@/utils/format';
import type { LogItem, LogDetailModalRef, AnnotationItem, AnnotationSettingForm, AnnotationSettingModalRef, AnnotationFormModalRef, HitHistoryDetailRef } from './types'
import LogDetailModal from './components/LogDetailModal'
import AnnotationsSettingsModal from './components/AnnotationsSettingsModal'
import AnnotationFormModal from './components/AnnotationFormModal'
import BatchImportModal from './components/BatchImportModal'
import SearchInput from '@/components/SearchInput'
import type { Application } from '@/views/ApplicationManagement/types'
import PageTabs from '@/components/PageTabs'
import HitHistoryDetail from './components/HitHistoryDetail'

const tabKeys = ['logs', 'annotations']
const Logs: FC<{ application: Application }> = ({ application }) => {
  const { t } = useTranslation();
  const { id } = useParams();
  const { message, modal } = App.useApp();
  const logDetailRef = useRef<LogDetailModalRef>(null);
  const batchImportRef = useRef<{ handleOpen: () => void; handleClose: () => void }>(null);
  const [form] = Form.useForm();
  const values = Form.useWatch([], form);

  const handleViewDetail = (item: LogItem) => {
    logDetailRef.current?.handleOpen(item);
  }

  /** Table column configuration */
  const columns: ColumnsType<LogItem> = [
    {
      title: t('application.logTitle'),
      dataIndex: 'title',
      key: 'title',
      className: 'rb:text-[#212332]'
    },
    {
      title: t('application.created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (createdAt: string) => formatDateTime(createdAt, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('common.updated_at'),
      dataIndex: 'updated_at',
      key: 'updated_at',
      render: (updatedAt: string) => updatedAt ? formatDateTime(updatedAt, 'YYYY-MM-DD HH:mm:ss') : '-',
    },
    {
      title: t('common.operation'),
      key: 'action',
      render: (_, record) => (
        <Flex wrap>
          <Button
            type="link"
            onClick={() => handleViewDetail(record as LogItem)}
          >
            {t('common.view')}
          </Button>
        </Flex>
      ),
    },
  ];
  const [activeTab, setActiveTab] = useState('logs');
  const formatTabItems = useMemo(() => {
    return tabKeys.map(value => ({
      value,
      label: t(`application.${value}`),
    }))
  }, [tabKeys, t])
  /** Handle tab change */
  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value as string);
    form.resetFields()
  }

  const [annotationsSettings, setAnnotationsSettings] = useState<AnnotationSettingForm | null>(null);
  const annotationsTableRef = useRef<TableRef>(null);
  const annotationsSettingsRef = useRef<AnnotationSettingModalRef>(null);
  const annotationFormRef = useRef<AnnotationFormModalRef>(null);
  const hitHistoryDetailRef = useRef<HitHistoryDetailRef>(null);
  
  useEffect(() => {
    if (activeTab === 'annotations') {
      getSetting()
    }
  }, [activeTab])
  const getSetting = () => {
    getAnnotationsSettings(id as string).then(res => {
      setAnnotationsSettings(res as AnnotationSettingForm | null)
    })
  }
  const updateSetting = () => {
    updateAnnotationsSettings(id as string, { enabled: 0 })
      .then(() => {
        message.success(t('common.operateSuccess'))
        getSetting()
      })
  }
  const refreshAnnotations = () => {
    annotationsTableRef.current?.loadData()
  }
  const handleDelete = (vo: AnnotationItem) => {
    if (!id) return
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: vo.question }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteAnnotations(id, vo.id)
          .then(() => {
            refreshAnnotations();
            message.success(t('common.deleteSuccess'))
          })
      }
    })
  }
  const handleDeleteAll = () => {
    if (!id) return
    modal.confirm({
      title: t('application.confirmDeleteAllAnnotation'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteAllAnnotations(id)
          .then(() => {
            refreshAnnotations();
            message.success(t('common.deleteSuccess'))
          })
      }
    })
  }
  const handleExport = (type: 'csv' | 'json') => {
    if (!id) return
    exportAnnotation(id, type)
  }
  const annotationsColumns: ColumnsType<AnnotationItem> = [
    {
      title: t('application.question'),
      dataIndex: 'question',
      key: 'question',
    },
    {
      title: t('application.answer'),
      dataIndex: 'answer',
      key: 'answer',
      minWidth: 200,
    },
    {
      title: t('application.hit_count'),
      dataIndex: 'hit_count',
      key: 'hit_count',
      width: 100,
      render: (hitCount: number) => hitCount,
    },
    {
      title: t('application.created_at'),
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (createdAt: string) => formatDateTime(createdAt, 'YYYY-MM-DD HH:mm:ss'),
    },
    {
      title: t('common.operation'),
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space size="large">
          <Button
            type="link"
            onClick={() => hitHistoryDetailRef.current?.handleOpen(id as string, record.id)}
          >
            {t('application.hitHistory')}
          </Button>
          <Button
            type="link"
            onClick={() => annotationFormRef.current?.handleOpen(record)}
          >
            {t('common.edit')}
          </Button>
          <Button
            type="link"
            danger={true}
            onClick={() => handleDelete(record)}
          >
            {t('common.delete')}
          </Button>
        </Space>
      ),
    },
  ]
  return (
    <div className="rb:bg-white rb:rounded-lg rb:pt-3 rb:px-3">
      <Flex justify="space-between" className="rb:mb-3!">
        <PageTabs
          value={activeTab}
          options={formatTabItems}
          onChange={handleChangeTab}
        />
        <Form form={form}>
          <Space size={8}>
            {activeTab === 'logs' &&
              <Form.Item name="keyword" noStyle>
                <SearchInput
                  placeholder={t(`application.${activeTab}SearchPlaceholder`)}
                  variant="outlined"
                />
              </Form.Item>
            }
            {activeTab === 'annotations' && <>
              <Form.Item name="search" noStyle>
                <SearchInput
                  placeholder={t(`application.${activeTab}SearchPlaceholder`)}
                  variant="outlined"
                />
              </Form.Item>

              <Flex align="center" gap={4} className="rb:px-3.75! rb-border rb:h-8 rb:rounded-lg">
                {t('application.annotationsQA')}
                <Switch 
                  value={annotationsSettings?.enabled === 1} 
                  onChange={(checked) => {
                    if (checked) {
                      // Show settings modal when enabling
                      annotationsSettingsRef.current?.handleOpen();
                    } else {
                      updateSetting()
                    }
                  }}
                />
                {annotationsSettings?.enabled === 1 && <>
                  <Divider type="vertical" />
                  <div
                    className="rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/set.svg')]"
                    onClick={() => annotationsSettingsRef.current?.handleOpen()}
                  ></div>
                </>}
              </Flex>
              <Button type="primary" onClick={() => annotationFormRef.current?.handleOpen()}>
                + {t('application.addAnnotations')}
              </Button>
              <Dropdown
                menu={{ items: [
                  {
                    key: 'batchImport',
                    icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/application/import.svg')]" />,
                    label: t('application.batchImport'),
                    onClick: () => batchImportRef.current?.handleOpen(),
                  },
                  {
                    key: 'batchExport',
                    icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/application/export.svg')]" />,
                    label: t('application.batchExport'),
                    children: [
                      {
                        key: 'CSV',
                        label: 'CSV',
                        onClick: () => handleExport('csv'),
                      },
                      {
                        key: 'JSON',
                        label: 'JSON',
                        onClick: () => handleExport('json'),
                      },
                    ]
                  },
                  {
                    key: 'delete',
                    danger: true,
                    icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                    label: t('application.deleteAllAnnotation'),
                    onClick: handleDeleteAll,
                  },
                ] }}
              >
                <Button
                  icon={<div
                    className="rb:cursor-pointer rb:size-5.5 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"
                  />}
                ></Button>
              </Dropdown>
            </>}
          </Space>
        </Form>
      </Flex>
      {activeTab === 'logs' && <>
        <Table<LogItem>
          apiUrl={getAppLogsUrl(id || '')}
          apiParams={{
            is_draft: false,
            ...(values ?? {})
          }}
          columns={columns}
          rowKey="id"
          isScroll={true}
          scrollY="calc(100vh - 242px)"
        />
        <LogDetailModal ref={logDetailRef} source={application?.type} />
      </>}
      {activeTab === 'annotations' && <>
        <Table<AnnotationItem>
          ref={annotationsTableRef}
          apiUrl={getAnnotationsListUrl(id || '')}
          apiParams={{
            ...(values ?? {})
          }}
          columns={annotationsColumns}
          rowKey="id"
          isScroll={true}
          scrollY="calc(100vh - 242px)"
        />
        <AnnotationsSettingsModal
          ref={annotationsSettingsRef}
          refresh={getSetting}
        />
        <AnnotationFormModal
          ref={annotationFormRef}
          refresh={refreshAnnotations}
        />
        <BatchImportModal
          ref={batchImportRef}
          refresh={refreshAnnotations}
        />
        <HitHistoryDetail
          ref={hitHistoryDetailRef}
        />
      </>}
    </div>
  );
}
export default Logs;