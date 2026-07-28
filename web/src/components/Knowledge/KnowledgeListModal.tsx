/**
 * Knowledge List Modal
 * Displays and allows selection of knowledge bases using Tree component
 */

import { forwardRef, useImperativeHandle, useState, useCallback, type Key, useEffect } from 'react';
import { Form, Flex, Tree, Spin } from 'antd';
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

  const [form] = Form.useForm()
  const keywords = Form.useWatch('keywords', form)

  // Load root list (first page)
  const loadRootList = useCallback(() => {
    setLoading(true)
    getKnowledgeBaseList(undefined, {
      keywords,
      page: 1,
      pagesize: 50,
      orderby: 'created_at',
      desc: true,
    })
      .then(res => {
        const items = (res as { items: KnowledgeBaseListItem[] }).items || []
        const newNodes = items.map(item => transformToTreeNode(item))
        
        setTreeData(newNodes)
      })
      .finally(() => {
        setLoading(false)
      })
  }, [keywords])

  // Transform item to tree node
  const transformToTreeNode = (item: KnowledgeBaseListItem): TreeNode => {
    const node: TreeNode = {
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
      isLeaf: !item.children || item.children.length === 0,
    }
    if (item.children && item.children.length > 0) {
      node.children = item.children.map(child => transformToTreeNode(child))
    }
    return node
  }

  // Reset selections when keywords change
  useEffect(() => {
    if (visible) {
      setCheckedIds([])
      setCheckedRows([])
      setExpandedKeys([])
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
  };

  // Handle folder expansion
  const handleExpand = (keys: Key[]) => {
    setExpandedKeys(keys)
  }

  // Handle selection (both check and select)
  const handleSelectNode = (_keys: Key[], info: { node: any }) => {
    const node = info.node as TreeNode

    
    const isChecked = checkedIds.includes(node.key)
    const newCheckedIds = isChecked
      ? checkedIds.filter(id => id !== node.key)
      : [...checkedIds, node.key]
    setCheckedIds(newCheckedIds)
    setCheckedRows(getCheckedItems(treeData, newCheckedIds))
  }

  // Handle tree check
  const handleCheck = (checked: Key[] | { checked: Key[]; halfChecked: Key[] }) => {
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
      .map(node => {
        const childNodes = node.children ? filterTreeData(node.children) : undefined
        return {
          ...node,
          isLeaf: !childNodes || childNodes.length < 1,
          children: childNodes,
        }
      })
  }

  const handleSave = () => {
    refresh(checkedRows.map(item => ({
      ...item,
      config: {
        vector_similarity_weight: 0.5,
        similarity_threshold: 0.7,
        retrieve_type: "hybrid",
        top_k: 3,
        weight: 1,
        enable_graph_retrieval: false,
      }
    })), 'knowledge')
    setVisible(false);
  }

  const filteredTreeData = filterTreeData(treeData)

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
          <Empty size={88} />
        )}

        {(loading || filteredTreeData.length > 0) && (
          <Tree
            treeData={filteredTreeData}
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
});

export default KnowledgeListModal