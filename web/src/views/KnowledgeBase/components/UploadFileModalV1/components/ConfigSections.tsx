import { Flex, Form, InputNumber, Select } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import ModelSelect from '@/components/ModelSelect';
import RadioGroupCard from '@/components/RadioGroupCard';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem';
import type { Model } from '@/views/ModelManagement/types';
import {
  DELIMITER_OPTIONS,
  horizontalClassNames,
  multimodalFields,
  parentChunkModeOptions,
} from '../../CreateKnowledgeBaseModal/constants';
import type { UploadFormValues } from '../types';

interface SectionProps {
  active: boolean;
}

const FieldLabel = ({ title, desc }: { title: string; desc?: string }) => (
  <Flex vertical gap={2} component="span" className="rb:leading-5">
    <div className="rb:font-medium">{title}</div>
    {desc && <div className="rb:text-xs rb:font-normal rb:leading-4.5 rb:text-gray-600 rb:wrap-break-word">{desc}</div>}
  </Flex>
);

const ParsingSection = ({ active }: SectionProps) => {
  const { t } = useTranslation();
  return (
    <section aria-label={t('knowledgeBase.createModal.parsingConfig')} hidden={!active}>
      <Form.Item
        name="layout_recognize"
        label={<FieldLabel title={t('knowledgeBase.createModal.pdfParsing')} desc={t('knowledgeBase.createModal.pdfParsingDesc')} />}
        rules={[{ required: true, message: t('knowledgeBase.createModal.pdfParsingRequired') }]}
        className={horizontalClassNames}
      >
        <Select
          options={[
            { value: 'mineru', label: 'MinerU' },
            { value: 'textln', label: 'TextLN' },
          ]}
        />
      </Form.Item>
      <Form.Item
        name="vision_mode"
        label={<FieldLabel title={t('knowledgeBase.createModal.imageParsing')} desc={t('knowledgeBase.createModal.imageParsingDesc')} />}
        getValueProps={(value) => ({ value: value === undefined ? undefined : String(value) })}
        getValueFromEvent={(value) => Number(value)}
        className={horizontalClassNames}
      >
        <Select
          options={[
            { value: '0', label: t('knowledgeBase.createModal.textImage') },
            { value: '1', label: t('knowledgeBase.createModal.mixedImage') },
            { value: '2', label: t('knowledgeBase.createModal.pureImage') },
          ]}
        />
      </Form.Item>
    </section>
  );
};

