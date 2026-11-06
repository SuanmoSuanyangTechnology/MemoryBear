import React, { useState, useRef, useEffect } from 'react';
import { type SegmentedProps, Flex, Space, Form, Button } from 'antd';
import { useTranslation } from 'react-i18next';

import Mcp from './Mcp';
import Inner from './Inner';
import Custom from './Custom';
import Workflow from './Workflow';
import PageTabs from '@/components/PageTabs'
import SearchInput from '@/components/SearchInput'
import { useToolStatus } from './hooks/useToolStatus';
import { getToolStatistics } from '@/api/tools';
import type { McpRef, CustomRef } from './types'

const tabKeys = ['inner', 'mcp', 'workflow', 'custom']

const tabInfo: Record<string, { title: string; desc: string }> = {
  inner: { title: 'tool.innerTitle', desc: 'tool.innerDesc' },
  mcp: { title: 'tool.mcpTitle', desc: 'tool.mcpDesc' },
  custom: { title: 'tool.customTitle', desc: 'tool.customDesc' },
  workflow: { title: 'tool.workflowTitle', desc: 'tool.workflowDesc' },
}

const tabTypeMap: Record<string, string> = {
  inner: 'builtin',
  mcp: 'mcp',
  custom: 'custom',
  workflow: 'workflow',
}

const ToolManagement: React.FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<SegmentedProps['value']>('inner');
  const mcpRef = useRef<McpRef>(null);
  const customRef = useRef<CustomRef>(null);
  const [form] = Form.useForm();
  const name = Form.useWatch(['name'], form)
  const getStatusTag = useToolStatus();
  const [typeCounts, setTypeCounts] = useState<Record<string, number>>({});

  useEffect(() => {
    getToolStatistics().then(res => {
      setTypeCounts(res.type_counts || {});
    }).catch(() => {});
  }, []);

  const formatTabItems = () => {
    return tabKeys.map(value => {
      const count = typeCounts[tabTypeMap[value]] || 0;
      return {
        value,
        label: `${t(`tool.${value}`)} (${count})`,
      }
    })
  }
  const handleChangeTab = (key: SegmentedProps['value']) => {
    setActiveTab(key);
    form.resetFields()
  }

  const currentInfo = tabInfo[activeTab as string];

  return (
    <Flex vertical gap={12} className="rb:h-full! rb:overflow-hidden!">
      <div className="rb:shrink-0!">
        <PageTabs
          value={activeTab}
          options={formatTabItems()}
          onChange={handleChangeTab}
        />
      </div>
      <Flex justify="space-between" align="center" className="rb:shrink-0! rb:pl-2!">
        <Flex vertical gap={2}>
          <div className="rb:text-[15px] rb:font-[MiSans-Bold] rb:font-bold rb:leading-5.5">
            {t(currentInfo?.title || '')}
          </div>
          <div className="rb:text-gray-600 rb:text-[13px] rb:leading-5">
            {t(currentInfo?.desc || '')}
          </div>
        </Flex>
        <Form form={form}>
          <Space size={12}>
            <Form.Item name="name" noStyle>
              <SearchInput
                placeholder={t(`tool.${activeTab === 'mcp'
                  ? 'mcpSearchPlaceholder'
                  : activeTab === 'custom'
                  ? 'customSearchPlaceholder'
                  : activeTab === 'workflow'
                  ? 'workflowSearchPlaceholder'
                  : 'innerSearchPlaceholder'
                }`)}
              />
            </Form.Item>
            {activeTab === 'mcp' && <Button type="primary" onClick={() => mcpRef.current?.handleEdit()}>{t('tool.addService')}</Button>}
            {activeTab === 'custom' && <Button type="primary" onClick={() => customRef.current?.handleEdit()}>{t('tool.addCustom')}</Button>}
          </Space>
        </Form>
      </Flex>
      <div className="rb:flex-1 rb:min-h-0! rb:overflow-y-auto rb:overflow-x-hidden">
        {activeTab === 'mcp' && <Mcp ref={mcpRef} keyword={name} />}
        {activeTab === 'inner' && <Inner keyword={name} getStatusTag={getStatusTag} />}
        {activeTab === 'custom' && <Custom ref={customRef} keyword={name} getStatusTag={getStatusTag} />}
        {activeTab === 'workflow' && <Workflow  getStatusTag={getStatusTag} keyword={name} />}
      </div>
    </Flex>
  );
};

export default ToolManagement;