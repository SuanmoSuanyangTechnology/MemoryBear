import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react';
import { MoreOutlined } from '@ant-design/icons';
import { App, Button, Dropdown, Flex, Space, Tooltip, Upload } from 'antd';
import type { MenuProps, UploadProps } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { UploadRef } from 'antd/es/upload/Upload';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';

import {
  createSync,
  deleteDocument,
  downloadFile,
} from '@/api/knowledgeBase';
import { getDocumentListV1Url, rechunkDocumentV1 } from '@/api/knowledgeBaseDocument';
import SearchInput from '@/components/SearchInput';
import StatusTag from '@/components/StatusTag';
import Table, { type TableRef } from '@/components/Table';
import { formatDateTime, formatFileSize } from '@/utils/format';
import { getFileIconClassByExtension } from '@/utils/fileIcon';
import type { BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import type {
  CreateFolderModalRef,
  FolderFormData,
} from '@/views/KnowledgeBase/types';
import type { V1KnowledgeListItem } from '@/views/KnowledgeBase/typesV1'
import DirectoryToggle from '../DirectoryToggle';
import RelatedWikiDrawer, { type RelatedWikiDrawerRef } from './RelatedWikiDrawer';
import UploadFileModalV1, { type UploadFileModalV1Ref } from '../../UploadFileModalV1';
import type { V1DocumentListQuery, V1DocumentsItem } from '../../../typesDocumentV1'

export interface PrivateFileWorkspaceRef {
  refreshTable: () => void;
  openCreateFolder: (parentId: string, parentName?: string, parentPath?: string[]) => void;
}

/** Remove the extension only when it actually sits at the end of the file name, so matching substrings elsewhere are kept */
const stripFileNameExtension = (fileName: string, fileExt?: string): string => {
  if (!fileExt) return fileName;
  const ext = fileExt.startsWith('.') ? fileExt : `.${fileExt}`;
  return fileName.toLowerCase().endsWith(ext.toLowerCase())
    ? fileName.slice(0, -ext.length)
    : fileName;
};

interface PrivateFileWorkspaceProps {
  knowledgeBase: V1KnowledgeListItem;
  knowledgeBaseId: string;
  parentId: string;
  folder: FolderFormData;
  folderPath: BreadcrumbItem[];
  knowledgeBaseFolderPath: BreadcrumbItem[];
  query: Record<string, unknown>;
  setQuery: Dispatch<SetStateAction<Record<string, unknown>>>;
  directoryCollapsed: boolean;
  onToggleDirectory: () => void;
  onRefreshKnowledgeBase: () => Promise<V1KnowledgeListItem | null>;
  onRefreshDirectory: () => Promise<void>;
  onOpenWikiPage: (pageId: string) => void;
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
  onOpenWikiPage,
}, ref) => {
  const { t } = useTranslation();
  const { modal, message: messageApi } = App.useApp();
  const navigate = useNavigate();
  const tableRef = useRef<TableRef>(null);
  const relatedWikiDrawerRef = useRef<RelatedWikiDrawerRef>(null);
  const createFolderModalRef = useRef<CreateFolderModalRef>(null);
  const uploadFileModalRef = useRef<UploadFileModalV1Ref>(null);
  const uploadRef = useRef<UploadRef>(null);
  const uploadContextRef = useRef({ knowledgeBaseId: '', parentId: '' });
  const uploadModeRef = useRef<'open' | 'append'>('open');
  const syncIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const syncTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const syncStartTimeRef = useRef<number | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const apiUrl = getDocumentListV1Url(knowledgeBaseId);
  const tableQuery = useMemo(() => ({ ...query, include_wiki_count: true }), [query]);

  const refreshTable = useCallback(() => tableRef.current?.loadData(), []);

  useEffect(() => {
    refreshTable();
  }, [apiUrl, refreshTable]);

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

  useImperativeHandle(ref, () => ({
    refreshTable,
    openCreateFolder: (targetParentId, parentName, parentPath) => createFolderModalRef.current?.handleOpen({
      kb_id: knowledgeBase.id,
      parent_id: targetParentId,
      parent_name: parentName,
    }, undefined, parentPath),
  }));

  // File selection is handled by antd Upload: beforeUpload intercepts the upload and passes the whole batch to the modal.
  const handleBeforeUpload: NonNullable<UploadProps['beforeUpload']> = (file, fileList) => {
    // When multiple is selected, beforeUpload is called once per file; process the batch only on the first file.
    if (file !== fileList[0]) return false;

    const selected: File[] = fileList.map((item) => item as File);
    if (selected.length === 0) return false;

    if (uploadModeRef.current === 'open') {
      const { knowledgeBaseId: kbId, parentId } = uploadContextRef.current;
      uploadFileModalRef.current?.handleOpen(selected, kbId, parentId, knowledgeBase.name);
    } else {
      uploadFileModalRef.current?.handleAddFiles(selected);
    }
    return false;
  };

  const openFilePicker = (mode: 'open' | 'append', parentId?: string) => {
    uploadModeRef.current = mode;
    uploadContextRef.current = { knowledgeBaseId: knowledgeBase.id, parentId: parentId ?? knowledgeBase.id };
    uploadRef.current?.nativeElement?.querySelector('input')?.click();
  };

  const handleRechunking = (item: V1DocumentsItem) => {
    modal.confirm({
      title: t('knowledgeBase.reparseTitle'),
      content: t('knowledgeBase.reparseConfirmContent', { name: item.file_name }),
      onOk: () => {
        rechunkDocumentV1(item.id)
          .then(() => {
            messageApi.success(t('common.operateSuccess'));
            refreshTable();
          })
      },
    })
  };

  const handleDelete = (item: V1DocumentsItem) => {
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

  const getOptMenuItems = (row: V1DocumentsItem): MenuProps['items'] => {
    const options: MenuProps['items'] = [
      {
        key: 'download',
        label: t('knowledgeBase.download'),
        onClick: () => void downloadFile(row.file_id ?? '', row.file_name ?? ''),
      },
      {
        key: 'delete',
        label: t('knowledgeBase.delete'),
        onClick: () => handleDelete(row),
      },
    ];
    if (row.processing_configuration?.parsing.document_kind === 'qa') return options;
    return [{
      key: 'rechunking',
      label: t('knowledgeBase.reparseTitle'),
      onClick: () => handleRechunking(row),
    }, ...options];
  };

  const columns: ColumnsType<V1DocumentsItem> = [
    {
      title: t('knowledgeBase.name'),
      dataIndex: 'file_name',
      key: 'file_name',
      render: (text, record) => {
        return (
          <Flex align="center" gap={8} className="rb:min-w-0">
            <div
              className={clsx(
                'rb:size-5 rb:shrink-0 rb:bg-cover',
                getFileIconClassByExtension(record.file_ext, record.file_name),
              )}
            />
            <span
              className="rb:min-w-0 rb:cursor-pointer rb:truncate rb:font-medium rb:text-gray-900 rb:hover:underline"
              onClick={() => record.id && navigate(`/knowledge-base/${knowledgeBaseId}/DocumentDetails`, {
                state: {
                  documentId: record.id,
                  parentId: parentId || knowledgeBaseId,
                  breadcrumbPath: {
                    knowledgeBaseFolderPath,
                    knowledgeBase: { id: knowledgeBase.id, name: knowledgeBase.name, type: 'knowledgeBase' },
                    documentFolderPath: folderPath,
                    document: { id: record.id, name: record.file_name || '', type: 'record' },
                  },
                },
              })}
            >
              {stripFileNameExtension(record.file_name || text, record.file_ext)}
            </span>
          </Flex>
        );
      },
    },
    { title: t('knowledgeBase.dataSize'), dataIndex: 'file_size', key: 'file_size', render: (file_size) => formatFileSize(file_size) },
    {
      title: t('knowledgeBase.uploadTime'),
      dataIndex: 'created_at',
      key: 'created_at',
      render: (value) => <span>{formatDateTime(value)}</span>,
    },
    {
      title: t('knowledgeBase.wikiColumn'),
      key: 'wiki',
      width: 120,
      render: (_, record) => {
        // Match the prototype: only "Organized · N" is a clickable link; other states are plain gray text
        const count = record.wiki_count ?? 0;
        if (count > 0) {
          return (
            <button
              type="button"
              onClick={() => relatedWikiDrawerRef.current?.handleOpen(record)}
              className="rb:p-0! rb:border-0 rb:bg-transparent rb:cursor-pointer rb:text-primary-600 rb:whitespace-nowrap rb:hover:underline"
            >
              {t('knowledgeBase.documentWiki.ready', { count })}
            </button>
          );
        }
        const processing = typeof record.progress === 'number'
          && record.progress > 0 && record.progress < 1;
        return (
          <span className="rb:text-gray-600">
            {processing ? t('knowledgeBase.documentWiki.processing') : t('knowledgeBase.documentWiki.pending')}
          </span>
        );
      },
    },
    {
      title: t('knowledgeBase.status'),
      dataIndex: 'progress',
      key: 'progress',
      width: 120,
      render: (value, record) => (
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
      render: (value) => {
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
    { title: 'ID', dataIndex: 'id', key: 'id' },
    {
      title: t('common.operation'),
      key: 'action',
      fixed: 'right',
      width: 100,
      render: (_, record) => (
        <Space size="middle">
          <Dropdown menu={{ items: getOptMenuItems(record) }} trigger={['click']}>
            <MoreOutlined className="rb:text-base rb:font-semibold" />
          </Dropdown>
        </Space>
      ),
    },
  ];

  const createItems: MenuProps['items'] = [
    {
      key: 'uploadFile',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/image.png')]" />,
      label: t('common.uploadFile'),
      onClick: () => openFilePicker('open', folder.parent_id ?? knowledgeBase.id),
    },
    {
      key: 'folder',
      icon: <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/folder.svg')]" />,
      label: <span>{t('knowledgeBase.createA')}{t('knowledgeBase.folder')}</span>,
      onClick: () => createFolderModalRef.current?.handleOpen({
        kb_id: knowledgeBase.id,
        parent_id: folder.parent_id ?? knowledgeBase.id,
        parent_name: folderPath.find((item) => item.id === folder.parent_id)?.name,
      }, undefined, folderPath.map((item) => item.name)),
    },
  ];

  const isSyncKnowledgeBase = typeof knowledgeBase?.processing_configuration?.sources?.website === 'object' || typeof knowledgeBase?.processing_configuration?.sources?.third_party === 'object';

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
              <>
                <Dropdown menu={{ items: createItems }} trigger={['click']}>
                  <Button type="primary">+ {t('knowledgeBase.createImport')}</Button>
                </Dropdown>
                <Upload
                  ref={uploadRef}
                  multiple
                  showUploadList={false}
                  beforeUpload={handleBeforeUpload}
                  className="rb:hidden"
                >
                  <span />
                </Upload>
              </>
            )}
          </Flex>
        </Flex>

        <div className="rb:flex-1 rb:overflow-hidden">
          <Table<V1DocumentsItem, V1DocumentListQuery>
            ref={tableRef}
            apiUrl={apiUrl}
            apiParams={tableQuery as V1DocumentListQuery}
            columns={columns}
            rowKey="id"
            scrollX={1500}
            fillHeight
          />
        </div>
      </Flex>
      <RelatedWikiDrawer
        ref={relatedWikiDrawerRef}
        knowledgeBaseId={knowledgeBaseId}
        onOpenWikiPage={onOpenWikiPage}
      />
      <UploadFileModalV1
        ref={uploadFileModalRef}
        refresh={handleRefreshTable}
        onRequestFiles={() => openFilePicker('append')}
      />
    </>
  );
});

export default PrivateFileWorkspace;
