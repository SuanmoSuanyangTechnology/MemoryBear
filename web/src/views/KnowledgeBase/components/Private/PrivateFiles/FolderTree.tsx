import { Trans } from 'react-i18next';
import { useMemo, useEffect, useState, type FC, useCallback } from 'react';
import type { CSSProperties, Key, ReactNode } from 'react';
import { Tree, Flex } from 'antd';
import type { DataNode, TreeProps } from 'antd/es/tree';
import clsx from 'clsx';

import switcherIcon from '@/assets/images/knowledgeBase/switcher.png';
import { getFolderList } from '@/api/knowledgeBase';

const { DirectoryTree } = Tree;
const ROOT_NODE_KEY = '__all_files__';
export interface TreeNodeData {
  key: Key;
  title: ReactNode;
  icon?: string;
  type?: string;
  isLeaf?: boolean;
  children?: TreeNodeData[];
}

interface FolderTreeProps {
  knowledgeBaseId: string;
  onSelect?: TreeProps['onSelect'];
  onExpand?: TreeProps['onExpand'];
  multiple?: boolean;
  className?: string;
  style?: CSSProperties;
  refreshKey?: number;
  onRootLoad?: (nodes: TreeNodeData[] | null) => void;
  onFolderPathChange?: (path: Array<{ id: string; name: string }>) => void;
  selectedKeys?: React.Key[];
  // New: Auto expand to specified path
  autoExpandPath?: Array<{ id: string; name: string }>;
  embedded?: boolean;
  rootTitle?: ReactNode;
  rootSelected?: boolean;
  onRootSelect?: () => void;
  onCreateFolder?: (parentId: string, parentName?: string, parentPath?: string[]) => void;
}

const renderIcon = (icon?: string) => {
  if (!icon) return undefined;
  return <img src={icon} alt="" className="rb:size-4" />;
};

const transformTreeData = (nodes: TreeNodeData[]): DataNode[] =>
  nodes.map((node) => {
    const children = node.children && node.children.length > 0 ? transformTreeData(node.children) : undefined;
    return {
      key: node.key,
      title: node.title ?? '',
      icon: renderIcon(node.icon),
      children,
    };
  });

const normalizeExt = (ext?: string): string => {
  if (typeof ext !== 'string') return '';
  return ext.trim().replace(/^\./, '').toLowerCase();
};

const isFolderLike = (node: any): boolean => {
  const ext = normalizeExt(node?.file_ext);
  if (ext) {
    return ext === 'folder';
  }
  const type = typeof node?.type === 'string' ? node.type.toLowerCase() : '';
  if (type === 'folder' || type === 'directory') return true;
  if (typeof node?.is_directory === 'boolean') return node.is_directory;
  if (typeof node?.is_dir === 'boolean') return node.is_dir;
  if (node?.folder_name || node?.children) return true;
  return false;
};

const getNodeTitle = (node: any): ReactNode => (
  node?.folder_name
  ?? node?.file_name
  ?? node?.name
  ?? node?.title
  ?? <Trans i18nKey="knowledgeBase.privateWiki.unnamed" />
);

const extractItems = (resp: any): any[] => {
  if (!resp) return [];
  if (Array.isArray(resp)) return resp;
  if (Array.isArray(resp?.items)) return resp.items;
  if (Array.isArray(resp?.list)) return resp.list;
  if (Array.isArray(resp?.data?.items)) return resp.data.items;
  return [];
};

// Only load nodes at current level, don't recursively load child nodes
const buildTreeNodes = async (
  kbId: string,
  parentId: string,
): Promise<TreeNodeData[]> => {
  const currentParent = String(parentId ?? '');
  if (!currentParent) return [];

  // Only request current level data once, no pagination
  const response = await getFolderList({ 
    kb_id: kbId, 
    parent_id: currentParent, 
    page: 1, 
    pagesize: 1000 
  } as any);
  
  const rawItems = extractItems(response);
  const nodes: TreeNodeData[] = [];

  for (let index = 0; index < rawItems.length; index += 1) {
    const raw = rawItems[index];
    const keySource = raw?.id ?? raw?.file_id ?? raw?.key ?? raw?.folder_id ?? `${currentParent}-${index}`;
    const nodeKey = String(keySource);
    const isFolder = isFolderLike(raw);
    
    // Only show folders
    if (!isFolder) {
      continue;
    }

    // Folder node initially doesn't load child nodes, isLeaf set to false indicates possible child nodes
    nodes.push({
      key: nodeKey,
      title: getNodeTitle(raw),
      type: isFolder ? 'folder' : (typeof raw?.type === 'string' ? raw.type : normalizeExt(raw?.file_ext) || 'file'),
      isLeaf: false, // Folder node initially set to false, indicating possible child nodes, load when expanded
      children: undefined, // Initially don't load child nodes
    });
  }

  return nodes;
};

