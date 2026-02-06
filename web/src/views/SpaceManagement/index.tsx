/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:48:59 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:48:59 
 */
/**
 * Space Management Page
 * Displays workspace list with creation and navigation capabilities
 */

import React, { useEffect, useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import { List, Button } from 'antd';

import type { Space, SpaceModalRef } from './types';
import SpaceModal from './components/SpaceModal';
import RbCard from '@/components/RbCard/Card'
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
                avatar={<div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                  {item.name[0]}
                </div>}
                title={item.name}
                subTitle={<Tag className="rb:mt-1 rb:font-regular!" color={item.storage_type === 'rag' ? 'processing' : 'warning'}>{t(`space.${item.storage_type || 'neo4j'}`)}</Tag>}
              >
                <div className={clsx("rb:absolute rb:-top-px rb:-right-px rb:p-[2px_9px] rb:text-[#FFFFFF] rb:leading-4 rb:text-[12px] rb:font-regular rb:rounded-[0px_12px_0px_12px]", {
                  'rb:bg-[#369F21]': item.is_active,
                  'rb:bg-[#A8A9AA]': !item.is_active,
                })}>{item.is_active ? t('space.associated') : t('space.notAssociated')}</div>
                
                <Button type="primary" ghost block className="rb:mt-10" onClick={() => handleJump(item.id)}>
                  {t('space.enterSpace')}
                </Button>
              </RbCard>
            </List.Item>
          )}
          className="rb:h-[calc(100vh-148px)] rb:overflow-y-auto rb:overflow-x-hidden"
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