import { useTranslation } from 'react-i18next';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { getKnowledgeBaseDetail } from '@/api/knowledgeBase';
import { useBreadcrumbManager, type BreadcrumbItem } from '@/hooks/useBreadcrumbManager';
import type { FolderFormData, KnowledgeBaseListItem } from '@/views/KnowledgeBase/types';

import type { PrivateFileWorkspaceRef } from '../PrivateFiles/PrivateFileWorkspace';

interface PrivateLocationState {
  refresh?: boolean;
  timestamp?: number;
  fromKnowledgeBaseList?: boolean;
  knowledgeBaseFolderPath?: BreadcrumbItem[];
  parentId?: string;
  navigateToDocumentFolder?: string;
  documentFolderPath?: BreadcrumbItem[];
  resetToRoot?: boolean;
}

const DEFAULT_QUERY: Record<string, unknown> = {
  orderby: 'created_at',
  desc: true,
};

export const usePrivateKnowledgeBasePage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { knowledgeBaseId = '' } = useParams<{ knowledgeBaseId: string }>();
  const { updateBreadcrumbs } = useBreadcrumbManager({ breadcrumbType: 'detail' });
  const fileWorkspaceRef = useRef<PrivateFileWorkspaceRef>(null);
  const [loading, setLoading] = useState(Boolean(knowledgeBaseId));
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBaseListItem | null>(null);
  const [parentId, setParentId] = useState<string>(knowledgeBaseId);
  const [folder, setFolder] = useState<FolderFormData>({
    kb_id: knowledgeBaseId,
    parent_id: knowledgeBaseId,
  });
  const [query, setQuery] = useState<Record<string, unknown>>(DEFAULT_QUERY);
  const [folderTreeRefreshKey, setFolderTreeRefreshKey] = useState(0);
  const [autoExpandPath, setAutoExpandPath] = useState<BreadcrumbItem[]>([]);
  const [folderPath, setFolderPath] = useState<BreadcrumbItem[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([]);
  const [knowledgeBaseFolderPath, setKnowledgeBaseFolderPath] = useState<BreadcrumbItem[]>(() => {
    const state = location.state as PrivateLocationState | null;
    return state?.knowledgeBaseFolderPath ?? [];
  });
  const [directoryCollapsed, setDirectoryCollapsed] = useState(false);

  const fetchKnowledgeBaseDetail = useCallback(async (id = knowledgeBaseId) => {
    if (!id) return null;
    setLoading(true);
    try {
      const response = await getKnowledgeBaseDetail(id);
      const listItem = response as unknown as KnowledgeBaseListItem;
      setKnowledgeBase(listItem);
      return listItem;
    } finally {
      setLoading(false);
    }
  }, [knowledgeBaseId]);

  useEffect(() => {
    void fetchKnowledgeBaseDetail();
  }, [fetchKnowledgeBaseDetail]);

  useEffect(() => {
    if (!knowledgeBaseId) return;
    updateBreadcrumbs({
      knowledgeBaseFolderPath,
      knowledgeBase: {
        id: knowledgeBase?.id ?? knowledgeBaseId,
        name: knowledgeBase?.name ?? t('common.loading'),
        type: 'knowledgeBase',
      },
      documentFolderPath: folderPath,
    });
  }, [folderPath, knowledgeBase, knowledgeBaseFolderPath, knowledgeBaseId, updateBreadcrumbs, t]);

  useEffect(() => {
    const state = location.state as PrivateLocationState | null;
    const timers: ReturnType<typeof setTimeout>[] = [];

    if (state?.refresh) fileWorkspaceRef.current?.refreshTable();

    if (state?.fromKnowledgeBaseList && state.knowledgeBaseFolderPath) {
      setKnowledgeBaseFolderPath(state.knowledgeBaseFolderPath);
    }

    if (state?.resetToRoot) {
      setParentId(knowledgeBaseId);
      setFolderPath([]);
      setSelectedKeys([]);
      setFolder({ kb_id: knowledgeBaseId, parent_id: knowledgeBaseId });
      setQuery({ ...DEFAULT_QUERY, _timestamp: Date.now() });
      setAutoExpandPath([]);
      timers.push(setTimeout(() => setFolderTreeRefreshKey((value) => value + 1), 100));
      timers.push(setTimeout(() => fileWorkspaceRef.current?.refreshTable(), 200));
    }

    if (state?.navigateToDocumentFolder && state.documentFolderPath) {
      const targetFolderId = state.navigateToDocumentFolder;
      setFolderPath(state.documentFolderPath);
      setParentId(targetFolderId);
      setFolder({ kb_id: knowledgeBaseId, parent_id: targetFolderId });
      setQuery((previous) => ({
        ...previous,
        parent_id: targetFolderId,
        _timestamp: Date.now(),
      }));
      setSelectedKeys([targetFolderId]);
      setAutoExpandPath(state.documentFolderPath);
      timers.push(setTimeout(() => fileWorkspaceRef.current?.refreshTable(), 100));
      timers.push(setTimeout(() => setAutoExpandPath([]), 2000));
    }

    if (state && Object.keys(state).length > 0) {
      navigate(location.pathname, { replace: true, state: {} });
    }

    return () => timers.forEach(clearTimeout);
  }, [knowledgeBaseId, location.pathname, location.state, navigate]);

  const handleFolderSelect = useCallback((keys: React.Key[]) => {
    if (!keys.length) {
      setParentId(knowledgeBaseId);
      setFolder({ kb_id: knowledgeBaseId, parent_id: knowledgeBaseId });
      setQuery({ ...DEFAULT_QUERY, _timestamp: Date.now() });
      setSelectedKeys([]);
      return;
    }

    const targetFolderId = String(keys[0]);
    setParentId(targetFolderId);
    setFolder((previous) => ({ ...previous, parent_id: targetFolderId }));
    setQuery((previous) => ({
      ...previous,
      parent_id: targetFolderId,
      _timestamp: Date.now(),
    }));
    setSelectedKeys(keys);
  }, [knowledgeBaseId]);

  const refreshDirectoryTree = useCallback(async () => {
    await fetchKnowledgeBaseDetail();
    await new Promise((resolve) => setTimeout(resolve, 300));
    setFolderTreeRefreshKey((value) => value + 1);
  }, [fetchKnowledgeBaseDetail]);

  const onToggleDirectory = () => {
    setDirectoryCollapsed((collapsed) => !collapsed)
  }

  return {
    autoExpandPath,
    fileWorkspaceRef,
    folder,
    folderPath,
    folderTreeRefreshKey,
    knowledgeBase,
    knowledgeBaseFolderPath,
    knowledgeBaseId,
    loading,
    parentId,
    query,
    selectedKeys,
    fetchKnowledgeBaseDetail,
    handleFolderPathChange: setFolderPath,
    handleFolderSelect,
    refreshDirectoryTree,
    setQuery,
    directoryCollapsed,
    onToggleDirectory,
  };
};
