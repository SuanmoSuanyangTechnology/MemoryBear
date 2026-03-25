/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 11:16:04
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
import { useSearchParams } from 'react-router-dom'

import ApplicationModal, { types } from './components/ApplicationModal';
import type { Application, ApplicationModalRef, Query, UploadWorkflowModalRef } from './types';
import SearchInput from '@/components/SearchInput'
import { getApplicationListUrl, deleteApplication, copyApplication } from '@/api/application'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import UploadWorkflowModal from './components/UploadWorkflowModal'
import UploadModal from './components/UploadModal'
import PageTabs from '@/components/PageTabs'
import MySharing from './MySharing'
import RbCard from '@/components/RbCard'
import RbButton from '@/components/RbButton'
import RbDescriptions from '@/components/RbDescriptions'


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
  const handleClick = ({ key }: { key: string }) => {
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
      <Flex justify="space-between" className="rb:mb-4!">
        <PageTabs
          value={activeTab}
          options={formatTabItems}
          onChange={handleChangeTab}
        />

        <Form
          form={form}
        >
          {activeTab !== 'myShare' &&
            <Space size={8}>
              <Form.Item name="type" noStyle>
                <Select
                  placeholder={t('application.applicationType')}
                  options={(activeTab === 'sharing' ? types.filter(type => type !== 'multi_agent') : types).map((type) => ({
                    value: type,
                    label: t(`application.${type}`),
                  }))}
                  allowClear
                  variant="filled"
                  className="rb:w-30!"
                />
              </Form.Item>
              <Form.Item name="search" noStyle>
                <SearchInput
                  placeholder={t('application.searchPlaceholder')}
                  className="rb:w-75!"
                />
              </Form.Item>
              {activeTab === 'apps' && <Space size={10}>
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
                <RbButton type="primary" icon={<div className="rb:size-3 rb:bg-cover rb:bg-[url('@/assets/images/common/plus.svg')]"></div>} onClick={handleCreate}>
                  {t('application.createApplication')}
                </RbButton>
              </Space>}
            </Space>
          }
        </Form>
      </Flex>

      {(activeTab === 'apps' || activeTab === 'sharing') &&
        <PageScrollList<Application, Query>
          ref={scrollListRef}
          url={getApplicationListUrl}
          needLoading={false}
          query={{ ...query, shared_only: activeTab === 'sharing', include_shared: activeTab !== 'apps' }}
          renderItem={(item) => (
            <RbCard
              title={item.name}
              avatarText={item.name.trim()[0]}
              avatarClassName={clsx({
                'rb:bg-[#155EEF]': item.type === 'agent',
                'rb:bg-[#9C6FFF]!': item.type === 'multi_agent',
                'rb:bg-[#171719]': item.type === 'workflow',
              })}
              footer={
                item.is_shared
                  ? <Flex justify="space-between" gap={12}>
                    <RbButton type="primary" ghost block onClick={() => handleEdit(item)}>{t('common.view')}</RbButton>
                    {item.share_permission === 'editable' && <RbButton type="primary" className="rb:w-[calc(100%-46px)]" onClick={() => handleCopy(item)}>{t('common.copy')}</RbButton>}
                  </Flex>
                  : <Flex justify="space-between" gap={12}>
                    <RbButton danger className="rb:w-22.25" onClick={() => handleDelete(item)}>{t('common.delete')}</RbButton>
                    <RbButton type="primary" ghost className="rb:flex-1" onClick={() => handleEdit(item)}>{t('application.configuration')}</RbButton>
                  </Flex>
              }
            >
              <RbDescriptions
                items={['type', 'source', 'created_at'].map(key => ({
                  key,
                  label: t(`application.${key}`),
                  children: <span className={clsx('rb:font-medium', {
                    'rb:text-[#155EEF]': key === 'type',
                  })}>
                    {key === 'source' && item.is_shared
                      ? t('application.shared')
                      : key === 'source' && !item.is_shared
                        ? t('application.configuration')
                        : key === 'created_at'
                          ? formatDateTime(item.created_at, 'YYYY-MM-DD HH:mm:ss')
                          : t(`application.${item[key as keyof Application]}`)
                    }
                  </span>
                }))}
              />
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