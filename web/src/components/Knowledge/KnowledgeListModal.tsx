/**
 * Knowledge List Modal
 * Displays and allows selection of knowledge bases using Tree component
 */

import { forwardRef, useEffect, useImperativeHandle, useState, useCallback, type Key } from 'react';
import { Form, Flex, Tree, Spin, Button } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import type { KnowledgeModalRef, KnowledgeBase } from './types'
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'
import RbModal from '@/components/RbModal'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'

interface KnowledgeModalProps {
  refresh: (rows: KnowledgeBase[], type: 'knowledge') => void;
  selectedList: KnowledgeBase[];
}

// Tree node type
interface TreeNode {
  key: string;
  title: React.ReactNode;
  item?: KnowledgeBaseListItem;
  children?: TreeNode[];
  isLeaf?: boolean;
  isLoadMore?: boolean;
}

const KnowledgeListModal = forwardRef<KnowledgeModalRef, KnowledgeModalProps>(({
  refresh,
  selectedList
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Key[]>([])
  const [checkedRows, setCheckedRows] = useState<KnowledgeBase[]>([])
  const [expandedKeys, setExpandedKeys] = useState<Key[]>([])
  const [treeData, setTreeData] = useState<TreeNode[]>([])
  const [loadMoreKeys, setLoadMoreKeys] = useState<Set<string>>(new Set()) // Track which nodes have load more

  // Pagination state for root and folders
  const [pagination, setPagination] = useState<Record<string, { page: number; hasMore: boolean }>>({})

  const [form] = Form.useForm()
  const keywords = Form.useWatch('keywords', form)

  // Load root list (first page)
  const loadRootList = useCallback((isLoadMore = false) => {
    setLoading(true)
    const page = isLoadMore ? (pagination['root']?.page || 1) + 1 : 1
    getKnowledgeBaseList(undefined, {
      keywords,
      pagesize: 20,
      orderby: 'created_at',
      desc: true,
    })
      .then(res => {
        const items = (res as { items: KnowledgeBaseListItem[] }).items || []
        const hasMore = items.length >= 20
        const newNodes = items.map(item => transformToTreeNode(item))
        
        if (isLoadMore) {
          setTreeData(prev => [...prev, ...newNodes])
        } else {
          setTreeData(newNodes)
        }
        
        setPagination(prev => ({
          ...prev,
          root: { page, hasMore }
        }))
        
        // Add or remove load more node
        if (hasMore) {
          setLoadMoreKeys(prev => new Set([...prev, 'root']))
        } else {
          setLoadMoreKeys(prev => {
            const next = new Set(prev)
            next.delete('root')
            return next
          })
        }
      })
      .finally(() => {
        setLoading(false)
      })
  }, [keywords, pagination])

  // Load folder children (with pagination)
  const loadFolderChildren = useCallback((parentId: string, isLoadMore = false) => {
    setLoading(true)
    const page = isLoadMore ? (pagination[parentId]?.page || 1) + 1 : 1
    return getKnowledgeBaseList(undefined, {
      keywords,
      parent_id: parentId,
      pagesize: 20,
      orderby: 'created_at',
      desc: true,
    }).then(res => {
      const items = (res as { items: KnowledgeBaseListItem[] }).items || []
      const hasMore = items.length >= 20
      const newChildren = items.map(item => transformToTreeNode(item))
      
      // Update pagination state
      setPagination(prev => ({
        ...prev,
        [parentId]: { page, hasMore }
      }))
      
      // Add or remove load more for this folder
      if (hasMore) {
        setLoadMoreKeys(prev => new Set([...prev, parentId]))
      } else {
        setLoadMoreKeys(prev => {
          const next = new Set(prev)
          next.delete(parentId)
          return next
        })
      }
      
      return newChildren
    }).finally(() => {
      setLoading(false)
    })
  }, [keywords, pagination])

  // Transform item to tree node
  const transformToTreeNode = (item: KnowledgeBaseListItem): TreeNode => ({
    key: item.id,
    title: (
      <Flex align="center" justify="space-between" className="rb:pr-2">
        <div className="rb:text-[14px]">
          {item.name}
          <div className="rb:text-[12px]">{t('application.contains', { include_count: item.doc_num })}</div>
        </div>
      </Flex>
    ),
    item,
    isLeaf: item.type?.toLowerCase() !== 'folder',
  })

  // Create load more node
  const createLoadMoreNode = (parentId: string, isRoot = false): TreeNode => ({
    key: `load-more-${parentId}`,
    title: (
      <Button 
        type="link" 
        size="small" 
        icon={<ReloadOutlined spin={loading} />}
        onClick={(e) => {
          e.stopPropagation()
          if (isRoot) {
            loadRootList(true)
          } else {
            loadFolderChildren(parentId, true).then(children => {
              updateTreeNodeChildren(parentId, children, true)
            })
          }
        }}
        loading={loading}
      >
        {t('common.loadMore')}
      </Button>
    ),
    isLoadMore: true,
    isLeaf: true,
  })

  // Update tree node children
  const updateTreeNodeChildren = useCallback((parentId: string, children: TreeNode[], append = false) => {
    setTreeData(prev => updateNodeChildren(prev, parentId, children, append))
  }, [])

  const updateNodeChildren = (nodes: TreeNode[], parentId: string, children: TreeNode[], append = false): TreeNode[] => {
    return nodes.map(node => {
      if (node.key === parentId) {
        if (append) {
          const existingChildren = node.children || []
          // Filter out load more nodes before appending
          const filteredExisting = existingChildren.filter(child => !child.isLoadMore)
          return { ...node, children: [...filteredExisting, ...children] }
        }
        return { ...node, children }
      }
      if (node.children) {
        return { ...node, children: updateNodeChildren(node.children, parentId, children, append) }
      }
      return node
    })
  }

  // Reset selections when keywords change
  useEffect(() => {
    if (visible) {
      setCheckedIds([])
      setCheckedRows([])
      setExpandedKeys([])
      setPagination({}) // Reset pagination on open
      setLoadMoreKeys(new Set())
      loadRootList()
    }
  }, [keywords, visible])

  const handleClose = () => {
    setVisible(false);
    form.resetFields()
    setCheckedIds([])
    setCheckedRows([])
    setExpandedKeys([])
  };

  const handleOpen = () => {
    setVisible(true);
    form.resetFields()
    setCheckedIds([])
    setCheckedRows([])
    setExpandedKeys([])
    loadRootList()
  };

  // Handle folder expansion
  const handleExpand = (keys: Key[], info: { node: any }) => {
    const node = info.node as TreeNode
    
    // Skip if it's a load more node
    if (node.isLoadMore) {
      return
    }
    
    if (!node.item) {
      setExpandedKeys(keys)
      return
    }

    // If children not loaded, load them
    if (!node.isLeaf && (!node.children || node.children.length === 0)) {
      loadFolderChildren(node.key).then(children => {
        updateTreeNodeChildren(node.key, children)
      })
    }

    setExpandedKeys(keys)
  }

  // Handle selection (both check and select)
  const handleSelectNode = (keys: Key[], info: { node: any }) => {
    const node = info.node as TreeNode
    
    // Skip if it's a load more node
    if (node.isLoadMore) {
      return
    }
    
    const isChecked = checkedIds.includes(node.key)
    const newCheckedIds = isChecked
      ? checkedIds.filter(id => id !== node.key)
      : [...checkedIds, node.key]
    setCheckedIds(newCheckedIds)
    setCheckedRows(getCheckedItems(treeData, newCheckedIds))
  }

  // Handle tree check (same as select)
  const handleCheck = (checked: Key[] | { checked: Key[]; halfChecked: Key[] }, info: any) => {
    const keys = Array.isArray(checked) ? checked : checked.checked
    setCheckedIds(keys)
    setCheckedRows(getCheckedItems(treeData, keys))
  }

  // Get checked items from tree data
  const getCheckedItems = (nodes: TreeNode[], checkedKeys: Key[]): KnowledgeBaseListItem[] => {
    const result: KnowledgeBaseListItem[] = []
    nodes.forEach(node => {
      if (checkedKeys.includes(node.key) && node.item) {
        result.push(node.item)
      }
      if (node.children) {
        result.push(...getCheckedItems(node.children, checkedKeys))
      }
    })
    return result
  }

  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));

  // Filter out items that are already selected in selectedList
  const filterTreeData = (nodes: TreeNode[]): TreeNode[] => {
    return nodes
      .filter(node => !selectedList.some(selected => selected.id === node.key))
      .map(node => ({
        ...node,
        children: node.children ? filterTreeData(node.children) : undefined,
      }))
  }

  const filteredTreeData = filterTreeData(treeData)

  // Add load more nodes to tree data
  const getTreeDataWithLoadMore = (nodes: TreeNode[]): TreeNode[] => {
    return nodes.map(node => {
      if (!node.children || node.children.length === 0) {
        return node
      }
      // Recursively process children
      const processedChildren = getTreeDataWithLoadMore(node.children)
      // Add load more for this folder if needed
      if (loadMoreKeys.has(node.key)) {
        processedChildren.push(createLoadMoreNode(node.key, false))
      }
      return { ...node, children: processedChildren }
    })
  }

  // Get tree data with load more nodes for root
  const getRootTreeData = (): TreeNode[] => {
    const nodes = getTreeDataWithLoadMore(filteredTreeData)
    if (loadMoreKeys.has('root')) {
      nodes.push(createLoadMoreNode('root', true))
    }
    return nodes
  }

  return (
    <RbModal
      title={t('application.chooseKnowledge')}
      open={visible}
      onCancel={handleClose}
      okText={t('common.save')}
      onOk={handleSave}
      width={600}
    >
      <Flex gap={24} vertical>
        <Form form={form}>
          <Form.Item name="keywords" noStyle>
            <SearchInput
              placeholder={t('knowledgeBase.searchPlaceholder')}
              className="rb:w-full!"
              variant="outlined"
            />
          </Form.Item>
        </Form>

        {loading && (
          <Flex justify="center" className="rb:py-4">
            <Spin />
          </Flex>
        )}

        {!loading && filteredTreeData.length === 0 && (
          <Empty />
        )}

        {!loading && filteredTreeData.length > 0 && (
          <Tree
            treeData={getRootTreeData()}
            expandedKeys={expandedKeys}
            onExpand={handleExpand}
            checkedKeys={checkedIds}
            onCheck={handleCheck}
            onSelect={handleSelectNode}
            checkable
            selectable={false}
            showIcon={true}
            blockNode={true}
            checkStrictly
          />
        )}
      </Flex>
    </RbModal>
  );

  function handleSave() {
    refresh(checkedRows.map(item => ({
      ...item,
      config: {
        vector_similarity_weight: 0.5,
        similarity_threshold: 0.7,
        retrieve_type: "hybrid",
        top_k: 3,
        weight: 1,
      }
    })), 'knowledge')
    setVisible(false);
  }
});

export default KnowledgeListModal