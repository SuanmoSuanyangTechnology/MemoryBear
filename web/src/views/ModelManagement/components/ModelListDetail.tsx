/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:49:45 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:49:45 
 */
/**
 * Model List Detail Drawer
 * Displays detailed list of models from a specific provider
 * Allows filtering by type and configuring API keys
 */

import { useState, useImperativeHandle, forwardRef, useRef, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Switch, Row, Col, Space, Tooltip } from 'antd'

import type { ProviderModelItem, ModelListItem, ModelListDetailRef, MultiKeyConfigModalRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag';
import PageEmpty from '@/components/Empty/PageEmpty';
import MultiKeyConfigModal from './MultiKeyConfigModal'
import { getModelNewList, updateModelStatus, modelTypeUrl } from '@/api/models'
import { getLogoUrl } from '../utils'
import CustomSelect from '@/components/CustomSelect'

/**
 * Component props
 */
interface ModelListDetailProps {
  /** Callback to refresh parent list */
  refresh?: () => void;
}

/**
 * Model list detail drawer component
 */
const ModelListDetail = forwardRef<ModelListDetailRef, ModelListDetailProps>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ProviderModelItem>({} as ProviderModelItem)
  const [list, setList] = useState<ModelListItem[]>([])
  const multiKeyConfigModalRef = useRef<MultiKeyConfigModalRef>(null)
  const [loading, setLoading] = useState(false)
  const [type, setType] = useState<string | undefined | null>(null)

  /** Open drawer with provider model data */
  const handleOpen = (vo: ProviderModelItem) => {
    setType(null)
    setOpen(true)
    getData(vo)
  }

  /** Fetch model data for provider */
  const getData = (vo: ProviderModelItem) => {
    if (!vo.provider) return
  
    getModelNewList({
      provider: vo.provider
    })
      .then(res => {
        const response = res as ProviderModelItem[]
        setData(response[0])
        setList(response[0].models)
      })
  }
  /** Open key configuration modal */
  const handleKeyConfig = (vo: ModelListItem) => {
    multiKeyConfigModalRef.current?.handleOpen(vo, data.provider)
  }
  /** Toggle model active status */
  const handleChange = (vo: ModelListItem) => {
    setLoading(true)
    updateModelStatus(vo.id, { is_active: !vo.is_active })
      .finally(() => {
        getData(data)
        setLoading(false)
      })
  }

  /** Close drawer */
  const handleClose = () => {
    setType(null)
    setOpen(false)
    refresh?.()
  }
  /** Refresh model list */
  const handleRefresh = () => {
    getData(data)
  }
  /** Handle type filter change */
  const handleTypeChange = (value: string) => {
    setType(value)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  /** Filter models by selected type */
  const filterList = useMemo(() => {
    if (!type) return list
    return list.filter(vo => vo.type === type)
  }, [type, list])

  return (
    <RbDrawer
      title={<>{t(`modelNew.${data.provider}`)} {t('modelNew.modelList')} ({list.length}{t('modelNew.item')})</>}
      open={open}
      onClose={handleClose}
    >
      <Row gutter={16}>
        <Col span={12}>
          <CustomSelect
            value={type}
            url={modelTypeUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: t(`modelNew.${item}`), value: String(item) }))}
            onChange={handleTypeChange}
            className="rb:w-full"
            allowClear={true}
            placeholder={t('modelNew.type')}
          />
        </Col>
      </Row>
      {filterList.length === 0 
        ? <PageEmpty />
        : <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-3">
          {filterList.map(item => (
            <RbCard
              key={item.id}
              title={item.name}
              subTitle={<Space className="rb:mt-1!">
                <Tag>{t(`modelNew.${item.type}`)}</Tag>
                <Tag color="warning">{item.api_keys.length}{t('modelNew.apiKeyNum')}</Tag>
              </Space>}
              avatarUrl={getLogoUrl(item.logo)}
              avatar={
                <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                  {item.name[0]}
                </div>
              }
              extra={<Switch defaultChecked={item.is_active} disabled={loading} onChange={() => handleChange(item)} />}
              bodyClassName="rb:relative rb:pb-[64px]! rb:h-[calc(100%-64px)]!"
            >
              <Tooltip title={item.description}>
                <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:font-regular rb:wrap-break-word rb:line-clamp-2">{item.description}</div>
              </Tooltip>
              <div className="rb:absolute rb:bottom-4 rb:left-6 rb:right-6">
                <Row gutter={12}>
                  <Col span={24}>
                    <Button type="primary" ghost block onClick={() => handleKeyConfig(item)}>{t('modelNew.keyConfig')}</Button>
                  </Col>
                </Row>
              </div>
            </RbCard>
          ))}
          </div>
        }

      <MultiKeyConfigModal
        ref={multiKeyConfigModalRef}
        refresh={handleRefresh}
      />
    </RbDrawer>
  );
});

export default ModelListDetail;