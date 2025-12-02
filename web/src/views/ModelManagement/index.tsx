import { useState, useRef, type FC } from 'react';
import { Row, Col, Button } from 'antd'
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import ConfigModal from './components/ConfigModal'
import type { Model, DescriptionItem, ConfigModalRef } from './types'
import RbCard from '@/components/RbCard/Card'
import SearchInput from '@/components/SearchInput'
import PageScrollList, { type PageScrollListRef } from '@/components/PageScrollList'
import { getModelListUrl } from '@/api/models'
import { formatDateTime } from '@/utils/format';

const ModelManagement: FC = () => {
  const { t } = useTranslation();
  const [query, setQuery] = useState({})
  const configModalRef = useRef<ConfigModalRef>(null)
  const scrollListRef = useRef<PageScrollListRef>(null)

  const formatData = (data: Model) => {
    return [
      {
        key: 'type',
        label: t(`model.type`),
        children: data.type || '-',
      },
      {
        key: 'provider',
        label: t(`model.provider`),
        children: data.api_keys[0].provider || '-',
      },
      {
        key: 'is_active',
        label: t(`model.status`),
        children: data.is_active ? t(`common.statusEnabled`) : t(`common.statusDisabled`),
      },
      {
        key: 'created',
        label: t(`model.created`),
        children: data.created_at ? formatDateTime(data.created_at, 'YYYY-MM-DD HH:mm:ss') : '-',
      },
    ]
  }

  const handleEdit = (model?: Model) => {
    configModalRef?.current?.handleOpen(model)
  }
  const handleSearch = (value?: string) => {
    setQuery({ search: value })
  }

  return (
    <div className="rb:w-full">
      <Row className='rb:mb-[16px] rb:w-full'>
        <Col span={6}>
          <SearchInput
            placeholder={t('model.searchPlaceholder')}
            onSearch={handleSearch}
            style={{width: '100%'}}
          />
        </Col>
        <Col span={18} className="rb:text-right">
          <Button type="primary" onClick={() => handleEdit()}>{t('model.createModel')}</Button>
        </Col>
      </Row>

      <PageScrollList
        ref={scrollListRef}
        url={getModelListUrl}
        query={query}
        renderItem={(item: Model) => (
          <RbCard
            title={item.name}
          >
            {formatData(item)?.map((description: DescriptionItem) => (
              <div 
                key={description.key}
                className="rb:flex rb:justify-between rb:text-[#5B6167] rb:text-[14px] rb:leading-[20px] rb:mb-[12px]"
              >
                  <span className="rb:whitespace-nowrap">{(description.label as string)}</span>
                  <span className={clsx({
                    "rb:text-[#212332]": description.key !== 'is_active',
                    "rb:text-[#369F21] rb:font-medium": description.key === 'is_active' && item.is_active,
                  })}>{(description.children as string)}</span>
              </div>
            ))}
            <Button className="rb:mt-[8px]" type="primary" ghost block onClick={() => handleEdit(item)}>{t('model.configureBtn')}</Button>
          </RbCard>
        )}
      />

      <ConfigModal
        ref={configModalRef}
        refresh={() => scrollListRef?.current?.refresh()}
      />
    </div>
  )
}

export default ModelManagement