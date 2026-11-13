import { forwardRef, useImperativeHandle, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { App, Flex, Space, Switch, Tag, Button } from 'antd';
import type { ColumnsType } from 'antd/es/table';

import type { Application, MySharedOutItem, ApplicationRecordModalRef, DelistModalRef, RelistModalRef } from './types';
import { getMySharedOutListUrl, cancelShare, switchSharePermission, appPublicationWithdraw } from '@/api/application'
import Table, { type TableRef } from '@/components/Table'
import ApplicationRecordModal from './components/ApplicationRecordModal'
import DelistModal from './components/DelistModal'
import RelistModal from './components/RelistModal'
import AppTypeIcon from '@/views/ResourceCenter/components/AppTypeIcon';

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

const listingStatusColorMap: Record<string, string> = {
  listed: 'success',
  not_listed: 'default',
  pending_listing: 'default',
  delisted: 'error',
};

const listingStatusLabelMap: Record<string, string> = {
  listed: 'application.listingStatus_listed',
  not_listed: 'application.listingStatus_not_listed',
  pending_listing: 'application.listingStatus_delisted',
  delisted: 'application.listingStatus_delisted',
};

const MySharing = forwardRef<MySharingRef, { switchOntology: (item: Application) => void; }>((_props, ref) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const navigate = useNavigate();
  const tableRef = useRef<TableRef>(null)
  const recordModalRef = useRef<ApplicationRecordModalRef>(null)
  const delistModalRef = useRef<DelistModalRef>(null)
  const relistModalRef = useRef<RelistModalRef>(null)

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
    switchSharePermission(item.asset_id, { share_mode: !item.editable ? 'editable' : 'readonly' })
      .then(() => {
        tableRef.current?.loadData();
        message.success(t('common.operateSuccess'))
      })
  }

  const handleWithdraw = (item: MySharedOutItem) => {
    if (!item.pending_publication_id) return;
    modal.confirm({
      title: t('application.confirmWithdrawTitle', { app: item.app_name }),
      content: t('application.confirmWithdrawContent'),
      okText: t('application.withdrawApplication'),
      cancelText: t('application.keepApplication'),
      onOk: () => {
        appPublicationWithdraw(item.pending_publication_id as string)
          .then(() => {
            tableRef.current?.loadData();
            message.success(t('common.operateSuccess'))
          })
      }
    });
  };

  const handleDelist = (item: MySharedOutItem) => {
    const pubId = item.market_resource_id;
    if (!pubId) return;
    delistModalRef.current?.handleOpen(pubId, item.app_name);
  };

  const handleRelist = (item: MySharedOutItem) => {
    const marketResourceId = item.market_resource_id;
    if (!marketResourceId) return;
    relistModalRef.current?.handleOpen(item);
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
      render: (text: string, record: MySharedOutItem) => {
        return (
          <Flex align="center" gap={12}>
            <AppTypeIcon icon={record.icon} source_app_type={record.app_type} className="rb:size-8!" />
            <div>
              <div className="rb:font-medium rb:cursor-pointer rb:hover:text-primary-600" onClick={() => handleEdit(record)}>
                {text}
              </div>
              <div className="rb:text-gray-500 rb:text-[12px] rb:mt-0.5">{t(`application.${record.app_type}`)}</div>
            </div>
          </Flex>
        );
      },
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
      render: (version: string) => version || '-',
    },
    {
      title: t('application.marketStatus'),
      key: 'listing_status',
      width: 120,
      render: (_: unknown, record: MySharedOutItem) => {
        const ls = record.listing_status;
        if (!ls) return <span>-</span>;
        return (
          <Tag color={listingStatusColorMap[ls] || 'default'}>
            {t(listingStatusLabelMap[ls] || 'application.listingStatus_not_listed')}
          </Tag>
        );
      },
    },
    {
      title: t('application.shareStatus'),
      key: 'latest_review',
      width: 160,
      render: (_: unknown, record: MySharedOutItem) => {
        const reviewStatus = record.share_status;
        const reviewVersion = record.version;
        if (!reviewStatus || reviewStatus === 'shared' || reviewStatus === 'unknown') {
          return <span>-</span>;
        }
        return (
          <Space>
            <Tag color={statusColorMap[reviewStatus] || 'default'}>
              {[reviewVersion, t(statusLabelMap[reviewStatus] || 'application.statusUnknown')].join(' · ')}
            </Tag>
          </Space>
        );
      },
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
      render: (_: unknown, record: MySharedOutItem) => {
        return (
          <Space>
            {record.scope === 'resource_center'
              ? (
                <Button type="link" danger onClick={() => handleCancelOne(record)}>
                  {t('application.cancelShare')}
                </Button>
              )
              : (
                <>
                  <Button type="link" onClick={() => recordModalRef.current?.handleOpen(record.app_id)}>{t('application.viewRecord')}</Button>
                  {record.share_status === 'pending' && record.pending_publication_id && (
                    <Button type="link" onClick={() => handleWithdraw(record)}>{t('application.withdraw')}</Button>
                  )}
                </>
              )
            }
            {record.can_delist && (
              <Button type="link" danger onClick={() => handleDelist(record)}>{t('application.delist')}</Button>
            )}
            {record.can_relist && (
              <Button type="link" onClick={() => handleRelist(record)}>{t('application.relist')}</Button>
            )}
          </Space>
        )
      },
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
      <ApplicationRecordModal ref={recordModalRef} />
      <DelistModal
        ref={delistModalRef}
        onRefresh={() => tableRef.current?.loadData()}
        onSuccess={() => message.success(t('common.operateSuccess'))}
      />
      <RelistModal
        ref={relistModalRef}
        onRefresh={() => tableRef.current?.loadData()}
        onSuccess={() => message.success(t('common.operateSuccess'))}
      />
    </div>
  )
});

export default MySharing;
