import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { MoreOutlined } from '@ant-design/icons';
import { App, Button, Dropdown, Flex, Space, Tooltip } from 'antd';
import type { MenuProps } from 'antd';
import type { AnyObject } from 'antd/es/_util/type';
import type { ColumnsType } from 'antd/es/table';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import {
  createSync,
  deleteDocument,
  downloadFile,
} from '@/api/knowledgeBase';
import SearchInput from '@/components/SearchInput';
import StatusTag from '@/components/StatusTag';
import Table, { type TableRef } from '@/components/Table';
import { formatDateTime } from '@/utils/format';
import type { BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import type {
  CreateDatasetModalRef,
  CreateFolderModalRef,
  CreateSetModalRef,
  FolderFormData,
  KnowledgeBaseDocumentData,
  KnowledgeBaseListItem,
} from '@/views/KnowledgeBase/types';
import DirectoryToggle from '../DirectoryToggle';
import PrivateKnowledgeBaseDialogs from '../PrivateKnowledgeBaseDialogs';

export interface PrivateFileWorkspaceRef {
  refreshTable: () => void;
  openKnowledgeBaseEditor: () => void;
  openCreateFolder: (parentId: string, parentName?: string, parentPath?: string[]) => void;
}

interface PrivateFileWorkspaceProps {
  knowledgeBase: KnowledgeBaseListItem;
  knowledgeBaseId: string;
  parentId: string;
  folder: FolderFormData;
  folderPath: BreadcrumbItem[];
  knowledgeBaseFolderPath: BreadcrumbItem[];
  query: Record<string, unknown>;
  setQuery: Dispatch<SetStateAction<Record<string, unknown>>>;
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
  onRefreshKnowledgeBase: () => Promise<KnowledgeBaseListItem | null>;
  onRefreshDirectory: () => Promise<void>;
}

const PrivateFileWorkspace = forwardRef<PrivateFileWorkspaceRef, PrivateFileWorkspaceProps>(({
  knowledgeBase,
  knowledgeBaseId,
  parentId,
  folder,
  folderPath,
  knowledgeBaseFolderPath,
  query,
  setQuery,
  directoryCollapsed,
  onToggleDirectory,
  onRefreshKnowledgeBase,
  onRefreshDirectory,
}, ref) => {
  const { t } = useTranslation();
  const { modal, message: messageApi } = App.useApp();
  const navigate = useNavigate();
  const tableRef = useRef<TableRef>(null);
  const createFolderModalRef = useRef<CreateFolderModalRef>(null);
  const createImageDatasetRef = useRef<CreateSetModalRef>(null);
  const createContentModalRef = useRef<CreateSetModalRef>(null);
  const datasetModalRef = useRef<CreateDatasetModalRef>(null);
  const syncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const syncTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncStartTimeRef = useRef<number | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const apiUrl = `/documents/${knowledgeBaseId}/documents`;

  const refreshTable = useCallback(() => tableRef.current?.loadData(), []);

  useEffect(() => {
    refreshTable();
  }, [apiUrl, query._timestamp, refreshTable]);

  const stopSyncing = useCallback(() => {
    if (syncIntervalRef.current) clearInterval(syncIntervalRef.current);
    if (syncTimeoutRef.current) clearTimeout(syncTimeoutRef.current);
    syncIntervalRef.current = null;
    syncTimeoutRef.current = null;
    syncStartTimeRef.current = null;
    setIsSyncing(false);
  }, []);

  useEffect(() => stopSyncing, [stopSyncing]);

  const handleRefreshTable = useCallback(async () => {
    if (syncStartTimeRef.current && Date.now() - syncStartTimeRef.current > 60000) {
      stopSyncing();
      messageApi.warning(t('knowledgeBase.syncTimeout'));
      return;
    }

    const updatedKnowledgeBase = await onRefreshKnowledgeBase();
    refreshTable();
    if (syncStartTimeRef.current && updatedKnowledgeBase?.doc_num && updatedKnowledgeBase.doc_num > 0) {
      stopSyncing();
      messageApi.success(t('knowledgeBase.syncCompleted'));
    }
  }, [messageApi, onRefreshKnowledgeBase, refreshTable, stopSyncing, t]);

  const handleSync = async () => {
    if (!knowledgeBase.id) {
      messageApi.error(t('knowledgeBase.syncError'));
      return;
    }

    try {
      setIsSyncing(true);
      syncStartTimeRef.current = Date.now();
      await createSync(knowledgeBase.id);
      messageApi.success(t('knowledgeBase.syncSuccess'));
      syncIntervalRef.current = setInterval(() => void handleRefreshTable(), 5000);
      syncTimeoutRef.current = setTimeout(() => void handleRefreshTable(), 1000);
    } catch {
      stopSyncing();
      messageApi.error(t('knowledgeBase.syncFailed'));
    }
  };

  const openKnowledgeBaseEditor = () => createFolderModalRef.current?.handleOpen({
    id: knowledgeBase.id,
    parent_id: knowledgeBase.parent_id,
    kb_id: knowledgeBase.id,
    folder_name: knowledgeBase.name,
  }, 'edit');

  useImperativeHandle(ref, () => ({
    refreshTable,
    openKnowledgeBaseEditor,
    openCreateFolder: (targetParentId, parentName, parentPath) => createFolderModalRef.current?.handleOpen({
      kb_id: knowledgeBase.id,
      parent_id: targetParentId,
      parent_name: parentName,
    }, undefined, parentPath),
  }));

  const handleRechunking = (item: KnowledgeBaseListItem) => {
    const document = item as unknown as KnowledgeBaseDocumentData;
    navigate(`/knowledge-base/${knowledgeBaseId}/create-dataset`, {
      state: {
        source: 'local',
        knowledgeBaseId,
        parentId: parentId || knowledgeBaseId,
        documentFolderPath: folderPath,
        knowledgeBaseFolderPath,
        startStep: 'parameterSettings',
        fileId: document.id || document.file_id,
      },
    });
  };

  const handleDelete = (item: KnowledgeBaseDocumentData) => {
    if (!item?.id) return
    modal.confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: item.file_name }),
      onOk: async () => {
        await deleteDocument(item.id as string);
        messageApi.success(t('common.deleteSuccess'));
        refreshTable();
      },
    });
  };

  const getOptMenuItems = (row: KnowledgeBaseListItem): MenuProps['items'] => {
    const document = row as unknown as KnowledgeBaseDocumentData;
    const options: MenuProps['items'] = [
      {
        key: 'download',
        label: t('knowledgeBase.download'),
        onClick: () => void downloadFile(document.file_id ?? '', document.file_name ?? ''),
      },
      {
        key: 'delete',
        label: t('knowledgeBase.delete'),
        onClick: () => handleDelete(document),
      },
    ];
    if (row.parser_config?.doc_type === 'qa') return options;
    return [{
      key: 'rechunking',
      label: t('knowledgeBase.rechunking'),
      onClick: () => handleRechunking(row),
    }, ...options];
  };

  const columns: ColumnsType = [
    {
      title: t('knowledgeBase.name'),
      dataIndex: 'file_name',
      key: 'file_name',
      render: (text: string, record: AnyObject) => {
        const document = record as KnowledgeBaseDocumentData;
        return (
          <span
            className="rb:cursor-pointer rb:font-medium rb:text-gray-900 rb:hover:underline"
            onClick={() => document.id && navigate(`/knowledge-base/${knowledgeBaseId}/DocumentDetails`, {
              state: {
                documentId: document.id,
                parentId: parentId || knowledgeBaseId,
                breadcrumbPath: {
                  knowledgeBaseFolderPath,
                  knowledgeBase: { id: knowledgeBase.id, name: knowledgeBase.name, type: 'knowledgeBase' },
                  documentFolderPath: folderPath,
                  document: { id: document.id, name: document.file_name || '', type: 'document' },
                },
              },
            })}
          >
            {text}
          </span>
        );
      },
    },
    {
      title: t('knowledgeBase.status'),
      dataIndex: 'progress',
      key: 'progress',
      width: 160,
      render: (value: string | number, record: KnowledgeBaseDocumentData) => (
        <StatusTag
          status={record.run === 0 && typeof value === 'number' && value < 0
            ? 'error' : value === 1 ? 'success' : value === 0 ? 'warning' : 'default'}
          text={record.run === 0 && typeof value === 'number' && value < 0
            ? t('knowledgeBase.failed') : value === 1 ? t('knowledgeBase.completed')
              : value === 0 ? t('knowledgeBase.pending') : t('knowledgeBase.processing')}
        />
      ),
    },
    {
      title: t('knowledgeBase.processMsg'),
      dataIndex: 'progress_msg',
      key: 'progress_msg',
      width: 320,
      render: (value: string) => {
        if (!value) return '-';
        const formattedText = value.replace(/\\n/g, '\n');
        return (
          <Tooltip
            placement="topLeft"
            title={<pre className="rb:m-0 rb:max-h-50 rb:overflow-y-auto rb:whitespace-pre-wrap">{formattedText}</pre>}
          >
            <div className="rb:max-w-80 rb:line-clamp-2 rb:wrap-break-word rb:whitespace-pre-wrap rb:leading-normal">
              {formattedText}
            </div>
          </Tooltip>
        );
      },
    },
    { title: t('knowledgeBase.processingMode'), dataIndex: 'parser_id', key: 'parser_id', width: 100 },
    { title: t('knowledgeBase.dataSize'), dataIndex: 'file_size', key: 'file_size' },
    {
      title: t('knowledgeBase.createUpdateTime'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value: string) => <span>{formatDateTime(value, 'YYYY-MM-DD HH:mm:ss')}</span>,
    },
    { title: 'ID', dataIndex: 'id', key: 'id' },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      width: 100,
      render: (_, record) => (
        <Space size="middle">
          <Dropdown menu={{ items: getOptMenuItems(record as KnowledgeBaseListItem) }} trigger={['click']}>
            <MoreOutlined className="rb:text-base rb:font-semibold" />
          </Dropdown>
        </Space>
      ),
    },
  ];

  const createItems: MenuProps['items'] = [
    {
      key: 'folder',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/folder.png')]" />,
      label: t('knowledgeBase.folder'),
      onClick: () => createFolderModalRef.current?.handleOpen({
        kb_id: knowledgeBase.id,
        parent_id: folder.parent_id ?? knowledgeBase.id,
      }),
    },
    {
      key: 'dataset',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/text.png')]" />,
      label: <span>{t('knowledgeBase.createA')} {t('knowledgeBase.dataset')}</span>,
      onClick: () => datasetModalRef.current?.handleOpen(
        knowledgeBase.id,
        folder.parent_id ?? knowledgeBase.id,
        folderPath,
        knowledgeBaseFolderPath,
      ),
    },
  ];

  const isSyncKnowledgeBase = knowledgeBase.type === 'Web' || knowledgeBase.type === 'Third-party';

  return (
    <>
      <Flex vertical gap={16} className="rb:h-full rb:min-w-0 rb:flex-1 rb:pt-3! rb:pr-3!">
        <Flex align="center" justify="space-between">
          <Flex align="center" gap={12}>
            <DirectoryToggle
              directoryCollapsed={directoryCollapsed}
              onToggleDirectory={onToggleDirectory}
            />
          </Flex>
          <Flex align="center" justify="flex-end" gap={12} className="rb:flex-1">
            <SearchInput
              placeholder={t('knowledgeBase.search')}
              variant="outlined"
              onSearch={(value) => setQuery((previous) => ({ ...previous, keywords: value }))}
            />
            {isSyncKnowledgeBase ? (
              <Button type="primary" loading={isSyncing} onClick={isSyncing ? stopSyncing : handleSync}>
                {isSyncing ? t('knowledgeBase.syncing') : t('knowledgeBase.syncNow')}
              </Button>
            ) : (
              <Dropdown menu={{ items: createItems }} trigger={['click']}>
                <Button type="primary">+ {t('knowledgeBase.createImport')}</Button>
              </Dropdown>
            )}
          </Flex>
        </Flex>

        <div className="rb:flex-1 rb:overflow-hidden">
          <Table
            ref={tableRef}
            apiUrl={apiUrl}
            apiParams={query}
            columns={columns}
            rowKey="id"
            scrollX={1500}
            fillHeight
          />
        </div>
      </Flex>

      <PrivateKnowledgeBaseDialogs
        createContentModalRef={createContentModalRef}
        createFolderModalRef={createFolderModalRef}
        createImageDatasetRef={createImageDatasetRef}
        datasetModalRef={datasetModalRef}
        onRefreshDirectory={onRefreshDirectory}
        onRefreshTable={handleRefreshTable}
      />
    </>
  );
});

export default PrivateFileWorkspace;
