/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:45:08 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:45:08 
 */
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { Space, List, Flex, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx'

import type { SkillModalRef } from './types'
import type { Skill } from '@/views/Skills/types'
import RbModal from '@/components/RbModal'
import { getSkillList } from '@/api/skill'
import SearchInput from '@/components/SearchInput'
import Empty from '@/components/Empty'

/**
 * Props for SkillListModal Component
 */
interface SkillModalProps {
  /** Callback function to refresh parent component with selected skills */
  refresh: (rows: Skill[], type: 'skill') => void;
  /** Array of already selected skills to exclude from selection */
  selectedList: Skill[];
}

/**
 * Skill List Modal Component
 * 
 * A modal dialog for selecting skills from a searchable list.
 * Features:
 * - Search functionality to filter skills by keywords
 * - Grid layout displaying skill cards with icons and descriptions
 * - Multi-select capability with visual feedback
 * - Excludes already selected skills from the list
 * - Displays skill name initial as avatar when no icon available
 * 
 * @param refresh - Callback to update parent with selected skills
 * @param selectedList - Currently selected skills to filter out
 * @param ref - Forwarded ref exposing handleOpen and handleClose methods
 */
const SkillListModal = forwardRef<SkillModalRef, SkillModalProps>(({
  refresh,
  selectedList
}, ref) => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [list, setList] = useState<Skill[]>([])
  const [filterList, setFilterList] = useState<Skill[]>([])
  const [query, setQuery] = useState<{keywords?: string}>({})
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectedRows, setSelectedRows] = useState<Skill[]>([])

  /**
   * Closes the modal and resets all state
   * Clears search query, selected IDs, and selected rows
   */
  const handleClose = () => {
    setVisible(false);
    setQuery({})
    setSelectedIds([])
    setSelectedRows([])
  };

  /**
   * Opens the modal and resets selection state
   * Clears any previous selections when reopening
   */
  const handleOpen = () => {
    setVisible(true);
    setQuery({})
    setSelectedIds([])
    setSelectedRows([])
  };

  /**
   * Effect: Fetch skill list when modal is visible or search query changes
   */
  useEffect(() => {
    if (visible) {
      getList()
    }
  }, [query.keywords, visible])
  
  /**
   * Fetches the skill list from API with current search parameters
   * Sorts by creation date in descending order
   */
  const getList = () => {
    getSkillList({
      ...query,
      pagesize: 100,
    })
      .then(res => {
        const response = res as { items: Skill[] }
        setList(response.items || [])
      })
  }
  
  /**
   * Saves selected skills and closes modal
   * Passes selected skills to parent component via refresh callback
   */
  const handleSave = () => {
    refresh(selectedRows, 'skill')
    setVisible(false);
  }

  /**
   * Exposes methods to parent component via ref
   * Allows parent to programmatically open/close the modal
   */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleClose
  }));
  
  /**
   * Handles search input changes and resets selection
   * Clears current selections when search query changes
   * @param value - Search keyword
   */
  const handleSearch = (value?: string) => {
    setQuery({keywords: value})
    setSelectedIds([])
    setSelectedRows([])
  }
  
  /**
   * Toggles skill selection state
   * Adds skill to selection if not selected, removes if already selected
   * @param item - Skill to select/deselect
   */
  const handleSelect = (item: Skill) => {
    const index = selectedIds.indexOf(item.id)
    if (index === -1) {
      // Add to selection
      setSelectedIds([...selectedIds, item.id])
      setSelectedRows([...selectedRows, item])
    } else {
      // Remove from selection
      setSelectedIds(selectedIds.filter(id => id !== item.id))
      setSelectedRows(selectedRows.filter(row => row.id !== item.id))
    }
  }

  /**
   * Effect: Filter out already selected skills from the display list
   * Updates filterList whenever list or selectedList changes
   */
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
        title={t('application.chooseSkill')}
        open={visible}
        onCancel={handleClose}
        okText={t('common.save')}
        onOk={handleSave}
        width={1000}
      >
        <Space size={24} direction="vertical" className="rb:w-full">
          {/* Search input for filtering skills */}
          <SearchInput
            placeholder={t('skills.searchPlaceholder')}
            onSearch={handleSearch}
            style={{ width: '100%' }}
          />
          {/* Display empty state or skill grid */}
          {filterList.length === 0 
            ? <Empty />
            : <List
              grid={{ gutter: 16, column: 2 }}
              dataSource={filterList}
              renderItem={(item: Skill) => (
                <List.Item>
                  {/* Skill card with selection state styling */}
                  <div key={item.id} className={clsx("rb:border rb:rounded-lg rb:p-[17px_16px] rb:cursor-pointer rb:hover:bg-[#F0F3F8]", {
                    "rb:bg-[rgba(21,94,239,0.06)] rb:border-[#155EEF] rb:text-[#155EEF]": selectedIds.includes(item.id),
                    "rb:border-[#DFE4ED] rb:text-[#212332]": !selectedIds.includes(item.id),
                  })} onClick={() => handleSelect(item)}>
                    <Flex>
                      {/* Skill avatar showing first letter of name */}
                      <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                        {item.name[0]}
                      </div>
                      {/* Skill name and description */}
                      <div className="rb:flex-1 rb:max-w-[calc(100%-60px)]">
                        <div className="rb:font-medium rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                        <Tooltip title={item.description}>
                          <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:-mt-1 rb:wrap-break-word rb:line-clamp-1">{item.description}</div>
                        </Tooltip>
                      </div>
                    </Flex>
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

export default SkillListModal;
