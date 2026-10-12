import { forwardRef, useImperativeHandle, useState, useRef } from 'react';
import { Flex, Tooltip, Space, Button } from 'antd';
import { useTranslation } from 'react-i18next';
import type { ColumnsType } from 'antd/es/table';

import {
  getWorkspaceReembedCurrenEndUsersUrl,
  getWorkspaceReembedCurrenEndUserRetry,
} from '@/api/workspaces'
import RbModal from '@/components/RbModal'
import RbAlert from '@/components/RbAlert';
import RbTable, { type TableRef } from '@/components/Table'
import StatusTag, { type StatusTagProps } from '@/components/StatusTag';
import type { Status } from './EmbeddingAlert'

interface DetailItem {
  end_user_id: string;
  name: string;
  external_id: string;
  status: Status;
  attempts: number;
  max_attempts: number;
  total_nodes: number;
  processed_nodes: number;
  failed_nodes: number;
  last_error: string | null;
  updated_at: number;
}

export interface EmbeddingRebuildDetailModalRef {
  handleOpen: () => void;
}

const statusObj: Record<Status, StatusTagProps['status']> = {
  succeeded: 'success',
  pending: 'warning',
  queued: 'warning',
  running: 'warning',
  failed: 'error',
}

const itemImpactKeys: Record<Status, string> = {
  failed: 'space.reembedding.detail.itemImpact.failed',
  succeeded: 'space.reembedding.detail.itemImpact.succeeded',
  pending: 'space.reembedding.detail.itemImpact.queued',
  queued: 'space.reembedding.detail.itemImpact.queued',
  running: 'space.reembedding.detail.itemImpact.running',
}

interface LoadResponse {
  job_id: string;
  job_status: Status;
  page?: {
    page: number;
    pagesize: number;
    total: number;
    hasnext: boolean;
  },
  summary: Record<Status, number>,
  items?: DetailItem[]
}

type SummaryDisplayMode = Exclude<Status, 'pending'>

const getSummaryDisplayMode = ({ job_status, summary }: LoadResponse): SummaryDisplayMode | null => {
  if (job_status === 'pending') {
    return 'queued'
  }
  if (job_status !== 'running') {
    return job_status
  }
  if (summary.queued > 0 && summary.failed < 0) {
    return 'queued'
  }
  if (summary.running > 0) {
    return 'running'
  }
  return null
}

const EmbeddingRebuildDetailModal = forwardRef<EmbeddingRebuildDetailModalRef, { refresh?: () => void; }>(({
  refresh
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const tableRef = useRef<TableRef>(null);
  const [reembedInfo, setReembedInfo] = useState<LoadResponse>({} as LoadResponse)

  const handleClose = () => {
    setVisible(false);
    refresh?.()
  };
  const handleOpen = () => {
    setVisible(true);
  };

  const handleLoad = (res: any) => {
    const { summary, job_id, job_status } = res as LoadResponse
    setReembedInfo({ summary, job_id, job_status })
  }

  const handleRetry = (record: DetailItem) => {
    getWorkspaceReembedCurrenEndUserRetry(record.end_user_id)
      .then(() => {
        tableRef.current?.loadData()
        refresh?.()
      })
  }
  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  const columns: ColumnsType<DetailItem> = [
    {
      title: t('space.reembedding.detail.memoryLibrary'),
      dataIndex: 'name',
      key: 'name',
      fixed: 'left',
      render: (value, record) => {
        const name = value || record.end_user_id;
        return (
          <Flex gap={4}>
            <div className="rb:size-6 rb:text-center rb:font-semibold rb:leading-6 rb:rounded-md rb:text-white rb:bg-blue-500 rb:shrink-0">
              {name[0]}
            </div>

            <Tooltip title={name || '-'}>
              <div className="rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">
                {name || '-'}
              </div>
            </Tooltip>
          </Flex>
        )
      }
    },
    {
      title: t('space.reembedding.detail.memoryLibraryId'),
      dataIndex: 'end_user_id',
      key: 'end_user_id',
    },
    {
      title: t('space.reembedding.detail.taskStatus'),
      dataIndex: 'status',
      key: 'status',
      render: (status: Status) => (
        <StatusTag status={statusObj[status]} text={t(`space.reembedding.status.${status}`)} />
      )
    },
    {
      title: t('space.reembedding.detail.currentImpact'),
      dataIndex: 'last_error',
      key: 'last_error',
      render: (_, record) => 
        <Tooltip title={t(itemImpactKeys[record.status])}>
          <div className="rb:max-w-60 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{t(itemImpactKeys[record.status])}</div>
        </Tooltip>
    },
    {
      title: t('space.reembedding.detail.actions'),
      key: 'operate',
      fixed: 'right',
      width: '100px',
      render: (_, record) => {
        if (record.status !== 'failed') {
          return null
        }
        return (
          <Space size={12}>
            <Button
              type="link"
              danger
              onClick={() => handleRetry(record)}
            >
              {t('space.reembedding.detail.retry')}
            </Button>
          </Space>
        )
      }
    },
  ]

  if (!visible) {
    return null
  }

  const summaryMode = getSummaryDisplayMode(reembedInfo)
  const summaryConfigs = {
    queued: {
      color: 'blue' as const,
      content: t('space.reembedding.detail.summaryQueued', {
        queued: reembedInfo.summary?.queued || 0,
      }),
    },
    running: {
      color: 'orange' as const,
      content: t('space.reembedding.detail.summaryRunning', {
        running: reembedInfo.summary?.running || 0,
        succeeded: reembedInfo.summary?.succeeded || 0,
      }),
    },
    succeeded: {
      color: 'green' as const,
      content: t('space.reembedding.detail.summarySucceeded'),
    },
    failed: {
      color: 'red' as const,
      content: t('space.reembedding.detail.summaryFailed', {
        succeeded: reembedInfo.summary?.succeeded || 0,
        failed: reembedInfo.summary?.failed || 0,
      }),
    },
  }
  const summaryConfig = summaryMode ? summaryConfigs[summaryMode] : null

  return (
    <RbModal
      title={t('space.reembedding.detail.title')}
      open={visible}
      onCancel={handleClose}
      footer={null}
      width={1000}
    >
      {summaryConfig && (
        <RbAlert color={summaryConfig.color}>
          <Flex vertical gap={4} className="rb:w-full!">
            <div>
              <span className="rb:font-medium">{t('space.reembedding.detail.taskBackground')}</span>
              {t('space.reembedding.detail.background')}
            </div>
            <div>
              <span className="rb:font-medium">{t('space.reembedding.detail.currentImpactLabel')}</span>
              {summaryConfig.content}
            </div>
          </Flex>
        </RbAlert>
      )}
      <RbTable<DetailItem>
        ref={tableRef}
        apiUrl={getWorkspaceReembedCurrenEndUsersUrl}
        columns={columns}
        rowKey="end_user_id"
        className="rb:mt-3!"
        onLoad={handleLoad}
      />
    </RbModal>
  );
});

export default EmbeddingRebuildDetailModal;
