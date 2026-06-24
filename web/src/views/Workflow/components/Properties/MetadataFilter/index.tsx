import { type FC, useEffect, useRef } from "react";
import { useTranslation } from 'react-i18next';
import { Form, Select, Button, Space, Flex, Tooltip } from 'antd';

import ModelConfig from '../ModelConfig'
import MetadataFilterModal, { type MetadataFilterModalRef, type FilterCondition } from './MetadataFilterModal';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'
import { getPublicMetadataFields } from '@/api/knowledgeBase';
import type { MetadataField } from '@/views/KnowledgeBase/types'

interface MetadataFilterProps {
  options: Suggestion[];
  needTranslation?: boolean;
}
const modeOptions = [
  'disabled',
  'manual',
  'auto',
]

const MetadataFilter: FC<MetadataFilterProps> = ({
  options,
}) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance();
  const metadataFilterModalRef = useRef<MetadataFilterModalRef>(null);
  const knowledge_bases = Form.useWatch(['knowledge_retrieval', 'knowledge_bases'], form) || [];
  const currentMode = Form.useWatch(['metadata_filter_mode'], form) || 'disabled';
  const metadata_filters = Form.useWatch(['metadata_filters'], form) || { conditions: [], logic: 'and' };
  const isInitialized = useRef(false);
  const prevKnowledgeBases = useRef<any[]>([]);

  const handleOpenModal = () => {
    metadataFilterModalRef.current?.open(metadata_filters);
  };

  const handleSaveFilters = (newFilters: { conditions: FilterCondition[], logic: 'or' | 'and' }) => {
    form.setFieldsValue({
      metadata_filters: newFilters
    })
  };

  useEffect(() => {
    const kb_ids = knowledge_bases?.map((kb: any) => kb.kb_id || kb.id).filter(Boolean) || [];
    if (kb_ids.length) {
      getPublicMetadataFields({ kb_ids })
        .then(res => {
          const { custom, builtin_fields } = res as { custom: MetadataField[], builtin_fields: MetadataField[] };
          const allMetadataFields = [...custom, ...builtin_fields]
          const conditions = metadata_filters.conditions || []
          const filterConditions = conditions.filter((item: FilterCondition) => allMetadataFields.find(f => f.name === item.field))

          if (!isInitialized.current) {
            isInitialized.current = true;
            prevKnowledgeBases.current = knowledge_bases;
            return;
          }

          const isKnowledgeBasesChanged = JSON.stringify(prevKnowledgeBases.current) !== JSON.stringify(knowledge_bases);
          if (isKnowledgeBasesChanged) {
            prevKnowledgeBases.current = knowledge_bases;
            form.setFieldsValue({
              metadata_filters: {
                conditions: filterConditions,
                logic: metadata_filters.logic
              }
            })
          }
        })
    } else {
      if (isInitialized.current) {
        form.setFieldsValue({
          metadata_filters: {
            conditions: [],
            logic: 'and'
          }
        })
      }
    }
  }, [knowledge_bases, metadata_filters])

  return (
    <>
      <Flex align="center" justify="space-between" className="rb:w-full!">
        <Flex align="center" gap={4} className="rb:font-medium rb:text-[12px] rb:leading-4.5">
          {t('workflow.config.knowledge-retrieval.metadata')}
          {currentMode === 'manual' &&
            <Tooltip title={t('workflow.config.knowledge-retrieval.metadataTip')}>
              <div className="rb:size-4 rb:bg-cover rb:bg-[url('@/assets/images/common/question.svg')]"></div>
            </Tooltip>
          }
        </Flex>
        <Space size={8}>
          <Form.Item
            name="metadata_filter_mode"
            noStyle
          >
            <Select
              options={modeOptions.map(opt => ({
                value: opt,
                label: t(`workflow.config.knowledge-retrieval.${opt}`)
              }))}
              className="rb:w-24!"
              size="small"
            />
          </Form.Item>
          {currentMode === 'manual' && (<>
            <Form.Item name="metadata_filters" noStyle />
            <Button
              size="small"
              className="rb:text-[12px]! rb:h-7! rb:bg-transparent! rb:rounded-md"
              onClick={handleOpenModal}
            >
              {t('workflow.config.knowledge-retrieval.condition')} ({metadata_filters?.conditions?.length || 0})
            </Button>
          </>
          )}
        </Space>
      </Flex>
      {currentMode === 'auto' && <>
        <div className="rb:text-[12px] rb:text-[#5B6167] rb:leading-4.5 rb:my-1">{t('workflow.config.knowledge-retrieval.autoDesc')}</div>
        
        <ModelConfig
          key="metadata_model"
          needLabel={false}
          parentName="metadata_model"
          variableOptions={[]}
        />
      </>}

      <MetadataFilterModal
        ref={metadataFilterModalRef}
        options={options}
        onSave={handleSaveFilters}
        kb_ids={knowledge_bases?.map((kb: any) => kb.kb_id || kb.id).filter(Boolean) || []}
      />
    </>
  );
};

export default MetadataFilter;
