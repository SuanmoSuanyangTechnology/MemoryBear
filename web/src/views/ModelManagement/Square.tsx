/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:14 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-25 12:19:24
 */
/**
 * Model Square View
 * Displays public model marketplace grouped by provider
 * Allows adding models and viewing details
 */

import { useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Button, Space, App, Flex, Tooltip } from 'antd'
import { UsergroupAddOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import type { ModelPlaza, ModelPlazaItem, BaseRef } from './types'
import RbCard from '@/components/RbCard'
import { getModelPlaza, addModelPlaza } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import Tag from '@/components/Tag';
import { getLogoUrl } from './utils'

/**
 * Model square component
 */
const ModelSquare = forwardRef <BaseRef, { query: any; }>(({ query }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [list, setList] = useState<ModelPlaza[]>([])
  useEffect(() => {
    getList()
  }, [query])
  /** Fetch model plaza list */
  const getList = () => {
    getModelPlaza(query)
      .then(res => {
        const response = res as ModelPlaza[]
        setList(response || [])
        if (!activeProvider) {
          setActiveProvider(response[0]?.provider || null)
        }
      })
  }

  /** Add model to workspace */
  const handleAdd = (item: ModelPlazaItem) => {
    addModelPlaza(item.id)
      .then(() => {
        message.success(`${item.name}${t('modelNew.addSuccess')}`)
        getList()
      })
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    getList,
  }));

  const [activeProvider, setActiveProvider] = useState<string | null>(null)
  return (
    <>
      {list.length === 0
        ? <PageEmpty />
        : <>
          <Space size={8} className="rb:mb-3!">
            {list.map(vo => (
              <div
                key={vo.provider}
                className={clsx('rb:border rb:border-[#171719] rb:rounded-full rb:px-2 rb:py-1 rb:cursor-pointer', {
                  'rb:text-white rb:bg-[#171719]': activeProvider === vo.provider,
                  'rb:text-[#171719]': activeProvider === vo.provider,
                })}
                onClick={() => setActiveProvider(vo.provider)}
              >{t(`modelNew.${vo.provider}`)}</div>
            ))}
          </Space>
          {list.filter(vo => vo.provider === activeProvider).map(vo => (
            <div key={vo.provider} className="rb:max-h-[calc(100%-50px)] rb:overflow-y-auto">
              <div className="rb:grid rb:grid-cols-3 rb:gap-4">
                {vo.models.map(item => (
                  <RbCard
                    key={item.id}
                    avatarUrl={getLogoUrl(item.logo)}
                    avatarText={item.name[0]}
                    title={
                      <Flex justify="space-between" gap={16}>
                        <Flex vertical gap={6}>
                          <Tooltip title={item.name}>
                            <div className="rb:wrap-break-word rb:line-clamp-1">{item.name}</div>
                          </Tooltip>
                          <Space size={8} className="rb:mt-1!">
                            <Tag>{t(`modelNew.${item.type}`)}</Tag>
                            {item.is_official && <Tag color="success">{t(`modelNew.official`)}</Tag>}
                          </Space>
                        </Flex>
                        <Button
                          size="small"
                          disabled={item.is_added || item.is_deprecated}
                          onClick={() => handleAdd(item)}
                        >{item.is_deprecated ? t('modelNew.deprecated') : '+'}</Button>
                    </Flex>
                    }
                    isNeedTooltip={false}
                    footer={<Flex justify="space-between" align="center" className="rb:text-[#5B6167] rb:text-[12px]">
                      @{t(`modelNew.${vo.provider}`)}
                      <Space size={4}><UsergroupAddOutlined /> {item.add_count}</Space>
                    </Flex>}
                  >
                    <Tooltip title={item.description}>
                      <div className="rb:h-10 rb:leading-5 rb:wrap-break-word rb:line-clamp-2">{item.description}</div>
                    </Tooltip>

                    <Flex gap={8} wrap align="center" className="rb:mt-2!">
                      <Flex gap={6}>
                        {item.tags?.slice(0, 2).map((type, i) => (
                          <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">{type}</div>
                        ))}
                      </Flex>
                      {item.tags.length > 2 && (
                        <Tooltip
                          title={<Flex wrap gap={6}>{item.tags?.slice(2, item.tags.length).map((type, i) => (
                            <div key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5 rb:text-[#171719]">{type}</div>
                          ))}</Flex>}
                          color="white"
                          placement="bottom"
                        >
                          <div className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">+{item.tags.length - 2}</div>
                        </Tooltip>
                      )}
                    </Flex>
                  </RbCard>
                ))}
              </div>
            </div>
          ))}
        </>
      }
    </>
  )
})

export default ModelSquare