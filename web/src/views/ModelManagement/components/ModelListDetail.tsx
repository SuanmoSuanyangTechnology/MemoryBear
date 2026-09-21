/**
 * Model List Detail Drawer
 * Displays detailed list of models from a specific provider
 * Allows filtering by type and configuring API keys
 */

import { useState, useImperativeHandle, forwardRef, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Switch, Row, Col, Tooltip, Flex } from 'antd'
import clsx from 'clsx';

import type { ProviderModelItem, ModelListItem, ModelListDetailRef, MultiKeyConfigModalRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag';
import PageEmpty from '@/components/Empty/PageEmpty';
import MultiKeyConfigModal from './MultiKeyConfigModal'
import { getModelNewList, updateModelStatus, modelTypeUrl } from '@/api/models'
import { getLogoUrl } from '../utils'
import CustomSelect from '@/components/CustomSelect'
import { formatModelType } from '../utils'
import OverflowTags from '@/components/OverflowTags';

/**
 * Component props
 */
interface ModelListDetailProps {
  /** Callback to refresh parent list */
  refresh?: () => void;
  handleEdit: (vo?: ModelListItem) => void;
  handleCloseConfig?: () => void;
  query?: any;
}

/**
 * Model list detail drawer component
 */
const ModelListDetail = forwardRef<ModelListDetailRef, ModelListDetailProps>(({ refresh, handleEdit, handleCloseConfig, query }, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ProviderModelItem>({} as ProviderModelItem)
  const [list, setList] = useState<ModelListItem[]>([])
  const multiKeyConfigModalRef = useRef<MultiKeyConfigModalRef>(null)
  const [loading, setLoading] = useState(false)
  const [type, setType] = useState<string | undefined>(undefined)

  /** Open drawer with provider model data */
  const handleOpen = (vo: ProviderModelItem) => {
    setType(undefined)
    setOpen(true)
    setData(vo)
  }

  useEffect(() => {
    if (!open) return
    setType(query?.type)
  }, [open, query?.type])

  useEffect(() => {
    if (!open) return
    getData()
  }, [open, type, data.provider])

  /** Fetch model data for provider */
  const getData = () => {
    if (!data.provider) return
  
    getModelNewList({
      provider: data.provider,
      type,
    })
      .then(res => {
        const response = res as ProviderModelItem[]
        setList(response[0].models)
      })
  }
  /** Open key configuration modal */
  const handleKeyConfig = (vo: ModelListItem) => {
    multiKeyConfigModalRef.current?.handleOpen(vo)
  }
  /** Toggle model active status */
  const handleChange = (vo: ModelListItem) => {
    setLoading(true)
    updateModelStatus(vo.id, { is_active: !vo.is_active })
      .finally(() => {
        getData()
        setLoading(false)
      })
  }

  /** Close drawer */
  const handleClose = () => {
    setType(undefined)
    setOpen(false)
    refresh?.()
    multiKeyConfigModalRef.current?.handleClose()
    handleCloseConfig?.()
  }
  /** Refresh model list */
  const handleRefresh = () => {
    getData()
  }
  /** Handle type filter change */
  const handleTypeChange = (value: string) => {
    setType(value)
  }

  /** Expose methods to parent component */
  useImperativeHandle(ref, () => ({
    handleOpen,
    handleRefresh,
  }));

  return (
    <RbDrawer
      title={<>{String(data.provider).charAt(0).toUpperCase() + String(data.provider).slice(1)} {t('modelNew.modelList')} ({list.length}{t('modelNew.item')})</>}
      open={open}
      onClose={handleClose}
    >
      <Row gutter={16}>
        <Col span={12}>
          <CustomSelect
            value={type}
            url={modelTypeUrl}
            hasAll={false}
            format={(items) => items.map((item) => ({ label: formatModelType(item), value: String(item) }))}
            onChange={handleTypeChange}
            className="rb:w-full"
            allowClear={true}
            placeholder={t('modelNew.type')}
          />
        </Col>
      </Row>
      {list.length === 0 
        ? <PageEmpty />
        : <div className="rb:grid rb:grid-cols-2 rb:gap-4 rb:mt-3">
          {list.map(item => (
            <RbCard
              key={item.id}
              title={item.name}
              subTitle={<Tag>{formatModelType(item.type)}</Tag>}
              avatarUrl={getLogoUrl(item.logo)}
              avatar={
                <Flex align="center" justify="center" className="rb:size-12 rb:rounded-lg rb:bg-blue-500 rb:text-[28px] rb:text-white">
                  {item.name[0]}
                </Flex>
              }
              extra={item.provider !== 'speedbear' && <Switch checked={item.is_active} disabled={loading} onChange={() => handleChange(item)} />}
              bodyClassName={clsx("rb:relative rb:h-[calc(100%-64px)]! rb:pt-3!", {
                "rb:pb-0!": item.provider === 'speedbear',
                "rb:pb-[64px]!": item.provider !== 'speedbear',
              })}
              variant="outlined"
            >
              <Tooltip title={item.description}>
                <div className="rb:text-gray-600 rb:text-[12px] rb:leading-4.5 rb:font-regular rb:wrap-break-word rb:line-clamp-2">{item.description}</div>
              </Tooltip>
              <dl className="rb:mt-3 rb:mb-0 rb:flex rb:flex-col rb:gap-2">
                {(['input_modalities', 'output_modalities', 'features'] as const).map(field => {
                  const values = [...new Set(item[field] ?? [])];
                  return (
                    <Flex
                      key={field}
                      justify="space-between"
                      gap={12}
                      className="rb:text-[14px] rb:leading-5"
                    >
                      <div className="rb:whitespace-nowrap rb:text-gray-600 rb:w-30 rb:shrink-0">
                        {t(field === 'features' ? 'modelNew.features' : `modelNew.${field}`)}
                      </div>

                      <div className="rb:flex-1 rb:text-right">
                        {values.length > 0
                          ? <OverflowTags
                            justify="flex-end"
                            items={values.map(value => <Tag key={value}>{t(`modelNew.${value}`)}</Tag>)}
                          />
                          : '-'
                        }
                      </div>
                    </Flex>
                  );
                })}
              </dl>
              {item.provider !== 'speedbear' &&
                <div className="rb:absolute rb:bottom-4 rb:left-6 rb:right-6">
                  <Row gutter={12}>
                    {!item.model_id && 
                      <Col span={12}>
                        <Button block onClick={() => handleEdit(item)}>{t('modelNew.modelConfiguration')}</Button>
                      </Col>
                    }
                    <Col span={!item.model_id ? 12 : 24}>
                      <Button type="primary" ghost block onClick={() => handleKeyConfig(item)}>{t('modelNew.keyConfig')}</Button>
                    </Col>
                  </Row>
                </div>
              }
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