const ChunkSection = ({ active, isParentChildMode }: SectionProps & { isParentChildMode: boolean | null }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<UploadFormValues>();
  const chunkStrategy = Form.useWatch('chunk_strategy', form) || 'default';
  const parentChunkMode = Form.useWatch('parent_chunk_mode', form) || 'paragraph';

  return (
    <section aria-label={t('knowledgeBase.createModal.chunkingStrategy')} hidden={!active}>
      <Form.Item
        name="chunk_strategy"
        label={<FieldLabel title={t('knowledgeBase.createModal.chunkMethod')} desc={t('knowledgeBase.createModal.chunkMethodDesc')} />}
        className={horizontalClassNames}
      >
        <Select
          options={[
            { value: 'default', label: t('knowledgeBase.createModal.default'), disabled: isParentChildMode === true },
            { value: 'parent-child', label: t('knowledgeBase.createModal.parentChild'), disabled: isParentChildMode === false },
            { value: 'custom', label: t('knowledgeBase.createModal.custom'), disabled: isParentChildMode === true },
          ]}
        />
      </Form.Item>

      {chunkStrategy === 'parent-child' && (
        <>
          <Form.Item
            name="parent_chunk_mode"
            label={<FieldLabel title={t('knowledgeBase.createModal.parentChunkScope')} desc={t('knowledgeBase.createModal.parentChunkScopeDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
          >
            <RadioGroupCard
              options={parentChunkModeOptions}
              allowClear={false}
              itemRender={(option) => (
                <div className="rb:text-left">
                  <div className="rb:font-medium rb:leading-5">{t(option.label)}</div>
                  <div className="rb:mt-1 rb:text-xs rb:leading-4.5 rb:text-gray-600">{t(option.labelDesc as string)}</div>
                </div>
              )}
            />
          </Form.Item>

          {parentChunkMode === 'paragraph' && (
            <>
              <Form.Item
                name="parent_chunk_token_num"
                label={<FieldLabel title={t('knowledgeBase.createModal.parentChunkSize')} desc={t('knowledgeBase.createModal.parentChunkSizeDesc')} />}
                className={horizontalClassNames}
                rules={[{ required: true, message: t('knowledgeBase.createModal.parentChunkSizeRequired') }]}
              >
                <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
              </Form.Item>
              <Form.Item
                name="parent_chunk_delimiter"
                label={<FieldLabel title={t('knowledgeBase.createModal.parentChunkDelimiter')} desc={t('knowledgeBase.createModal.parentChunkDelimiterDesc')} />}
                className={horizontalClassNames}
              >
                <Select mode="multiple" options={DELIMITER_OPTIONS.map((o) => ({ ...o, label: t(o.label) }))} />
              </Form.Item>
            </>
          )}

          <Form.Item
            name="chunk_token_num"
            label={<FieldLabel title={t('knowledgeBase.createModal.childChunkSize')} desc={t('knowledgeBase.createModal.childChunkSizeDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.childChunkSizeRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
          </Form.Item>
          <Form.Item
            name="delimiter"
            label={<FieldLabel title={t('knowledgeBase.createModal.childChunkDelimiter')} desc={t('knowledgeBase.createModal.childChunkDelimiterDesc')} />}
            className={horizontalClassNames}
          >
            <Select mode="multiple" options={DELIMITER_OPTIONS.map((o) => ({ ...o, label: t(o.label) }))} />
          </Form.Item>
        </>
      )}

      {chunkStrategy === 'custom' && (
        <>
          <Form.Item
            name="chunk_token_num"
            label={<FieldLabel title={t('knowledgeBase.createModal.chunkSize')} desc={t('knowledgeBase.createModal.chunkSizeDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.chunkSizeRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
          </Form.Item>
          <Form.Item
            name="chunk_overlap"
            label={<FieldLabel title={t('knowledgeBase.createModal.chunkOverlap')} desc={t('knowledgeBase.createModal.chunkOverlapDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.chunkOverlapRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={0} max={128} />
          </Form.Item>
          <Form.Item
            name="delimiter"
            label={<FieldLabel title={t('knowledgeBase.createModal.delimiter')} desc={t('knowledgeBase.createModal.delimiterDesc')} />}
            className={horizontalClassNames}
          >
            <Select mode="multiple" options={DELIMITER_OPTIONS.map((o) => ({ ...o, label: t(o.label) }))} />
          </Form.Item>
        </>
      )}
    </section>
  );
};

const MultimodalSection = ({ active, customModels }: SectionProps & { customModels: Record<string, Model[]> }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<UploadFormValues>();
  const multimodalEnabled = Form.useWatch('multimodal_enabled', form) || false;

  return (
    <section aria-label={t('knowledgeBase.createModal.multimodalProcessing')} hidden={!active}>
      <div className="rb:border-b rb:border-[#ebeef2] rb:py-4.5 rb:first-of-type:pt-0 rb:last:border-b-0 rb:last:pb-0">
        <SwitchFormItem
          name="multimodal_enabled"
          title={t('knowledgeBase.createModal.multimodalProcessing')}
          desc={t('knowledgeBase.createModal.multimodalSwitchDesc')}
        />

        {multimodalEnabled && (
          <div className={clsx('rb:pt-5')}>
            {multimodalFields.map((item) => (
              <Form.Item
                key={item.name}
                name={`${item.name}_id`}
                label={<FieldLabel title={t(item.title)} desc={t(item.desc)} />}
                className={horizontalClassNames}
              >
                <ModelSelect
                  placeholder={t('knowledgeBase.createModal.selectModel', { title: t(item.title) })}
                  isAutoFetch={false}
                  initialData={customModels[item.type]}
                  allowClear
                />
              </Form.Item>
            ))}
          </div>
        )}
      </div>
    </section>
  );
};

const AdvancedSection = ({ active }: SectionProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<UploadFormValues>();
  const qaEnabled = Form.useWatch('qa_enabled', form) || false;

  return (
    <section aria-label={t('knowledgeBase.createModal.advancedSettings')} hidden={!active}>
      <div className="rb:border-b rb:border-[#ebeef2] rb:py-4.5 rb:first-of-type:pt-0 rb:last:border-b-0 rb:last:pb-0">
        <SwitchFormItem
          name="qa_enabled"
          title={t('knowledgeBase.createModal.qaGeneration')}
          desc={t('knowledgeBase.createModal.qaGenerationDesc')}
        />

        {qaEnabled && (
          <div className={clsx('rb:pt-5')}>
            <Form.Item
              name="auto_questions"
              label={<FieldLabel title={t('knowledgeBase.createModal.questionCount')} desc={t('knowledgeBase.createModal.questionCountDesc')} />}
              className={horizontalClassNames}
              rules={[{ required: true, message: t('knowledgeBase.createModal.questionCountRequired') }]}
            >
              <InputNumber className="rb:w-full!" min={1} max={10} />
            </Form.Item>
          </div>
        )}
      </div>
    </section>
  );
};

export { AdvancedSection, ChunkSection, MultimodalSection, ParsingSection };
