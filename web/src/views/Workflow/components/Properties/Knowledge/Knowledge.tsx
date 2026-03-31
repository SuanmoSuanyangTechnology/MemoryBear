import { type FC, useRef, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Space, Button, Flex } from 'antd'

import type {
  KnowledgeConfigForm,
  KnowledgeConfig,
  RerankerConfig,
  KnowledgeBase,
  KnowledgeModalRef,
  KnowledgeConfigModalRef,
  KnowledgeGlobalConfigModalRef,
} from './types'
import KnowledgeListModal from './KnowledgeListModal'
import KnowledgeConfigModal from './KnowledgeConfigModal'
import KnowledgeGlobalConfigModal from './KnowledgeGlobalConfigModal'
import Tag from '@/components/Tag'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'

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
      
      // 检查是否有knowledge_bases缺少name字段
      const basesWithoutName = knowledge_bases.filter(base => !base.name)
      if (basesWithoutName.length > 0) {
        // 调用接口获取完整的知识库信息
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

  const handleKnowledgeConfig = () => {
    knowledgeGlobalConfigModalRef.current?.handleOpen()
  }
  const handleAddKnowledge = () => {
    knowledgeModalRef.current?.handleOpen()
  }
  const handleDeleteKnowledge = (id: string) => {
    const list = knowledgeList.filter(item => item.id !== id)
    setKnowledgeList([...list])
    onChange && onChange({
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
    <div>
      <Flex align="center" justify="space-between" className="rb:mb-2!">
        <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
          {t('application.knowledgeBaseAssociation')}
        </div>

        <Button
          onClick={handleKnowledgeConfig}
          className="rb:py-0! rb:px-1! rb:text-[12px]! rb:group rb:gap-0.5!"
          size="small"
          disabled={knowledgeList.length === 0}
        >
          <div
            className="rb:size-3.5 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/workflow/recall.svg')]"
          ></div>
          {t('application.globalConfig')}
        </Button>
      </Flex>

      <Flex gap={10} vertical>
        <Button
          type="dashed"
          block
          size="middle"
          className="rb:text-[12px]!"
          onClick={handleAddKnowledge}
        >
          + {t('workflow.config.knowledge-retrieval.addKnowledge')}
        </Button>

        {knowledgeList.length > 0 && knowledgeList.map(item => {
            if (!item.id) return null
            return (
              <Flex key={item.id} align="center" justify="space-between" className="rb:text-[12px] rb:py-1.75! rb:px-2.5! rb-border rb:rounded-lg">
                <div className="">
                  <span className="rb:font-medium rb:leading-4.25">{item.name}</span>
                  <Tag 
                    color={item.status === 1 ? 'success' : item.status === 0 ? 'default' : 'error'} 
                    className="rb:ml-1 rb:py-0! rb:px-1! rb:text-[12px] rb:leading-4!"
                  >
                    {item.status === 1 ? t('common.enable') : item.status === 0 ? t('common.disabled') : t('common.deleted')}
                  </Tag>
                  <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4.25">{t('application.contains', { include_count: item.doc_num })}</div>
                </div>
                <Space size={12}>
                  <div
                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/edit.svg')]"
                    onClick={() => handleEditKnowledge(item)}
                  ></div>
                  <div
                    className="rb:size-4 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/delete.svg')]"
                    onClick={() => handleDeleteKnowledge(item.id)}
                  ></div>
                </Space>
              </Flex>
            )
          })
        }
      </Flex>
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
    </div>
  )
}
export default Knowledge