/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:50:10 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:50:10 
 */
/**
 * Model List View
 * Displays models grouped by provider with key configuration
 * Shows model tags and allows viewing model details
 */

import { useRef, useState, useEffect, type FC } from 'react';
import { Button, Flex, Row, Col } from 'antd'
import { useTranslation } from 'react-i18next';

import type { ProviderModelItem, KeyConfigModalRef, ModelListDetailRef } from './types'
import RbCard from '@/components/RbCard/Card'
import { getModelNewList } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import Tag from '@/components/Tag';
import KeyConfigModal from './components/KeyConfigModal'
import ModelListDetail from './components/ModelListDetail'
import { getLogoUrl } from './utils'

/**
 * Model list component
 */
const ModelList: FC<{ query: any }> = ({ query }) => {
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

  return (
    <>
      {list.length === 0
        ? <PageEmpty />
        :(
          <div className="rb:grid rb:grid-cols-4 rb:gap-4">
            {list.map(item => (
              <RbCard
                key={item.provider}
                title={t(`modelNew.${item.provider}`)}
                avatarUrl={getLogoUrl(item.logo)}
                avatar={
                  <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                    {item.provider[0].toUpperCase()}
                  </div>
                }
                bodyClassName="rb:relative rb:pb-[64px]! rb:h-[calc(100%-64px)]!"
              >
                <Flex gap={8} wrap>{item.tags.map(tag => <Tag key={tag}>{t(`modelNew.${tag}`)}</Tag>)}</Flex>
                <div className="rb:absolute rb:bottom-4 rb:left-6 rb:right-6">
                  <Row gutter={12}>
                    <Col span={12}>
                      <Button block onClick={() => handleShowModel(item)}>{t('modelNew.showModel')}</Button>
                    </Col>
                    <Col span={12}>
                      <Button type="primary" ghost block onClick={() => handleKeyConfig(item)}>{t('modelNew.keyConfig')}</Button>
                    </Col>
                  </Row>
                </div>
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
        refresh={getList}
      />
    </>
  )
}

export default ModelList