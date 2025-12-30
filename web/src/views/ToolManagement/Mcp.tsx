import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import {
  Button,
  Row,
  Col,
  App,
  List,
  Space,
} from 'antd';
import { LinkOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import type { ToolItem, Query, McpServiceModalRef } from './types';
import McpServiceModal from './components/McpServiceModal';
import SearchInput from '@/components/SearchInput'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard/Card'
import { getTools, deleteTool, testConnection } from '@/api/tools'

const Mcp: React.FC<{ getStatusTag: (status: string) => ReactNode }> = ({ getStatusTag }) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
  const [query, setQuery] = useState<Query>({ name: undefined, tool_type: 'mcp' });
  const addServiceModalRef = useRef<McpServiceModalRef>(null);

  useEffect(() => {
    getData()
  }, [query.name])

  const getData = () => {
    setLoading(true)
    getTools(query)
      .then((res) => {
        setData(res as ToolItem[])
      })
      .finally(() => {
        setLoading(false)
      })
  }
  const handleSearch = (value?: string) => {
    setQuery(prev => ({ ...prev, name: value }))
  }

  // 打开添加服务弹窗
  const handleEdit = (data?: ToolItem) => {
    addServiceModalRef.current?.handleOpen(data);
  };

  // 测试连接
  const handleTestConnection = (item: ToolItem) => {
    if (!item.id) {
      return
    }
    testConnection(item.id)
      .then(() => {
        message.success(t('tool.testConnectionSuccess'));
        getData()
      })
  };

  // 删除服务
  const handleDeleteService = (item: ToolItem) => {
    if (!item.id) {
      return
    }
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      okType: 'danger',
      onOk: () => {
        deleteTool(item.id as string)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            getData()
          })
      }
    })
  };

  return (
    <div>
      <Row gutter={16} className='rb:mb-4 rb:w-full'>
        <Col span={8}>
          <SearchInput
            placeholder={t('tool.mcpSearchPlaceholder')}
            onSearch={handleSearch}
            style={{width: '100%'}}
          />
        </Col>
        <Col span={16} className="rb:text-right">
          <Button type="primary" onClick={() => {handleEdit()}}>{t('tool.addService')}</Button>
        </Col>
      </Row>
      <BodyWrapper loading={loading} empty={data?.length === 0}>
        <List
          grid={{ gutter: 16, column: 3 }}
          dataSource={data}
          renderItem={(item) => (
            <List.Item key={item.id}>
              <RbCard
                // avatar={
                //   <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                //     {item.name[0]}
                //   </div>
                // }
                title={item.name}
                extra={getStatusTag(item.status)}
              >
                <div>
                  {[
                    'server_url',
                    'last_health_check',
                  ].map(key => {
                    const value = item.config_data?.[key as keyof typeof item.config_data];
                    let displayValue: React.ReactNode;
                    
                    if (key === 'last_health_check') {
                      displayValue = value ? new Date(value as number).toLocaleString() : '-';
                    } else if (typeof value === 'string' || typeof value === 'number') {
                      displayValue = value;
                    } else {
                      displayValue = '-';
                    }
                    
                    return (
                      <div 
                        key={key}
                        className="rb:flex rb:gap-4 rb:justify-start rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
                      >
                        <div className="rb:whitespace-nowrap rb:w-27.5">{t(`tool.${key}`)}</div>
                        <div className="rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-1">{displayValue}</div>
                      </div>
                    );
                  })}
                  <div className="rb:mt-4 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167] rb:flex rb:items-center rb:justify-end">
                    <Space size={16}>
                      <div 
                        className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
                        onClick={() => handleEdit(item)}
                      ></div>
                      <Button type="text" icon={<LinkOutlined />} onClick={() => handleTestConnection(item)}></Button>
                      <div 
                        className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]" 
                        onClick={() => handleDeleteService(item)}
                      ></div>
                    </Space>
                  </div>
                </div>
              </RbCard>
            </List.Item>
          )}
          className="rb:h-[calc(100vh-178px)] rb:overflow-y-auto rb:overflow-x-hidden"
        />
      </BodyWrapper>

      {/* 添加服务弹窗组件 */}
      <McpServiceModal 
        ref={addServiceModalRef}
        refresh={getData} 
      />
    </div>
  );
};

export default Mcp;