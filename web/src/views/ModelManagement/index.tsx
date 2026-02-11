/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:05 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:50:05 
 */
/**
 * Model Management Main Page
 * Manages AI models with three views: group models, model list, and model square
 * Supports filtering, searching, and CRUD operations
 */

import { useState, useRef, type FC } from 'react';
import { Button, Flex, Space, type SegmentedProps, Form } from 'antd'
import { useTranslation } from 'react-i18next';

import GroupModelModal from './components/GroupModelModal'
import type { ModelListItem, GroupModelModalRef, CustomModelModalRef, ModelPlazaItem, BaseRef, Query } from './types'
import SearchInput from '@/components/SearchInput'
import PageTabs from '@/components/PageTabs'
import GroupModel from './Group'
import ModelList from './List'
import ModelSquare from './Square'
import CustomModelModal from './components/CustomModelModal'
import CustomSelect from '@/components/CustomSelect'
import { modelTypeUrl, modelProviderUrl } from '@/api/models'

/**
 * Available tab keys
 */
const tabKeys = ['group', 'list', 'square']

/**
 * Model management main component
 */const ModelManagement: FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('group');
  const configModalRef = useRef<GroupModelModalRef>(null)
  const customModelModalRef = useRef<CustomModelModalRef>(null)
  const groupRef = useRef<BaseRef>(null)
  const squareRef = useRef<BaseRef>(null)
  const [form] = Form.useForm<Query>()
  const query = Form.useWatch([], form)

  /** Format tab items with translations */
  const formatTabItems = () => {
    return tabKeys.map(value => ({
      value,
      label: t(`modelNew.${value}`),
    }))
  }
  /** Handle tab change */
  const handleChangeTab = (value: SegmentedProps['value']) => {
    setActiveTab(value as string);
    form.resetFields()
  }

  /** Open edit modal based on active tab */
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
  /** Refresh list based on active tab */
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

  return (
    <>
      <Flex justify="space-between" align="center">
        <PageTabs
          value={activeTab}
          options={formatTabItems()}
          onChange={handleChangeTab}
        />

        <Form form={form}>
          <Space size={12}>
            {activeTab === 'list' &&
              <Form.Item name="type" noStyle>
                <CustomSelect
                  url={modelTypeUrl}
                  hasAll={false}
                  format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
                  className="rb:w-30"
                  allowClear={true}
                  placeholder={t('modelNew.type')}
                />
              </Form.Item>
            }
            {(activeTab === 'list' || activeTab === 'square') &&
              <Form.Item name="provider" noStyle>
                <CustomSelect
                  url={modelProviderUrl}
                  hasAll={false}
                  format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
                  className="rb:w-30"
                  allowClear={true}
                  placeholder={t('modelNew.provider')}
                />
              </Form.Item>
            }
            {activeTab !== 'list' &&
              <Form.Item name="search" noStyle>
                <SearchInput
                  placeholder={t(`modelNew.${activeTab}SearchPlaceholder`)}
                  className="rb:w-70!"
                />
              </Form.Item>
            }
            {activeTab === 'group' && <Button type="primary" onClick={() => handleEdit()}>+ {t('modelNew.createGroupModel')}</Button>}
            {activeTab === 'square' && <Button type="primary" onClick={() => handleEdit()}>+ {t('modelNew.createCustomModel')}</Button>}
          </Space>
        </Form>
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