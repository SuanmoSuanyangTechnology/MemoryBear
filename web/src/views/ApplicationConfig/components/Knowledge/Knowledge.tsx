/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:32 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:32 
 */
/**
 * Knowledge Base Component
 * Manages knowledge base associations for the application
 * Allows adding, configuring, and removing knowledge bases
 */

import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, List } from 'antd'

import knowledgeEmpty from '@/assets/images/application/knowledgeEmpty.svg'
import type {
  KnowledgeConfigForm,
  KnowledgeConfig,
  RerankerConfig,
  KnowledgeBase,
  KnowledgeModalRef,
  KnowledgeConfigModalRef,
  KnowledgeGlobalConfigModalRef,
} from './types'
import Empty from '@/components/Empty'
import KnowledgeListModal from './KnowledgeListModal'
import KnowledgeConfigModal from './KnowledgeConfigModal'
import KnowledgeGlobalConfigModal from './KnowledgeGlobalConfigModal'
import Tag from '@/components/Tag'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import Card from '../Card'

/**
 * Knowledge base management component
 * @param value - Current knowledge configuration
 * @param onChange - Callback when configuration changes
 */
const Knowledge: FC<{value?: KnowledgeConfig; onChange?: (config: KnowledgeConfig) => void}> = ({value = {knowledge_bases: []}, onChange}) => {
  const { t } = useTranslation()
  const knowledgeModalRef = useRef<KnowledgeModalRef>(null)
  const knowledgeConfigModalRef = useRef<KnowledgeConfigModalRef>(null)
  const knowledgeGlobalConfigModalRef = useRef<KnowledgeGlobalConfigModalRef>(null)
  const [knowledgeList, setKnowledgeList] = useState<KnowledgeBase[]>([])
  const [editConfig, setEditConfig] = useState<KnowledgeConfig>({} as KnowledgeConfig)

  useEffect(() => {
    if (value && JSON.stringify(value) !== JSON.stringify(editConfig)) {
      setEditConfig({ ...(value || {}) })
      const knowledge_bases = [...(value.knowledge_bases || [])]
      
      // Check if knowledge_bases are missing name field
      const basesWithoutName = knowledge_bases.filter(base => !base.name)
      if (basesWithoutName.length > 0) {
        // Call API to get complete knowledge base information
        getKnowledgeBaseList().then(res => {
          const fullBases = knowledge_bases.map(base => {
            if (!base.name) {
              const fullBase = res.items.find((item: any) => item.id === base.kb_id)
              return fullBase ? { ...base, ...fullBase } : base
            }
            return base
          })
          setKnowledgeList(fullBases)
        }).catch(() => {
          setKnowledgeList(knowledge_bases)
        })
      } else {
        setKnowledgeList(knowledge_bases)
      }
    }
  }, [value])

  /** Open global knowledge configuration modal */
  const handleKnowledgeConfig = () => {
    knowledgeGlobalConfigModalRef.current?.handleOpen()
  }
  /** Open knowledge base selection modal */
  const handleAddKnowledge = () => {
    knowledgeModalRef.current?.handleOpen()
  }
  /** Remove knowledge base from list */
  const handleDeleteKnowledge = (id: string) => {
    const list = knowledgeList.filter(item => item.id !== id)
    setKnowledgeList([...list])
    onChange && onChange({
      ...editConfig,
      knowledge_bases: [...list],
    })
  }
  /** Open knowledge base configuration modal */
  const handleEditKnowledge = (item: KnowledgeBase) => {
    knowledgeConfigModalRef.current?.handleOpen(item)
  }
  /** Update knowledge configuration */
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
      onChange && onChange({
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
      onChange && onChange({
        ...editConfig,
        knowledge_bases: [...list],
      })
    } else if (type === 'rerankerConfig') {
      const rerankerValues = values as RerankerConfig
      setEditConfig(prev => ({ ...prev, ...rerankerValues }))
      onChange && onChange({
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
        <Space>
          <Button style={{ padding: '0 8px', height: '24px' }} onClick={handleKnowledgeConfig}>{t('application.globalConfig')}</Button>
          <Button style={{ padding: '0 8px', height: '24px' }} onClick={handleAddKnowledge}>+</Button>
        </Space>
      }
    >
      {knowledgeList.length === 0
        ? <Empty url={knowledgeEmpty} size={88} subTitle={t('application.knowledgeEmpty')} />
        : 
          <List
            grid={{ gutter: 12, column: 1 }}
            dataSource={knowledgeList}
            renderItem={(item) => {
              if (!item.id) return null
              return (
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
              )
            }}
          />
      }
      <KnowledgeGlobalConfigModal
        data={editConfig}
        ref={knowledgeGlobalConfigModalRef}
        refresh={refresh}
      />
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