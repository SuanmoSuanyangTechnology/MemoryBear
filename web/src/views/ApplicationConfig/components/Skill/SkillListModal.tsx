/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:45:08 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-24 16:59:57
 */
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { List, Flex, Tooltip, Form } from 'antd';
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
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [selectedRows, setSelectedRows] = useState<Skill[]>([])

  const [form] = Form.useForm()
  const query = Form.useWatch([], form)

  /**
   * Closes the modal and resets all state
   * Clears search query, selected IDs, and selected rows
   */
  const handleClose = () => {
    setVisible(false);
    form.resetFields()
    setSelectedIds([])
    setSelectedRows([])
  };

  /**
   * Opens the modal and resets selection state
   * Clears any previous selections when reopening
   */
  const handleOpen = () => {
    setVisible(true);
    form.resetFields()
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
  }, [query?.search, visible])
  
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
        setSelectedIds([])
        setSelectedRows([])
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
    } else {
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
        <Flex gap={24} vertical>
          {/* Search input for filtering skills */}
          <Form form={form}>
            <Form.Item name="search" noStyle>
              <SearchInput
                placeholder={t('skills.searchPlaceholder')}
                className="rb:w-full!"
              />
            </Form.Item>
          </Form>
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
                    {/* Skill name and description */}
                    <div className="rb:flex-1 rb:max-w-[calc(100%-60px)]">
                      <Tooltip title={item.name}>
                        <div className="rb:flex-1 rb:leading-5.5 rb:min-w-0 rb:whitespace-break-spaces rb:wrap-break-word rb:line-clamp-1 rb:font-medium rb:text-[16px] rb:mb-4">
                          {item.name}
                        </div>
                      </Tooltip>
                      
                      {/* Skill description with tooltip */}
                      <Tooltip title={item.description}>
                        <div className="rb:h-10 rb:leading-5 rb:wrap-break-word rb:line-clamp-2">{item.description}</div>
                      </Tooltip>
                    </div>
                  </div>
                </List.Item>
              )}
            />
          }
        </Flex>
      </RbModal>
    </>
  );
});

export default SkillListModal;
