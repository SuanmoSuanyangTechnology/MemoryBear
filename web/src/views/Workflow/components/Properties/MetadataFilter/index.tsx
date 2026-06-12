import { type FC, useRef } from "react";
import { useTranslation } from 'react-i18next';
import { Form, Select, Button, Space, Flex } from 'antd';

import ModelConfig from '../ModelConfig'
import MetadataFilterModal, { type MetadataFilterModalRef, type FilterCondition } from './MetadataFilterModal';
import type { Suggestion } from '../../Editor/plugin/AutocompletePlugin'

interface MetadataFilterProps {
  options: Suggestion[];
  needTranslation?: boolean;
}
const modeOptions = [
  'disabled',
  'manual',
  // 'auto',
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

  const handleOpenModal = () => {
    metadataFilterModalRef.current?.open(metadata_filters);
  };

  const handleSaveFilters = (newFilters: { conditions: FilterCondition[], logic: 'or' | 'and' }) => {
    form.setFieldsValue({
      metadata_filters: newFilters
    })
  };

  return (
    <>
      <Flex align="center" justify="space-between" className="rb:w-full!">
        <div className="rb:font-medium rb:text-[12px] rb:leading-4.5">{t('workflow.config.knowledge-retrieval.metadata')}</div>
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
