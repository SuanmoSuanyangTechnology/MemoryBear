/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-03-20 16:36:02
 */
import { type FC, useState, useRef } from 'react';
import type { MenuInfo } from 'rc-menu/lib/interface';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Row, Col, Flex, Space, App, Tooltip, Dropdown } from 'antd'

import SearchInput from '@/components/SearchInput';
import OntologyModal from './components/OntologyModal'
import type { OntologyModalRef, OntologyItem, Query, OntologyImportModalRef, OntologyExportModalRef } from './types'
import RbCard from '@/components/RbCard'
import Tag from '@/components/Tag'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { getOntologyScenesUrl, deleteOntologyScene } from '@/api/ontology'
import { formatDateTime } from '@/utils/format'
import OntologyImportModal from './components/OntologyImportModal'
import OntologyExportModal from './components/OntologyExportModal'
import RbButton from '@/components/RbButton'

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
  const handleEdit = (record: OntologyItem, e: MenuInfo) => {
    e.domEvent.stopPropagation();
    entityModalRef.current?.handleOpen(record)
  }
  
  /**
   * Delete an ontology scene with confirmation
   * @param item - The ontology item to delete
   * @param e - Menu click info
   */
  const handleDelete = (item: OntologyItem, e: MenuInfo) => {
    e.domEvent.stopPropagation();
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
      <Flex align="center" justify="space-between" className="rb:mb-4!">
        <SearchInput
          placeholder={t('ontology.searchPlaceholder')}
          onSearch={(value) => setQuery({ scene_name: value })}
        />
        <Space size={12}>
          <RbButton ghost type="primary" onClick={handleExport}>
            {t('ontology.export')}
          </RbButton>
          <RbButton ghost type="primary" onClick={handleImport}>
            {t('ontology.import')}
          </RbButton>
          <RbButton type="primary" onClick={handleCreate}>
            + {t('ontology.create')}
          </RbButton>
        </Space>
      </Flex>

      <PageScrollList<OntologyItem, Query>
        ref={scrollListRef}
        url={getOntologyScenesUrl}
        query={query}
        column={3}
        renderItem={(item) =>(
          <RbCard
            title={
              <Flex justify="space-between">
                <Flex gap={4} vertical>
                  {item.scene_name}
                  <Space size={8}>
                    <Tag>{item.type_num} {t('ontology.typeCount')}</Tag>
                    {item.is_system_default  && <Tag color="warning">{t('common.default')}</Tag>}
                  </Space>
                </Flex>
                <Dropdown
                  menu={{
                    items: [
                      {
                        key: 'edit',
                        icon: <div className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit.svg')]" />,
                        label: t('common.edit'),
                        onClick: (e: MenuInfo) => handleEdit(item, e),
                      },
                      {
                        key: 'delete',
                        icon: <div className="rb:size-6 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete.svg')]" />,
                        label: t('common.delete'),
                        onClick: (e: MenuInfo) => handleDelete(item, e),
                      },
                    ]
                  }}
                  placement="bottomRight"
                >
                  <div className="rb:cursor-pointer rb:size-6 rb:bg-[url('@/assets/images/common/more.svg')] rb:hover:bg-[url('@/assets/images/common/more_hover.svg')]"></div>
                </Dropdown>
              </Flex>
            }
            isNeedTooltip={false}
            headerClassName="rb:pb-0!"
            onClick={() => handleJump(item)}
            className="rb:cursor-pointer!"
          >
            <Tooltip title={item.scene_description}>
              <div className="rb:h-10 rb:wrap-break-word rb:line-clamp-2 rb:leading-5">{item.scene_description}</div>
            </Tooltip>

            <Flex gap={8} wrap align="center" className="rb:mt-2!">
              <Flex gap={8} className="rb:flex-1 rb:overflow-hidden rb:wrap-break-word! rb:line-clamp-1!">
                {item.entity_type?.map((type, i) => (
                  <span key={i} className="rb:bg-[#F6F6F6] rb:rounded-md rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">{type}</span>
                ))}
              </Flex>
              {item.type_num > 3 && (
                <span className="rb:bg-[#F6F6F6] rb:rounded-full rb:py-px rb:px-1 rb:text-[12px] rb:leading-4.5">+{item.type_num - 3}</span>
              )}
            </Flex>

            <Row className="rb:mt-4!">
              {(['created_at', 'updated_at'] as const).map(key => (
                <Col
                  key={key}
                  span={12}
                  className="rb:text-[#5B6167] rb:text-[12px]! rb:leading-4.5"
                >
                  <div>{t(`ontology.${key}`)}</div>
                  <div>{formatDateTime(item[key])}</div>
                </Col>
              ))}
            </Row>
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