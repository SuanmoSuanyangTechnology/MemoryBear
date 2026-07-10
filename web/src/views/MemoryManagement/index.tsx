/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:33:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-07-06 10:05:34
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
import clsx from 'clsx';

import MemoryForm from './components/MemoryForm';
import OnboardingGuide from './components/OnboardingGuide';
import ActiveConfigBanner from './components/ActiveConfigBanner';
import type { Memory, MemoryFormRef } from '@/views/MemoryManagement/types'
import RbCard from '@/components/RbCard/Card'
import { getMemoryConfigList, deleteMemoryConfig, setActiveMemoryConfig } from '@/api/memory'
import Empty from '@/components/Empty'
import PageLoading from '@/components/Empty/PageLoading'
import pageEmptyIcon from '@/assets/images/empty/pageEmpty.png'
import { formatDateTime } from '@/utils/format';
import Tag from '@/components/Tag'

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

  /** Set configuration as the active online configuration (with confirmation) */
  const handleSetActive = (item: Memory) => {
    modal.confirm({
      title: t('memory.switchActiveTitle'),
      icon: null,
      content: (
        <div className="rb:text-[14px] rb:leading-6 rb:text-[#5B6167]">
          <div>
            {t('memory.switchActiveConfirmPrefix')}
            「<span className="rb:font-medium rb:text-[#212332]">{item.config_name}</span>」
            {t('memory.switchActiveConfirmSuffix')}
          </div>
          <div>{t('memory.switchActiveDesc')}</div>
        </div>
      ),
      okText: t('memory.confirmActive'),
      cancelText: t('common.cancel'),
      onOk: () => {
        setActiveMemoryConfig(item.config_id)
          .then((res) => {
            const { warnings = [], success } = res as {
              success: boolean;
              config_id: string;
              config_name: string;
              warnings: {
                model_type: string;
                model_id: string | null;
                message: string;
                source: string;
              }[];
            }
            if (success) {
              message.success(t('memory.setActiveSuccess'));
            } else {
              modal.warning({
                title: t('memory.setActiveFailed'),
                content: warnings.map((item, index) => <div key={item.model_type}>{index + 1}. {item.message} ({t(`memory.${item.source}`)})</div>),
              })
            }
            loadMoreData();
          })
      }
    })
  };

  /** Navigate to engine configuration page */
  const handleClick = (id: string, type: string, config_name: string) => {
    document.title = `${config_name} - ${t('memoryBear')}`;
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

  /** 是否存在用户自建配置（非系统默认） */
  const hasUserConfig = data.some((item) => !item.is_system_default)
  /** 当前线上生效配置：优先取已生效的，其次系统默认，最后取第一条 */
  const activeConfig = data.find((item) => item.is_active) || data[0]

  return (
    <>
      {!loading && (
        hasUserConfig
          ? activeConfig && <ActiveConfigBanner config={activeConfig} />
          : <OnboardingGuide completed={0} onCreate={() => handleEdit()} />
      )}

      <Flex align="center" justify={hasUserConfig ? "end" : 'start'} className="rb:mb-4!">
        {!hasUserConfig &&
          <span className="rb:text-[16px] rb:font-medium rb:leading-5.5 rb:text-[#212332]">
            {t('memory.myConfigurations')}
          </span>
        }
        {hasUserConfig &&
          <Button type="primary" onClick={() => handleEdit()}>
            {t('memory.createConfiguration')}
          </Button>
        }
      </Flex>

      {loading
        ? <PageLoading className="rb:max-h-[calc(100%-48px)]!" />
        : data.length === 0
          ? <Empty
              url={pageEmptyIcon}
              title={t('memory.emptyConfigTitle')}
              subTitle={t('memory.emptyConfigDesc')}
              size={[240, 210]}
              className="rb:h-full"
            />
          : <Row
              gutter={[12, 12]}
              className="rb:max-h-[calc(100%-48px)] rb:overflow-y-auto"
            >
              {data.map((item) => (
                <Col key={item.config_id} span={12}>
                  <RbCard
                    title={item.config_name}
                    className={clsx("rb:relative rb:hover:shadow-[0px_2px_8px_0px_rgba(23,23,25,0.16)]!", {
                      'rb:opacity-65': item.is_system_default
                    })}
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
                            onClick={() => handleClick(item.config_id, key, item.config_name)}
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
                          {item.is_active
                            ? <Tag color="success">
                                {t('memory.activated')}
                              </Tag>
                            : <Button
                                type="link"
                                size="small"
                                className="rb:text-[12px]!"
                                onClick={(e) => { e.stopPropagation(); handleSetActive(item); }}
                              >
                                {t('memory.setActive')}
                              </Button>
                          }
                          {!item.is_system_default && <>
                            <div className="rb:size-4.5 rb:hover:bg-[#EBEBEB] rb:rounded-md">
                              <div
                                className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/edit.svg')]"
                                onClick={() => handleEdit(item)}
                              ></div>
                            </div>
                            {!item.is_active &&
                              <div
                                className="rb:size-4.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                                onClick={() => handleDelete(item)}
                              ></div>
                            }
                          </>}
                        </Space>
                      </Flex>
                    </Flex>
                  </RbCard>
                </Col>
              ))}
            </Row>
      }

      <MemoryForm
        ref={memoryFormRef}
        refresh={loadMoreData}
      />
    </>
  );
};

export default MemoryManagement;