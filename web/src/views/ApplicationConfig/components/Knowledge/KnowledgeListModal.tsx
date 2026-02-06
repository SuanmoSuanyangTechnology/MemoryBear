/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:49 
 */
/**
 * Knowledge List Modal
 * Displays and allows selection of knowledge bases to associate with the application
 */

import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Space, List } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { KnowledgeModalRef, KnowledgeBase } from './types'
import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'
import RbModal from '@/components/RbModal'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'
import { formatDateTime } from '@/utils/format';
/**
 * Component props
 */
interface KnowledgeModalProps {
  /** Callback to add selected knowledge bases */
  refresh: (rows: KnowledgeBase[], type: 'knowledge') => void;
  /** Currently selected knowledge bases */
  selectedList: KnowledgeBase[];
}

/**
 * Modal for selecting knowledge bases
 */
const KnowledgeListModal = forwardRef<KnowledgeModalRef, KnowledgeModalProps>(({
  refresh,
  selectedList
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [list, setList] = useState<KnowledgeBaseListItem[]>([])
  const [filterList, setFilterList] = useState<KnowledgeBaseListItem[]>([])
  const [query, setQuery] = useState<{keywords?: string}>({})
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectedRows, setSelectedRows] = useState<KnowledgeBase[]>([])

  /** Close modal and reset state */
  const handleClose = () => {
    setVisible(false);
    setQuery({})
    setSelectedIds([])
    setSelectedRows([])
  };

  /** Open modal */
  const handleOpen = () => {
    setVisible(true);
    setQuery({})
    setSelectedIds([])
    setSelectedRows([])
  };

  useEffect(() => {
    if (visible) {
      getList()
    }
  }, [query.keywords, visible])
  /** Fetch knowledge base list */
  const getList = () => {
    getKnowledgeBaseList(undefined, {
      ...query,
      pagesize: 100,
      orderby:'created_at',
      desc:true,
    })
      .then(res => {
        const response = res as { items: KnowledgeBaseListItem[] }
        setList(response.items || [])
      })
  }
  /** Save selected knowledge bases */
  const handleSave = () => {
    refresh(selectedRows.map(item => ({
      ...item,
      config: {
        similarity_threshold: 0.7,
        retrieve_type: "hybrid",
        top_k: 3,
        weight: 1,
      }
    })), 'knowledge')
    setVisible(false);
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  /** Search knowledge bases */
  const handleSearch = (value?: string) => {
    setQuery({keywords: value})
    setSelectedIds([])
    setSelectedRows([])
  }
  /** Toggle knowledge base selection */
  const handleSelect = (item: KnowledgeBase) => {
    const index = selectedIds.indexOf(item.id)
    if (index === -1) {
      setSelectedIds([...selectedIds, item.id])
      setSelectedRows([...selectedRows, item])
    } else {
      setSelectedIds(selectedIds.filter(id => id !== item.id))
      setSelectedRows(selectedRows.filter(row => row.id !== item.id))
    }
  }

  useEffect(() => {
    if (list.length && selectedList.length) {
      const unSelectedList = list.filter(item => selectedList.findIndex(vo => vo.id === item.id) < 0)
      setFilterList([...unSelectedList])
    } else if (list.length) {
      setFilterList([...list])
    }
  }, [list, selectedList])

  return (
    <>
      <RbModal
        title={t('application.chooseKnowledge')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.save')}
        onOk={handleSave}
        width={1000}
      >
        <Space size={24} direction="vertical" className="rb:w-full">
          <SearchInput
            placeholder={t('knowledgeBase.searchPlaceholder')}
            onSearch={handleSearch}
            style={{ width: '100%' }}
          />
          {filterList.length === 0 
            ? <Empty />
            : <List
              grid={{ gutter: 16, column: 2 }}
              dataSource={filterList}
              renderItem={(item: KnowledgeBase) => (
                <List.Item>
                  <div key={item.id} className={clsx("rb:flex rb:items-center rb:justify-between rb:border rb:rounded-lg rb:p-[17px_16px] rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                    "rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF] rb:text-[#155EEF]": selectedIds.includes(item.id),
                    "rb:border-[#DFE4ED] rb:text-[#212332]": !selectedIds.includes(item.id),
                  })} onClick={() => handleSelect(item)}>
                    <div className="rb:text-[16px] rb:leading-5.5">
                      {item.name}
                      <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167] rb:mt-2">{t('application.contains', {include_count: item.doc_num})}</div>
                    </div>
                    <div className="rb:text-[12px] rb:leading-4 rb:text-[#5B6167]">{formatDateTime(item.created_at, 'YYYY-MM-DD HH:mm:ss')}</div>
                  </div>
                </List.Item>
              )}
            />
          }
        </Space>
      </RbModal>
    </>
  );
});

export default KnowledgeListModal;