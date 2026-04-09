import React, { useState, useRef, useEffect, type ReactNode } from 'react';
import {
  Flex,
  Space,
  Tooltip,
  Row,
  Col,
} from 'antd';
import { useTranslation } from 'react-i18next';
import dayjs, { type Dayjs } from 'dayjs'

import type { ToolItem, TimeToolModalRef, JsonToolModalRef, InnerToolModalRef } from './types';
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard'
import TimeToolModal from './components/TimeToolModal'
import JsonToolModal from './components/JsonToolModal'
import InnerToolModal from './components/InnerToolModal'
import { getTools } from '@/api/tools'
import { InnerConfigData } from './constant'

const Inner: React.FC<{ getStatusTag: (status: string) => ReactNode; keyword?: string | undefined }> = ({ getStatusTag, keyword }) => {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<ToolItem[]>([]);
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
  }, [keyword])

  const getData = () => {
    setLoading(true)
    getTools({
      tool_type: 'builtin',
      name: keyword
    })
      .then((res) => {
        setData(res as ToolItem[])
      })
      .finally(() => {
        setLoading(false)
      })
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
    <>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <Row
          gutter={[12, 12]}
          className="rb:max-h-[calc(100%-48px)] rb:overflow-y-auto"
        >
          {data.map((item) => (
            <Col span={8} key={item.id}>
              <RbCard
                title={
                  <Flex justify="space-between" gap={16}>
                    <Space size={8}>
                      <Tooltip title={item.name}>
                        <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                      </Tooltip>
                      {getStatusTag(item.status)}
                    </Space>
                    <Flex align="center" justify="center" className="rb:size-5.5 rb:hover:bg-[#F6F6F6] rb:rounded-md">
                      <div
                        className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]"
                        onClick={() => handleEdit(item)}
                      />
                    </Flex>
                  </Flex>
                }
                isNeedTooltip={false}
              >
                <Tooltip title={t(`tool.${item.config_data.tool_class}_features`)}>
                  <div className="rb:h-10 rb:wrap-break-word rb:line-clamp-2 rb:leading-5">{t(`tool.${item.config_data.tool_class}_features`)}</div>
                </Tooltip>

                <Flex gap={8} wrap align="center" className="rb:mt-2! rb:mb-4!">
                  <Flex gap={6}>
                    {InnerConfigData[item.config_data.tool_class].features?.slice(0, 2).map((type, i) => (
                      <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">{t(`tool.${type}`)}</div>
                    ))}
                  </Flex>
                  {InnerConfigData[item.config_data.tool_class].features.length > 2 && (
                    <Tooltip
                      title={<Flex wrap gap={6}>{InnerConfigData[item.config_data.tool_class].features?.slice(2, InnerConfigData[item.config_data.tool_class].features.length).map((type, i) => (
                        <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5 rb:text-[#171719]">{t(`tool.${type}`)}</div>
                      ))}</Flex>}
                      color="white"
                      placement="bottom"
                    >
                      <div className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">+{InnerConfigData[item.config_data.tool_class].features.length - 2}</div>
                    </Tooltip>
                  )}
                </Flex>

                <Row className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-2! rb:px-3! rb:leading-5">
                  {item.config_data.tool_class === 'DateTimeTool'
                    ? <>
                      <Col span={12}>
                        <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.currentTime')}</div>
                        {curTime.format('YYYY-MM-DD HH:mm:ss')}
                      </Col>
                      <Col span={12}>
                        <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.timestamp')}</div>
                        {curTime.unix()}
                      </Col>
                    </>
                    : item.config_data.tool_class === 'JsonTool'
                      ? <Col span={24}>
                        <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.jsonEg')}</div>
                        {InnerConfigData[item.config_data.tool_class].eg}
                      </Col>
                      : <Col span={24}>
                        <div className="rb:text-[#5B6167] rb:mb-1">{t('tool.configStatus')}</div>
                        {t(`tool.${item.status}_desc`)}
                      </Col>
                  }
                </Row>
              </RbCard>
            </Col>
          ))}
        </Row>
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
    </>
  );
};

export default Inner;