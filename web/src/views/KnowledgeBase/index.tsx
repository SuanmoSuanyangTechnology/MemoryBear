/**
 * Knowledge base management page (replicates the prototype #/knowledge-base)
 * Uses the v1 API: list / directory tree / view detail / create knowledge base or directory / partial update;
 * delete and copy reuse the existing API (the v1 docs do not provide the corresponding capabilities).
 * The card list uses PageScrollList for infinite scroll pagination.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type FC } from 'react';
import { App, Button, Flex } from 'antd';
import type { MenuProps } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { deleteKnowledgeBase } from '@/api/knowledgeBase';
import {
  getKnowledgeBaseDetailV1,
  getKnowledgeBaseFolderTreeV1,
  getKnowledgeBaseListV1Url,
} from '@/api/knowledgeBaseV1';
import SearchInput from '@/components/SearchInput';
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList';
import type {
  V1FolderTreeNode,
  V1KnowledgeListItem,
  V1ListQuery,
} from '@/views/KnowledgeBase/typesV1';
import { menuItems } from './constants';
import CreateFolderV1Modal, { type CreateFolderV1ModalRef } from './components/CreateFolderV1Modal';
import CreateKnowledgeBaseModal, { type CreateKnowledgeBaseModalRef } from './components/CreateKnowledgeBaseModal';
import CopyModal, { type CopyModalRef } from './components/CopyModal';
import DirectorySidebar from './components/DirectorySidebar';
import KbV1Card from './components/KbV1Card';

const KnowledgeBaseManagement: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { modal, message: messageApi } = App.useApp();

  const [tree, setTree] = useState<V1FolderTreeNode[]>([]);
  const [selectedFolder, setSelectedFolder] = useState<V1FolderTreeNode | null>(null);
  const [sharedMode, setSharedMode] = useState(false);
  const [keyword, setKeyword] = useState<string | undefined>();
  const [total, setTotal] = useState(0);
  const [loadedItems, setLoadedItems] = useState<V1KnowledgeListItem[]>([]);

  const folderModalRef = useRef<CreateFolderV1ModalRef>(null);
  const kbModalRef = useRef<CreateKnowledgeBaseModalRef>(null);
  const copyModalRef = useRef<CopyModalRef>(null);
  const pageScrollListRef = useRef<PageScrollListRef>(null);

  const fetchTree = useCallback(() => {
    getKnowledgeBaseFolderTreeV1()
      .then((res) => setTree(res.folders ?? []))
      .catch(() => setTree([]));
  }, []);

  useEffect(() => {
    fetchTree();
  }, [fetchTree]);

  const refresh = () => {
    pageScrollListRef.current?.refresh();
    fetchTree();
  };

  const listQuery: V1ListQuery = useMemo(() => ({
    parent_id: selectedFolder?.id,
    orderby: 'created_at',
    desc: true,
    keywords: keyword?.trim() || undefined,
    is_shared: sharedMode,
    exclude_types: 'Folder',
  }), [selectedFolder?.id, keyword, sharedMode]);

  const handleSearch = (value?: string) => {
    setKeyword(value?.trim() || undefined);
  };

  const handleSelectAll = () => {
    setSelectedFolder(null);
    setSharedMode(false);
  };

  const handleSelectFolder = (folder: V1FolderTreeNode) => {
    setSelectedFolder(folder);
    setSharedMode(false);
  };

  const handleSharedSpaces = () => {
    setSharedMode(true);
    setSelectedFolder(null);
  };

  const handleAddFolder = (folder: V1FolderTreeNode) => {
    folderModalRef.current?.handleOpen(folder.id);
  };

  const handleEditFolder = (folder: V1FolderTreeNode) => {
    folderModalRef.current?.handleEdit(folder);
  };

  const handleDeleteFolder = (folder: V1FolderTreeNode) => {
    modal.confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: folder.name }),
      onOk: () => deleteKnowledgeBase(folder.id).then((res) => {
        if (res) {
          messageApi.success(t('common.deleteSuccess'));
          if (selectedFolder?.id === folder.id) setSelectedFolder(null);
          refresh();
        }
      }),
    });
  };

  const handleToDetail = (item: V1KnowledgeListItem) => {

    const targetPath = item.is_shared 
      ? `/knowledge-base/${item.id}/share`
      : `/knowledge-base/${item.id}/private`;
    navigate(targetPath, {
      state: {
        fromKnowledgeBaseList: true,
        knowledgeBaseFolderPath: item.folder_path,
        parentId: listQuery.parent_id,
        timestamp: Date.now(), // Add a timestamp to ensure the navigation state differs each time
      },
    });
  };

  const handleEdit = (item: V1KnowledgeListItem) => {
    getKnowledgeBaseDetailV1(item.id).then((detail) => {
      kbModalRef.current?.handleOpen(detail);
    });
  };

  const handleCopy = (item: V1KnowledgeListItem) => {
    copyModalRef.current?.handleOpen(item as never);
  };

  const handleDelete = (item: V1KnowledgeListItem) => {
    modal.confirm({
      title: t('common.deleteWarning'),
      content: t('common.deleteWarningContent', { content: item.name }),
      onOk: () => {
        deleteKnowledgeBase(item.id).then(() => {
          messageApi.success(t('common.deleteSuccess'));
          refresh();
        })
      },
    });
  };

  const getMenuItems = (item: V1KnowledgeListItem): MenuProps['items'] => {
    return (item.is_shared ? menuItems.filter(menu => menu.key === 'delete') : menuItems).map((menu) => ({
      ...menu,
      label: t(`knowledgeBase.${menu.key}`),
      icon: <div className={clsx('rb:size-4 rb:bg-cover', menu.icon)} />,
      onClick: () => {
        if (menu.key === 'edit') handleEdit(item);
        if (menu.key === 'copy') handleCopy(item);
        if (menu.key === 'delete') handleDelete(item);
      },
    }));
  }

  const locationTitle = selectedFolder?.name ?? (sharedMode ? t('knowledgeBase.sharedToMeKnowledgeBases') : t('knowledgeBase.allKnowledgeBases'));
  const sharedCount = loadedItems.filter((item) => item.permission_id === 'Share').length;

  return (
    <Flex vertical gap={16} className="rb:h-full! rb:min-h-0! rb:overflow-hidden!">

      <Flex gap={16} className="rb:flex-1! rb:min-h-0!">
        <DirectorySidebar
          folders={tree}
          selectedFolderId={selectedFolder?.id}
          sharedMode={sharedMode}
          onSelectAll={handleSelectAll}
          onSelectFolder={handleSelectFolder}
          onNewFolder={() => folderModalRef.current?.handleOpen(selectedFolder?.id)}
          onSharedSpaces={handleSharedSpaces}
          onAddFolder={handleAddFolder}
          onEditFolder={handleEditFolder}
          onDeleteFolder={handleDeleteFolder}
        />

        <Flex vertical gap={8} className="rb:flex-1! rb:min-w-0! rb:min-h-0! rb:overflow-hidden!">
          <Flex align="center" justify="space-between" className="rb:shrink-0!">
            <div>
              <h2 className="rb:m-0! rb:text-lg rb:font-semibold">{locationTitle}</h2>
              <p className="rb:mt-1! rb:text-[12px] rb:text-gray-500">
                {keyword
                  ? t('knowledgeBase.foundKnowledgeBases', { count: sharedMode ? sharedCount : total })
                  : sharedMode
                  ? t('knowledgeBase.sharedFromOtherSpaces', { count: sharedCount })
                  : selectedFolder
                  ? t('knowledgeBase.folderKnowledgeBaseCount', { count: total })
                  : t('knowledgeBase.spaceKnowledgeBaseCount', { count: total })
                }
              </p>
            </div>
            <Flex align="center" gap={8}>
              <SearchInput placeholder={t('knowledgeBase.searchKnowledgeBases')} onSearch={handleSearch} className="rb:w-72!" />
              {!sharedMode &&
                <Button type="primary" onClick={() => kbModalRef.current?.handleOpen(null, 'basic', selectedFolder?.id)}>
                  + {t('knowledgeBase.createModal.createTitle')}
                </Button>
              }
            </Flex>
          </Flex>

          <PageScrollList<V1KnowledgeListItem, V1ListQuery>
            ref={pageScrollListRef}
            url={getKnowledgeBaseListV1Url}
            query={listQuery}
            column={3}
            heightClass="rb:flex-1!"
            className="rb:flex-1!"
            onTotalChange={setTotal}
            onLoad={(res) => {
              const page = res?.page?.page ?? 1;
              const items: V1KnowledgeListItem[] = Array.isArray(res?.items) ? res.items : [];
              setLoadedItems((prev) => (page === 1 ? items : [...prev, ...items]));
            }}
            renderItem={(item) => {
              return (
                <KbV1Card key={item.id} item={item} onOpen={handleToDetail} menuItems={getMenuItems(item)} />
              )
            }}
          />
        </Flex>
      </Flex>

      <CreateFolderV1Modal ref={folderModalRef} refresh={refresh} />
      <CreateKnowledgeBaseModal ref={kbModalRef} refreshTable={refresh} />
      <CopyModal ref={copyModalRef} refresh={refresh} />
    </Flex>
  );
};

export default KnowledgeBaseManagement;
