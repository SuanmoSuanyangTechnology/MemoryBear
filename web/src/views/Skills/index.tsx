/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-05 10:43:49 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-05 10:43:49 
 */
import React, { useRef } from 'react';
import { Button, Tooltip } from 'antd';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import type { Skill } from './types'
import RbCard from '@/components/RbCard/Card'
import { getSkillListUrl } from '@/api/skill'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'

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
              avatar={<div className="rb:w-12 rb:h-12 rb:text-center rb:font-semibold rb:text-[28px] rb:leading-12 rb:rounded-lg rb:text-[#FBFDFF] rb:bg-[#155EEF] rb:mr-2">{item.name[0]}</div>}
              className="rb:cursor-pointer"
              onClick={() => handleView(item)}
            >
              {/* Skill description with tooltip */}
              <Tooltip title={item.description}>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:-mt-1 rb:wrap-break-word rb:line-clamp-1">{item.description}</div>
              </Tooltip>
            </RbCard>
          );
        }}
      />
    </>
  );
};

export default Skills;
