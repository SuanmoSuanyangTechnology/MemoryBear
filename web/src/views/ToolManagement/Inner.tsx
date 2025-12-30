import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import {
  Row,
  Col,
  Tag,
  List,
  Space
} from 'antd';
import { EyeOutlined } from '@ant-design/icons';
import clsx from 'clsx'
import { useTranslation } from 'react-i18next';
import dayjs, { type Dayjs } from 'dayjs'

import type { Query, ToolItem, TimeToolModalRef, JsonToolModalRef, InnerToolModalRef } from './types';
import SearchInput from '@/components/SearchInput'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard/Card'
import TimeToolModal from './components/TimeToolModal'
import JsonToolModal from './components/JsonToolModal'
import InnerToolModal from './components/InnerToolModal'
import { getTools } from '@/api/tools'
import { InnerConfigData } from './constant'

const Inner: React.FC<{ getStatusTag: (status: string) => ReactNode }> = ({ getStatusTag }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
  const [query, setQuery] = useState<Query>({ name: undefined, tool_type: 'builtin' });
  const [curTime, setCurTime] = useState<Dayjs>(dayjs())
  const timeToolModalRef = useRef<TimeToolModalRef>(null)
  const jsonToolModalRef = useRef<JsonToolModalRef>(null)
  const innerToolModalRef = useRef<InnerToolModalRef>(null)

  useEffect(() => {
    getData()
    const timer = setInterval(() => {
      setCurTime(dayjs())
    }, 1000)
    return () => {
      clearInterval(timer)
    }
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
  const handleEdit = (data: ToolItem) => {
    switch (data.config_data.tool_class) {
      case 'DateTimeTool':
        timeToolModalRef.current?.handleOpen(data);
        break
      case 'JsonTool':
        jsonToolModalRef.current?.handleOpen(data);
        break
      default: 
        innerToolModalRef.current?.handleOpen(data);
        break;
    }
  }

  return (
    <div>
      <Row gutter={16} className='rb:mb-4 rb:w-full'>
        <Col span={8}>
          <SearchInput
            placeholder={t('tool.innerSearchPlaceholder')}
            onSearch={handleSearch}
            style={{width: '100%'}}
          />
        </Col>
      </Row>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <List
          grid={{ gutter: 16, column: 2 }}
          dataSource={data}
          renderItem={(item) => (
            <List.Item key={item.id} className='rb:h-full!'>
              <RbCard
                // className={clsx({
                //   'rb:h-85.5!': item.config_data.tool_class === 'DateTimeTool' || item.config_data.tool_class === 'JsonTool'
                // })}
                // avatar={
                //   <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                //     {item.name[0]}
                //   </div>
                // }
                title={item.name}
                extra={getStatusTag(item.status)}
                bodyClassName='rb:h-[calc(100%-40px)]'
              >
                <div className="rb:h-full rb:flex rb:flex-col rb:justify-between">
                  <div className="rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167]">
                    {t(`tool.${item.config_data.tool_class}_features`)} <br />
                    <Space size={4} className="rb:mt-2">
                      {InnerConfigData[item.config_data.tool_class].features.map(vo => <Tag key={vo} color="default">{ t(`tool.${vo}`) }</Tag>) }
                    </Space>

                    {item.config_data.tool_class === 'DateTimeTool'
                      ? <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
                        {t('tool.currentTime')}
                        <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:my-2">
                          {curTime.format('YYYY-MM-DD HH:mm:ss')}
                        </div>
                        {t('tool.timestamp')}
                        <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:mt-2">
                          {curTime.unix()}
                        </div>
                      </div>
                      :item.config_data.tool_class === 'JsonTool'
                      ? <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
                        {t('tool.jsonEg')}
                        <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:my-2">
                          {InnerConfigData[item.config_data.tool_class].eg}
                        </div>
                      </div>
                        : <div className="rb:mt-3 rb:bg-[#F0F3F8] rb:px-3 rb:py-2.5 rb:rounded-md">
                          {t('tool.configStatus')}
                          <div className="rb:font-medium rb:bg-white rb:px-3 rb:py-2.5 rb:rounded-md rb:my-2">
                            {t(`tool.${item.status}_desc`)}
                          </div>
                        </div>
                    }
                  </div>

                  <div className="rb:mt-4 rb:flex rb:items-center rb:justify-end">
                    {item.config_data.tool_class === 'DateTimeTool' || item.config_data.tool_class === 'JsonTool' ? 
                        <EyeOutlined className="rb:text-5 rb:text-[#5B6167]! rb:hover:text-[#212332]!" onClick={() => handleEdit(item)} />
                    : <div
                        className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                      onClick={() => handleEdit(item)}
                    ></div>
                    }
                  </div>
                </div>
              </RbCard>
            </List.Item>
          )}
          className="rb:h-[calc(100vh-178px)] rb:overflow-y-auto rb:overflow-x-hidden"
        />
      </BodyWrapper>

      <TimeToolModal
        ref={timeToolModalRef}
      />
      <JsonToolModal
        ref={jsonToolModalRef}
      />
      <InnerToolModal
        ref={innerToolModalRef}
        refreshTable={getData}
      />
    </div>
  );
};

export default Inner;