const FolderTree: FC<FolderTreeProps> = ({
  knowledgeBaseId,
  onSelect,
  onExpand,
  multiple,
  className,
  style,
  refreshKey = 0,
  onRootLoad,
  onFolderPathChange,
  selectedKeys,
  autoExpandPath,
  embedded = false,
  rootTitle,
  rootSelected = false,
  onRootSelect,
  onCreateFolder,
}) => {
  const [treeData, setTreeData] = useState<TreeNodeData[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>(() => (
    rootTitle === undefined ? [] : [ROOT_NODE_KEY]
  ));
  const [autoExpandInProgress, setAutoExpandInProgress] = useState(false);

  // Helper function to update tree node data
  const updateTreeData = useCallback((nodes: TreeNodeData[], key: Key, children: TreeNodeData[]): TreeNodeData[] => {
    return nodes.map((node) => {
      if (node.key === key) {
        return {
          ...node,
          children: children.length > 0 ? children : undefined,
          isLeaf: children.length === 0,
        };
      }
      if (node.children) {
        return {
          ...node,
          children: updateTreeData(node.children, key, children),
        };
      }
      return node;
    });
  }, []);

  // Load root nodes
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!knowledgeBaseId) {
        setTreeData([]);
        setExpandedKeys(rootTitle === undefined ? [] : [ROOT_NODE_KEY]); // Reset expand state
        return;
      }
      try {
        // Reset expand state, ensure starting from root directory
        setExpandedKeys(rootTitle === undefined ? [] : [ROOT_NODE_KEY]);
        
        const nodes = await buildTreeNodes(knowledgeBaseId, knowledgeBaseId);
        if (!cancelled) {
          setTreeData(nodes);
          if (onRootLoad) {
            onRootLoad(nodes.length > 0 ? nodes : null);
          }
        }
      } catch (e) {
        console.error('Failed to load folder tree:', e);
        if (!cancelled) {
          setTreeData([]);
          if (onRootLoad) {
            onRootLoad(null);
          }
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [knowledgeBaseId, refreshKey, rootTitle, onRootLoad]);

  // Lazy load child nodes - only load when expanded
  const onLoadData = async (node: any) => {
    const { key } = node;
    
    // If child nodes already loaded, don't reload
    if (node.children !== undefined) {
      return Promise.resolve();
    }

    try {
      // Use node's key as parent_id to load child folders
      const children = await buildTreeNodes(knowledgeBaseId, String(key));
      setTreeData((prevData) => updateTreeData(prevData, key, children));
    } catch (e) {
      console.error('Failed to load child nodes:', e);
      // On load failure, mark this node as leaf node (no child nodes)
      setTreeData((prevData) => updateTreeData(prevData, key, []));
    }
  };

  // Helper function to find node path
  const findNodePath = (nodes: TreeNodeData[], targetKey: Key, currentPath: Array<{ id: string; name: string }> = []): Array<{ id: string; name: string }> | null => {
    for (const node of nodes) {
      const newPath = [...currentPath, { id: String(node.key), name: String(node.title) }];
      
      if (node.key === targetKey) {
        return newPath;
      }
      
      if (node.children) {
        const found = findNodePath(node.children, targetKey, newPath);
        if (found) {
          return found;
        }
      }
    }
    return null;
  };

  // Helper function to find node
  const findNodeInTree = useCallback((nodes: TreeNodeData[], key: string): TreeNodeData | null => {
    for (const node of nodes) {
      if (String(node.key) === key) {
        return node;
      }
      if (node.children) {
        const found = findNodeInTree(node.children, key);
        if (found) return found;
      }
    }
    return null;
  }, []);

  // Progressive auto expand to specified path
  useEffect(() => {
    if (!autoExpandPath || autoExpandPath.length === 0 || autoExpandInProgress || treeData.length === 0) {
      return;
    }

    const expandToPath = async () => {
      setAutoExpandInProgress(true);
      
      try {
        const keysToExpand: React.Key[] = [];
        let currentTreeData = treeData;
        
        // Expand level by level, starting from first level (skip root node as it's already loaded)
        for (let i = 0; i < autoExpandPath.length - 1; i++) {
          const nodeKey = autoExpandPath[i].id;
          keysToExpand.push(nodeKey);
          
          // Find current node
          const targetNode = findNodeInTree(currentTreeData, nodeKey);
          
          if (targetNode && targetNode.children === undefined) {
            // If child nodes not loaded, load first
            try {
              console.log(`Auto expand: Loading child nodes of ${nodeKey}`);
              const children = await buildTreeNodes(knowledgeBaseId, nodeKey);
              
              // Update tree data
              setTreeData((prevData) => {
                const newData = updateTreeData(prevData, nodeKey, children);
                currentTreeData = newData; // Update current reference
                return newData;
              });
              
              // Wait for state update to complete
              await new Promise(resolve => setTimeout(resolve, 150));
              
            } catch (error) {
              console.error(`Failed to load node ${nodeKey} during auto expand:`, error);
              // Stop expanding on load failure
              break;
            }
          }
        }
        
        // Set expanded nodes
        setExpandedKeys(rootTitle === undefined ? keysToExpand : [ROOT_NODE_KEY, ...keysToExpand]);
        
        // Select last node (target folder)
        const targetKey = autoExpandPath[autoExpandPath.length - 1]?.id;
        if (targetKey) {
          console.log(`Auto expand: Select target node ${targetKey}`);
          // Delay selection to ensure expand animation completes
          setTimeout(() => {
            if (onSelect) {
              onSelect([targetKey], {
                selected: true,
                selectedNodes: [],
                node: {} as any,
                event: 'select',
                nativeEvent: new MouseEvent('click')
              });
            }
          }, 200);
        }
        
      } catch (error) {
        console.error('Auto expand path failed:', error);
      } finally {
        // Delay reset flag to ensure expand process is fully complete
        setTimeout(() => {
          setAutoExpandInProgress(false);
        }, 500);
      }
    };

    // Delay execution to ensure tree data is loaded
    const timer = setTimeout(expandToPath, 300);
    return () => clearTimeout(timer);
  }, [autoExpandPath, treeData, findNodeInTree, updateTreeData, knowledgeBaseId, onSelect, autoExpandInProgress, rootTitle]);

  // Handle expand event
  const handleExpand: TreeProps['onExpand'] = (expandedKeys, info) => {
    setExpandedKeys(expandedKeys);
    if (onExpand) {
      onExpand(expandedKeys, info);
    }
  };

  // Handle select event, calculate and pass path
  const handleSelect: TreeProps['onSelect'] = (nextSelectedKeys, info) => {
    if (String(info.node.key) === ROOT_NODE_KEY) {
      onFolderPathChange?.([]);
      onRootSelect?.();
      return;
    }

    const folderSelectedKeys = nextSelectedKeys.filter((key) => String(key) !== ROOT_NODE_KEY);
    if (folderSelectedKeys.length > 0) {
      const path = findNodePath(treeData, folderSelectedKeys[0]);
      if (path) onFolderPathChange?.(path);
    } else {
      onFolderPathChange?.([]);
    }

    onSelect?.(folderSelectedKeys, info);
  };
  const treeNodes = useMemo(() => {
    const folderNodes = transformTreeData(treeData);
    if (rootTitle === undefined) return folderNodes;
    return [{
      key: ROOT_NODE_KEY,
      title: rootTitle,
      isLeaf: folderNodes.length === 0,
      children: folderNodes.length > 0 ? folderNodes : undefined,
    }];
  }, [rootTitle, treeData]);


  if (!treeNodes.length) {
    return null;
  }

  console.log('treeNodes', treeNodes)
  const selectedKeyList = rootSelected ? [ROOT_NODE_KEY] : selectedKeys
  return (
    <div className={embedded
      ? 'rb:w-full rb:bg-transparent rb:pt-1'
      : 'rb:w-64 rb:py-4 rb:shrink-0 rb:h-[calc(100%+40px)] rb:border-r rb:border-[#EAECEE] rb:p-4 rb:bg-transparent'
    }>
      <DirectoryTree
        showIcon={false}
        key={refreshKey} // Add key to ensure component re-renders when refreshKey changes
        multiple={multiple}
        className={className}
        style={style}
        onSelect={handleSelect}
        onExpand={handleExpand}
        expandedKeys={expandedKeys}
        loadData={onLoadData}
        treeData={treeNodes}
        titleRender={(node) => {
          return (
            <Flex align="center" justify="space-between" gap={8} className="rb:w-full! rb:group">
              <span className="rb:min-w-0 rb:flex-1 rb:truncate">{node.title as ReactNode}</span>
              {onCreateFolder && (
                <span
                  className={clsx("rb:hidden rb:size-4 rb:bg-cover rb:shrink-0 rb:group-hover:block", {
                    "rb:bg-[url('@/assets/images/knowledgeBase/folder.svg')]": !selectedKeyList?.includes(node?.key),
                    "rb:bg-[url('@/assets/images/knowledgeBase/folder_white.svg')]": selectedKeyList?.includes(node?.key)
                  })}
                  onClick={(event) => {
                    event.stopPropagation();
                    const path = node.key === ROOT_NODE_KEY
                      ? [String(node.title)]
                      : findNodePath(treeData, node.key)?.map((item) => item.name) ?? [];
                    onCreateFolder(
                      node.key === ROOT_NODE_KEY ? knowledgeBaseId : String(node.key),
                      node.title as string,
                      path,
                    );
                  }}
                />
              )}
            </Flex>
          )
        }}
        switcherIcon={renderIcon(switcherIcon)}
        selectedKeys={selectedKeyList}
      />
    </div>
  );
};

export default FolderTree;
