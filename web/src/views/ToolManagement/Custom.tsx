import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import {
  Button,
  Row,
  Col,
  App,
  List,
  Space
} from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs from 'dayjs';

import type { ToolItem, Query, CustomToolModalRef } from './types';
import CustomToolModal from './components/CustomToolModal';
import SearchInput from '@/components/SearchInput'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard/Card'
import { getTools, deleteTool } from '@/api/tools'

const Custom: React.FC<{ getStatusTag: (status: string) => ReactNode }> = ({ getStatusTag }) => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp()
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
  const [query, setQuery] = useState<Query>({ name: undefined, tool_type: 'custom' });
  const customToolModalRef = useRef<CustomToolModalRef>(null);

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
    customToolModalRef.current?.handleOpen(data);
  };

  // 删除服务
  const handleDeleteService = (item: ToolItem) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      okType: 'danger',
      onOk: () => {
        deleteTool(item.id).then(() => {
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
            placeholder={t('tool.customSearchPlaceholder')}
            onSearch={handleSearch}
            style={{width: '100%'}}
          />
        </Col>
        <Col span={16} className="rb:text-right">
          <Button type="primary" onClick={() => {handleEdit()}}>{t('tool.addCustom')}</Button>
        </Col>
      </Row>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <List
          grid={{ gutter: 16, column: 2 }}
          dataSource={data}
          renderItem={(item) => (
            <List.Item key={item.id}>
              <RbCard
                // avatar={
                //   <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                //     {item.name[0]}
                //   </div>
                // }
                title={
                  <div>
                    {item.name}<br/>
                    {/* <div className="rb:mt-1 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167]">xx个工具</div> */}
                  </div>
                }
                extra={getStatusTag(item.status)}
              >
                <div>
                  {['auth_type', 'tags', 'created_at'].map(key => (
                    <div 
                      key={key}
                      className="rb:flex rb:gap-4 rb:justify-start rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
                    >
                      <div className="rb:whitespace-nowrap rb:w-32">{t(`tool.${key}`)}</div>
                      <div className='rb:flex-1 rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap rb:flex-inline rb:text-left rb:py-px rb:rounded rb:font-medium'>
                        {key === 'created_at' && item[key]
                          ? dayjs(item[key]).format('YYYY-MM-DD HH:mm:ss')
                          : key === 'auth_type'
                          ? t(`tool.${(item.config_data as any)?.[key]}`)
                          : key === 'tags'
                          ? (item[key] as string[]).join('、')
                          : (item.config_data as any)?.[key] || '-'
                        }
                      </div>
                    </div>
                  ))}
                  <div className="rb:mt-4 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167] rb:flex rb:items-center rb:justify-end">
                    <Space size={16}>
                      <div 
                        className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]" 
                        onClick={() => handleEdit(item)}
                      ></div>
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
      <CustomToolModal 
        ref={customToolModalRef}
        refresh={getData} 
      />
    </div>
  );
};

export default Custom;