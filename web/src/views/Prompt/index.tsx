/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:44:09 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:44:09 
 */
/**
 * Prompt Management Page
 * Main page with editor and history tabs for prompt optimization
 */

import { type FC, useState } from 'react';
import { type SegmentedProps, Flex } from 'antd';
import { useTranslation } from 'react-i18next';

import PageTabs from '@/components/PageTabs';
import SearchInput from '@/components/SearchInput'
import PromptEditor from './Prompt';
import History from './History'
import type { HistoryQuery, HistoryItem } from './types';

/** Available tab keys */
const tabs = ['editor', 'history']
const Prompt: FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>(tabs[0])
  const [query, setQuery] = useState<HistoryQuery>({});
  const [editVo, setEditVo] = useState<HistoryItem | null>(null)

  /** Handle tab change */
  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value)
    setEditVo(null)
    setQuery({})
  }
  /** Handle search in history */
  const handleSearch = (value?: string) => {
    setQuery(prev => ({ ...prev, keyword: value }))
  }
  /** Handle edit history item */
  const handleEdit = (item: HistoryItem) => {
    console.log('edit', item)
    setEditVo(item)
    setActiveTab('editor')
  }
  /** Refresh editor state */
  const refresh = () => {
    setEditVo(null)
  }
  return (
    <>
      <Flex justify="space-between" align="center" className="rb:mb-4">
        <PageTabs
          value={activeTab}
          options={tabs.map(key => ({ label: t(`prompt.${key}`), value: key }))}
          onChange={handleChangeTab}
        />
        {activeTab === 'history' &&
          <SearchInput
            placeholder={t('prompt.historySearchPlaceholder')}
            onSearch={handleSearch}
            className="rb:w-70"
          />
        }
      </Flex>
      
      <div className="rb:mt-4 rb:h-[calc(100vh-128px)]">
        {activeTab === 'editor' && <PromptEditor editVo={editVo} refresh={refresh} />}
        {activeTab === 'history' && <History query={query} edit={handleEdit} />}
      </div>
    </>
  );
};

export default Prompt;