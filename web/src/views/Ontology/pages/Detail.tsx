/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:20 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:20 
 */
import { type FC, useEffect, useState, useRef } from 'react'
import { useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { App, Row, Col, Tooltip, Space, Button } from 'antd'

import PageHeader from '../components/PageHeader'
import { getOntologyClassList, deleteOntologyClass } from '@/api/ontology'
import type { OntologyClassData, OntologyClassModalRef, OntologyClassExtractModalRef, OntologyClassItem } from '@/views/Ontology/types'
import RbCard from '@/components/RbCard/Card';
import OntologyClassModal from '../components/OntologyClassModal'
import SearchInput from '@/components/SearchInput';
import OntologyClassExtractModal from '../components/OntologyClassExtractModal'
import BodyWrapper from '@/components/Empty/BodyWrapper'

/**
 * Ontology detail page component
 * Displays and manages classes within a specific ontology scene
 */
const Detail: FC = () => {
  // Hooks
  const { t } = useTranslation();
  const { id } = useParams()
  const { modal, message } = App.useApp()
  
  // Refs
  const ontologyClassModalRef = useRef<OntologyClassModalRef>(null)
  const ontologyClassExtractModalRef = useRef<OntologyClassExtractModalRef>(null)
  
  // State
  const [query, setQuery] = useState<{
    class_name?: string;
  }>({});
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<OntologyClassData>({} as OntologyClassData)

  // Fetch data when component mounts or dependencies change
  useEffect(() => {
    getData()
  }, [id, query])

  /**
   * Fetch ontology class list data
   */
  const getData = () => {
    if (!id) return;
    setLoading(true)
    getOntologyClassList({
      ...query,
      scene_id: id
    })
      .then(res => {
        setData(res as OntologyClassData)
      })
      .finally(() => {
        setLoading(false)
      })
  }
  
  /**
   * Delete an ontology class with confirmation
   * @param item - The class item to delete
   */
  const handleDelete = (item: OntologyClassItem) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.class_name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteOntologyClass(item.class_id)
          .then(() => {
            getData();
            message.success(t('common.deleteSuccess'))
          })
      }
    })
  }
  
  /**
   * Open modal to add a new class
   */
  const handleAdd = () => {
    ontologyClassModalRef.current?.handleOpen(data.scene_id)
  }
  
  /**
   * Open modal to extract classes using LLM
   */
  const handleExtract = () => {
    ontologyClassExtractModalRef.current?.handleOpen(data)
  }

  return (
    <>
      <PageHeader
        name={data.scene_name}
        subTitle={<div>{data.scene_description}</div>}
        extra={<Space>
          <Button type="primary" ghost className="rb:h-6! rb:px-2! rb:leading-5.5!" onClick={handleAdd}>+ {t('ontology.addClass')}</Button>
          <Button className="rb:h-6! rb:px-2! rb:leading-5.5!" type="primary" onClick={handleExtract}>+ {t('ontology.extract')}</Button>
        </Space>}
      />

      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:py-3 rb:px-4">
        <Row gutter={16} className="rb:mb-4">
          <Col span={6} offset={18}>
            <SearchInput
              placeholder={t('ontology.searchPlaceholder')}
              onSearch={(value) => setQuery({ class_name: value })}
              className="rb:w-full!"
            />
          </Col>
        </Row>
        <BodyWrapper loading={loading} empty={!data.items?.length}>
          <Row gutter={[16, 16]}>
            {data.items?.map(item => (
              <Col key={item.class_id} span={6}>
                <RbCard
                  title={item.class_name}
                  extra={<div
                    className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                    onClick={() => handleDelete(item)}
                  ></div>}
                  className="rb:bg-transparent!"
                >
                  <Tooltip title={item.class_description}>
                    <div className="rb:h-8.5 rb:text-[#5B6167] rb:text-[12px] rb:leading-4.25 rb:font-regular rb:-mt-1 rb:wrap-break-word rb:line-clamp-2">{item.class_description}</div>
                  </Tooltip>
                </RbCard>
              </Col>
            ))}
          </Row>
        </BodyWrapper>
      </div>

      <OntologyClassModal
        ref={ontologyClassModalRef}
        refresh={getData}
      />
      <OntologyClassExtractModal
        ref={ontologyClassExtractModalRef}
        refresh={getData}
      />
    </>
  )
}

export default Detail