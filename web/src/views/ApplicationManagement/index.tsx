/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-04 10:44:29
 */
/**
 * Application Management Page
 * Displays and manages all applications in the workspace
 * Supports creating, editing, and deleting applications
 */

import React, { useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { App, Select, Space, Form, Flex, Dropdown, Button } from 'antd';
import clsx from 'clsx';
import { useSearchParams } from 'react-router-dom'

import ApplicationModal, { types } from './components/ApplicationModal';
import type { Application, ApplicationModalRef, Query, UploadWorkflowModalRef } from './types';
import SearchInput from '@/components/SearchInput'
import { getApplicationListUrl, deleteApplication } from '@/api/application'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import UploadWorkflowModal from './components/UploadWorkflowModal'
import RbCard from '@/components/RbCard'
import RbButton from '@/components/RbButton'
import RbDescriptions from '@/components/RbDescriptions'

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

  const [form] = Form.useForm()
  const query = Form.useWatch([], form)

  useEffect(() => {
    // Convert URLSearchParams to a plain object for easier access
    const data = Object.fromEntries(searchParams)
    const { type } = data

    form.setFieldValue('type', type || null)
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
    window.open(`/#/application/config/${item.id}`);
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
    }
  }
  return (
    <>
      <Form form={form} className="rb:mb-4!">
        <Flex justify="space-between">
          <Space size={10}>
            <Form.Item name="type" noStyle>
              <Select
                placeholder={t('application.applicationType')}
                options={[
                  { value: null, label: t('application.allType') },
                  ...types.map((type) => ({
                    value: type,
                    label: t(`application.${type}`),
                  }))
                ]}
                className="rb:w-30!"
              />
            </Form.Item>
            <Form.Item name="search" noStyle>
              <SearchInput
                placeholder={t('application.searchPlaceholder')}
                className="rb:w-75!"
              />
            </Form.Item>
          </Space>
          <Space size={10}>
            <Dropdown
              menu={{
                items: [
                  { key: 'thirdParty', label: t('application.importWorkflow') },
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
          </Space>
        </Flex>
      </Form>

      <PageScrollList<Application, Query>
        ref={scrollListRef}
        url={getApplicationListUrl}
        query={query}
        renderItem={(item) => (
          <RbCard 
            title={item.name}
            avatarText={item.name.trim()[0]}
            avatarClassName={clsx({
              'rb:bg-[#155EEF]': item.type === 'agent',
              'rb:bg-[#9C6FFF]!': item.type === 'multi_agent',
              'rb:bg-[#171719]': item.type === 'workflow',
            })}
            footer={<Flex justify="space-between" gap={12}>
              <RbButton danger className="rb:w-22.25" onClick={() => handleDelete(item)}>{t('common.delete')}</RbButton>
              <RbButton type="primary" ghost className="rb:flex-1" onClick={() => handleEdit(item)}>{t('application.configuration')}</RbButton>
            </Flex>}
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

      <ApplicationModal
        ref={applicationModalRef}
        refresh={refresh}
      />

      <UploadWorkflowModal
        ref={uploadWorkflowModalRef}
        refresh={refresh}
      />
    </>
  );
};

export default ApplicationManagement