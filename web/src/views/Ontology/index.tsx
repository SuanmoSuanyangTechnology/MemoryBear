/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:15 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 14:10:15 
 */
import { type FC, useState, useRef, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Row, Col, Button, Flex, Divider, Space, App, Tooltip } from 'antd'

import SearchInput from '@/components/SearchInput';
import OntologyModal from './components/OntologyModal'
import type { OntologyModalRef, OntologyItem, Query, OntologyImportModalRef, OntologyExportModalRef } from './types'
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { getOntologyScenesUrl, deleteOntologyScene } from '@/api/ontology'
import { formatDateTime } from '@/utils/format'
import OntologyImportModal from './components/OntologyImportModal'
import OntologyExportModal from './components/OntologyExportModal'

/**
 * Ontology management page component
 * Displays a list of ontology scenes with search, create, import, export functionality
 */
const Ontology: FC = () => {
  // Hooks
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { modal, message } = App.useApp();
  
  // State
  const [query, setQuery] = useState<Query>({});
  
  // Refs
  const scrollListRef = useRef<PageScrollListRef>(null)
  const entityModalRef = useRef<OntologyModalRef>(null)
  const ontologyImportModalRef = useRef<OntologyImportModalRef>(null)
  const ontologyExportModalRef = useRef<OntologyExportModalRef>(null)

  /**
   * Open modal to create a new ontology scene
   */
  const handleCreate = () => {
    entityModalRef.current?.handleOpen()
  }
  
  /**
   * Open modal to edit an existing ontology scene
   * @param record - The ontology item to edit
   * @param e - Mouse event to prevent propagation
   */
  const handleEdit = (record: OntologyItem, e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    entityModalRef.current?.handleOpen(record)
  }
  
  /**
   * Delete an ontology scene with confirmation
   * @param item - The ontology item to delete
   * @param e - Mouse event to prevent propagation
   */
  const handleDelete = (item: OntologyItem, e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: item.scene_name }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        deleteOntologyScene(item.scene_id)
          .then(() => {
            message.success(t('common.deleteSuccess'))
            scrollListRef.current?.refresh()
          })
      }
    })
  }
  
  /**
   * Navigate to ontology detail page
   * @param record - The ontology item to view
   */
  const handleJump = (record: OntologyItem) => {
    navigate(`/ontology/${record.scene_id}`)
  }
  
  /**
   * Refresh the ontology list
   */
  const handleRefresh = () => {
    scrollListRef.current?.refresh()
  }
  
  /**
   * Open export modal
   */
  const handleExport = () => {
    ontologyExportModalRef.current?.handleOpen()
  }
  
  /**
   * Open import modal
   */
  const handleImport = () => {
    ontologyImportModalRef.current?.handleOpen()
  }

  return (
    <>
      <Row gutter={16} className="rb:mb-4">
        <Col span={8}>
          <SearchInput
            placeholder={t('ontology.searchPlaceholder')}
            onSearch={(value) => setQuery({ scene_name: value })}
            className="rb:w-full!"
          />
        </Col>
        <Col span={16} className="rb:text-right">
          <Space size={12}>
            <Button onClick={handleExport}>
              {t('ontology.export')}
            </Button>
            <Button onClick={handleImport}>
              {t('ontology.import')}
            </Button>
            <Button type="primary" onClick={handleCreate}>
              + {t('ontology.create')}
            </Button>
          </Space>
        </Col>
      </Row>

      <PageScrollList<OntologyItem, Query>
        ref={scrollListRef}
        url={getOntologyScenesUrl}
        query={query}
        column={3}
        renderItem={(item) =>(
          <RbCard
            title={item.scene_name}
            extra={<Tag>{item.type_num} {t('ontology.typeCount')}</Tag>}
            onClick={() => handleJump(item)}
            className="rb:cursor-pointer"
          >
            <div
              className="rb:flex rb:gap-2 rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
            >
              <span className="rb:whitespace-nowrap">{t(`ontology.scene_description`)}</span>
              <Tooltip title={item.scene_description} placement="topRight">
                <span className="rb:font-medium rb:flex-1 rb:text-right rb:text-ellipsis rb:overflow-hidden rb:whitespace-nowrap">{item.scene_description}</span>
              </Tooltip>
            </div>
            {(['created_at', 'updated_at'] as const).map(key => (
              <div
                key={key}
                className="rb:flex rb:gap-2 rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-5 rb:mb-3"
              >
                <span className="rb:whitespace-nowrap">{t(`ontology.${key}`)}</span>
                <span className="rb:font-medium">{formatDateTime(item[key])}</span>
              </div>
            ))}
            <Divider size="middle" />
            <Flex gap={8} wrap>
              <div className="rb:text-[#5B6167] rb:leading-4.5">{t('ontology.entityTypes')}: </div>
              {item.entity_type?.map((type, i) => (
                <Tag key={i} color={i % 2 ? 'processing' : 'success'}>{type}</Tag>
              ))}
              {item.type_num > 3 && (
                <Tag color="default">+{item.type_num - 3}</Tag>
              )}
            </Flex>

            <div className="rb:mt-4 rb:text-[12px] rb:leading-4 rb:font-regular rb:text-[#5B6167] rb:flex rb:items-center rb:justify-end">
              <Space size={16}>
                <div
                  className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')] rb:hover:bg-[url('@/assets/images/edit_hover.svg')]"
                  onClick={(e) => handleEdit(item, e)}
                ></div>
                <div
                  className="rb:w-5 rb:h-5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')] rb:hover:bg-[url('@/assets/images/delete_hover.svg')]"
                  onClick={(e) => handleDelete(item, e)}
                ></div>
              </Space>
            </div>
          </RbCard>
        )}
      />

      <OntologyModal
        ref={entityModalRef}
        refresh={handleRefresh}
      />
      <OntologyImportModal
        ref={ontologyImportModalRef}
        refresh={handleRefresh}
      />
      <OntologyExportModal
        ref={ontologyExportModalRef}
        refresh={handleRefresh}
      />
    </>
  )
}

export default Ontology