/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 14:10:15 
 * @Last Modified by: ZhaoYing
 * @Last Modified time: 2026-04-22 11:47:38
 */
import { type FC, useState, useRef } from 'react';
import type { MenuInfo } from 'rc-menu/lib/interface';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Row, Col, Flex, Space, Tooltip } from 'antd'

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
import MoreDropdown from '@/components/MoreDropdown'
import useDeleteConfirm from '@/hooks/useDeleteConfirm'
import OverflowTags from '@/components/OverflowTags'

/**
 * Ontology management page component
 * Displays a list of ontology scenes with search, create, import, export functionality
 */
const Ontology: FC = () => {
  // Hooks
  const { t } = useTranslation();
  const navigate = useNavigate()
  const deleteConfirm = useDeleteConfirm();
  
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
    deleteConfirm({
      name: item.scene_name,
      onOk: () => deleteOntologyScene(item.scene_id).then(() => scrollListRef.current?.refresh()),
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
                <MoreDropdown
                  items={[
                    {
                      key: 'edit',
                      icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/edit_bold.svg')]" />,
                      label: t('common.edit'),
                      onClick: (e: MenuInfo) => handleEdit(item, e),
                    },
                    {
                      key: 'delete',
                      icon: <div className="rb:size-4 rb:bg-cover rb:cursor-pointer rb:bg-[url('@/assets/images/common/delete_red_big.svg')]" />,
                      label: t('common.delete'),
                      onClick: (e: MenuInfo) => handleDelete(item, e),
                    },
                  ]}
                />
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

            <div className="rb:mt-2">
              <OverflowTags
                popoverProps={false}
                items={[...item.entity_type?.map((type, i) => <Tag key={i} variant="borderless" color="dark">{type}</Tag>), <Tag variant="borderless" color="dark">{`+${item.type_num - 3}`}</Tag>]}
                numTag={(num?: number) => <Tag variant="borderless" color="dark">{`+${item.type_num - 3 + (num ? num - 1 : 0)}`}</Tag>}
              />
            </div>

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