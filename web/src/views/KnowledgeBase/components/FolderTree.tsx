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
  // 新增：自动展开到指定路径
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

// 只加载当前层级的节点，不递归加载子节点
const buildTreeNodes = async (
  kbId: string,
  parentId: string,
): Promise<TreeNodeData[]> => {
  const currentParent = String(parentId ?? '');
  if (!currentParent) return [];

  // 只请求一次当前层级的数据，不分页
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
    
    // 只显示文件夹
    if (!isFolder) {
      continue;
    }

    // 文件夹节点初始不加载子节点，isLeaf设为false表示可能有子节点
    nodes.push({
      key: nodeKey,
      title: getNodeTitle(raw),
      icon: getNodeIcon(raw, isFolder),
      switcherIcon: isFolder ? switcherIcon : undefined,
      type: isFolder ? 'folder' : (typeof raw?.type === 'string' ? raw.type : normalizeExt(raw?.file_ext) || 'file'),
      isLeaf: false, // 文件夹节点初始设为false，表示可能有子节点，需要展开时加载
      children: undefined, // 初始不加载子节点
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

  // 更新树节点数据的辅助函数
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

  // 加载根节点
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!knowledgeBaseId) {
        setTreeData([]);
        setExpandedKeys([]); // 重置展开状态
        return;
      }
      try {
        // 重置展开状态，确保从根目录开始
        setExpandedKeys([]);
        
        const nodes = await buildTreeNodes(knowledgeBaseId, knowledgeBaseId);
        if (!cancelled) {
          setTreeData(nodes);
          if (onRootLoad) {
            onRootLoad(nodes.length > 0 ? nodes : null);
          }
        }
      } catch (e) {
        console.error('加载文件夹树失败:', e);
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

  // 懒加载子节点 - 只在展开时加载
  const onLoadData = async (node: any) => {
    const { key } = node;
    
    // 如果已经加载过子节点，不再重复加载
    if (node.children !== undefined) {
      return Promise.resolve();
    }

    try {
      // 使用节点的 key 作为 parent_id 加载子文件夹
      const children = await buildTreeNodes(knowledgeBaseId, String(key));
      setTreeData((prevData) => updateTreeData(prevData, key, children));
    } catch (e) {
      console.error('加载子节点失败:', e);
      // 加载失败时，将该节点标记为叶子节点（没有子节点）
      setTreeData((prevData) => updateTreeData(prevData, key, []));
    }
  };

  // 查找节点路径的辅助函数
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

  // 查找节点的辅助函数
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

  // 渐进式自动展开到指定路径
  useEffect(() => {
    if (!autoExpandPath || autoExpandPath.length === 0 || autoExpandInProgress || treeData.length === 0) {
      return;
    }

    const expandToPath = async () => {
      setAutoExpandInProgress(true);
      
      try {
        const keysToExpand: React.Key[] = [];
        let currentTreeData = treeData;
        
        // 逐级展开，从第一级开始（跳过根节点，因为根节点已经加载）
        for (let i = 0; i < autoExpandPath.length - 1; i++) {
          const nodeKey = autoExpandPath[i].id;
          keysToExpand.push(nodeKey);
          
          // 查找当前节点
          const targetNode = findNodeInTree(currentTreeData, nodeKey);
          
          if (targetNode && targetNode.children === undefined) {
            // 如果子节点未加载，先加载
            try {
              console.log(`自动展开：加载节点 ${nodeKey} 的子节点`);
              const children = await buildTreeNodes(knowledgeBaseId, nodeKey);
              
              // 更新树数据
              setTreeData((prevData) => {
                const newData = updateTreeData(prevData, nodeKey, children);
                currentTreeData = newData; // 更新当前引用
                return newData;
              });
              
              // 等待状态更新完成
              await new Promise(resolve => setTimeout(resolve, 150));
              
            } catch (error) {
              console.error(`自动展开时加载节点 ${nodeKey} 失败:`, error);
              // 加载失败时停止展开
              break;
            }
          }
        }
        
        // 设置展开的节点
        setExpandedKeys(keysToExpand);
        
        // 选中最后一个节点（目标文件夹）
        const targetKey = autoExpandPath[autoExpandPath.length - 1]?.id;
        if (targetKey) {
          console.log(`自动展开：选中目标节点 ${targetKey}`);
          // 延迟选中，确保展开动画完成
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
        console.error('自动展开路径失败:', error);
      } finally {
        // 延迟重置标志，确保展开过程完全完成
        setTimeout(() => {
          setAutoExpandInProgress(false);
        }, 500);
      }
    };

    // 延迟执行，确保树数据已经加载完成
    const timer = setTimeout(expandToPath, 300);
    return () => clearTimeout(timer);
  }, [autoExpandPath, treeData.length, knowledgeBaseId, onSelect, autoExpandInProgress]);

  // 处理展开事件
  const handleExpand: TreeProps['onExpand'] = (expandedKeys, info) => {
    setExpandedKeys(expandedKeys);
    if (onExpand) {
      onExpand(expandedKeys, info);
    }
  };

  // 处理选择事件，计算并传递路径
  const handleSelect: TreeProps['onSelect'] = (selectedKeys, info) => {
    if (selectedKeys.length > 0) {
      const path = findNodePath(treeData, selectedKeys[0]);
      if (path && onFolderPathChange) {
        onFolderPathChange(path);
      }
    } else if (onFolderPathChange) {
      onFolderPathChange([]);
    }
    
    // 调用原始的 onSelect 回调
    if (onSelect) {
      onSelect(selectedKeys, info);
    }
  };

  const treeNodes = useMemo(() => transformTreeData(treeData), [treeData]);

  return (
    <DirectoryTree
      key={refreshKey} // 添加key确保refreshKey变化时重新渲染整个组件
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
