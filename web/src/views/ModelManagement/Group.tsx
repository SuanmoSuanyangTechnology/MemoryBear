/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:00 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 18:50:41
 */
/**
 * Group Model View
 * Displays composite/group models in card grid layout
 * Supports filtering and configuration
 */

import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import clsx from 'clsx'
import { Button, Flex, Tooltip, Space } from 'antd'
import { useTranslation } from 'react-i18next';

import type { ProviderModelItem, ModelListItem, DescriptionItem, BaseRef } from './types'
import RbCard from '@/components/RbCard'
import { getModelNewList } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import { formatDateTime } from '@/utils/format';
import Tag from '@/components/Tag'

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
          <div className="rb:grid rb:grid-cols-4 rb:gap-3">
            {list.map(item => (
              <RbCard
                key={item.id}
                avatarUrl={item.logo}
                avatarText={item.name[0]}
                title={<Flex vertical gap={6}>
                  <Tooltip title={item.name}>
                    <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                  </Tooltip>
                  <Space>
                    <Tag color={item.is_active ? 'success' : 'error'}>{item.is_active ? t(`common.statusEnabled`) : t(`common.statusDisabled`)}</Tag>
                  </Space>
                </Flex>}
                isNeedTooltip={false}
                footer={<Button className="rb:h-9!" type="primary" ghost block onClick={() => handleEdit(item)}>{t('modelNew.configureBtn')}</Button>}
              >
                <Flex vertical gap={8}>
                  {formatData(item)?.map((description: DescriptionItem) => (
                    <div
                      key={description.key}
                      className="rb:flex rb:justify-between rb:text-[14px] rb:leading-5"
                    >
                      <span className="rb:whitespace-nowrap rb:text-[#5B6167]">{(description.label as string)}</span>
                      <span className={clsx({
                        "rb:font-medium": description.key === 'type',
                      })}>{(description.children as string)}</span>
                    </div>
                  ))}
                </Flex>
              </RbCard>
            ))}
          </div>
        )
      }
    </>
  )
})

export default Group