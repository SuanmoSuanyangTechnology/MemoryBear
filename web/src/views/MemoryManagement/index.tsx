/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:33:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-12 15:44:32
 */
/**
 * Memory Management Page
 * Manages memory configurations with extraction, forgetting, emotion, and reflection engines
 * Displays configuration cards with navigation to engine settings
 */

import React, { useState, useEffect, useRef } from 'react';
import { Button, Space, App, Flex, Row, Col } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import MemoryForm from './components/MemoryForm';
import type { Memory, MemoryFormRef } from '@/views/MemoryManagement/types'
import RbCard from '@/components/RbCard/Card'
import { getMemoryConfigList, deleteMemoryConfig } from '@/api/memory'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import { formatDateTime } from '@/utils/format';

const MemoryManagement: React.FC = () => {
  const { t } = useTranslation();
  const { message, modal } = App.useApp();
  const navigate = useNavigate();
  const [data, setData] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(false);

  const memoryFormRef = useRef<MemoryFormRef>(null);

  useEffect(() => {
    loadMoreData()
  }, []);
  
  /** Load configuration list */
  const loadMoreData = () => {
    setLoading(true);
    getMemoryConfigList()
      .then((res) => {
        const response = res as Memory[];
        const results = Array.isArray(response) ? response : [];
        setData(results);
      })
      .catch(() => {
        console.error('Failed to load data');
      })
      .finally(() => {
        setLoading(false);
      });
  };

  /** Open create/edit modal */
  const handleEdit = (config?: Memory) => {
    memoryFormRef.current?.handleOpen(config);
  }
  /** Delete configuration */
  const handleDelete = (item: Memory) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.config_name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteMemoryConfig(item.config_id)
          .then(() => {
            message.success(t('common.deleteSuccess'));
            loadMoreData();
          })
      }
    })
  };

  /** Navigate to engine configuration page */
  const handleClick = (id: number, type: string) => {
    switch (type) {
      case 'memoryExtractionEngine':
        navigate(`/memory-extraction-engine/${id}`)
        break
      case 'forgottenEngine':
        navigate(`/forgetting-engine/${id}`)
        break
      case 'emotionEngine':
        navigate(`/emotion-engine/${id}`)
        break;
      case 'reflectionEngine':
        navigate(`/reflection-engine/${id}`)
        break;
    }
  }

  return (
    <>
      <div className="rb:text-right rb:mb-4">
        <Button type="primary" onClick={() => handleEdit()}>
          {t('memory.createConfiguration')}
        </Button>
      </div>
      
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <Row gutter={[12, 12]}>
          {data.map((item) => (
            <Col key={item.config_id} span={12}>
              <RbCard
                title={item.config_name}
                className="rb:relative rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]!"
                headerType="borderless"
                headerClassName="rb:h-[46px]"
                bodyClassName="rb:p-3! rb:pt-0!"
              >
                {item.is_system_default &&
                  <div className="rb:absolute rb:right-0 rb:top-0 rb:bg-[#FF5D34] rb:rounded-[0px_12px_0px_12px] rb:text-[12px] rb:text-white rb:font-medium rb:leading-4 rb:py-0.75 rb:px-2">
                    {t('common.default')}
                  </div>
                }
                <Flex vertical gap={12}>
                  <div className="rb:bg-[rgba(21,94,239,0.06)] rb:rounded-lg rb:text-[#155EEF] rb:font-medium rb:leading-5 rb:py-1.5 rb:px-2">
                    {t('memory.scene_id')}: {item.scene_name || '-'}
                  </div>

                  <div className="rb:grid rb:grid-cols-2 rb:gap-x-3 rb:gap-y-2">
                    {['memoryExtractionEngine', 'forgottenEngine', 'emotionEngine', 'reflectionEngine'].map((key) => (
                      <Flex
                        key={key}
                        align="center"
                        justify="space-between"
                        className="rb:cursor-pointer rb:bg-[#F6F6F6] rb:h-8 rb:rounded-lg rb:font-medium rb:leading-5 rb:pl-2! rb:pr-1! rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]"
                        onClick={() => handleClick(item.config_id, key)}
                      >
                        {t(`memory.${key}`)}
                        <div
                          className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/memory/arrow_right.svg')] rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]"
                        ></div>
                      </Flex>
                    ))}
                  </div>

                  <Flex
                    align="center"
                    justify={item.updated_at ? "space-between" : "flex-end"}
                    className="rb:text-[12px] rb:leading-4.5 rb:font-regular rb:text-[#5B6167] rb:pl-1!"
                  >
                    {formatDateTime(item.updated_at, 'YYYY-MM-DD HH:mm:ss')}
                    <Space size={8}>
                      <div className="rb:size-4.5 rb:hover:bg-[#EBEBEB] rb:rounded-md">
                        <div
                          className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
                          onClick={() => handleEdit(item)}
                        ></div>
                      </div>
                      {!item.is_system_default &&
                        <div
                          className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                          onClick={() => handleDelete(item)}
                        ></div>
                      }
                    </Space>
                  </Flex>
                </Flex>
              </RbCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>

      <MemoryForm
        ref={memoryFormRef}
        refresh={loadMoreData}
      />
    </>
  );
};

export default MemoryManagement;