/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:43:49 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 20:28:44
 */
import React, { useRef } from 'react';
import { Button, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import type { Skill } from './types'
import RbCard from '@/components/RbCard'
import { getSkillListUrl } from '@/api/skill'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { formatDateTime } from '@/utils/format'

/**
 * Skills List Page Component
 * 
 * Main page for displaying and managing skills.
 * Features:
 * - Grid layout of skill cards
 * - Infinite scroll pagination
 * - Create new skills
 * - Navigate to skill configuration
 * - Display skill name and description
 * 
 * @returns Skills list page with grid of skill cards
 */
const Skills: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const scrollListRef = useRef<PageScrollListRef>(null)

  /**
   * Navigate to create new skill page
   */
  const handleAdd = () => {
    navigate('/skills/add')
  }
  
  /**
   * Navigate to skill configuration page
   * @param item - Skill to view/edit
   */
  const handleView = (item: Skill) => {
    navigate(`/skills/config/${item.id}`)
  }

  return (
    <>
      {/* Create skill button */}
      <div className="rb:text-right rb:mb-4">
        <Button type="primary" onClick={handleAdd}>
          + {t('skills.create')}
        </Button>
      </div>

      {/* Infinite scroll skill list */}
      <PageScrollList<Skill>
        ref={scrollListRef}
        url={getSkillListUrl}
        query={{ is_active: true, type: 'service' }}
        column={3}
        renderItem={(item) => {
          return (
            <RbCard
              title={item.name}
              className="rb:cursor-pointer"
              titleClassName="rb:line-clamp-1!"
              onClick={() => handleView(item)}
            >
              {/* Skill description with tooltip */}
              <Tooltip title={item.description}>
                <div className="rb:h-10 rb:leading-5 rb:wrap-break-word rb:line-clamp-2">{item.description}</div>
              </Tooltip>
              <div className="rb:text-[#5B6167] rb:leading-4.5 rb:text-[12px] rb:mt-4">{t('common.updated_at')}: {formatDateTime(item.updated_at)}</div>
            </RbCard>
          );
        }}
      />
    </>
  );
};

export default Skills;
