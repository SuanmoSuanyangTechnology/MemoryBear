import { type FC, useRef } from "react";
import { useTranslation } from 'react-i18next';
import { Flex, Button, Space, App } from 'antd'

import type { SubModelModalRef, ModelList } from './types'
import SubModelModal from './SubModelModal'
import Empty from '@/components/Empty'
import Tag from '@/components/Tag'

interface ModelImplementProps {
  type?: string;
  value?: any;
  onChange?: (value: any) => void;
}
const ModelImplement: FC<ModelImplementProps> = ({ type, value, onChange }) => {
  const { t } = useTranslation();
  const { modal, message } = App.useApp();
  const subModelModalRef = useRef<SubModelModalRef>(null)

  const handleAdd = () => {
    if (!type || type.trim() === '') {
      message.warning(t('common.selectPlaceholder', { title: t('modelNew.type') }))
      return
    }
    subModelModalRef.current?.handleOpen()
  }
  const handleDelete = (provider: string) => {
    modal.confirm({
      title: t('common.confirmDeleteDesc', { name: provider }),
      content: t('application.apiKeyDeleteContent'),
      okText: t('common.delete'),
      cancelText: t('common.cancel'),
      okType: 'danger',
      onOk: () => {
        onChange?.(value?.filter((item: any) => item.provider !== provider))
      }
    })
  }
  const handleRefresh = (list: ModelList[]) => {
    const existingModels = value || [];
    let updatedModels = [...existingModels];

    const provider = list[0].provider

    updatedModels = updatedModels.filter(item => item.provider !== provider)
    updatedModels = [...updatedModels, ...list]

    onChange?.([...updatedModels]);
  }

  const groupedByProvider: Record<string, ModelList[]> = (value || []).reduce((acc: Record<string, ModelList[]>, item: ModelList) => {
    const provider = item.provider || 'unknown';
    if (!acc[provider]) acc[provider] = [];
    acc[provider].push(item);
    return acc;
  }, {} as Record<string, ModelList[]>);

  return (
    <div>
      <Flex justify="space-between" align="center">
        {t('modelNew.modelImplement')}

        <Space>
          <Button type="primary" onClick={handleAdd} className="rb:px-2! rb:h-6!">+ {t('modelNew.addImplement')}</Button>
          <Button size="small" className="rb:px-2! rb:h-6!">{t('modelNew.noAuth')}</Button>
        </Space>
      </Flex>


      <div className="rb:bg-[#F5F6F7] rb:rounded-lg rb:p-3 rb:mt-2">
        {!value || value.length === 0
        ? <Empty size={88} />
          : Object.entries(groupedByProvider).map(([provider, items]: [string, ModelList[]]) => {
          return (
            <div key={provider} className="rb:mb-4 last:rb:mb-0">
              <Flex justify="space-between" align="center" className="rb:mb-2 last:rb:mb-0">
                <div className="rb:font-medium">{[...new Set(items?.map((vo) => vo.model_name))].join(', ')}</div>

                <div
                  className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                  onClick={() => handleDelete(provider)}
                ></div>
              </Flex>
              <Tag className="rb:mb-2">{t(`modelNew.${provider}`)}</Tag>
            </div>
          )
        })}
      </div>
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