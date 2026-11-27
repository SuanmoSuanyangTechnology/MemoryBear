import { Button, Checkbox, Flex, Form, Input, InputNumber, Radio, Select } from 'antd';
import { DeleteOutlined, PlusOutlined, ThunderboltOutlined, UserOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import ModelSelect from '@/components/ModelSelect';
import RadioGroupCard from '@/components/RadioGroupCard';
import SliderInput from '@/components/SliderInput';
import SwitchFormItem from '@/components/FormItem/SwitchFormItem';
import { stringRegExp } from '@/utils/validator';
import {
  IndexingStrategy,
  horizontalClassNames,
  modelFields,
  parentChunkModeOptions,
} from './constants';
import DelimiterSelect from './DelimiterSelect';
import type { V1WorkspaceModels } from '@/views/KnowledgeBase/typesV1';
import type { Model } from '@/views/ModelManagement/types';
import type { CreateKbFormValues } from './types';

const { TextArea } = Input;

interface SectionProps {
  active: boolean;
}

interface AdvancedSectionProps extends SectionProps {
  generatingEntityTypes: boolean;
  showEntityGenerate: boolean;
  setShowEntityGenerate: (value: boolean) => void;
  generateEntityTypes: () => void;
}

/** Nested field path for processing_configuration (consistent with the v1 API fields). */
const pc = (seg: string) => ['processing_configuration', ...seg.split('.')] as any;

const FieldLabel = ({ title, desc }: { title: string; desc?: string }) => (
  <Flex vertical gap={2} component="span" className="rb:leading-5">
    <div className="rb:font-medium">{title}</div>
    {desc && <div className="rb:text-xs rb:font-normal rb:leading-4.5 rb:text-gray-600 rb:wrap-break-word">{desc}</div>}
  </Flex>
);

const StrategyCard = ({ name, title, desc, disabled }: { name: 'rag_enabled' | 'wiki_enabled'; title: string; desc: string; disabled?: boolean }) => {
  const checked = !!Form.useWatch(name);
  return (
    <Form.Item name={name} valuePropName="checked" noStyle>
      <Checkbox
        disabled={disabled}
        className={clsx(
          'rb:border rb:rounded-xl rb:p-3! rb:[&.ant-checkbox-wrapper>.ant-checkbox]:self-start!',
          checked ? 'rb:border-primary-600' : 'rb-border',
        )}
      >
        <Flex vertical gap={4} component="span" className="rb:pl-1! rb:text-left">
          <strong className="rb:text-sm rb:font-medium rb:leading-5 rb:text-gray-800">{title}</strong>
          <small className="rb:text-xs rb:leading-4.5 rb:text-gray-600">{desc}</small>
        </Flex>
      </Checkbox>
    </Form.Item>
  );
};

const BasicSection = ({ active, editing }: SectionProps & { editing?: boolean }) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<CreateKbFormValues>();
  const contentType = Form.useWatch('content_type', form) || 'document';
  const source = Form.useWatch('source', form) || 'space';
  const thirdPartyPlatform = Form.useWatch(pc('sources.third_party.platform'), form) || 'yuque';
  const wikiEnabled = Form.useWatch('wiki_enabled', form) || false;

  return (
    <section aria-label={t('knowledgeBase.createModal.basicInfo')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.basicInfo')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.basicInfoDesc')}</p>

      <Form.Item
        name="name"
        label={<FieldLabel title={t('knowledgeBase.createModal.kbName')} />}
        layout="vertical"
        labelCol={{span: 24}}
        rules={[
          { required: true, message: t('knowledgeBase.createModal.kbNameRequired') },
          { max: 50, message: t('knowledgeBase.createModal.kbNameMax') },
          { pattern: stringRegExp, message: t('common.nameInvalid') },
        ]}
      >
        <Input placeholder={t('knowledgeBase.createModal.kbNamePlaceholder')} showCount maxLength={50} />
      </Form.Item>

      <Form.Item
        name="description"
        label={<FieldLabel title={t('knowledgeBase.createModal.kbDescription')} />}
        layout="vertical"
        labelCol={{span: 24}}
        rules={[{ max: 500, message: t('knowledgeBase.createModal.kbDescriptionMax') }]}
      >
        <TextArea rows={3} placeholder={t('knowledgeBase.createModal.kbDescriptionPlaceholder')} showCount maxLength={500} />
      </Form.Item>

      <Form.Item
        name="content_type"
        label={<FieldLabel title={t('knowledgeBase.createModal.kbType')} desc={t('knowledgeBase.createModal.kbTypeDesc')} />}
        layout="vertical"
        labelCol={{span: 24}}
        rules={[{ required: true, message: t('knowledgeBase.createModal.kbTypeRequired') }]}
      >
        <Radio.Group optionType="button" buttonStyle="solid" disabled={editing}>
          <Radio.Button value="document">{t('knowledgeBase.createModal.document')}</Radio.Button>
          <Radio.Button value="qa" disabled>{t('knowledgeBase.createModal.qa')}</Radio.Button>
        </Radio.Group>
      </Form.Item>

      <Form.Item
        name="source"
        label={<FieldLabel title={t('knowledgeBase.createModal.kbSource')} desc={t('knowledgeBase.createModal.kbSourceDesc')} />}
        layout="vertical"
        labelCol={{span: 24}}
        rules={[{ required: true, message: t('knowledgeBase.createModal.kbSourceRequired') }]}
      >
        <Radio.Group optionType="button" buttonStyle="solid" disabled={editing}>
          <Radio.Button value="space">{t('knowledgeBase.createModal.space')}</Radio.Button>
          <Radio.Button value="website">{t('knowledgeBase.createModal.webSync')}</Radio.Button>
          <Radio.Button value="third_party">{t('knowledgeBase.createModal.thirdParty')}</Radio.Button>
        </Radio.Group>
      </Form.Item>

      {source === 'website' && (
        <>
          <Form.Item
            name={pc('sources.website.entry_url')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.entryUrl')} desc={t('knowledgeBase.createModal.entryUrlDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[
              { required: true, message: t('knowledgeBase.createModal.entryUrlRequired') },
              { type: 'url', message: t('knowledgeBase.createModal.entryUrlInvalid') },
            ]}
          >
            <Input placeholder="https://example.com" />
          </Form.Item>

          <Form.Item
            name={pc('sources.website.max_pages')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.maxPages')} desc={t('knowledgeBase.createModal.maxPagesDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[{ required: true, type: 'number', min: 10, max: 200, message: t('knowledgeBase.createModal.maxPagesRequired') }]}
            initialValue={20}
          >
            <SliderInput min={10} max={200} step={10} />
          </Form.Item>

          <Form.Item
            name={pc('sources.website.delay_seconds')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.delaySeconds')} desc={t('knowledgeBase.createModal.delaySecondsDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[{ required: true, type: 'number', min: 1, max: 3, message: t('knowledgeBase.createModal.delaySecondsRequired') }]}
            initialValue={2}
          >
            <SliderInput min={1} max={3} step={1} />
          </Form.Item>

          <Form.Item
            name={pc('sources.website.timeout_seconds')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.timeoutSeconds')} desc={t('knowledgeBase.createModal.timeoutSecondsDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[{ required: true, type: 'number', min: 5, max: 15, message: t('knowledgeBase.createModal.timeoutSecondsRequired') }]}
            initialValue={10}
          >
            <SliderInput min={5} max={15} step={1} />
          </Form.Item>

          <Form.Item
            name={pc('sources.website.user_agent')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.userAgent')} desc={t('knowledgeBase.createModal.userAgentDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[{ required: true, message: t('knowledgeBase.createModal.userAgentRequired') }]}
            initialValue="KnowledgeBaseCrawler/1.0"
          >
            <Input placeholder="KnowledgeBaseCrawler/1.0" />
          </Form.Item>
        </>
      )}

      {source === 'third_party' && (
        <>
          <Form.Item
            name={pc('sources.third_party.platform')}
            preserve={false}
            label={<FieldLabel title={t('knowledgeBase.createModal.platform')} desc={t('knowledgeBase.createModal.platformDesc')} />}
            layout="vertical"
            labelCol={{span: 24}}
            rules={[{ required: true, message: t('knowledgeBase.createModal.platformRequired') }]}
            initialValue="yuque"
          >
            <Select
              disabled={editing}
              options={[
                { value: 'yuque', label: t('knowledgeBase.createModal.yuque') },
                { value: 'feishu', label: t('knowledgeBase.createModal.feishu') },
              ]}
            />
          </Form.Item>

          {thirdPartyPlatform === 'yuque' && (
            <>
              <Form.Item
                name={pc('sources.third_party.yuque.user_id')}
                preserve={false}
                label={<FieldLabel title={t('knowledgeBase.createModal.userId')} desc={t('knowledgeBase.createModal.yuqueUserIdDesc')} />}
                layout="vertical"
                labelCol={{span: 24}}
                rules={[{ required: true, message: t('knowledgeBase.createModal.yuqueUserIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createModal.yuqueUserIdPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={pc('sources.third_party.yuque.token')}
                preserve={false}
                label={<FieldLabel title={t('knowledgeBase.createModal.token')} desc={t('knowledgeBase.createModal.yuqueTokenDesc')} />}
                layout="vertical"
                labelCol={{span: 24}}
                rules={[{ required: true, message: t('knowledgeBase.createModal.yuqueTokenRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createModal.yuqueTokenPlaceholder')} />
              </Form.Item>
            </>
          )}

          {thirdPartyPlatform === 'feishu' && (
            <>
              <Form.Item
                name={pc('sources.third_party.feishu.app_id')}
                preserve={false}
                label={<FieldLabel title={t('knowledgeBase.createModal.feishuAppId')} desc={t('knowledgeBase.createModal.feishuAppIdDesc')} />}
                layout="vertical"
                labelCol={{span: 24}}
                rules={[{ required: true, message: t('knowledgeBase.createModal.feishuAppIdRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createModal.feishuAppIdPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={pc('sources.third_party.feishu.app_secret')}
                preserve={false}
                label={<FieldLabel title={t('knowledgeBase.createModal.feishuAppSecret')} desc={t('knowledgeBase.createModal.feishuAppSecretDesc')} />}
                layout="vertical"
                labelCol={{span: 24}}
                rules={[{ required: true, message: t('knowledgeBase.createModal.feishuAppSecretRequired') }]}
              >
                <Input.Password placeholder={t('knowledgeBase.createModal.feishuAppSecretPlaceholder')} />
              </Form.Item>

              <Form.Item
                name={pc('sources.third_party.feishu.folder_token')}
                preserve={false}
                label={<FieldLabel title={t('knowledgeBase.createModal.folderToken')} desc={t('knowledgeBase.createModal.folderTokenDesc')} />}
                layout="vertical"
                labelCol={{span: 24}}
                rules={[{ required: true, message: t('knowledgeBase.createModal.folderTokenRequired') }]}
              >
                <Input placeholder={t('knowledgeBase.createModal.folderTokenPlaceholder')} />
              </Form.Item>
            </>
          )}
        </>
      )}

      {contentType !== 'qa' && (
        <>
          <Form.Item
            label={<FieldLabel title={t('knowledgeBase.createModal.indexingStrategy')} />}
            required
            name="indexing_strategy"
            layout="vertical"
            labelCol={{span: 24}}
            dependencies={['rag_enabled', 'wiki_enabled']}
            rules={[
              {
                validator: () => {
                  const rag = form.getFieldValue('rag_enabled');
                  const wiki = form.getFieldValue('wiki_enabled');
                  return rag || wiki
                    ? Promise.resolve()
                    : Promise.reject(new Error(t('knowledgeBase.createModal.indexingStrategyRequired')));
                },
              },
            ]}
            className="rb:mb-0!"
          >
            <div className={clsx('rb:grid rb:w-full rb:grid-cols-2 rb:gap-3')}>
              {IndexingStrategy.map(item => (
                <StrategyCard key={item.name} {...item} title={t(item.title)} desc={t(item.desc)} disabled={editing} />
              ))}
            </div>
          </Form.Item>

          {wikiEnabled && (
            <Form.Item
              name={pc('wiki.extract_prompt')}
              className="rb:mt-5!"
              layout="vertical"
              labelCol={{span: 24}}
              label={<FieldLabel title={t('knowledgeBase.createModal.wikiExtractPrompt')} desc={t('knowledgeBase.createModal.wikiExtractPromptDesc')} />}
            >
              <TextArea rows={3} placeholder={t('knowledgeBase.createModal.leaveEmptyForDefault')} />
            </Form.Item>
          )}
        </>
      )}
    </section>
  );
};

const ModelsSection = ({ active, customModels, onEmbeddingChange }: SectionProps & { customModels: Record<string, Model[]>; onEmbeddingChange?: (value?: string) => void }) => {
  const { t } = useTranslation();
  return (
    <section aria-label={t('knowledgeBase.createModal.modelsConfig')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.modelsConfig')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.modelsConfigDesc')}</p>
      {modelFields.map((item) => (
        <Form.Item
          key={item.name}
          name={`${item.name}_id`}
          label={<FieldLabel title={t(item.title)} desc={t(item.desc)} />}
          rules={[{ required: item.required, message: t('knowledgeBase.createModal.selectModel', { title: t(item.title) }) }]}
          className={horizontalClassNames}
        >
          <ModelSelect
            isAutoFetch={false}
            initialData={customModels[item.type]}
            placeholder={t('knowledgeBase.createModal.selectModel', { title: t(item.title) })}
            allowClear={!item.required}
            onChange={item.name === 'embedding' ? (value) => onEmbeddingChange?.(value as string) : undefined}
          />
        </Form.Item>
      ))}
    </section>
  );
};

const ParsingSection = ({ active }: SectionProps) => {
  const { t } = useTranslation();
  return (
    <section aria-label={t('knowledgeBase.createModal.parsingConfig')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.parsingConfig')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.parsingConfigDesc')}</p>
      <Form.Item
        name={pc('defaults.parsing.layout')}
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
        name={pc('defaults.image.mode')}
        label={<FieldLabel title={t('knowledgeBase.createModal.imageParsing')} desc={t('knowledgeBase.createModal.imageParsingDesc')} />}
        className={horizontalClassNames}
      >
        <Select
          options={[
            { value: 0, label: t('knowledgeBase.createModal.textImage') },
            { value: 1, label: t('knowledgeBase.createModal.mixedImage') },
            { value: 2, label: t('knowledgeBase.createModal.pureImage') },
          ]}
        />
      </Form.Item>
    </section>
  );
};

const ChunkSection = ({ active }: SectionProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<CreateKbFormValues>();
  const chunkMethod = Form.useWatch(pc('defaults.chunking.method'), form) || 'default';
  const parentChunkMode = Form.useWatch(pc('defaults.chunking.parent.mode'), form) || 'paragraph';

  return (
    <section aria-label={t('knowledgeBase.createModal.chunkingStrategy')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.chunkingStrategy')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.chunkingStrategyDesc')}</p>

      <Form.Item
        name={pc('defaults.chunking.method')}
        label={<FieldLabel title={t('knowledgeBase.createModal.chunkMethod')} desc={t('knowledgeBase.createModal.chunkMethodDesc')} />}
        className={horizontalClassNames}
      >
        <Select
          options={[
            { value: 'default', label: t('knowledgeBase.createModal.default') },
            { value: 'parent_child', label: t('knowledgeBase.createModal.parentChild') },
            { value: 'custom', label: t('knowledgeBase.createModal.custom') },
          ]}
        />
      </Form.Item>

      {chunkMethod === 'parent_child' && (
        <>
          <Form.Item
            name={pc('defaults.chunking.parent.mode')}
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
                name={pc('defaults.chunking.parent.max_tokens')}
                label={<FieldLabel title={t('knowledgeBase.createModal.parentChunkSize')} desc={t('knowledgeBase.createModal.parentChunkSizeDesc')} />}
                className={horizontalClassNames}
                rules={[{ required: true, message: t('knowledgeBase.createModal.parentChunkSizeRequired') }]}
              >
                <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
              </Form.Item>
              <Form.Item
                name={pc('defaults.chunking.parent.delimiters')}
                label={<FieldLabel title={t('knowledgeBase.createModal.parentChunkDelimiter')} desc={t('knowledgeBase.createModal.parentChunkDelimiterDesc')} />}
                className={horizontalClassNames}
              >
                <DelimiterSelect />
              </Form.Item>
            </>
          )}

          <Form.Item
            name={pc('defaults.chunking.max_tokens')}
            label={<FieldLabel title={t('knowledgeBase.createModal.childChunkSize')} desc={t('knowledgeBase.createModal.childChunkSizeDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.childChunkSizeRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
          </Form.Item>
          <Form.Item
            name={pc('defaults.chunking.delimiters')}
            label={<FieldLabel title={t('knowledgeBase.createModal.childChunkDelimiter')} desc={t('knowledgeBase.createModal.childChunkDelimiterDesc')} />}
            className={horizontalClassNames}
          >
            <DelimiterSelect />
          </Form.Item>
        </>
      )}

      {chunkMethod === 'custom' && (
        <>
          <Form.Item
            name={pc('defaults.chunking.max_tokens')}
            label={<FieldLabel title={t('knowledgeBase.createModal.chunkSize')} desc={t('knowledgeBase.createModal.chunkSizeDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.chunkSizeRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={1} max={1024} />
          </Form.Item>
          <Form.Item
            name={pc('defaults.chunking.overlap_tokens')}
            label={<FieldLabel title={t('knowledgeBase.createModal.chunkOverlap')} desc={t('knowledgeBase.createModal.chunkOverlapDesc')} />}
            className={horizontalClassNames}
            rules={[{ required: true, message: t('knowledgeBase.createModal.chunkOverlapRequired') }]}
          >
            <InputNumber className="rb:w-full!" suffix={t('knowledgeBase.createModal.chars')} min={0} max={128} />
          </Form.Item>
          <Form.Item
            name={pc('defaults.chunking.delimiters')}
            label={<FieldLabel title={t('knowledgeBase.createModal.delimiter')} desc={t('knowledgeBase.createModal.delimiterDesc')} />}
            className={horizontalClassNames}
          >
            <DelimiterSelect />
          </Form.Item>
        </>
      )}
    </section>
  );
};

interface MultimodalSectionProps extends SectionProps {
  workspaceModels?: V1WorkspaceModels | null;
  customModels: Record<string, Model[]>;
}

const MultimodalSection = ({ active, workspaceModels, customModels }: MultimodalSectionProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<CreateKbFormValues>();
  const multimodalEnabled = Form.useWatch(pc('multimodal.enabled'), form) || false;
  const fields = [
    { name: pc('multimodal.vision_model_id'), type: 'vision', workspaceKey: 'vision_model_id' as const, title: t('knowledgeBase.createModal.visionModel'), desc: t('knowledgeBase.createModal.visionModelDesc') },
    { name: pc('multimodal.audio_model_id'), type: 'audio', workspaceKey: 'audio_model_id' as const, title: t('knowledgeBase.createModal.audioModel'), desc: t('knowledgeBase.createModal.audioModelDesc') },
    { name: pc('multimodal.video_model_id'), type: 'video', workspaceKey: 'video_model_id' as const, title: t('knowledgeBase.createModal.videoModel'), desc: t('knowledgeBase.createModal.videoModelDesc') },
  ];

  return (
    <section aria-label={t('knowledgeBase.createModal.multimodalProcessing')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.multimodalProcessing')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.multimodalProcessingDesc')}</p>

      <div className="rb:border-b rb:border-[#ebeef2] rb:py-4.5 rb:first-of-type:pt-0 rb:last:border-b-0 rb:last:pb-0">
        <SwitchFormItem
          name={pc('multimodal.enabled')}
          title={t('knowledgeBase.createModal.multimodalProcessing')}
          desc={t('knowledgeBase.createModal.multimodalSwitchDesc')}
          onChange={(checked) => {
            if (checked) {
              // When multimodal is enabled: use the space default model from the API only when the model value is empty, otherwise keep the detail/user-selected value.
              fields.forEach((item) => {
                if (!form.getFieldValue(item.name)) {
                  form.setFieldValue(item.name, workspaceModels?.[item.workspaceKey] ?? null);
                }
              });
            }
          }}
        />

        {multimodalEnabled && (
          <div className={clsx('rb:pt-5')}>
            {fields.map((item) => (
              <Form.Item
                key={item.title}
                name={item.name}
                label={<FieldLabel title={item.title} desc={item.desc} />}
                className={horizontalClassNames}
              >
                <ModelSelect
                  isAutoFetch={false}
                  initialData={customModels[item.type]}
                  placeholder={t('knowledgeBase.createModal.selectModel', { title: item.title })}
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

const AdvancedSection = ({
  active,
  generatingEntityTypes,
  showEntityGenerate,
  setShowEntityGenerate,
  generateEntityTypes,
}: AdvancedSectionProps) => {
  const { t } = useTranslation();
  const form = Form.useFormInstance<CreateKbFormValues>();
  const useGraphrag = Form.useWatch(pc('graph.enabled'), form) || false;
  const qaEnabled = Form.useWatch('qa_enabled', form) || false;

  return (
    <section aria-label={t('knowledgeBase.createModal.advancedSettings')} hidden={!active}>
      <h3 className="rb:mb-1.5 rb:text-base rb:font-semibold rb:leading-6 rb:text-gray-800">{t('knowledgeBase.createModal.advancedSettings')}</h3>
      <p className="rb:mb-6 rb:text-xs rb:leading-5 rb:text-gray-600">{t('knowledgeBase.createModal.advancedSettingsDesc')}</p>

      <div className="rb:border-b rb:border-[#ebeef2] rb:py-4.5 rb:first-of-type:pt-0 rb:last:border-b-0 rb:last:pb-0">
        <SwitchFormItem
          name={pc('graph.enabled')}
          title={t('knowledgeBase.createModal.knowledgeGraph')}
          desc={t('knowledgeBase.createModal.knowledgeGraphDesc')}
          onChange={(checked) => {
            if (!checked) {
              setShowEntityGenerate(false);
              form.setFieldValue(pc('graph.entity_types'), ['']);
              form.setFieldValue(pc('graph.scene_name'), '');
            }
          }}
        />

        {useGraphrag && (
          <div className={clsx('rb:pt-5')}>
            <Form.Item
              label={<FieldLabel title={t('knowledgeBase.createModal.entityTypes')} desc={t('knowledgeBase.createModal.entityTypesDesc')} />}
              required
              className={horizontalClassNames.replace('rb:mb-6!', 'rb:mb-3!')}
            >
              {showEntityGenerate ? (
                <Flex align="center" justify="end" gap={8} className="rb:min-w-0">
                  <Form.Item name={pc('graph.scene_name')} noStyle>
                    <Input placeholder={t('knowledgeBase.createModal.describeScenario')} />
                  </Form.Item>
                  <Button
                    type="primary"
                    ghost
                    icon={<ThunderboltOutlined />}
                    loading={generatingEntityTypes}
                    onClick={generateEntityTypes}
                  >
                    {t('knowledgeBase.createModal.generate')}
                  </Button>
                </Flex>
              ) : (
                <Flex align="center" justify="end" gap={8} className="rb:min-w-0">
                  <Button icon={<ThunderboltOutlined />} onClick={() => setShowEntityGenerate(true)}>
                    {t('knowledgeBase.createModal.aiGenerate')}
                  </Button>
                </Flex>
              )}
            </Form.Item>

            <Form.List
              name={pc('graph.entity_types')}
              rules={[
                {
                  validator: (_, types) => {
                    const list = (types || []) as string[];
                    return list.some((item) => typeof item === 'string' && item.trim())
                      ? Promise.resolve()
                      : Promise.reject(new Error(t('knowledgeBase.createModal.entityTypesRequired')));
                  },
                },
              ]}
            >
              {(fields, { add, remove }, { errors }) => (
                <>
                  <div className={clsx('rb:rounded-lg rb:border rb:border-gray-200 rb:p-3', {
                    'rb:border-red-500!': errors?.length > 0
                  })}>
                    {fields.map((field, index) => (
                      <Flex key={field.key} align="center" gap={10} className={clsx({ 'rb:mt-2!': index > 0 })}>
                        <UserOutlined className="rb:shrink-0 rb:text-green-600!" />
                        <Form.Item name={field.name} rules={[{ required: true, message: t('common.pleaseEnter') }]} noStyle>
                          <Input placeholder={t('knowledgeBase.createModal.entityNamePlaceholder')} />
                        </Form.Item>
                        <Button
                          type="text"
                          icon={<DeleteOutlined />}
                          aria-label={t('knowledgeBase.createModal.deleteEntityType', { index: index + 1 })}
                          disabled={fields.length <= 1}
                          onClick={() => remove(field.name)}
                        />
                      </Flex>
                    ))}
                    <Button type="dashed" block className="rb:mt-2!" icon={<PlusOutlined />} onClick={() => add('')}>
                      {t('knowledgeBase.createModal.addEntityType')}
                    </Button>
                  </div>
                  <Form.ErrorList errors={errors} className="rb:mt-2! rb:text-red-500 rb:text-[12px]!" />
                </>
              )}
            </Form.List>
          </div>
        )}
      </div>

      <div className="rb:border-b rb:border-[#ebeef2] rb:py-4.5 rb:first-of-type:pt-0 rb:last:border-b-0 rb:last:pb-0">
        <SwitchFormItem
          name="qa_enabled"
          title={t('knowledgeBase.createModal.qaGeneration')}
          desc={t('knowledgeBase.createModal.qaGenerationDesc')}
        />

        {qaEnabled && (
          <div className={clsx('rb:pt-5')}>
            <Form.Item
              name={pc('defaults.qa.questions_per_chunk')}
              label={<FieldLabel title={t('knowledgeBase.createModal.questionCount')} desc={t('knowledgeBase.createModal.questionCountDesc')} />}
              className={horizontalClassNames}
              rules={[{ required: true, message: t('knowledgeBase.createModal.questionCountRequired') }]}
            >
              <InputNumber className="rb:w-full!" min={1} max={10} />
            </Form.Item>
            <Form.Item
              name={pc('defaults.qa.prompt')}
              label={<FieldLabel title={t('knowledgeBase.createModal.qaPrompt')} desc={t('knowledgeBase.createModal.qaPromptDesc')} />}
              className={horizontalClassNames}
            >
              <TextArea rows={3} placeholder={t('knowledgeBase.createModal.leaveEmptyForDefault')} />
            </Form.Item>
          </div>
        )}
      </div>
    </section>
  );
};

export { AdvancedSection, BasicSection, ChunkSection, ModelsSection, MultimodalSection, ParsingSection };

