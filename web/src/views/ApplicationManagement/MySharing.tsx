/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-16 11:19:20
 */
import React, { useState, useEffect, useMemo, type MouseEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { App, Flex, Row, Col, Space } from 'antd';
import clsx from 'clsx';

import type { MySharedOutItem } from './types';
import { mySharedOutList, cancelShare, cancelSpaceShare } from '@/api/application'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import RbCard from '@/components/RbCard/Card'
import RbDescriptions from '@/components/RbDescriptions'
import Tag from '@/components/Tag'

const MySharing: React.FC = () => {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<MySharedOutItem[]>([])

  useEffect(() => { getList() }, [])

  const getList = () => {
    setLoading(true)
    mySharedOutList()
      .then(res => setData(res as MySharedOutItem[]))
      .finally(() => setLoading(false))
  }

  /** Group items by target_workspace_id */
  const grouped = useMemo(() => {
    const map = new Map<string, { workspace: Pick<MySharedOutItem, 'target_workspace_id' | 'target_workspace_name' | 'target_workspace_icon'>, items: MySharedOutItem[] }>();
    data.forEach(item => {
      if (!map.has(item.target_workspace_id)) {
        map.set(item.target_workspace_id, {
          workspace: {
            target_workspace_id: item.target_workspace_id,
            target_workspace_name: item.target_workspace_name,
            target_workspace_icon: item.target_workspace_icon,
          },
          items: [],
        });
      }
      map.get(item.target_workspace_id)!.items.push(item);
    });
    return Array.from(map.values());
  }, [data]);

  const handleAllCancel = (workspace: { target_workspace_name: string; target_workspace_id: string;  }) => {
    modal.confirm({
      title: t('application.confirmWorkspaceCancelShareDesc', { workspace: workspace.target_workspace_name }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        cancelSpaceShare(workspace.target_workspace_id)
          .then(() => {
            getList();
          })
      }
    });
  };

  const handleCancelOne = (item: MySharedOutItem, e: MouseEvent) => {
    e.stopPropagation()
    modal.confirm({
      title: t('application.confirmAppCancelShareDesc', { app: item.source_app_name, workspace: item.target_workspace_name }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        cancelShare(item.source_app_id, item.target_workspace_id)
          .then(() => {
            getList();
          })
      }
    });
  };
    /** Navigate to application configuration page */
  const handleEdit = (item: MySharedOutItem) => {
    let url = `/#/application/config/${item.source_app_id}`
    window.open(url);
  }

  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null)
  const [appList, setAppList] = useState<MySharedOutItem[]>([])

  useEffect(() => {
    if (grouped.length === 0) {
      setSelectedWorkspace(null)
      setAppList([])
      return
    }
    const current = grouped.find(g => g.workspace.target_workspace_id === selectedWorkspace)
    if (current) {
      setAppList(current.items)
    } else {
      setSelectedWorkspace(grouped[0].workspace.target_workspace_id)
      setAppList(grouped[0].items)
    }
  }, [grouped, selectedWorkspace])

  const handleSelectWorkspace = async (target_workspace_id: string) => {
    if (target_workspace_id === selectedWorkspace) return
    setSelectedWorkspace(target_workspace_id);
    const filterWorkspace = grouped.find(item => item.workspace.target_workspace_id === target_workspace_id);

    setAppList(filterWorkspace?.items || [])
  };

  return (
    <BodyWrapper loading={loading} empty={data.length === 0}>
      <Row gutter={12} wrap={false}>
        <Col flex="384px">
          <Flex vertical gap={12}>
            {grouped.map(({ workspace, items }) => (
              <Flex
                key={workspace.target_workspace_id}
                gap={8}
                justify="space-between"
                align="center"
                className={clsx("rb:cursor-pointer rb:bg-white rb:py-3! rb:px-4! rb:rounded-2xl rb:border rb:border-white rb:group", {
                  'rb:border-[#171719]!': selectedWorkspace === workspace.target_workspace_id
                })}
                onClick={() => handleSelectWorkspace(workspace.target_workspace_id)}
              >
                <Flex align="center" gap={12}>
                  {workspace.target_workspace_icon
                    ? <img src={workspace.target_workspace_icon} alt={workspace.target_workspace_icon} className="rb:size-8.5 rb:rounded-lg rb:object-cover" />
                    : <div className="rb:size-8.5 rb:rounded-lg rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[14px] rb:text-white">
                      {workspace.target_workspace_name[0]}
                    </div>
                  }
                  <div>
                    <span className="rb:font-medium rb:text-[16px] rb:leading-5.5">{workspace.target_workspace_name}</span>
                    <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mt-0.5">{t('application.appCount', { count: items.length })}</div>
                  </div>
                </Flex>
                <div
                  className="rb:hidden rb:group-hover:block rb:size-7 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                  onClick={e => { e.stopPropagation(); handleAllCancel(workspace); }}
                ></div>
              </Flex>
            ))}
          </Flex>
        </Col>
        <Col flex="1">
          <div className="rb:grid rb:grid-cols-2 rb:gap-3">
            {appList.map(item => (
              <RbCard
                key={item.source_app_id}
                title={item.source_app_name}
                avatar={<Flex align="center" justify="center" className={clsx("rb:size-12 rb:rounded-lg rb:text-[24px] rb:text-[#ffffff] rb:bg-[#155EEF]", {
                  'rb:bg-[#155EEF]': item.source_app_type === 'agent',
                  'rb:bg-[#9C6FFF]!': item.source_app_type === 'multi_agent',
                  'rb:bg-[#171719]': item.source_app_type === 'workflow',
                })}>{item.source_app_name.trim()[0]}</Flex>}
                subTitle={<Space size={6}>
                  <Tag color={item.source_app_type === 'agent' ? 'processing' : item.source_app_type === 'multi_agent' ? 'dark' : 'purple'}>{t(`application.${item.source_app_type}`)}</Tag>
                  <Tag color={item.source_app_is_active ? 'success' : 'error'}>{item.source_app_is_active ? t('application.sourceActive') : t('application.sourceInactive')}</Tag>
                </Space>}
                extra={<div
                  className="rb:-mt-6 rb:cursor-pointer rb:size-5.5 rb:rounded-lg rb:hover:bg-[#F6F6F6] rb:bg-[url('@/assets/images/common/close_grey.svg')] rb:bg-size-[16px_16px] rb:bg-center rb:bg-no-repeat"
                  onClick={(e) => handleCancelOne(item, e)}
                ></div>}
                bodyClassName="rb:py-6! rb:px-4!"
                className="rb:cursor-pointer"
                onClick={() => handleEdit(item)}
              >
                <RbDescriptions
                  items={[
                    {
                      key: 'version',
                      label: t(`application.version`),
                      children: item.source_app_version
                    },
                    {
                      key: 'permission',
                      label: t(`application.permission`),
                      children: <span className={clsx('rb:font-medium', {
                        'rb:text-[#369F21]': item.permission === 'editable',
                      })}>{t(`application.${item.permission}`)}</span>
                    },
                  ]}
                />
              </RbCard>
            ))}
          </div>
        </Col>
      </Row>
    </BodyWrapper>
  )
};

export default MySharing;
