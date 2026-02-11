import { useMemo, useEffect, useState } from 'react';
import type { FC } from 'react';
import type { CSSProperties, Key, ReactNode } from 'react';
import { Tree } from 'antd';
import type { DataNode, TreeProps } from 'antd/es/tree';
import folderIcon from '@/assets/images/knowledgeBase/folder.png';
import textIcon from '@/assets/images/knowledgeBase/text.png';
import imageIcon from '@/assets/images/knowledgeBase/image.png';
import datasetsIcon from '@/assets/images/knowledgeBase/datasets.png';
import switcherIcon from '@/assets/images/knowledgeBase/switcher.png';
import { getFolderList } from '@/api/knowledgeBase';

const { DirectoryTree } = Tree;

const TEXT_EXTENSIONS = new Set([
  'txt',
  'md',
  'rtf',
  'doc',
  'docx',
  'pdf',
  'csv',
  'json',
  'xml',
  'html',
  'htm',
  'log',
]);

const IMAGE_EXTENSIONS = new Set([
  'jpg',
  'jpeg',
  'png',
  'gif',
  'bmp',
  'webp',
  'svg',
  'tiff',
  'ico',
]);

export interface TreeNodeData {
  key: Key;
  title: ReactNode;
  icon?: string;
  switcherIcon?: string;
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
}

const renderIcon = (icon?: string) => {
  if (!icon) return undefined;
  return <img src={icon} alt="icon" style={{ width: 16, height: 16 }} />;
};

const transformTreeData = (nodes: TreeNodeData[]): DataNode[] =>
  nodes.map((node) => {
    const children = node.children && node.children.length > 0 ? transformTreeData(node.children) : undefined;
    return {
      key: node.key,
      title: node.title ?? '',
      icon: renderIcon(node.icon),
      switcherIcon: renderIcon(node.switcherIcon),
      isLeaf: node.isLeaf,
      children,
    };
  });

const buildMockTreeData = (): TreeNodeData[] => ([
  {
    title: '数据集文件夹',
    key: '0',
    icon: folderIcon,
    switcherIcon: switcherIcon,
    type: 'folder',
    children: [
      {
        title: '文本数据集',
        key: '0-0',
        icon: textIcon,
        switcherIcon: switcherIcon,
        type: 'text',
        children: [
          {
            title: '子文件夹1',
            key: '0-0-0',
            icon: folderIcon,
            switcherIcon: switcherIcon,
            type: 'folder',
            children: [
              {
                title: '文档1.txt',
                key: '0-0-0-0',
                icon: textIcon,
                type: 'text',
              },
              {
                title: '文档2.txt',
                key: '0-0-0-1',
                icon: textIcon,
                type: 'text',
              },
            ],
          },
          {
            title: '子文件夹2',
            key: '0-0-1',
            icon: folderIcon,
            switcherIcon: switcherIcon,
            type: 'folder',
            children: [
              {
                title: '嵌套文件夹',
                key: '0-0-1-0',
                icon: folderIcon,
                switcherIcon: switcherIcon,
                type: 'folder',
                children: [
                  {
                    title: '深度文档.txt',
                    key: '0-0-1-0-0',
                    icon: textIcon,
                    type: 'text',
                  },
                ],
              },
            ],
          },
        ],
      },
      {
        title: '图片数据集',
        key: '0-1',
        icon: imageIcon,
        switcherIcon: switcherIcon,
        type: 'image',
        children: [
          {
            title: '图片1.jpg',
            key: '0-1-0',
            icon: imageIcon,
            type: 'image',
          },
          {
            title: '图片2.png',
            key: '0-1-1',
            icon: imageIcon,
            type: 'image',
          },
        ],
      },
      {
        title: '通用数据集',
        key: '0-2',
        icon: datasetsIcon,
        type: 'dataset',
      },
    ],
  },
]);

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

const getNodeTitle = (node: any): string => (
  node?.folder_name
  ?? node?.file_name
  ?? node?.name
  ?? node?.title
  ?? '未命名节点'
);

const getNodeIcon = (node: any, isFolder: boolean): string => {
  if (isFolder) return folderIcon;
  const type = typeof node?.type === 'string' ? node.type.toLowerCase() : '';
  if (type === 'image') return imageIcon;
  if (type === 'text') return textIcon;
  const ext = normalizeExt(node?.file_ext);
  if (IMAGE_EXTENSIONS.has(ext)) return imageIcon;
  if (TEXT_EXTENSIONS.has(ext)) return textIcon;
  return datasetsIcon;
};

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
      icon: getNodeIcon(raw, isFolder),
      switcherIcon: isFolder ? switcherIcon : undefined,
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
}) => {
  const [treeData, setTreeData] = useState<TreeNodeData[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<React.Key[]>([]);
  const [autoExpandInProgress, setAutoExpandInProgress] = useState(false);

  // Helper function to update tree node data
  const updateTreeData = (nodes: TreeNodeData[], key: Key, children: TreeNodeData[]): TreeNodeData[] => {
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
  };

  // Load root nodes
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!knowledgeBaseId) {
        setTreeData([]);
        setExpandedKeys([]); // Reset expand state
        return;
      }
      try {
        // Reset expand state, ensure starting from root directory
        setExpandedKeys([]);
        
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
          const fallback = buildMockTreeData();
          setTreeData(fallback);
          if (onRootLoad) {
            onRootLoad(fallback.length > 0 ? fallback : null);
          }
        }
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, [knowledgeBaseId, refreshKey]);

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
  const findNodeInTree = (nodes: TreeNodeData[], key: string): TreeNodeData | null => {
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
  };

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
        setExpandedKeys(keysToExpand);
        
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
  }, [autoExpandPath, treeData.length, knowledgeBaseId, onSelect, autoExpandInProgress]);

  // Handle expand event
  const handleExpand: TreeProps['onExpand'] = (expandedKeys, info) => {
    setExpandedKeys(expandedKeys);
    if (onExpand) {
      onExpand(expandedKeys, info);
    }
  };

  // Handle select event, calculate and pass path
  const handleSelect: TreeProps['onSelect'] = (selectedKeys, info) => {
    if (selectedKeys.length > 0) {
      const path = findNodePath(treeData, selectedKeys[0]);
      if (path && onFolderPathChange) {
        onFolderPathChange(path);
      }
    } else if (onFolderPathChange) {
      onFolderPathChange([]);
    }
    
    // Call original onSelect callback
    if (onSelect) {
      onSelect(selectedKeys, info);
    }
  };

  const treeNodes = useMemo(() => transformTreeData(treeData), [treeData]);

  return (
    <DirectoryTree
      key={refreshKey} // Add key to ensure component re-renders when refreshKey changes
      multiple={multiple}
      className={className}
      style={style}
      onSelect={handleSelect}
      onExpand={handleExpand}
      expandedKeys={expandedKeys}
      loadData={onLoadData}
      treeData={treeNodes}
      selectedKeys={selectedKeys}
    />
  );
};

export default FolderTree;
