/*
 * @Description: 
 * @Version: 0.0.1
 * @Author: yujiangping
 * @Date: 2026-01-05 17:22:23
 * @LastEditors: yujiangping
 * @LastEditTime: 2026-03-06 15:08:38
 */
import React, { useState } from 'react';
import { Tabs } from 'antd';
import { useTranslation } from 'react-i18next';

import Mcp from './Mcp';
import Inner from './Inner';
import Custom from './Custom';
import Market from './Market';
import Tag from '@/components/Tag'

const tabKeys = ['mcp', 'inner', 'custom'] // , 'market'
const ToolManagement: React.FC = () => {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState('mcp');

  const formatTabItems = () => {
    return tabKeys.map(key => ({
      key,
      label: t(`tool.${key}`),
    }))
  }
  const handleChangeTab = (key: string) => {
    setActiveTab(key);
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
    <div className="rb:-mt-4">
      <Tabs 
        activeKey={activeTab} 
        items={formatTabItems()} 
        onChange={handleChangeTab}
      />
      {activeTab === 'mcp' && <Mcp getStatusTag={getStatusTag} />}
      {activeTab === 'inner' && <Inner getStatusTag={getStatusTag} />}
      {activeTab === 'custom' && <Custom getStatusTag={getStatusTag} />}
      {/* {activeTab === 'market' && <Market getStatusTag={getStatusTag} />} */}
    </div>
  );
};

export default ToolManagement;