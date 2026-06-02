/*
 * @Author: ZhaoYing 
 * @Date: 2026-05-28 13:41:06 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-05-28 15:12:27
 */

import React, { useEffect, useState, useRef, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Tooltip, Row, Col, Flex, Space, App } from 'antd';

import type { WorkflowToolItem, WorkflowToolModalRef } from './types';
import RbCard from '@/components/RbCard'
import { switchWorkspace } from '@/api/workspaces'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import PublishAsToolModal from './components/PublishAsToolModal'
import { getTools, deleteTool } from '@/api/tools'
import OverflowTags from '@/components/OverflowTags'
import Tag from '@/components/Tag'
import MoreDropdown from '@/components/MoreDropdown';

const Workflow: React.FC<{ getStatusTag: (status: string) => ReactNode; keyword?: string }> = ({ getStatusTag, keyword }) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<WorkflowToolItem[]>([]);
  const publishAsToolModalRef = useRef<WorkflowToolModalRef>(null);

  /** Load workspace list */
  const getData = () => {
    setLoading(true);
    getTools({
      tool_type: 'workflow',
      name: keyword
    })
      .then((res) => {
        setData(res as WorkflowToolItem[])
      })
      .finally(() => {
        setLoading(false)
      })
  };

  useEffect(() => {
    getData();
  }, [keyword]);

  /** Switch to selected workspace */
  const handleJump = (item: WorkflowToolItem) => {
    // switchWorkspace(item.id)
    //   .then(() => {
    //     navigate('/')
    //   })
  }
  const handleEdit = (item: WorkflowToolItem) => {
    publishAsToolModalRef.current?.handleOpen(item);
  }
  const handleDelete = (item: WorkflowToolItem) => {
    if (!item.id) return
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteTool(item.id as string).then(() => {
          message.success(t('common.deleteSuccess'));
          getData()
        })
      }
    })
  }
  return (
    <>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <Row
          gutter={[16, 16]}
          className="rb:max-h-[calc(100%-48px)] rb:overflow-y-auto"
        >
          {data.map(item => (
            <Col key={item.id} span={6}>
              <RbCard
                key={item.id}
                title={
                  <Flex justify="space-between" gap={16}>
                    <Space size={8} className="rb:flex-1!">
                      <Tooltip title={item.name}>
                        <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                      </Tooltip>
                      {getStatusTag(item.status)}
                    </Space>
                    <MoreDropdown
                      items={[
                        {
                          key: 'edit',
                          icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />,
                          label: t('common.edit'),
                          onClick: () => handleEdit(item),
                        },
                        {
                          key: 'view',
                          icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/eye.svg')]" />,
                          label: t('common.view'),
                          onClick: () => handleJump(item),
                        },
                        {
                          key: 'delete',
                          danger: true,
                          icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                          label: t('common.delete'),
                          onClick: () => handleDelete(item),
                        },
                      ]}
                    />
                  </Flex>
                }
                isNeedTooltip={false}
              >
                
                {item.tags?.length > 0
                  ? <div>
                    <OverflowTags
                      items={item.tags?.map((type, i) => <Tag variant="borderless" color="dark" key={i}>{type}</Tag>)}
                      numTag={(num?: number) => <Tag variant="borderless" color="dark">{`+${num}`}</Tag>}
                    />
                  </div>
                  : <div className="rb:text-[#A8A9AA] rb:leading-5">{t('tool.noTags')}</div>
                }
                <Tooltip title={item.description}>
                  <div className="rb:h-10 rb:wrap-break-word rb:line-clamp-2 rb:leading-5">{item.description}</div>
                </Tooltip>
                {/* <div className="rb:absolute rb:bottom-4 rb:left-6 rb:right-6">
                  <Row gutter={12}>
                    <Col span={12}>
                      <Button block
                        // onClick={() => handleJump(item)}
                      >{t('common.view')}</Button>
                    </Col>
                    <Col span={12}>
                      <Button type="primary" ghost block
                        onClick={() => publishAsToolModalRef.current?.handleOpen(item)}
                      >{t('common.edit')}</Button>
                    </Col>
                  </Row>
                </div> */}
              </RbCard>
            </Col>
          ))}
        </Row>
      </BodyWrapper>
      <PublishAsToolModal
        ref={publishAsToolModalRef}
        refresh={getData}
      />
    </>
  );
};

export default Workflow;
