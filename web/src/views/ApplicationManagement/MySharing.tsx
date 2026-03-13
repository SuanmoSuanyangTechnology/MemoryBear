/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:34:12 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-13 16:19:37
 */
import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, App, Flex, Row, Col, Collapse, Tag } from 'antd';
import clsx from 'clsx';

import type { MySharedOutItem } from './types';
import { mySharedOutList, cancelShare, cancelSpaceShare } from '@/api/application'

const MySharing: React.FC = () => {
  const { t } = useTranslation();
  const { modal } = App.useApp();
  const [data, setData] = useState<MySharedOutItem[]>([])

  useEffect(() => { getList() }, [])

  const getList = () => {
    mySharedOutList().then(res => setData(res as MySharedOutItem[]))
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

  const handleCancelOne = (item: MySharedOutItem) => {
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

  return (
    <Flex vertical gap={12}>
      {grouped.map(({ workspace, items }) => (
        <Collapse
          key={workspace.target_workspace_id}
          defaultActiveKey={[workspace.target_workspace_id]}
          items={[{
            key: workspace.target_workspace_id,
            label: (
              <Flex align="center" gap={12}>
                {workspace.target_workspace_icon
                  ? <img src={workspace.target_workspace_icon} className="rb:w-8 rb:h-8 rb:rounded-lg rb:object-cover" />
                  : <div className="rb:w-8 rb:h-8 rb:rounded-lg rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[14px] rb:text-white">
                      {workspace.target_workspace_name[0]}
                    </div>
                }
                <span className="rb:font-medium">{workspace.target_workspace_name}</span>
                <Tag color="blue">{t('application.appCount', { count: items.length })}</Tag>
              </Flex>
            ),
            extra: (
              <Button
                size="small"
                danger
                onClick={e => { e.stopPropagation(); handleAllCancel(workspace); }}
              >
                {t('application.allCancel')}
              </Button>
            ),
            children: (
              <Row gutter={[12, 12]}>
                {items.map(item => (
                  <Col key={item.id} span={6} className="rb:bg-[#F6F6F6] rb:rounded-lg rb:py-3! rb:px-4! rb:relative">
                    <div
                      className="rb:absolute rb:top-3 rb:right-3 rb:cursor-pointer rb:size-4 rb:bg-cover rb:bg-[url('src/assets/images/close.svg')]"
                      onClick={() => handleCancelOne(item)}
                    />
                    <Flex gap={8} align="center">
                      <div className="rb:size-7 rb:rounded-lg rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[14px] rb:text-white">
                        {item.source_app_name[0]}
                      </div>
                      <div className="rb:font-medium">{item.source_app_name}</div>
                    </Flex>
                    <Flex vertical gap={4} className="rb:mt-3! rb:text-[12px]!">
                      <Flex gap={5} justify="space-between">
                        <span className="rb:text-[#5B6167]">{t('application.type')}</span>
                        <span className={clsx({
                          'rb:text-[#155EEF] rb:font-medium': item.source_app_type === 'agent',
                          'rb:text-[#369F21] rb:font-medium': item.source_app_type === 'multi_agent',
                        })}>
                          {t(`application.${item.source_app_type}`)}
                        </span>
                      </Flex>
                      <Flex gap={5} justify="space-between">
                        <span className="rb:text-[#5B6167]">{t('application.version')}</span>
                        <span>{item.source_app_version}</span>
                      </Flex>
                      <Flex gap={5} justify="space-between">
                        <span className="rb:text-[#5B6167]">{t('application.permission')}</span>
                        <span className={clsx({
                          'rb:text-[#369F21] rb:font-medium': item.permission === 'editable',
                          'rb:text-[#5B6167] rb:font-medium': item.permission === 'readonly',
                        })}>
                          {t(`application.${item.permission}`)}
                        </span>
                      </Flex>
                      <Flex gap={5} justify="space-between">
                        <span className="rb:text-[#5B6167]">{t('application.souceStatus')}</span>
                        <span>{item.source_app_is_active ? t('application.sourceActive') : t('application.sourceInactive')}</span>
                      </Flex>
                    </Flex>
                  </Col>
                ))}
              </Row>
            ),
          }]}
        />
      ))}
    </Flex>
  );
};

export default MySharing;
