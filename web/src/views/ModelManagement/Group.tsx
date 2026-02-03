/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:00 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:50:00 
 */
/**
 * Group Model View
 * Displays composite/group models in card grid layout
 * Supports filtering and configuration
 */

import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import clsx from 'clsx'
import { Button } from 'antd'
import { useTranslation } from 'react-i18next';

import type { ProviderModelItem, ModelListItem, DescriptionItem, BaseRef } from './types'
import RbCard from '@/components/RbCard/Card'
import { getModelNewList } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import { formatDateTime } from '@/utils/format';

/**
 * Group model list component
 */
const Group = forwardRef <BaseRef,{ query: any; handleEdit: (data: ModelListItem) => void; }>(({ query, handleEdit }, ref) => {
  const { t } = useTranslation();
  const [list, setList] = useState<ModelListItem[]>([])
  useEffect(() => {
    getList()
  }, [query])
  /** Fetch group model list */
  const getList = () => {
    getModelNewList({
      ...query,
      is_composite: true,
      is_active: true,
    })
      .then(res => {
        const response = res as ProviderModelItem[]
        setList(response[0]?.models || [])
      })
  }
  /** Format model data for display */
  const formatData = (data: ModelListItem) => {
    return [
      {
        key: 'type',
        label: t(`modelNew.type`),
        children: data.type ? t(`modelNew.${data.type}`) : '-',
      },
      {
        key: 'is_active',
        label: t(`modelNew.status`),
        children: data.is_active ? t(`common.statusEnabled`) : t(`common.statusDisabled`),
      },
      {
        key: 'created_at',
        label: t(`modelNew.created_at`),
        children: data.created_at ? formatDateTime(data.created_at, 'YYYY-MM-DD HH:mm:ss') : '-',
      },
    ]
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    getList,
  }));

  return (
    <>
      {list.length === 0
        ? <PageEmpty />
        :(
          <div className="rb:grid rb:grid-cols-4 rb:gap-4">
            {list.map(item => (
              <RbCard
                key={item.id}
                title={item.name}
                avatarUrl={item.logo}
                avatar={
                  <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                    {item.name[0]}
                  </div>
                }
              >
                {formatData(item)?.map((description: DescriptionItem) => (
                  <div
                    key={description.key}
                    className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
                  >
                    <span className="rb:whitespace-nowrap">{(description.label as string)}</span>
                    <span className={clsx({
                      "rb:text-[#212332]": description.key !== 'is_active',
                      "rb:text-[#369F21] rb:font-medium": description.key === 'is_active' && item.is_active,
                    })}>{(description.children as string)}</span>
                  </div>
                ))}
                <Button className="rb:mt-2" type="primary" ghost block onClick={() => handleEdit(item)}>{t('modelNew.configureBtn')}</Button>
              </RbCard>
            ))}
          </div>
        )
      }
    </>
  )
})

export default Group