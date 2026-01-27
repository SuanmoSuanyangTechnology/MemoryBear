import { useState, useImperativeHandle, forwardRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Space, App } from 'antd'

import type { ModelPlaza, ModelPlazaItem, ModelSquareDetailRef } from '../types';
import RbDrawer from '@/components/RbDrawer';
import { getModelPlaza, addModelPlaza } from '@/api/models'
import RbCard from '@/components/RbCard/Card'
import Tag from '@/components/Tag';
import PageEmpty from '@/components/Empty/PageEmpty';

interface ModelSquareDetailProps {
  refresh: () => void;
}
const ModelSquareDetail = forwardRef<ModelSquareDetailRef, ModelSquareDetailProps>(({ refresh }, ref) => {
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
      title={<>{model.provider} {t('modelNew.modelList')} ({list.length}{t('modelNew.item')})</>}
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
              avatarUrl={item.logo}
              avatar={
                <div className="rb:w-12 rb:h-12 rb:rounded-lg rb:mr-3.25 rb:bg-[#155eef] rb:flex rb:items-center rb:justify-center rb:text-[28px] rb:text-[#ffffff]">
                  {item.name[0]}
                </div>
              }
            >
              <Tag>{t(`modelNew.${item.type}`)}</Tag>
              <div className="rb:text-[#5B6167] rb:text-[12px] rb:leading-4.5 rb:mt-3 rb:h-9">{item.description}</div>
              <Space size={8} className="rb:mt-3">{item.tags.map((tag, tagIndex) => <Tag key={tagIndex}>{tag}</Tag>)}</Space>
              {item.is_added
                ? <Button className="rb:mt-3" type="primary" disabled block>{t('modelNew.added')}</Button>
                : <Button className="rb:mt-3" type="primary" ghost block onClick={() => handleAdd(item)}>+ {t('common.add')}</Button>
              }
            </RbCard>
          ))}
          </div>
        }
    </RbDrawer>
  );
});

export default ModelSquareDetail;