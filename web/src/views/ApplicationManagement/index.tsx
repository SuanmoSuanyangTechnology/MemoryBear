/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-18 10:50:33
 */
/**
 * Application Management Page
 * Displays and manages all applications in the workspace
 * Supports creating, editing, and deleting applications
 */

import React, { useState, useRef, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, App, Select, Space, Dropdown, type SegmentedProps, Flex, Form } from 'antd';
import clsx from 'clsx';
import { DeleteOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom'

import ApplicationModal, { types } from './components/ApplicationModal';
import type { Application, ApplicationModalRef, Query, UploadWorkflowModalRef } from './types';
import SearchInput from '@/components/SearchInput'
import RbCard from '@/components/RbCard/Card'
import { getApplicationListUrl, deleteApplication, copyApplication } from '@/api/application'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import UploadWorkflowModal from './components/UploadWorkflowModal'
import UploadModal from './components/UploadModal'
import PageTabs from '@/components/PageTabs'
import MySharing from './MySharing'


const tabKeys = ['apps', 'sharing', 'myShare']
/**
 * Application management main component
 */
const ApplicationManagement: React.FC = () => {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [searchParams] = useSearchParams()
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const scrollListRef = useRef<PageScrollListRef>(null)
  const uploadWorkflowModalRef = useRef<UploadWorkflowModalRef>(null);
  const uploadModalRef = useRef<UploadWorkflowModalRef>(null);
  const [form] = Form.useForm<Query>()
  const query = Form.useWatch([], form)
  const [activeTab, setActiveTab] = useState('apps');

  useEffect(() => {
    // Convert URLSearchParams to a plain object for easier access
    const data = Object.fromEntries(searchParams)
    const { type } = data
    form.setFieldValue('type', type || undefined)
  }, [searchParams])

  /** Refresh application list */
  const refresh = () => {
    scrollListRef.current?.refresh();
  }
  
  /** Open create application modal */
  const handleCreate = () => {
    applicationModalRef.current?.handleOpen();
  }
  /** Navigate to application configuration page */
  const handleEdit = (item: Application) => {
    let url = `/#/application/config/${item.id}`
    if (item.is_shared) {
      url += `/${activeTab}`
    }
    window.open(url);
  }
  /** Delete application with confirmation */
  const handleDelete = (item: Application) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
      deleteApplication(item.id)
        .then(() => {
          refresh();
        })
        .catch(() => {
          console.error('Failed to delete application');
        });
      }
    })
  }

  const handleImport = () => {
    uploadWorkflowModalRef.current?.handleOpen()
  }
  const handleClick = ({ key }: { key: string } ) => {
    switch (key) {
      case 'thirdParty':
        handleImport()
        break;
      case 'import':
        uploadModalRef.current?.handleOpen()
    }
  }
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
  const handleCopy = (item: Application) => {
    modal.confirm({
      title: t('application.confirmCopyDesc', { app: item.name }),
      okText: t('common.copy'),
      cancelText: t('common.cancel'),
      onOk: () => {
        copyApplication(item.id)
          .then(() => {
            setActiveTab('apps')
          })
      }
    });
  }
  return (
    <>
      <Flex justify="space-between" className="rb:mb-3!">
        <PageTabs
          value={activeTab}
          options={formatTabItems}
          onChange={handleChangeTab}
        />

        <Form
          form={form}
          initialValues={{}}
        >
          {activeTab !== 'myShare' &&
            <Space size={8}>
              <Form.Item name="type" noStyle>
                <Select
                  placeholder={t('application.applicationType')}
                  options={types.map((type) => ({
                    value: type,
                    label: t(`application.${type}`),
                  }))}
                  allowClear
                  className="rb:w-30"
                />
              </Form.Item>
              <Form.Item name="search" noStyle>
                <SearchInput
                  placeholder={t('application.searchPlaceholder')}
                  className="rb:w-75!"
                />
              </Form.Item>
              
              {activeTab === 'apps' && <>
                <Dropdown
                  menu={{
                    items: [
                      { key: 'thirdParty', label: t('application.importWorkflow') },
                      { key: 'import', label: t('application.import') },
                    ], onClick: handleClick
                  }}
                  placement="bottomRight"
                >
                  <Button>
                    {t('application.import')}
                  </Button>
                </Dropdown>
                <Button type="primary" onClick={handleCreate}>
                  {t('application.createApplication')}
                </Button>
              </>}
            </Space>
          }
        </Form>
      </Flex>

      {(activeTab === 'apps' || activeTab === 'sharing') &&
        <PageScrollList<Application, Query>
          ref={scrollListRef}
          url={getApplicationListUrl}
          query={{ ...query, shared_only: activeTab === 'sharing', include_shared: activeTab !== 'apps' }}
          renderItem={(item) => (
            <RbCard
              title={item.name}
              avatar={
                <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                  {item.name[0]}
                </div>
              }
            >
              {['type', 'source', 'created_at'].map((key, index) => (
                <div key={key} className={clsx("rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px]", {
                  'rb:mt-3': index !== 0
                })}>
                  <span className="rb:text-[#5B6167]">{t(`application.${key}`)}</span>
                  <span className={clsx({
                    'rb:text-[#155EEF] rb:font-medium': key === 'type' && item[key] === 'agent',
                    'rb:text-[#369F21] rb:font-medium': key === 'type' && item[key] === 'multi_agent',
                  })}>
                    {key === 'source' && item.is_shared
                      ? item.source_workspace_name
                      : key === 'source' && !item.is_shared
                        ? t('application.configuration')
                        : key === 'created_at'
                          ? formatDateTime(item.created_at, 'YYYY-MM-DD HH:mm:ss')
                          : t(`application.${item[key as keyof Application]}`)
                    }
                  </span>
                </div>
              ))}

              {item.is_shared
                ? <div className="rb:mt-5 rb:flex rb:justify-between rb:gap-2.5">
                  <Button type="primary" ghost block onClick={() => handleEdit(item)}>{t('common.view')}</Button>
                  {item.share_permission === 'editable' && <Button type="primary" className="rb:w-[calc(100%-46px)]" onClick={() => handleCopy(item)}>{t('common.copy')}</Button>}
                </div>
                : <div className="rb:mt-5 rb:flex rb:justify-between rb:gap-2.5">
                  <Button type="primary" ghost className="rb:w-[calc(100%-46px)]" onClick={() => handleEdit(item)}>{t('application.configuration')}</Button>
                  <Button icon={<DeleteOutlined />} onClick={() => handleDelete(item)}></Button>
                </div>
              }
            </RbCard>
          )}
        />
      }
      {activeTab === 'myShare' && <MySharing />}
      

      <ApplicationModal
        ref={applicationModalRef}
        refresh={refresh}
      />

      <UploadWorkflowModal
        ref={uploadWorkflowModalRef}
        refresh={refresh}
      />
      <UploadModal
        ref={uploadModalRef}
        refresh={refresh}
      />
    </>
  );
};

export default ApplicationManagement