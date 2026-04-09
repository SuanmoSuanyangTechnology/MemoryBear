/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:10 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-27 19:18:55
 */
/**
 * Model List View
 * Displays models grouped by provider with key configuration
 * Shows model tags and allows viewing model details
 */

import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Button, Flex, Row, Col, Tooltip, Popover } from 'antd'
import { useTranslation } from 'react-i18next';

import type { ProviderModelItem, KeyConfigModalRef, ModelListDetailRef, ModelListItem, BaseRef } from './types'
import RbCard from '@/components/RbCard'
import { getModelNewList } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import Tag from '@/components/Tag';
import KeyConfigModal from './components/KeyConfigModal'
import ModelListDetail from './components/ModelListDetail'
import { getListLogoUrl } from './utils'

/**
 * Model list component
 */
const ModelList = forwardRef<BaseRef, { query: any; handleEdit: (vo?: ModelListItem) => void; handleCloseModel: () => void; }>(({ query, handleEdit, handleCloseModel }, ref) => {
  const { t } = useTranslation();
  const keyConfigModalRef = useRef<KeyConfigModalRef>(null)
  const modelListDetailRef = useRef<ModelListDetailRef>(null)
  const [list, setList] = useState<ProviderModelItem[]>([])

  useEffect(() => {
    getList()
  }, [query])
  /** Fetch model list grouped by provider */
  const getList = () => {
    getModelNewList({
      ...query,
      is_composite: false,
    })
      .then(res => {
        setList((res || []) as ProviderModelItem[])
      })
  }

  /** Open model detail drawer */
  const handleShowModel = (vo: ProviderModelItem) => {
    modelListDetailRef.current?.handleOpen(vo)
  }
  /** Open key configuration modal */
  const handleKeyConfig = (vo: ProviderModelItem) => {
    keyConfigModalRef.current?.handleOpen(vo)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    getList,
    modelListDetailRefresh: () => modelListDetailRef.current?.handleRefresh()
  }));
  return (
    <>
      {list.length === 0
        ? <PageEmpty />
        :(
          <div className="rb:grid rb:grid-cols-4 rb:gap-4">
            {list.map(item => (
              <RbCard
                key={item.provider}
                avatarUrl={getListLogoUrl(item.provider, item.logo)}
                avatarText={item.provider[0].toUpperCase()}
                title={<Flex vertical gap={6}>
                  <Tooltip title={String(item.provider).charAt(0).toUpperCase() + String(item.provider).slice(1)}>
                    <div className="rb:wrap-break-word rb:line-clamp-1">{String(item.provider).charAt(0).toUpperCase() + String(item.provider).slice(1)}</div>
                  </Tooltip>

                  <Popover content={
                    <Flex gap={8} className="rb:overflow-hidden rb:flex-nowrap rb:w-auto!">{item.tags.map(tag => <Tag key={tag} className="rb:shrink-0">{t(`modelNew.${tag}`)}</Tag>)}</Flex>
                  }>
                    <Flex gap={8} className="rb:overflow-hidden rb:flex-nowrap rb:w-auto!">
                      {item.tags.map(tag => <Tag key={tag} className="rb:shrink-0">{t(`modelNew.${tag}`)}</Tag>)}
                    </Flex>
                  </Popover>
                </Flex>}
                isNeedTooltip={false}
                footer={<Row gutter={9} className="rb:pt-2!">
                  <Col span={12}>
                    <Button className="rb:h-9!" block onClick={() => handleShowModel(item)}>{t('modelNew.showModel')}</Button>
                  </Col>
                  <Col span={12}>
                    <Button className="rb:h-9!" type="primary" ghost block onClick={() => handleKeyConfig(item)}>{t('modelNew.keyConfig')}</Button>
                  </Col>
                </Row>}
              >
              </RbCard>
            ))}
          </div>
        )
      }

      <KeyConfigModal
        ref={keyConfigModalRef}
        refresh={getList}
      />
      <ModelListDetail
        ref={modelListDetailRef}
        query={{
          ...query,
          is_composite: false,
        }}
        refresh={getList}
        handleEdit={handleEdit}
        handleCloseConfig={handleCloseModel}
      />
    </>
  )
})

export default ModelList