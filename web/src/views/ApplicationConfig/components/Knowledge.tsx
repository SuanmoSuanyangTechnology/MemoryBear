import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, List } from 'antd'
import knowledgeEmpty from '@/assets/images/application/knowledgeEmpty.svg'
import Card from './Card'
import type {
  KnowledgeConfigForm,
  KnowledgeConfig,
  RerankerConfig,
  KnowledgeBase,
  KnowledgeModalRef,
  KnowledgeConfigModalRef,
  KnowledgeGlobalConfigModalRef,
} from '../types'
import Empty from '@/components/Empty'
import KnowledgeListModal from './KnowledgeListModal'
import KnowledgeConfigModal from './KnowledgeConfigModal'
import KnowledgeGlobalConfigModal from './KnowledgeGlobalConfigModal'
import Tag from '@/components/Tag'

const Knowledge: FC<{data: KnowledgeConfig; onUpdate: (config: KnowledgeConfig) => void}> = ({data, onUpdate}) => {
  const { t } = useTranslation()
  const knowledgeModalRef = useRef<KnowledgeModalRef>(null)
  const knowledgeConfigModalRef = useRef<KnowledgeConfigModalRef>(null)
  const knowledgeGlobalConfigModalRef = useRef<KnowledgeGlobalConfigModalRef>(null)
  const [knowledgeList, setKnowledgeList] = useState<KnowledgeBase[]>([])
  const [editConfig, setEditConfig] = useState<KnowledgeConfig>({} as KnowledgeConfig)

  useEffect(() => {
    if (data) {
      setEditConfig({ ...(data || {}) })
      const knowledge_bases = [...(data.knowledge_bases || [])]
      setKnowledgeList(knowledge_bases)
    }
  }, [data])

  const handleKnowledgeConfig = () => {
    knowledgeGlobalConfigModalRef.current?.handleOpen()
  }
  const handleAddKnowledge = () => {
    knowledgeModalRef.current?.handleOpen()
  }
  const handleDeleteKnowledge = (id: string) => {
    const list = knowledgeList.filter(item => item.id !== id)
    setKnowledgeList([...list])
    onUpdate({
      ...editConfig,
      knowledge_bases: [...list],
    })
  }
  const handleEditKnowledge = (item: KnowledgeBase) => {
    knowledgeConfigModalRef.current?.handleOpen(item)
  }
  const refresh = (values: KnowledgeBase[] | KnowledgeConfigForm | RerankerConfig, type: 'knowledge' | 'knowledgeConfig' | 'rerankerConfig') => {
    if (type === 'knowledge') {
        let list = [...knowledgeList]
        if (list.length > 0) {
          (Array.isArray(values) ? values : [values]).forEach(vo => {
            const index = list.findIndex(item => item.id === (vo as KnowledgeBase).id)
            if (index === -1) {
              list.push(vo as KnowledgeBase)
            }
          })
        } else {
          list = [...values as KnowledgeBase[]]
        }
      setKnowledgeList([...list])
      onUpdate({
        ...editConfig,
        knowledge_bases: [...list],
      })
    } else if (type === 'knowledgeConfig') {
      const index = knowledgeList.findIndex(item => item.id === (values as KnowledgeBase).kb_id)
      const list = [...knowledgeList]
      list[index] = {
        ...list[index],
        config: {...values as KnowledgeConfigForm}
      }
      setKnowledgeList([...list])
      onUpdate({
        ...editConfig,
        knowledge_bases: [...list],
      })
    } else if (type === 'rerankerConfig') {
      const rerankerValues = values as RerankerConfig
      setEditConfig(prev => ({ ...prev, ...rerankerValues }))
      onUpdate({
        ...editConfig,
        ...rerankerValues,
        reranker_id: rerankerValues.rerank_model ? rerankerValues.reranker_id : undefined,
        reranker_top_k: rerankerValues.rerank_model ? rerankerValues.reranker_top_k : undefined,
      })
    }
  }
  return (
    <Card 
      title={t('application.knowledgeBaseAssociation')}
      extra={
        <Button style={{padding: '0 8px', height: '24px'}} onClick={() => handleKnowledgeConfig()}>{t('application.globalConfig')}</Button>
      }
    >
      <div className="rb:flex rb:items-center rb:justify-between rb:mb-3">
        <div className="rb:font-medium rb:leading-5">{t('application.associatedKnowledgeBase')}</div>
        <Button style={{padding: '0 8px', height: '24px'}} onClick={handleAddKnowledge}>+{t('application.addKnowledgeBase')}</Button>
      </div>

      {knowledgeList.length === 0
        ? <Empty url={knowledgeEmpty} size={88} subTitle={t('application.knowledgeEmpty')} />
        : 
          <List
            grid={{ gutter: 12, column: 1 }}
            dataSource={knowledgeList}
            renderItem={(item) => (
              <List.Item>
                <div key={item.id} className="rb:flex rb:items-center rb:justify-between rb:p-[12px_16px] rb:bg-[#FBFDFF] rb:border rb:border-[#DFE4ED] rb:rounded-lg">
                  <div className="rb:font-medium rb:leading-4">
                    {item.name}
                    <Tag color={item.status === 1 ? 'success' : item.status === 0 ? 'default' : 'error'} className="rb:ml-2">
                      {item.status === 1 ? t('common.enable') : item.status === 0 ? t('common.disabled') : t('common.deleted')}
                    </Tag>
                    <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-5">{t('application.contains', {include_count: item.doc_num})}</div>
                  </div>
                  <Space size={12}>
                    <div 
                      className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]" 
                      onClick={() => handleEditKnowledge(item)}
                    ></div>
                    <div 
                      className="rb:w-6 rb:h-6 rb:cursor-pointer rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]" 
                      onClick={() => handleDeleteKnowledge(item.id)}
                    ></div>
                  </Space>
                </div>
              </List.Item>
            )}
          />
      }
      {/* 全局设置 */}
      <KnowledgeGlobalConfigModal
        data={editConfig}
        ref={knowledgeGlobalConfigModalRef}
        refresh={refresh}
      />
      {/* 知识库列表 */}
      <KnowledgeListModal
        ref={knowledgeModalRef}
        selectedList={knowledgeList}
        refresh={refresh}
      />
      <KnowledgeConfigModal
        ref={knowledgeConfigModalRef}
        refresh={refresh}
      />
    </Card>
  )
}
export default Knowledge