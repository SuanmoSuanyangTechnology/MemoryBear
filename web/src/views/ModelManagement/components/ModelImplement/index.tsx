/**
 * Model Implementation Component
 * Manages model implementations with API keys for group models
 * Allows adding and removing model-API key associations
 */

import { type FC, useRef } from "react";
import { useTranslation } from 'react-i18next';
import { Flex, Button, Space, App } from 'antd'

import type { SubModelModalRef } from './types'
import type { ModelListItem } from '../../types'
import SubModelModal from './SubModelModal'
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'

/**
 * Component props
 */
interface ModelImplementProps {
  /** Model type */
  type?: string;
  /** Current model list value */
  value?: ModelListItem['members'];
  /** Callback when value changes */
  onChange?: (value: any) => void;
}

/**
 * Model implementation management component
 */
const ModelImplement: FC<ModelImplementProps> = ({ type, value, onChange }) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const subModelModalRef = useRef<SubModelModalRef>(null)

  /** Open add implementation modal */
  const handleAdd = () => {
    if (!type || type.trim() === '') {
      message.warning(t('common.selectPlaceholder', { title: t('modelNew.type') }))
      return
    }
    subModelModalRef.current?.handleOpen()
  }
  /** Delete model implementation */
  const handleDelete = (vo: any) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: [vo.model_name, vo.api_key].join(' / ') }),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        onChange?.(value?.filter((item: any) => item.id !== vo.id))
      }
    })
  }
  /** Refresh model list after adding implementations */
  const handleRefresh = (list: ModelListItem['members']) => {
    const existingModels = value || [];
    let updatedModels = [...existingModels];

    const provider = list[0].provider

    updatedModels = updatedModels.filter(item => item.provider !== provider)
    updatedModels = [...updatedModels, ...list]

    onChange?.([...updatedModels]);
  }

  /** Group models by provider */
  const groupedByProvider: Record<string, ModelListItem['members']> = (value || []).reduce((acc, item) => {
    const provider = item.provider || 'unknown';
    if (!acc[provider]) acc[provider] = [];
    acc[provider].push(item);
    return acc;
  }, {} as Record<string, ModelListItem['members']>);

  return (
    <div>
      <Flex justify="space-between" align="center">
        {t('modelNew.modelImplement')}

        <Space>
          <Button type="primary" onClick={handleAdd} className="rb:px-2! rb:h-6!">+ {t('modelNew.addImplement')}</Button>
          <Button size="small" className="rb:px-2! rb:h-6!">{t('modelNew.noAuth')}</Button>
        </Space>
      </Flex>


      <Flex vertical gap={12} className="rb:mt-2!">
        {!value || value.length === 0
        ? <Empty size={88} />
          : value.map((item: any) => {
          return (
            <Flex key={item.id} align="center" justify="space-between" className="rb:bg-gray-100 rb:rounded-lg rb:p-3!">
              <Flex gap={8} align="center">
                <div className="rb:font-medium">
                  {item.model_name}
                </div>
                <Tag>{String(item.provider).charAt(0).toUpperCase() + String(item.provider).slice(1)}</Tag>
              </Flex>
              <div
                className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                onClick={() => handleDelete(item)}
              ></div>
            </Flex>
          )
        })}
      </Flex>
      <SubModelModal
        ref={subModelModalRef}
        refresh={handleRefresh}
        type={type}
        groupedByProvider={groupedByProvider}
      />
    </div>
  )
}

export default ModelImplement