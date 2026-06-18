/**
 * Knowledge Component
 * Manages knowledge base associations with variant-based styling
 * - application: Uses Card wrapper, larger styles for ApplicationConfig
 * - workflow: Compact styles for Workflow panel
 */

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
  KnowledgeVariant,
} from './types'
import KnowledgeListModal from './KnowledgeListModal'
import KnowledgeConfigModal from './KnowledgeConfigModal'
import KnowledgeGlobalConfigModal from './KnowledgeGlobalConfigModal'
import Tag from '@/components/Tag'
import { getKnowledgeBaseList } from '@/api/knowledgeBase'
import Card from '@/views/ApplicationConfig/components/Card';
import knowledgeEmpty from '@/assets/images/application/knowledgeEmpty.svg'
import Empty from '@/components/Empty'

interface KnowledgeProps {
  value?: KnowledgeConfig;
  onChange?: (config: KnowledgeConfig) => void;
  variant?: KnowledgeVariant;
}

const Knowledge: FC<KnowledgeProps> = ({
  value = { knowledge_bases: [] },
  onChange,
  variant = 'application',
}) => {
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
      setKnowledgeList(knowledge_bases)

      const basesWithoutName = knowledge_bases.filter(base => !base.name)
      if (basesWithoutName.length > 0) {
        getKnowledgeBaseList(undefined, { kb_ids: basesWithoutName.map(vo => vo.kb_id).join(',') }).then(res => {
          const fullBases = knowledge_bases.map(base => {
            if (!base.name) {
              const fullBase = res.items.find((item: any) => item.id === base.kb_id)
              return fullBase ? { ...fullBase, ...base, config: base } : base
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
        ...values,
        config: { ...values as KnowledgeConfigForm }
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

  // Application variant styles
  if (variant === 'application') {
    return (
      <Card
        title={t('application.knowledgeBaseAssociation')}
        extra={
          <Space>
            <Button
              className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-[#21233"
              icon={<div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/application/set.svg')]"></div>}
              onClick={handleKnowledgeConfig}
            >{t('application.globalConfig')}</Button>
            <Button className="rb:h-6! rb:py-0! rb:px-2! rb:rounded-md! rb:text-[#21233" onClick={handleAddKnowledge}>+</Button>
          </Space>
        }
      >
        <div className="rb:leading-4.5 rb:text-[12px] rb:mb-2 rb:font-medium">
          {t('application.associatedKnowledgeBase')}
        </div>

        {knowledgeList.length === 0
          ? <div className="rb-border rb:rounded-xl rb:min-h-37">
              <Empty url={knowledgeEmpty} size={88} subTitle={t('application.knowledgeEmpty')} className="rb:mt-4!" />
            </div>
          : <Flex vertical gap={10}>
              {knowledgeList.map(item => {
                if (!item.id) return null
                return (
                  <Flex key={item.id} align="center" justify="space-between" className="rb:py-3! rb:px-4! rb-border rb:rounded-lg">
                    <div>
                      <span className="rb:font-medium rb:leading-4">{item.name}</span>
                      <Tag color={item.status === 1 ? 'success' : item.status === 0 ? 'default' : 'error'} className="rb:ml-2">
                        {item.status === 1 ? t('common.enable') : item.status === 0 ? t('common.disabled') : t('common.deleted')}
                      </Tag>
                      <div className="rb:mt-1 rb:text-[12px] rb:text-[#5B6167] rb:font-regular rb:leading-4">{t('application.contains', { include_count: item.doc_num })}</div>
                    </div>
                    <Space size={12}>
                      <div
                        className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/editBorder.svg')] rb:hover:bg-[url('@/assets/images/editBg.svg')]"
                        onClick={() => handleEditKnowledge(item)}
                      ></div>
                      <div
                        className="rb:size-6 rb:cursor-pointer rb:bg-cover rb:bg-[url('@/assets/images/deleteBorder.svg')] rb:hover:bg-[url('@/assets/images/deleteBg.svg')]"
                        onClick={() => handleDeleteKnowledge(item.id)}
                      ></div>
                    </Space>
                  </Flex>
                )
              })}
            </Flex>
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

  // Workflow variant styles (default)
  return (
    <div>
      <Flex align="center" justify="space-between" className="rb:mb-2!">
        <div className="rb:text-[12px] rb:font-medium rb:leading-4.5">
          <span className="rb:text-[#ff5d34] rb:text-[14px] rb:font-[SimSun,sans-serif] rb:mr-1">*</span>{t('application.knowledgeBaseAssociation')}
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
    </div>
  )
}

export default Knowledge