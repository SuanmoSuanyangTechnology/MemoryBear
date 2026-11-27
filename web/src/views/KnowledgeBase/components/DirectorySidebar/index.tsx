/**
 * Left directory sidebar of the knowledge base management page.
 * The directory tree is lazily loaded per level: expanding a folder fetches its direct children.
 * Contains: directory title, folder search, all knowledge bases entry, create folder, shared space, directory tree.
 */
import { useEffect, useState } from 'react';
import { Button, Flex, Tree } from 'antd';
import type { DataNode, EventDataNode, TreeProps } from 'antd/es/tree';
import {
  PlusOutlined,
  ShareAltOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';

import { getKnowledgeBaseFolderTreeV1 } from '@/api/knowledgeBaseV1';
import SearchInput from '@/components/SearchInput';
import MoreDropdown from '@/components/MoreDropdown';
import switcherIcon from '@/assets/images/knowledgeBase/switcher.png';
import type { V1FolderTreeNode } from '@/views/KnowledgeBase/typesV1';

import './index.css';

interface DirectorySidebarProps {
  folders: V1FolderTreeNode[];
  selectedFolderId?: string;
  sharedMode: boolean;
  onSelectAll: () => void;
  onSelectFolder: (folder: V1FolderTreeNode) => void;
  onNewFolder: () => void;
  onSharedSpaces: () => void;
  onAddFolder: (folder: V1FolderTreeNode) => void;
  onEditFolder: (folder: V1FolderTreeNode) => void;
  onDeleteFolder: (folder: V1FolderTreeNode) => void;
}

interface FolderDataNode extends DataNode {
  key: string;
  folder: V1FolderTreeNode;
  level: number;
  isLeaf?: boolean;
  children?: FolderDataNode[];
}

/** Build directory nodes (level starts at 0); in search mode the returned children are expanded recursively, while normal browsing is lazily loaded per level. */
const buildNodes = (folders: V1FolderTreeNode[], level = 0): FolderDataNode[] =>
  folders.map((folder) => ({
    key: folder.id,
    folder,
    level,
    isLeaf: folder.children ? folder.children.length === 0 : !folder.has_children,
    children: folder.children ? buildNodes(folder.children, level + 1) : undefined,
  }));

/** Recursively update the children of the given node. */
const updateTreeData = (
  nodes: FolderDataNode[],
  key: string,
  children: FolderDataNode[],
): FolderDataNode[] =>
  nodes.map((node) => {
    if (node.key === key) {
      return {
        ...node,
        children,
        isLeaf: children.length === 0,
      };
    }
    if (node.children?.length) {
      return { ...node, children: updateTreeData(node.children, key, children) };
    }
    return node;
  });

/** Collect the keys of loaded and expandable nodes (with children), used to expand all loaded levels when searching. */
const collectLoadedParentKeys = (nodes: FolderDataNode[]): string[] =>
  nodes.flatMap((node) =>
    node.children?.length ? [node.key, ...collectLoadedParentKeys(node.children)] : [],
  );

const DirectorySidebar = ({
  folders,
  selectedFolderId,
  sharedMode,
  onSelectAll,
  onSelectFolder,
  onNewFolder,
  onSharedSpaces,
  onAddFolder,
  onEditFolder,
  onDeleteFolder,
}: DirectorySidebarProps) => {
  const { t } = useTranslation();
  const [keyword, setKeyword] = useState('');
  const [treeData, setTreeData] = useState<FolderDataNode[]>(() => buildNodes(folders));
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);

  // Rebuild the tree when the root-level directories change (refresh / create / delete).
  useEffect(() => {
    setTreeData(buildNodes(folders));
    setExpandedKeys([]);
  }, [folders]);

  // Lazy loading: request direct children when expanding a directory.
  const handleLoadData = async (node: EventDataNode<FolderDataNode>) => {
    const dataNode = node as FolderDataNode;
    console.log('handleLoadData', node)
    if (dataNode.children !== undefined) return;
    try {
      const res = await getKnowledgeBaseFolderTreeV1({ parent_id: dataNode.key });
      const children = buildNodes(res.folders ?? [], dataNode.level + 1);
      setTreeData((prev) => updateTreeData(prev, dataNode.key, children));
    } catch {
      // Mark as a leaf node on load failure to avoid repeated requests.
      setTreeData((prev) => updateTreeData(prev, dataNode.key, []));
    }
  };

  // Search: a non-empty keyword uses the backend search (returns a recursively pruned tree); clearing it restores root-level lazy loading.
  const handleSearch = (value: string) => {
    const trimmed = value.trim();
    setKeyword(value);
    getKnowledgeBaseFolderTreeV1(trimmed ? { keywords: trimmed } : undefined)
      .then((res) => {
        setTreeData(buildNodes(res.folders ?? []));
        if (!trimmed) setExpandedKeys([]);
      })
      .catch(() => {});
  };

  const handleSelect: TreeProps['onSelect'] = (_keys, info) => {
    const folder = (info.node as unknown as FolderDataNode).folder;
    if (folder) onSelectFolder(folder);
  };

  const handleExpand: TreeProps['onExpand'] = (keys) => {
    console.log('handleExpand', keys)
    setExpandedKeys(keys)
  };

  const trimmedKeyword = keyword.trim().toLowerCase();
  // Expand all loaded nodes when searching; otherwise follow the user's manual expansion state.
  const effectiveExpandedKeys = trimmedKeyword
    ? collectLoadedParentKeys(treeData)
    : expandedKeys;

  const renderNodeTitle = (node: DataNode) => {
    const dataNode = node as FolderDataNode;
    return (
      <Flex align="center" gap={4} className="rb:w-full! rb:min-w-0">
        <span className="rb:size-4 rb:shrink-0 rb:bg-cover rb:bg-[url('@/assets/images/knowledgeBase/folder.svg')]" />
        <span className="rb:flex-1 rb:truncate rb:text-[13px]">{dataNode.folder.name}</span>
        <Flex align="center" gap={4} className="folder-actions rb:shrink-0">
          {dataNode.level < 2 && (
            <Button
              type="text"
              size="small"
              aria-label={t('knowledgeBase.addDirectory')}
              title={t('knowledgeBase.addDirectory')}
              icon={<PlusOutlined />}
              onClick={(event) => {
                event.stopPropagation();
                onAddFolder(dataNode.folder);
              }}
            />
          )}
          <MoreDropdown
            iconClassName="rb:size-6!"
            items={[
              {
                key: 'edit',
                icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />,
                label: t('common.edit'),
                onClick: () => onEditFolder(dataNode.folder),
              },
              {
                key: 'delete',
                danger: true,
                icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                label: t('common.delete'),
                onClick: () => onDeleteFolder(dataNode.folder),
              },
            ]}
          />
        </Flex>
      </Flex>
    );
  };

  return (
    <Flex
      vertical
      aria-label={t('knowledgeBase.directory')}
      className="rb:w-60 rb:shrink-0 rb:h-full! rb:overflow-hidden! rb:rounded-lg! rb:border rb:border-gray-200 rb:bg-white"
    >
      <div className="rb:px-4! rb:pt-4! rb:pb-2! rb:text-sm rb:font-medium rb:text-gray-600">
        {t('knowledgeBase.directory')}
      </div>
      <div className="rb:px-3! rb:pb-2!">
        <SearchInput
          className="rb:w-full!"
          placeholder={t('knowledgeBase.searchFolders')}
          onSearch={handleSearch}
          variant="outlined"
        />
      </div>
      <Flex vertical gap={4} className="rb:overflow-y-auto! rb:px-2! rb:py-1!">
        <Flex
          gap={8}
          align="center"
          onClick={onSelectAll}
          className={clsx(
            'rb:rounded-md rb:px-2! rb:cursor-pointer rb:h-9!',
            {
              'rb:bg-[#F2F4F7]': !selectedFolderId && !sharedMode,
              'rb:hover:bg-[#F7F9FC]': selectedFolderId || sharedMode,
            },
          )}
        >
          <UnorderedListOutlined className="rb:text-gray-600" />
          <span className="rb:flex-1 rb:text-[13px]">{t('knowledgeBase.allKnowledgeBases')}</span>
          <Button
            type="text"
            size="small"
            icon={<PlusOutlined />}
            aria-label={t('knowledgeBase.newFolder')}
            onClick={(event) => {
              event.stopPropagation();
              onNewFolder();
            }}
          />
        </Flex>
        {treeData.length > 0 && (
          <Tree
            className="directory-tree"
            blockNode
            showIcon={false}
            treeData={treeData}
            loadData={handleLoadData}
            onSelect={handleSelect}
            onExpand={handleExpand}
            expandedKeys={effectiveExpandedKeys}
            selectedKeys={selectedFolderId ? [selectedFolderId] : []}
            switcherIcon={<img src={switcherIcon} alt="" className="rb:size-4" />}
            titleRender={renderNodeTitle}
          />
        )}
      </Flex>
      <Flex
        gap={8}
        align="center"
        onClick={onSharedSpaces}
        className={clsx(
          'rb:rounded-md rb:px-2! rb:cursor-pointer rb:mx-2! rb:mt-1! rb:h-9!',
          sharedMode ? 'rb:bg-[#F2F4F7]' : 'rb:hover:bg-[#F7F9FC]',
        )}
      >
        <ShareAltOutlined className="rb:text-gray-600" />
        <span className="rb:text-[13px]">{t('knowledgeBase.sharedSpaces')}</span>
      </Flex>
    </Flex>
  );
};

export default DirectorySidebar;
