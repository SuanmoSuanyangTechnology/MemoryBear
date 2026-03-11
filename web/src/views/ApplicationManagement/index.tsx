/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-02 17:48:51
 */
/**
 * Application Management Page
 * Displays and manages all applications in the workspace
 * Supports creating, editing, and deleting applications
 */

import React, { useState, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Row, Col, App, Select, Space, Dropdown, Tabs, Popconfirm } from 'antd';
import clsx from 'clsx';
import { DeleteOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom'

import ApplicationModal, { types } from './components/ApplicationModal';
import type { Application, ApplicationModalRef, Query, UploadWorkflowModalRef } from './types';
import SearchInput from '@/components/SearchInput'
import RbCard from '@/components/RbCard/Card'
import { getApplicationListUrl, getApplicationList, deleteApplication, removeSharedApp, getMySharedOut, unshareAppFromWorkspace, updateSharePermission } from '@/api/application'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format';
import UploadWorkflowModal from './components/UploadWorkflowModal'
import UploadModal from './components/UploadModal'

/**
 * Application management main component
 */
const ApplicationManagement: React.FC = () => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const [searchParams] = useSearchParams()
  const [query, setQuery] = useState<Query>({} as Query);
  const [activeTab, setActiveTab] = useState('all');
  const [sharedOutList, setSharedOutList] = useState<any[]>([]);
  const [sharedOutLoading, setSharedOutLoading] = useState(false);
  const [sharedToMeList, setSharedToMeList] = useState<Application[]>([]);
  const [sharedToMeLoading, setSharedToMeLoading] = useState(false);
  const applicationModalRef = useRef<ApplicationModalRef>(null);
  const scrollListRef = useRef<PageScrollListRef>(null)
  const uploadWorkflowModalRef = useRef<UploadWorkflowModalRef>(null);
  const uploadModalRef = useRef<UploadWorkflowModalRef>(null);

  useEffect(() => {
    const data = Object.fromEntries(searchParams)
    const { type } = data
    setQuery(prev => ({ ...prev, type: type || undefined }))
  }, [searchParams])

  useEffect(() => {
    if (activeTab === 'myShared') {
      fetchSharedOut()
    }
    if (activeTab === 'sharedToMe') {
      fetchSharedToMe()
    }
  }, [activeTab])

  const fetchSharedOut = () => {
    setSharedOutLoading(true)
    getMySharedOut()
      .then((res: any) => setSharedOutList(res || []))
      .finally(() => setSharedOutLoading(false))
  }

  const fetchSharedToMe = () => {
    setSharedToMeLoading(true)
    getApplicationList({ include_shared: true, pagesize: 200 })
      .then((res: any) => setSharedToMeList((res?.items || []).filter((a: Application) => a.is_shared)))
      .finally(() => setSharedToMeLoading(false))
  }

  const refresh = () => { scrollListRef.current?.refresh() }
  const handleCreate = () => { applicationModalRef.current?.handleOpen() }

  const handleEdit = (item: Application) => {
    window.open(`/#/application/config/${item.id}`);
  }

  const handleDelete = (item: Application) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        const deleteAction = item.is_shared ? removeSharedApp(item.id) : deleteApplication(item.id)
        deleteAction.then(() => refresh()).catch(() => console.error('Failed to delete application'))
      }
    })
  }

  const handleUnshare = (share: any) => {
    unshareAppFromWorkspace(share.source_app_id, share.target_workspace_id)
      .then(() => {
        message.success(t('appShare.unshareSuccess'))
        fetchSharedOut()
      })
      .catch(() => message.error(t('appShare.unshareFailed')))
  }

  const handleUnshareAll = (group: { id: string; name: string; items: any[] }) => {
    modal.confirm({
      title: t('appShare.unshareAllTitle'),
      content: t('appShare.confirmUnshareAll', { name: group.name }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'primary',
      onOk: () =>
        Promise.all(
          group.items.map((r: any) => unshareAppFromWorkspace(r.source_app_id, r.target_workspace_id))
        ).then(() => {
          message.success(t('appShare.unshareAllSuccess'))
          fetchSharedOut()
        }).catch(() => message.error(t('appShare.unshareFailed')))
    })
  }
  const handleUpdatePermission = (share: any, newPermission: string) => {
    updateSharePermission(share.source_app_id, share.target_workspace_id, newPermission)
      .then(() => {
        message.success(t('appShare.permissionUpdated'))
        fetchSharedOut()
      })
      .catch(() => message.error(t('appShare.permissionUpdateFailed')))
  }

  const handleChangeType = (value?: string) => {
    setQuery(prev => ({ ...prev, type: value }))
  }

  const handleImport = () => { uploadWorkflowModalRef.current?.handleOpen() }
  const handleClick = ({ key }: { key: string }) => {
    if (key === 'thirdParty') handleImport()
    if (key === 'import') uploadModalRef.current?.handleOpen()
  }

  // 工作空间头像颜色池
  const wsColors = ['#6366F1', '#8B5CF6', '#EC4899', '#F59E0B', '#10B981', '#3B82F6', '#EF4444']
  const getWsColor = (name: string) => wsColors[(name?.charCodeAt(0) || 0) % wsColors.length]

  // 共享应用：按来源工作空间分组
  const groupedSharedToMe = sharedToMeList.reduce((acc: Record<string, any>, app: Application) => {
    const wsName = app.source_workspace_name || app.workspace_id
    const wsIcon = app.source_workspace_icon || null
    if (!acc[wsName]) acc[wsName] = { name: wsName, icon: wsIcon, items: [] }
    acc[wsName].items.push(app)
    return acc
  }, {})
  const sharedToMeGroups = Object.values(groupedSharedToMe) as { name: string; icon: string | null; items: Application[] }[]

  const [expandedSharedToMe, setExpandedSharedToMe] = useState<Record<string, boolean>>({})
  const toggleSharedToMe = (name: string) => setExpandedSharedToMe(prev => ({ ...prev, [name]: !prev[name] }))

  const handleRemoveAllShared = (group: { name: string; items: Application[] }) => {
    modal.confirm({
      title: t('appShare.removeAllTitle'),
      content: t('appShare.confirmRemoveAll', { name: group.name }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () =>
        Promise.all(group.items.map((item) => removeSharedApp(item.id)))
          .then(() => {
            message.success(t('appShare.removeAllSuccess'))
            fetchSharedToMe()
          })
          .catch(() => message.error(t('common.deleteFailed')))
    })
  }

  const sharedToMeContent = (
    <div className="rb:flex rb:flex-col rb:gap-3">
      {sharedToMeLoading && <div className="rb:text-center rb:text-gray-400 rb:py-10">{t('common.loading')}</div>}
      {!sharedToMeLoading && sharedToMeGroups.length === 0 && (
        <div className="rb:text-center rb:text-gray-400 rb:py-10">{t('appShare.noSharedToMe')}</div>
      )}
      {sharedToMeGroups.map((group) => {
        const color = getWsColor(group.name)
        const isExpanded = expandedSharedToMe[group.name] ?? false
        return (
          <div key={group.name} className="rb:bg-white rb:rounded-xl rb:border rb:border-gray-100 rb:shadow-sm rb:overflow-hidden">
            <div
              className="rb:flex rb:items-center rb:gap-4 rb:px-5 rb:py-4 rb:cursor-pointer rb:hover:bg-gray-50 rb:transition-colors"
              onClick={() => toggleSharedToMe(group.name)}
            >
              {/* 工作空间头像 */}
              <div
                className="rb:w-12 rb:h-12 rb:rounded-xl rb:flex-shrink-0 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-base rb:font-bold rb:overflow-hidden"
                style={group.icon ? {} : { background: color }}
              >
                {group.icon
                  ? <img src={group.icon} alt={group.name} className="rb:w-full rb:h-full rb:object-cover" />
                  : group.name?.[0]
                }
              </div>
              <div className="rb:flex-1 rb:min-w-0">
                <div className="rb:text-sm rb:font-semibold rb:text-gray-800 rb:truncate">{group.name}</div>
                <div className="rb:text-xs rb:text-gray-400 rb:mt-0.5">
                  {t('appShare.sharedAppCount', { count: group.items.length })}
                </div>
              </div>
              <Button
                type="text"
                danger
                size="small"
                onClick={(e) => { e.stopPropagation(); handleRemoveAllShared(group) }}
              >
                {t('appShare.removeAll')}
              </Button>
              <div className={`rb:text-gray-400 rb:text-lg rb:transition-transform rb:duration-200 ${isExpanded ? 'rb:rotate-180' : ''}`}>▾</div>
            </div>
            {isExpanded && (
              <div className="rb:border-t rb:border-gray-100 rb:p-4 rb:flex rb:flex-wrap rb:gap-3">
                {group.items.map((item) => (
                  <div
                    key={item.id}
                    onClick={() => window.open(`/#/application/config/${item.id}`)}
                    className="rb:flex rb:flex-col rb:gap-2 rb:px-4 rb:py-3 rb:rounded-xl rb:border rb:border-gray-200 rb:bg-gray-50 rb:min-w-[160px] rb:max-w-[220px] rb:cursor-pointer rb:hover:border-[#155EEF] rb:hover:bg-blue-50 rb:transition-all"
                  >
                    {/* 应用名 */}
                    <div className="rb:text-sm rb:text-gray-800 rb:font-medium rb:truncate">{item.name}</div>
                    {/* 类型 + 版本 + 生效状态 */}
                    <div className="rb:flex rb:flex-wrap rb:items-center rb:gap-1">
                      {item.type && (
                        <span className="rb:px-1.5 rb:py-0.5 rb:rounded rb:bg-gray-200 rb:text-gray-600 rb:text-xs">
                          {t(`application.${item.type}`)}
                        </span>
                      )}
                      {item.source_app_version && (
                        <span className="rb:px-1.5 rb:py-0.5 rb:rounded rb:bg-blue-50 rb:text-blue-500 rb:text-xs">
                          {item.source_app_version}
                        </span>
                      )}
                      <span className={`rb:px-1.5 rb:py-0.5 rb:rounded rb:text-xs ${
                        item.source_app_is_active
                          ? 'rb:bg-green-50 rb:text-green-600'
                          : 'rb:bg-gray-100 rb:text-gray-400'
                      }`}>
                        {item.source_app_is_active ? t('appShare.active') : t('appShare.inactive')}
                      </span>
                    </div>
                    {/* 权限标签（只读展示）+ 删除 */}
                    <div className="rb:flex rb:items-center rb:justify-between rb:gap-2" onClick={(e) => e.stopPropagation()}>
                      <span className={`rb:px-2 rb:py-0.5 rb:rounded-full rb:text-xs rb:font-medium rb:border ${
                        item.share_permission === 'editable'
                          ? 'rb:bg-blue-50 rb:text-blue-500 rb:border-blue-200'
                          : 'rb:bg-green-50 rb:text-green-600 rb:border-green-200'
                      }`}>
                        {t(`appShare.${item.share_permission || 'readonly'}`)}
                      </span>
                      <Popconfirm
                        title={t('appShare.confirmRemoveShared')}
                        onConfirm={() => removeSharedApp(item.id).then(() => fetchSharedToMe()).catch(() => message.error(t('common.deleteFailed')))}
                        okText={t('common.confirm')}
                        cancelText={t('common.cancel')}
                      >
                        <Button type="text" danger size="small" icon={<DeleteOutlined />} className="rb:flex-shrink-0" />
                      </Popconfirm>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )

  // 按目标工作空间分组
  const groupedByWorkspace = sharedOutList.reduce((acc: Record<string, any>, record: any) => {
    const wsId = record.target_workspace_id
    const wsName = record.target_workspace_name || record.target_workspace_id
    const wsIcon = record.target_workspace_icon || null
    if (!acc[wsId]) acc[wsId] = { id: wsId, name: wsName, icon: wsIcon, items: [] }
    acc[wsId].items.push(record)
    return acc
  }, {})
  const workspaceGroups = Object.values(groupedByWorkspace) as { id: string; name: string; icon: string | null; items: any[] }[]

  const [expandedWs, setExpandedWs] = useState<Record<string, boolean>>({})
  const toggleWs = (id: string) => setExpandedWs(prev => ({ ...prev, [id]: !prev[id] }))

  const mySharedContent = (
    <div className="rb:flex rb:flex-col rb:gap-3">
      {sharedOutLoading && (
        <div className="rb:text-center rb:text-gray-400 rb:py-10">{t('common.loading')}</div>
      )}
      {!sharedOutLoading && workspaceGroups.length === 0 && (
        <div className="rb:text-center rb:text-gray-400 rb:py-10">{t('appShare.noSharedOut')}</div>
      )}
      {workspaceGroups.map((group) => {
        const color = getWsColor(group.name)
        const isExpanded = expandedWs[group.id] ?? false
        return (
          <div key={group.id} className="rb:bg-white rb:rounded-xl rb:border rb:border-gray-100 rb:shadow-sm rb:overflow-hidden">
            {/* 工作空间行 - 可点击展开 */}
            <div
              className="rb:flex rb:items-center rb:gap-4 rb:px-5 rb:py-4 rb:cursor-pointer rb:hover:bg-gray-50 rb:transition-colors"
              onClick={() => toggleWs(group.id)}
            >
              <div
                className="rb:w-12 rb:h-12 rb:rounded-xl rb:flex-shrink-0 rb:flex rb:items-center rb:justify-center rb:text-white rb:text-base rb:font-bold rb:overflow-hidden"
                style={group.icon ? {} : { background: color }}
              >
                {group.icon
                  ? <img src={group.icon} alt={group.name} className="rb:w-full rb:h-full rb:object-cover" />
                  : group.name?.[0]
                }
              </div>
              <div className="rb:flex-1 rb:min-w-0">
                <div className="rb:text-sm rb:font-semibold rb:text-gray-800 rb:truncate">{group.name}</div>
                <div className="rb:text-xs rb:text-gray-400 rb:mt-0.5">
                  {t('appShare.sharedAppCount', { count: group.items.length })}
                </div>
              </div>
              <Button
                type="text"
                danger
                size="small"
                onClick={(e) => { e.stopPropagation(); handleUnshareAll(group) }}
              >
                {t('appShare.unshareAll')}
              </Button>
              <div className={`rb:text-gray-400 rb:text-lg rb:transition-transform rb:duration-200 ${isExpanded ? 'rb:rotate-180' : ''}`}>
                ▾
              </div>
            </div>

            {/* 展开的应用列表 */}
            {isExpanded && (
              <div className="rb:border-t rb:border-gray-100 rb:p-4 rb:flex rb:flex-wrap rb:gap-3">
                {group.items.map((record: any) => (
                  <div
                    key={record.id}
                    onClick={() => window.open(`/#/application/config/${record.source_app_id}`)}
                    className="rb:flex rb:flex-col rb:gap-2 rb:px-4 rb:py-3 rb:rounded-xl rb:border rb:border-gray-200 rb:bg-gray-50 rb:min-w-[160px] rb:max-w-[220px] rb:cursor-pointer rb:hover:border-[#155EEF] rb:hover:bg-blue-50 rb:transition-all"
                  >
                    {/* 应用名 */}
                    <div className="rb:text-sm rb:text-gray-800 rb:font-medium rb:truncate">
                      {record.source_app_name || record.source_app_id}
                    </div>
                    {/* 类型 + 版本 + 生效状态 */}
                    <div className="rb:flex rb:flex-wrap rb:items-center rb:gap-1">
                      {record.source_app_type && (
                        <span className="rb:px-1.5 rb:py-0.5 rb:rounded rb:bg-gray-200 rb:text-gray-600 rb:text-xs">
                          {t(`application.${record.source_app_type}`)}
                        </span>
                      )}
                      {record.source_app_version && (
                        <span className="rb:px-1.5 rb:py-0.5 rb:rounded rb:bg-blue-50 rb:text-blue-500 rb:text-xs">
                        {record.source_app_version}
                        </span>
                      )}
                      <span className={`rb:px-1.5 rb:py-0.5 rb:rounded rb:text-xs ${
                        record.source_app_is_active
                          ? 'rb:bg-green-50 rb:text-green-600'
                          : 'rb:bg-gray-100 rb:text-gray-400'
                      }`}>
                        {record.source_app_is_active ? t('appShare.active') : t('appShare.inactive')}
                      </span>
                    </div>
                    {/* 权限切换 + 删除 */}
                    <div className="rb:flex rb:items-center rb:justify-between rb:gap-2">
                      {/* 权限滑动切换 */}
                      <button
                        onClick={(e) => { e.stopPropagation(); handleUpdatePermission(record, record.permission === 'readonly' ? 'editable' : 'readonly') }}
                        className="rb:relative rb:flex rb:items-center rb:rounded-full rb:border rb:border-gray-200 rb:bg-gray-100 rb:p-0.5 rb:cursor-pointer rb:transition-all rb:hover:border-green-300"
                        style={{ width: 110 }}
                      >
                        {/* 滑块 */}
                        <span
                          className="rb:absolute rb:top-0.5 rb:bottom-0.5 rb:rounded-full rb:bg-[#D1FAE5] rb:border rb:border-[#6EE7B7] rb:transition-all rb:duration-200"
                          style={{
                            width: '50%',
                            left: record.permission === 'readonly' ? 2 : 'calc(50% - 2px)',
                          }}
                        />
                        {/* 左标签：仅使用 */}
                        <span className={`rb:relative rb:z-10 rb:flex-1 rb:text-center rb:text-xs rb:font-medium rb:transition-colors rb:duration-200 rb:py-0.5 ${
                          record.permission === 'readonly' ? 'rb:text-[#059669]' : 'rb:text-gray-400'
                        }`}>
                          {t('appShare.readonly')}
                        </span>
                        {/* 右标签：可编辑 */}
                        <span className={`rb:relative rb:z-10 rb:flex-1 rb:text-center rb:text-xs rb:font-medium rb:transition-colors rb:duration-200 rb:py-0.5 ${
                          record.permission === 'editable' ? 'rb:text-[#059669]' : 'rb:text-gray-400'
                        }`}>
                          {t('appShare.editable')}
                        </span>
                      </button>
                      <Popconfirm
                        title={t('appShare.confirmUnshare')}
                        onConfirm={() => handleUnshare(record)}
                        okText={t('common.confirm')}
                        cancelText={t('common.cancel')}
                      >
                        <Button type="text" danger size="small" icon={<DeleteOutlined />} className="rb:flex-shrink-0" onClick={(e) => e.stopPropagation()} />
                      </Popconfirm>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )

  const appListContent = (
    <>
      <Row gutter={16} className="rb:mb-4">
        <Col span={4}>
          <Select
            value={query.type}
            placeholder={t('application.applicationType')}
            options={types.map((type) => ({ value: type, label: t(`application.${type}`) }))}
            allowClear
            className="rb:w-full"
            onChange={handleChangeType}
          />
        </Col>
        <Col span={8}>
          <SearchInput
            placeholder={t('application.searchPlaceholder')}
            onSearch={(value) => setQuery({ search: value })}
            style={{ width: '100%' }}
          />
        </Col>
        <Col span={12} className="rb:text-right">
          <Space size={12}>
            <Dropdown
              menu={{ items: [
                { key: 'thirdParty', label: t('application.importWorkflow') },
                { key: 'import', label: t('application.import') },
              ], onClick: handleClick }}
              placement="bottomRight"
            >
              <Button>{t('application.import')}</Button>
            </Dropdown>
            <Button type="primary" onClick={handleCreate}>
              {t('application.createApplication')}
            </Button>
          </Space>
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
              <div key={key} className={clsx("rb:flex rb:justify-between rb:gap-5 rb:font-regular rb:text-[14px]", { 'rb:mt-3': index !== 0 })}>
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
                    : t(`application.${item[key as keyof Application]}`)}
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
    </>
  )

  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          { key: 'all', label: t('appShare.tabAll'), children: appListContent },
          {
            key: 'sharedToMe', label: t('appShare.tabSharedToMe'),
            children: sharedToMeContent
          },
          {
            key: 'myShared', label: t('appShare.tabMyShared'),
            children: mySharedContent
          },
        ]}
      />

      <ApplicationModal ref={applicationModalRef} refresh={refresh} />
      <UploadWorkflowModal ref={uploadWorkflowModalRef} refresh={refresh} />
      <UploadModal ref={uploadModalRef} refresh={refresh} />
    </>
  );
};

export default ApplicationManagement