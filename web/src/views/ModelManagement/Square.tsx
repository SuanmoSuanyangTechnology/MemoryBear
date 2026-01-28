import { useRef, useState, useEffect, forwardRef, useImperativeHandle } from 'react';
import { Button, Space, App, Divider, Flex } from 'antd'
import { UsergroupAddOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';

import type { ModelPlaza, ModelPlazaItem, ModelSquareDetailRef, BaseRef } from './types'
import RbCard from '@/components/RbCard/Card'
import { getModelPlaza, addModelPlaza } from '@/api/models'
import PageEmpty from '@/components/Empty/PageEmpty';
import Tag from '@/components/Tag';
import ModelSquareDetail from './components/ModelSquareDetail'

const ModelSquare = forwardRef <BaseRef, { query: any; handleEdit: (vo?: ModelPlazaItem) => void; }>(({ query, handleEdit }, ref) => {
  const { t } = useTranslation();
  const { message } = App.useApp()
  const modelSquareDetailRef = useRef<ModelSquareDetailRef>(null)
  const [list, setList] = useState<ModelPlaza[]>([])
  useEffect(() => {
    getList()
  }, [query])
  const getList = () => {
    getModelPlaza(query)
      .then(res => {
        setList((res as ModelPlaza[]) || [])
      })
  }

  const handleMore = (vo: ModelPlaza) => {
    modelSquareDetailRef.current?.handleOpen(vo)
  }
  const handleAdd = (item: ModelPlazaItem) => {
    addModelPlaza(item.id)
      .then(() => {
        message.success(`${item.name}${t('modelNew.addSuccess')}`)
        getList()
      })
  }

  useImperativeHandle(ref, () => ({
    getList,
  }));
  return (
    <>
      {list.length === 0
        ? <PageEmpty />
        : list.map(vo => (
          <div key={vo.provider}>
            <div className="rb:flex rb:justify-between rb:items-center rb:bg-[rgba(21,94,239,0.12)] rb:px-4 rb:py-2.5 rb:leading-5 rb:mb-4 rb:mt-6 rb:rounded-md">
              <div className="rb:font-medium">{vo.provider}</div>
              <Button type="link" onClick={() => handleMore(vo)}>{t('modelNew.viewAll')}({t(`modelNew.modelCount`, { count: vo.models.length })})&gt;</Button>
            </div>

            <div className="rb:grid rb:grid-cols-3 rb:gap-4">
              {vo.models.slice(0, 6).map(item => (
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
                </RbCard>
              ))}
            </div>
          </div>
        ))
      }

      <ModelSquareDetail
        ref={modelSquareDetailRef}
        refresh={getList}
        handleEdit={handleEdit}
      />
    </>
  )
})

export default ModelSquare