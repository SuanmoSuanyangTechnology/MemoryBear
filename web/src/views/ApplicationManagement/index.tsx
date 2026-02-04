/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:34:12 
 */
/**
 * Application Management Page
 * Displays and manages all applications in the workspace
 * Supports creating, editing, and deleting applications
 */

import React, { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Row, Col, App } from 'antd';
import clsx from 'clsx';
import { DeleteOutlined } from '@ant-design/icons';

import type { Application, ApplicationModalRef, Query } from './types';
import ApplicationModal from './components/ApplicationModal';
import SearchInput from '@/components/SearchInput'
import RbCard from '@/components/RbCard/Card'
import { getApplicationListUrl, deleteApplication } from '@/api/application'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';

/**
 * Application management main component
 */
const ApplicationManagement: React.FC = () => {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [query, setQuery] = useState<Query>({} as Query);
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const scrollListRef = useRef<PageScrollListRef>(null)

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
  return (
    <>
      <Row gutter={16} className="rb:mb-4">
        <Col span={12}>
          <SearchInput
            placeholder={t('application.searchPlaceholder')}
            onSearch={(value) => setQuery({ search: value })}
            style={{width: '100%'}}
          />
        </Col>
        <Col span={12} className="rb:text-right">
          <Button type="primary" onClick={handleCreate}>
            {t('application.createApplication')}
          </Button>
        </Col>
      </Row>

      <PageScrollList<Application, Query>
        ref={scrollListRef}
        url={getApplicationListUrl}
        query={query}
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
                    ? t('application.shared')
                    : key === 'source' && !item.is_shared
                    ? t('application.configuration')
                    : key === 'created_at'
                    ? formatDateTime(item.created_at, 'YYYY-MM-DD HH:mm:ss')
                    : t(`application.${item[key as keyof Application]}`)
                  }
                </span>
              </div>
            ))}

            <div className="rb:mt-5 rb:flex rb:justify-between rb:gap-2.5">
              <Button type="primary" ghost className="rb:w-[calc(100%-46px)]" onClick={() => handleEdit(item)}>{t('application.configuration')}</Button>
              <Button icon={<DeleteOutlined />} onClick={() => handleDelete(item)}></Button>
            </div>
          </RbCard>
        )}
      />

      <ApplicationModal
        ref={applicationModalRef}
        refresh={refresh}
      />
    </>
  );
};

export default ApplicationManagement;