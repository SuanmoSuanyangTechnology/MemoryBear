import { useState, useImperativeHandle, forwardRef, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Switch, Row, Col, Space, Tooltip } from 'antd'

import type { ProviderModelItem, ModelListItem, ModelListDetailRef, MultiKeyConfigModalRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag';
import PageEmpty from '@/components/Empty/PageEmpty';
import MultiKeyConfigModal from './MultiKeyConfigModal'
import { getModelNewList, updateModelStatus } from '@/api/models'
import { getLogoUrl } from '../utils'

interface ModelListDetailProps {
  refresh?: () => void;
}

const ModelListDetail = forwardRef<ModelListDetailRef, ModelListDetailProps>(({ refresh }, ref) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<ProviderModelItem>({} as ProviderModelItem)
  const [list, setList] = useState<ModelListItem[]>([])
  const multiKeyConfigModalRef = useRef<MultiKeyConfigModalRef>(null)
  const [loading, setLoading] = useState(false)

  const handleOpen = (vo: ProviderModelItem) => {
    setOpen(true)
    getData(vo)
  }

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
  const handleKeyConfig = (vo: ModelListItem) => {
    multiKeyConfigModalRef.current?.handleOpen(vo, data.provider)
  }
  const handleChange = (vo: ModelListItem) => {
    setLoading(true)
    updateModelStatus(vo.id, { is_active: !vo.is_active })
      .finally(() => {
        getData(data)
        setLoading(false)
      })
  }

  const handleClose = () => {
    setOpen(false)
    refresh?.()
  }
  const handleRefresh = () => {
    getData(data)
  }

  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  return (
    <RbDrawer
      title={<>{data.provider} {t('modelNew.modelList')} ({list.length}{t('modelNew.item')})</>}
      open={open}
      onClose={handleClose}
    >
      {list.length === 0 
        ? <PageEmpty />
        : <div className="rb:grid rb:grid-cols-2 rb:gap-4">
          {list.map(item => (
            <RbCard
              key={item.id}
              title={item.name}
              subTitle={<Space>
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