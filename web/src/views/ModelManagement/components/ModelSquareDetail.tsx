import { useState, useImperativeHandle, forwardRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Space, App, Flex, Tooltip, Divider } from 'antd'
import { UsergroupAddOutlined } from '@ant-design/icons';

import type { ModelPlaza, ModelPlazaItem, ModelSquareDetailRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import { getModelPlaza, addModelPlaza } from '@/api/models'
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag';
import PageEmpty from '@/components/Empty/PageEmpty';
import { getLogoUrl } from '../utils'

interface ModelSquareDetailProps {
  refresh: () => void;
  handleEdit: (vo: ModelPlazaItem) => void;
}
const ModelSquareDetail = forwardRef<ModelSquareDetailRef, ModelSquareDetailProps>(({ refresh, handleEdit }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const [model, setModel] = useState<ModelPlaza>({} as ModelPlaza)
  const [open, setOpen] = useState(false);

  const [list, setList] = useState<ModelPlazaItem[]>([])

  const handleOpen = (vo: ModelPlaza) => {
    setModel(vo)
    setOpen(true)
    getList(vo)
  }
  const handleClose = () => {
    setOpen(false)
    refresh()
  }
  const getList = (vo: ModelPlaza) => {
    getModelPlaza({ provider: vo.provider })
      .then(res => {
        const response = res as ModelPlaza[]
        setList(response.length > 0 ? response[0].models : [])
      })
  }
  const handleAdd = (item: ModelPlazaItem) => {
    addModelPlaza(item.id)
      .then(() => {
        message.success(`${item.name}${t('modelNew.addSuccess')}`)
        getList(model)
      })
  }

  useImperativeHandle(ref, () => ({
      handleOpen,
  }));

  return (
    <RbDrawer
      title={<>{t(`modelNew.${model.provider}`)} {t('modelNew.modelList')} ({list.length}{t('modelNew.item')})</>}
      open={open}
      onClose={handleClose}
    >
      <div className="rb:h-full rb:overflow-y-auto">
        {list.length === 0 
          ? <PageEmpty />
          : <div className="rb:grid rb:grid-cols-2 rb:gap-4">
            {list.map(item => (
              <RbCard
                key={item.id}
                title={item.name}
                subTitle={<Tag className="rb:mt-1">{t(`modelNew.${item.type}`)}</Tag>}
                avatarUrl={getLogoUrl(item.logo)}
                avatar={
                  <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                    {item.name[0]}
                  </div>
                }
                bodyClassName="rb:relative rb:pb-[80px]! rb:h-[calc(100%-64px)]!"
              >
                <Tooltip title={item.description}>
                  <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:font-regular rb:wrap-break-word rb:line-clamp-2 rb:mt-3">{item.description}</div>
                </Tooltip>
                <Flex gap={8} wrap className="rb:mt-3!">{item.tags.map((tag, tagIndex) => <Tag key={tagIndex}>{tag}</Tag>)}</Flex>
                <div className="rb:absolute rb:bottom-4 rb:left-6 rb:right-6">
                  <Divider size="middle" />
                  <Flex justify="space-between">
                    <Space size={8}><UsergroupAddOutlined /> {item.add_count}</Space>
                    <Space>
                      {!item.is_official && <Button type="primary" disabled={item.is_deprecated} onClick={() => handleEdit(item)}>{t('modelNew.edit')}</Button>}
                      {item.is_added
                        ? <Button type="primary" disabled>{t('modelNew.added')}</Button>
                        : <Button type="primary" ghost disabled={item.is_deprecated} onClick={() => handleAdd(item)}>+ {t('common.add')}</Button>
                      }
                    </Space>
                  </Flex>
                </div>
              </RbCard>
            ))}
            </div>
          }
      </div>
    </RbDrawer>
  );
});

export default ModelSquareDetail;