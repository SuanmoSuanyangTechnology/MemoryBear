import { type FC, useState, useRef, type MouseEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Row, Col, Button, Flex, Divider, Space, App, Tooltip } from 'antd'

import SearchInput from '@/components/SearchInput';
import OntologyModal from './components/OntologyModal'
import type { OntologyModalRef, OntologyItem, Query } from './types'
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { getOntologyScenesUrl, deleteOntologyScene } from '@/api/ontology'
import { formatDateTime } from '@/utils/format'

const Ontology: FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate()
  const { modal, message } = App.useApp();
  const [query, setQuery] = useState<Query>({});
  const scrollListRef = useRef<PageScrollListRef>(null)
  const entityModalRef = useRef<OntologyModalRef>(null)

  const handleCreate = () => {
    entityModalRef.current?.handleOpen()
  }
  const handleEdit = (record: OntologyItem, e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    entityModalRef.current?.handleOpen(record)
  }
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
  const handleJump = (record: OntologyItem) => {
    navigate(`/ontology/${record.scene_id}`)
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
          <Button type="primary" onClick={handleCreate}>
            + {t('ontology.create')}
          </Button>
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
        refresh={() => scrollListRef.current?.refresh()}
      />
    </>
  )
}

export default Ontology