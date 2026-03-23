/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-05 17:22:23
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-03-06 15:11:31
 */
import React, { useState, useRef } from 'react';
import { type SegmentedProps, Flex, Space, Form, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import Mcp from './Mcp';
import Inner from './Inner';
import Custom from './Custom';
import Market from './Market';
import Tag from '@/components/Tag'
import PageTabs from '@/components/PageTabs'
import SearchInput from '@/components/SearchInput'
import type { McpRef, CustomRef } from './types'

const tabKeys = ['mcp', 'inner', 'custom', 'market'] // 
const ToolManagement: React.FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>('mcp');
  const mcpRef = useRef<McpRef>(null);
  const customRef = useRef<CustomRef>(null);
  const [form] = Form.useForm();
  const name = Form.useWatch(['name'], form)

  const formatTabItems = () => {
    return tabKeys.map(value => ({
      value,
      label: t(`tool.${value}`),
    }))
  }
  const handleChangeTab = (key: SegmentedProps['value']) => {
    setActiveTab(key);
    form.resetFields()
  }
  // 获取状态标签
  const getStatusTag = (status: string) => {
    switch (status) {
      case 'available':
        return <Tag color="processing">{t('tool.status.available')}</Tag>;
      case 'unconfigured':
        return <Tag color="default">{t('tool.status.unconfigured')}</Tag>;
      case 'configured_disabled':
        return <Tag color="warning">{t('tool.status.configured_disabled')}</Tag>;
      case 'error':
        return <Tag color="error">{t('tool.status.error')}</Tag>;
    }
  };

  return (
    <>
      <Flex justify="space-between" className="rb:mb-4!">
        <PageTabs
          value={activeTab}
          options={formatTabItems()}
          onChange={handleChangeTab}
        />

        {activeTab !== 'market' && <Form form={form}>
          <Space size={12}>
            <Form.Item name="name" noStyle>
              <SearchInput
                placeholder={t(`tool.${activeTab === 'mcp'
                  ? 'mcpSearchPlaceholder'
                  : activeTab === 'custom'
                  ? 'customSearchPlaceholder'
                  : 'innerSearchPlaceholder'
                }`)}
              />
            </Form.Item>
            {activeTab === 'mcp' && <Button type="primary" onClick={() => mcpRef.current?.handleEdit()}>{t('tool.addService')}</Button>}
            {activeTab === 'custom' && <Button type="primary" onClick={() => customRef.current?.handleEdit()}>{t('tool.addCustom')}</Button>}
          </Space>
        </Form>}
      </Flex>
      {activeTab === 'mcp' && <Mcp ref={mcpRef} keyword={name} getStatusTag={getStatusTag} />}
      {activeTab === 'inner' && <Inner keyword={name} getStatusTag={getStatusTag} />}
      {activeTab === 'custom' && <Custom ref={customRef} keyword={name} getStatusTag={getStatusTag} />}
      {activeTab === 'market' && <Market getStatusTag={getStatusTag} />}
    </>
  );
};

export default ToolManagement;