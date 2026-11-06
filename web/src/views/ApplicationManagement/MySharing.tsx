import { forwardRef, useImperativeHandle, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { App, Flex, Space, Switch, Tag, Button } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import type { Application, MySharedOutItem, ShareStatus } from './types';
import { getMySharedOutListUrl, cancelShare, switchSharePermission } from '@/api/application'
import Table, { type TableRef } from '@/components/Table'

export interface MySharingRef {
  refresh: () => void;
}

const statusColorMap: Record<string, string> = {
  shared: 'success',
  approved: 'success',
  pending: 'processing',
  pending_sync: 'processing',
  rejected: 'error',
  sync_failed: 'error',
  withdrawn: 'default',
  unknown: 'default',
};

const statusLabelMap: Record<string, string> = {
  shared: 'application.statusShared',
  approved: 'application.statusApproved',
  pending: 'application.statusPending',
  pending_sync: 'application.statusPendingSync',
  rejected: 'application.statusRejected',
  sync_failed: 'application.statusSyncFailed',
  withdrawn: 'application.statusWithdrawn',
  unknown: 'application.statusUnknown',
};

const MySharing = forwardRef<MySharingRef, { switchOntology: (item: Application) => void; }>((_props, ref) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const navigate = useNavigate();
  const tableRef = useRef<TableRef>(null)

  useImperativeHandle(ref, () => ({ refresh: () => tableRef.current?.loadData() }))

  const handleCancelOne = (item: MySharedOutItem) => {
    if (!item.asset_id) return;

modal.confirm({
      title: t('application.confirmCancelOneShareTitle', { app: item.app_name }),
      content: item.scope === 'community_market'
        ? t('application.confirmCancelOneShareContentMarket')
        : t('application.confirmCancelOneShareContentResource'),
      okText: t('application.cancelShare'),
      cancelText: t('application.keepShare'),
      okType: 'danger',
      onOk: () => {
        cancelShare(item.asset_id as string)
          .then(() => {
            tableRef.current?.loadData();
            message.success(t('common.operateSuccess'))
          })
      }
    });
  };
  const handleSwitchSharePermission = (item: MySharedOutItem) => {
    if (!item.asset_id) return
    switchSharePermission(item.asset_id, { share_mode: item.editable ? 'editable' : 'readonly' })
      .then(() => {
        tableRef.current?.loadData();
        message.success(t('common.operateSuccess'))
      })
  }

  const handleWithdraw = (item: MySharedOutItem) => {
    if (!item.pending_publication_id) return;
    modal.confirm({
      title: t('application.confirmWithdrawDesc', { app: item.app_name }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: () => {
        // TODO: call withdraw API
        tableRef.current?.loadData();
        message.success(t('common.operateSuccess'))
      }
    });
  };

  const handleEdit = (item: MySharedOutItem) => {
    navigate(`/application/config/${item.app_id}`, { state: { tab: 'release' } });
  }

  const columns: ColumnsType<MySharedOutItem> = [
    {
      title: t('application.appName'),
      dataIndex: 'app_name',
      key: 'app_name',
      width: 200,
      render: (text: string, record: MySharedOutItem) => (
        <Flex align="center" gap={12}>
          {record.icon ? (
            <img src={record.icon} alt="" className="rb:size-8 rb:rounded-lg rb:object-cover rb:shrink-0" />
          ) : (
            <Flex align="center" justify="center" className="rb:size-8 rb:rounded-lg rb:text-[12px] rb:text-[#ffffff] rb:shrink-0" style={{
              backgroundColor: record.app_type === 'agent' ? '#3b82f6' : record.app_type === 'multi_agent' ? '#9C6FFF' : '#7c3aed'
            }}>
              {text.trim()[0]}
            </Flex>
          )}
          <div>
            <div className="rb:font-medium rb:cursor-pointer rb:hover:text-primary-600" onClick={() => handleEdit(record)}>
              {text}
            </div>
            <div className="rb:text-gray-500 rb:text-[12px] rb:mt-0.5">{t(`application.${record.app_type}`)}</div>
          </div>
        </Flex>
      ),
    },
    {
      title: t('application.shareScope'),
      dataIndex: 'scope',
      key: 'scope',
      width: 140,
      render: (scope: string) => (
        <Tag>{t(`application.scope_${scope}`)}</Tag>
      ),
    },
    {
      title: t('application.shareVersion'),
      dataIndex: 'version',
      key: 'version',
      width: 120,
    },
    {
      title: t('application.shareStatus'),
      dataIndex: 'share_status',
      key: 'share_status',
      width: 120,
      render: (status: ShareStatus) => (
        <Tag color={statusColorMap[status] || 'default'}>
          {t(statusLabelMap[status] || 'application.statusUnknown')}
        </Tag>
      ),
    },
    {
      title: t('application.editPermission'),
      dataIndex: 'editable',
      key: 'editable',
      width: 120,
      render: (editable: boolean, record: MySharedOutItem) => (
        <Switch
          checked={editable}
          disabled={record.scope === 'community_market'}
          size="small"
          onChange={() => handleSwitchSharePermission(record)}
        />
      ),
    },
    {
      title: t('application.sharedAt'),
      dataIndex: 'shared_at',
      key: 'shared_at',
      width: 180,
      render: (ts: number) => ts ? new Date(ts).toLocaleString() : '-',
    },
    {
      title: t('common.operation'),
      key: 'operation',
      fixed: 'right',
      render: (_: unknown, record: MySharedOutItem) => (
        <Space>
          <Button type="link" onClick={() => handleEdit(record)}>{t('application.releaseManage')}</Button>
          {record.scope === 'resource_center'
            ? (
              <Button type="link" danger onClick={() => handleCancelOne(record)}>
                {t('application.cancelShare')}
              </Button>
            )
            : (
              <>
                <Button type="link" onClick={() => {}}>{t('application.viewRecord')}</Button>
                {record.share_status === 'pending' && record.pending_publication_id && (
                  <Button type="link" onClick={() => handleWithdraw(record)}>{t('application.withdraw')}</Button>
                )}
              </>
            )
          }
        </Space>
      ),
    },
  ];

  return (
    <div className="rb:flex-1! rb:overflow-hidden rb:bg-white rb:rounded-lg rb:pt-3! rb:px-3!">
      <Table<MySharedOutItem>
        ref={tableRef}
        apiUrl={getMySharedOutListUrl}
        columns={columns}
        rowKey="app_id"
      />
    </div>
  )
});

export default MySharing;
