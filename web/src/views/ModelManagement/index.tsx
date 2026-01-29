import { useState, useRef, type FC } from 'react';
import { Button, Flex, Space, type SegmentedProps } from 'antd'
import { useTranslation } from 'react-i18next';

import GroupModelModal from './components/GroupModelModal'
import type { ModelListItem, GroupModelModalRef, CustomModelModalRef, ModelPlazaItem, BaseRef } from './types'
import SearchInput from '@/components/SearchInput'
import PageTabs from '@/components/PageTabs'
import GroupModel from './Group'
import ModelList from './List'
import ModelSquare from './Square'
import CustomModelModal from './components/CustomModelModal'
import CustomSelect from '@/components/CustomSelect'
import { modelTypeUrl, modelProviderUrl } from '@/api/models'

const tabKeys = ['group', 'list', 'square']
const ModelManagement: FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('group');
  const [query, setQuery] = useState({})
  const configModalRef = useRef<GroupModelModalRef>(null)
  const customModelModalRef = useRef<CustomModelModalRef>(null)
  const groupRef = useRef<BaseRef>(null)
  const squareRef = useRef<BaseRef>(null)

  const formatTabItems = () => {
    return tabKeys.map(value => ({
      value,
      label: t(`modelNew.${value}`),
    }))
  }
  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value as string);
    setQuery({})
  }

  const handleEdit = (vo?: ModelListItem | ModelPlazaItem) => {
    switch(activeTab) {
      case 'group':
        configModalRef?.current?.handleOpen(vo as ModelListItem)
        break
      case 'square':
        customModelModalRef?.current?.handleOpen(vo as ModelPlazaItem)
        break
    }
  }
  const handleRefresh = () => {
    switch (activeTab) {
      case 'group':
        groupRef.current?.getList()
        break
      case 'square':
        squareRef.current?.getList()
        break
    }
  }
  const handleSearch = (value?: string) => {
    setQuery({ search: value })
  }
  const handleTypeChange = (value: string) => {
    setQuery(pre => ({ ...pre, type: value }))
  }
  const handleProviderChange = (value: string) => {
    setQuery(pre => ({ ...pre, provider: value }))
  }

  return (
    <>
      <Flex justify="space-between" align="center">
        <PageTabs
          value={activeTab}
          options={formatTabItems()}
          onChange={handleChangeTab}
        />

        <Space size={12}>
          {activeTab === 'list' ? <>
            <CustomSelect
              url={modelTypeUrl}
              hasAll={false}
              format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
              onChange={handleTypeChange}
              className="rb:w-30"
              allowClear={true}
              placeholder={t('modelNew.type')}
            />
            <CustomSelect
              url={modelProviderUrl}
              hasAll={false}
              format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
              onChange={handleProviderChange}
              className="rb:w-30"
              allowClear={true}
              placeholder={t('modelNew.provider')}
            />
          </>
          : <SearchInput
            placeholder={t(`modelNew.${activeTab}SearchPlaceholder`)}
            onSearch={handleSearch}
            className="rb:w-70!"
          />}
          {activeTab === 'group' && <Button type="primary" onClick={() => handleEdit()}>+ {t('modelNew.createGroupModel')}</Button>}
          {activeTab === 'square' && <Button type="primary" onClick={() => handleEdit()}>+ {t('modelNew.createCustomModel')}</Button>}
        </Space>
      </Flex>

      <div className="rb:w-full rb:h-[calc(100%-48px)] rb:my-4">
        {activeTab === 'group' && <GroupModel ref={groupRef} query={query} handleEdit={handleEdit} />}
        {activeTab === 'list' && <ModelList query={query} />}
        {activeTab === 'square' && <ModelSquare ref={squareRef} query={query} handleEdit={handleEdit} />}
      </div>
      <GroupModelModal
        ref={configModalRef}
        refresh={handleRefresh}
      />
      <CustomModelModal
        ref={customModelModalRef}
        refresh={handleRefresh}
      />
    </>
  )
}

export default ModelManagement