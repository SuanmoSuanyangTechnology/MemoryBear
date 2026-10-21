import { type FC } from 'react';
import { Flex, Spin } from 'antd';
import { useTranslation } from 'react-i18next';

import Empty from '@/components/Empty';
import { usePrivateKnowledgeBaseActions } from '../components/Private/hooks/usePrivateKnowledgeBaseActions';
import MetadataDrawer from '../components/MetadataDrawer';
import RecallTestDrawer from '../components/RecallTestDrawer';
import ShareModal from '../components/ShareModal';
import CreateModal from '../components/CreateModal';
import NoData from '@/assets/images/knowledgeBase/noData.png';
import FolderTree from '../components/Private/PrivateFiles/FolderTree';
import PrivateKnowledgeBaseWorkspace from '../components/Private/PrivateKnowledgeBaseWorkspace';
import PrivateFileWorkspace from '../components/Private/PrivateFiles/PrivateFileWorkspace';
import { usePrivateKnowledgeBasePage } from '../components/Private/hooks/usePrivateKnowledgeBasePage';
import './Private.css';

const Private: FC = () => {
  const { t } = useTranslation();
  const {
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
    handleFolderPathChange,
    handleFolderSelect,
    refreshDirectoryTree,
    setQuery,
    directoryCollapsed,
    onToggleDirectory,
  } = usePrivateKnowledgeBasePage();
  const actions = usePrivateKnowledgeBaseActions(knowledgeBase, fetchKnowledgeBaseDetail);


  if (loading) {
    return (
      <Flex align="center" justify="center" className="rb:h-full!">
        <Spin size="large" />
      </Flex>
    );
  }

  if (!knowledgeBase) {
    return (
      <Empty
        url={NoData}
        subTitle={t('knowledgeBase.knowledgeBaseNotFound')}
        size={[260, 180]}
        className="rb:h-full!"
      />
    );
  }
  console.log('knowledgeBase', knowledgeBase)
  return (
    <>
      <PrivateKnowledgeBaseWorkspace
        knowledgeBase={knowledgeBase}
        knowledgeBaseId={knowledgeBaseId}
        breadcrumbPath={{
          knowledgeBaseFolderPath,
          knowledgeBase: { id: knowledgeBase.id, name: knowledgeBase.name, type: 'knowledgeBase' },
          documentFolderPath: folderPath,
        }}
        directoryCollapsed={directoryCollapsed}
        onToggleDirectory={onToggleDirectory}
        onCopyKnowledgeBaseId={actions.copyKnowledgeBaseId}
        onKnowledgeBaseStatusChange={actions.changeKnowledgeBaseStatus}
        onShareKnowledgeBase={actions.shareKnowledgeBase}
        onOpenRecallTest={actions.openRecallTest}
        onOpenMetadata={actions.openMetadata}
        onOpenKnowledgeBaseSettings={actions.openKnowledgeBaseSettings}
        onBatchDownloadKnowledgeBase={actions.batchDownloadKnowledgeBase}
        onExportKnowledgeBaseQa={actions.exportKnowledgeBaseQa}
        fileDirectory={(
          <FolderTree
            embedded
            multiple
            className="customTree"
            style={{ background: 'transparent' }}
            onSelect={handleFolderSelect}
            knowledgeBaseId={knowledgeBaseId}
            refreshKey={folderTreeRefreshKey}
            onCreateFolder={(targetParentId, parentName, parentPath) => fileWorkspaceRef.current?.openCreateFolder(targetParentId, parentName, parentPath)}
            onFolderPathChange={handleFolderPathChange}
            selectedKeys={selectedKeys}
            autoExpandPath={autoExpandPath}
            rootTitle={t('knowledgeBase.allFiles')}
            rootSelected={selectedKeys.length === 0}
            onRootSelect={() => {
              handleFolderSelect([]);
              handleFolderPathChange([]);
            }}
          />
        )}
        fileContent={(
          <PrivateFileWorkspace
            ref={fileWorkspaceRef}
            knowledgeBase={knowledgeBase}
            knowledgeBaseId={knowledgeBaseId}
            parentId={parentId}
            folder={folder}
            folderPath={folderPath}
            knowledgeBaseFolderPath={knowledgeBaseFolderPath}
            query={query}
            setQuery={setQuery}
            directoryCollapsed={directoryCollapsed}
            onToggleDirectory={onToggleDirectory}
            onRefreshKnowledgeBase={fetchKnowledgeBaseDetail}
            onRefreshDirectory={refreshDirectoryTree}
          />
        )}
      />
      <MetadataDrawer ref={actions.metadataDrawerRef} />
      <RecallTestDrawer ref={actions.recallTestDrawerRef} />
      <ShareModal ref={actions.shareModalRef} handleShare={() => undefined} />
      <CreateModal
        ref={actions.createModalRef}
        refreshTable={() => {
          void fetchKnowledgeBaseDetail();
        }}
      />
    </>
  );
};

export default Private;
