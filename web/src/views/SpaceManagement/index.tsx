/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:59 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 15:33:38
 */
/**
 * Space Management Page
 * Displays workspace list with creation and navigation capabilities
 */

import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { List, Button, Flex, Space as AntSpace, Tooltip } from 'antd';

import type { Space, SpaceModalRef } from './types';
import SpaceModal from './components/SpaceModal';
import RbCard from '@/components/RbCard'
import { getWorkspaces, switchWorkspace } from '@/api/workspaces'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import Tag from '@/components/Tag'

const SpaceManagement: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<Space[]>([]);
  const spaceModalRef = useRef<SpaceModalRef>(null);

  /** Load workspace list */
  const loadMoreData = () => {
    setLoading(true);
    getWorkspaces()
      .then((res) => {
        const response = res as Space[];
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

  useEffect(() => {
    loadMoreData();
  }, []);
  
  /** Open create space modal */
  const handleCreate = () => {
    spaceModalRef.current?.handleOpen();
  }

  /** Switch to selected workspace */
  const handleJump = (id: string) => {
    switchWorkspace(id)
      .then(() => {
        localStorage.removeItem('user')
        navigate('/')
      })
  }
  return (
    <>
      <Button type="primary" className="rb:mb-4" onClick={handleCreate}>
        {t('space.createSpace')}
      </Button>
      <BodyWrapper loading={loading} empty={data.length === 0}>
        <List
          grid={{ gutter: 16, column: 4 }}
          dataSource={data}
          renderItem={(item) => (
            <List.Item key={item.id}>
              <RbCard
                avatarUrl={item.icon}
                avatarText={item.name[0]}
                title={<Flex vertical gap={6}>
                  <Tooltip title={item.name}>
                    <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                  </Tooltip>
                  <AntSpace>
                    <Tag color={item.storage_type === 'rag' ? 'processing' : 'warning'}>{t(`space.${item.storage_type || 'neo4j'}`)}</Tag>
                    <Tag color={item.is_active ? 'success' : 'error'}>{item.is_active ? t('space.associated') : t('space.notAssociated')}</Tag>
                  </AntSpace>
                </Flex>}
                isNeedTooltip={false}
                footer={<Button type="primary" ghost block className="rb:mt-2 rb:h-9!" onClick={() => handleJump(item.id)}>
                  {t('space.enterSpace')}
                </Button>}
              >
              </RbCard>
            </List.Item>
          )}
          className="rb:h-[calc(100vh-124px)] rb:overflow-y-auto rb:overflow-x-hidden"
        />
      </BodyWrapper>

      <SpaceModal
        ref={spaceModalRef}
        refresh={loadMoreData}
      />
    </>
  );
};

export default SpaceManagement;