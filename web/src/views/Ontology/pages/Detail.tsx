/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:20 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 16:35:14
 */
import { type FC, useEffect, useState, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { App, Row, Col, Tooltip, Space, Button, Flex } from 'antd'

import PageHeader from '@/components/Layout/PageHeader'
import { getOntologyClassList, deleteOntologyClass } from '@/api/ontology'
import type { OntologyClassData, OntologyClassModalRef, OntologyClassExtractModalRef, OntologyClassItem } from '@/views/Ontology/types'
import RbCard from '@/components/RbCard';
import OntologyClassModal from '../components/OntologyClassModal'
import SearchInput from '@/components/SearchInput';
import OntologyClassExtractModal from '../components/OntologyClassExtractModal'
import BodyWrapper from '@/components/Empty/BodyWrapper'
import Tag from '@/components/Tag'

/**
 * Ontology detail page component
 * Displays and manages classes within a specific ontology scene
 */
const Detail: FC = () => {
  // Hooks
  const { t } = useTranslation();
  const navigate = useNavigate()
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
        title={<Space>
          {data.scene_name}
          {data.is_system_default ? <Tag color="warning">{t('common.default')}</Tag> : undefined}
          <Tooltip title={data.scene_description}>
            <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
          </Tooltip>
        </Space>}
        extra={<Space size={12}>
          {data.is_system_default ? undefined : (<Space>
            <Button type="primary" ghost className="rb:h-6! rb:px-2! rb:leading-5.5!" onClick={handleAdd}>+ {t('ontology.addClass')}</Button>
            <Button className="rb:h-6! rb:px-2! rb:leading-5.5!" type="primary" onClick={handleExtract}>+ {t('ontology.extract')}</Button>
          </Space>)}
          <Flex align="center" className="rb:leading-5 rb:text-[14px] rb:text-[#5B6167] rb:font-regular rb:cursor-pointer" onClick={() => navigate(-1)}>
            <div
              className="rb:mr-2 rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/logout.svg')]"
            ></div>
            {t('common.return')}
          </Flex>
        </Space>}
      />

      <div className="rb:h-[calc(100vh-64px)] rb:overflow-y-auto rb:pb-3 rb:px-3">
        <Row gutter={12} className="rb:mb-4">
          <Col span={6} offset={18}>
            <SearchInput
              placeholder={t('ontology.classSearchPlaceholder')}
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
                  extra={data.is_system_default ? undefined : (<div
                    className="rb:size-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/common/delete.svg')] rb:hover:bg-[url('@/assets/images/common/delete_hover.svg')]"
                    onClick={() => handleDelete(item)}
                  ></div>)}
                >
                  <Tooltip title={item.class_description}>
                    <div className="rb:h-10 rb:text-[#5B6167] rb:leading-5 rb:font-regular rb:wrap-break-word rb:line-clamp-2">{item.class_description}</div>
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