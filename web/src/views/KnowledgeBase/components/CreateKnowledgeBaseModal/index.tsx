/**
 * New "Create Knowledge Base" modal
 *
 * Integrates with the v1 knowledge base API: create via POST /knowledges/v1, edit via PATCH /knowledges/v1/{id}.
 * The form value structure stays isomorphic with the v1 API (V1KnowledgeListItem): name / description / source / embedding_id / llm_id /
 * processing_configuration are written directly via Form.Item nested names, avoiding manual conversion between flat fields and the API structure.
 * Only content_type / rag_enabled / wiki_enabled / qa_enabled are UI helper fields, derived into API fields on submit.
 */
import { forwardRef, useEffect, useImperativeHandle, useState } from 'react';
import { flushSync } from 'react-dom';
import { App, Button, Flex, Form } from 'antd';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { getKnowledgeGraphEntityTypes } from '@/api/knowledgeBase';
import { createKnowledgeBaseV1, getKnowledgeConfigurationOptions, patchKnowledgeBaseV1 } from '@/api/knowledgeBaseV1';
import { getCustomWorkspaceModels } from '@/api/workspaces';
import RbModal from '@/components/RbModal';
import type {
  V1ChunkingConfig,
  V1ConfigurationOptions,
  V1IndexStrategy,
  V1KnowledgeListItem,
  V1ProcessingConfiguration,
  V1Source,
  V1SourcesConfig,
} from '@/views/KnowledgeBase/typesV1';
import type { Model } from '@/views/ModelManagement/types';
import { getFieldSection, sectionList } from './constants';
import {
  AdvancedSection,
  BasicSection,
  ChunkSection,
  ModelsSection,
  MultimodalSection,
  ParsingSection,
} from './Sections';
import type {
  CreateKbFormValues,
  CreateKnowledgeBaseModalProps,
  CreateKnowledgeBaseModalRef,
  SectionKey,
} from './types';

/** Deep merge with base as fallback and override taking precedence (arrays are replaced wholesale, null/undefined fall back to base, base reference is not mutated). */
const mergeDeep = <T,>(base: T, override: unknown): T => {
  if (Array.isArray(override)) return [...override] as unknown as T;
  if (override && typeof override === 'object') {
    const baseObj = (base && typeof base === 'object' && !Array.isArray(base) ? base : {}) as Record<string, unknown>;
    const overrideObj = override as Record<string, unknown>;
    const result: Record<string, unknown> = {};
    new Set([...Object.keys(baseObj), ...Object.keys(overrideObj)]).forEach((key) => {
      result[key] = mergeDeep(baseObj[key], overrideObj[key]);
    });
    return result as unknown as T;
  }
  return (override === undefined || override === null ? base : override) as unknown as T;
};

/** Clear parsing layout defaults that the frontend does not support: set to empty when layout is 'plain' (falls back to the default mineru on submit). */
const clearPlainParsingLayout = (pc: V1ProcessingConfiguration): V1ProcessingConfiguration => {
  if (pc.defaults?.parsing?.layout !== 'plain') return pc;
  return {
    ...pc,
    defaults: {
      ...pc.defaults,
      parsing: { ...pc.defaults.parsing, layout: undefined },
    },
  };
};

/** v1 detail → form backfill (processing_configuration is deep-merged with base as fallback). */
const toFormValues = (record: V1KnowledgeListItem, base: V1ProcessingConfiguration): Partial<CreateKbFormValues> => {
  const indexStrategy = record.index_strategy ?? 'rag';
  const pc = clearPlainParsingLayout(mergeDeep(base, record.processing_configuration ?? {}));
  if (!pc.graph?.entity_types?.length) {
    pc.graph = { ...pc.graph, entity_types: [''] };
  }
  return {
    name: record.name,
    description: record.description ?? '',
    content_type: ['rag', 'wiki', 'rag_wiki'].includes(indexStrategy) ? 'document' : 'qa',
    rag_enabled: indexStrategy !== 'wiki',
    wiki_enabled: indexStrategy === 'wiki' || indexStrategy === 'rag_wiki',
    qa_enabled: (pc.defaults?.qa?.questions_per_chunk ?? 0) > 0,
    source: record.source ?? 'space',
    embedding_id: record.embedding_id,
    llm_id: record.llm_id,
    processing_configuration: pc,
  };
};

/** Narrow the chunking config by method, keeping only the fields the strategy needs. */
const buildChunking = (chunking: V1ChunkingConfig | undefined): V1ChunkingConfig => {
  const method = chunking?.method ?? 'default';
  if (method === 'custom') {
    return {
      method,
      max_tokens: chunking?.max_tokens ?? 128,
      overlap_tokens: chunking?.overlap_tokens ?? 0,
      delimiters: chunking?.delimiters ?? [],
    };
  }
  if (method === 'parent_child') {
    const parent = chunking?.parent;
    return {
      method,
      max_tokens: chunking?.max_tokens ?? 128,
      delimiters: chunking?.delimiters ?? [],
      parent: {
        mode: parent?.mode ?? 'paragraph',
        max_tokens: parent?.max_tokens ?? 1024,
        delimiters: parent?.delimiters ?? [],
      },
    };
  }
  return { method: 'default' };
};

/** Form → v1 processing_configuration (only does strategy narrowing: QA mutual exclusion, source filtering, clearing multimodal models, entity filtering). */
const finalizeProcessingConfiguration = (
  pc: V1ProcessingConfiguration,
  source: V1Source,
  indexStrategy: V1IndexStrategy,
  qaEnabled: boolean,
): V1ProcessingConfiguration => {
  const method = pc.defaults?.chunking?.method ?? 'default';
  const multimodalEnabled = pc.multimodal?.enabled ?? false;
  const questionsPerChunk = qaEnabled && method !== 'parent_child' && indexStrategy !== 'wiki'
    ? pc.defaults?.qa?.questions_per_chunk ?? 3
    : 0;

  const sources: V1SourcesConfig = {};
  const rawSources = pc.sources ?? {};
  if (source === 'website' && rawSources.website) sources.website = rawSources.website;
  else if (source === 'third_party' && rawSources.third_party) sources.third_party = rawSources.third_party;

  return {
    multimodal: {
      enabled: multimodalEnabled,
      vision_model_id: multimodalEnabled ? pc.multimodal?.vision_model_id ?? null : null,
      audio_model_id: multimodalEnabled ? pc.multimodal?.audio_model_id ?? null : null,
      video_model_id: multimodalEnabled ? pc.multimodal?.video_model_id ?? null : null,
    },
    defaults: {
      parsing: { layout: pc.defaults?.parsing?.layout ?? 'mineru' },
      image: { enabled: true, mode: pc.defaults?.image?.mode ?? 1, embedded_enabled: true },
      chunking: buildChunking(pc.defaults?.chunking),
      qa: {
        questions_per_chunk: questionsPerChunk,
        prompt: questionsPerChunk > 0 ? pc.defaults?.qa?.prompt ?? null : null,
      },
    },
    graph: {
      enabled: pc.graph?.enabled ?? false,
      scene_name: pc.graph?.scene_name ?? '',
      entity_types: (pc.graph?.entity_types ?? [])
        .map((item) => item?.trim())
        .filter((item): item is string => Boolean(item)),
      method: 'general',
    },
    wiki: { language: 'zh-CN', extract_prompt: pc.wiki?.extract_prompt ?? '' },
    sources,
  };
};

const CreateKnowledgeBaseModal = forwardRef<
  CreateKnowledgeBaseModalRef,
  CreateKnowledgeBaseModalProps
>(({ refreshTable }, ref) => {
  const { t } = useTranslation();
  const { message: messageApi, modal } = App.useApp();
  const [form] = Form.useForm<CreateKbFormValues>();
  const [visible, setVisible] = useState(false);
  const [loading, setLoading] = useState(false);
  const [generatingEntityTypes, setGeneratingEntityTypes] = useState(false);
  const [showEntityGenerate, setShowEntityGenerate] = useState(false);
  const [activeSection, setActiveSection] = useState<SectionKey>('basic');
  const [editing, setEditing] = useState<V1KnowledgeListItem | null>(null);
  const [parentId, setParentId] = useState<string | undefined>();
  const [isRebuildMode, setIsRebuildMode] = useState(false);
  const [configurationOptions, setConfigurationOptions] = useState<V1ConfigurationOptions | null>(null);
  const [customModels, setCustomModels] = useState<Record<string, Model[]>>({});

  // Prefetch config options on mount as the initial defaults for processing_configuration (replacing hardcoded defaults and the space model API).
  useEffect(() => {
    if (!visible) return;
    getKnowledgeConfigurationOptions()
      .then((data) => setConfigurationOptions(data))
      .catch(() => undefined);
    getCustomWorkspaceModels()
      .then((res) => setCustomModels((res || {}) as Record<string, Model[]>))
      .catch(() => setCustomModels({}));
  }, [visible]);

  const handleClose = () => {
    setVisible(false);
    setLoading(false);
    setGeneratingEntityTypes(false);
    setShowEntityGenerate(false);
    setActiveSection('basic');
    setEditing(null);
    setParentId(undefined);
    setIsRebuildMode(false);
    setConfigurationOptions(null)
    setCustomModels({})
    form.resetFields();
  };

  /** Initialize the form with the library upload presets + space default models returned by the API (create mode). */
  const fillCreateDefaults = (options: V1ConfigurationOptions | null) => {
    const defaults = options?.knowledge_defaults;
    if (!defaults) return;
    const pc = clearPlainParsingLayout(mergeDeep(defaults, {}));
    if (!pc.graph?.entity_types?.length) {
      pc.graph = { ...pc.graph, entity_types: [''] };
    }
    const workspaceModels = options?.workspace_models;
    form.setFieldsValue({
      embedding_id: workspaceModels?.embedding_id ?? null,
      llm_id: workspaceModels?.llm_id ?? null,
      processing_configuration: {
        ...pc,
        multimodal: {
          ...pc.multimodal,
          vision_model_id: workspaceModels?.vision_model_id ?? pc.multimodal?.vision_model_id ?? null,
          audio_model_id: workspaceModels?.audio_model_id ?? pc.multimodal?.audio_model_id ?? null,
          video_model_id: workspaceModels?.video_model_id ?? pc.multimodal?.video_model_id ?? null,
        },
      },
    } as never);
  };

  const handleOpen = (record?: V1KnowledgeListItem | null, initialSection?: SectionKey, parent?: string, isRebuild?: boolean) => {
    setEditing(record ?? null);
    setParentId(parent ?? record?.parent_id ?? undefined);
    setActiveSection(initialSection ?? 'basic');
    setIsRebuildMode(Boolean(isRebuild));
    setVisible(true);

    const fill = (options: V1ConfigurationOptions | null) => {
      if (record) {
        // RecursivePartial's null-union handling for V1ProcessingConfiguration is incomplete; assert explicitly here.
        form.setFieldsValue(toFormValues(record, options?.knowledge_defaults ?? ({} as V1ProcessingConfiguration)) as never);
      } else {
        form.resetFields();
        fillCreateDefaults(options);
      }
    };

    if (configurationOptions) {
      fill(configurationOptions);
    } else {
      getKnowledgeConfigurationOptions()
        .then((data) => {
          setConfigurationOptions(data);
          fill(data);
        })
        .catch(() => fill(null));
    }
  };

  const generateEntityTypes = () => {
    const sceneName = (form.getFieldValue(['processing_configuration', 'graph', 'scene_name']) || '').trim();
    if (!sceneName) {
      messageApi.error(t('knowledgeBase.createModal.describeScenarioRequired'));
      return;
    }
    const llmId = form.getFieldValue('llm_id');
    if (!llmId) {
      messageApi.error(t('knowledgeBase.createModal.selectLlmFirst'));
      return;
    }

    setGeneratingEntityTypes(true);
    getKnowledgeGraphEntityTypes({ scenario: sceneName, llm_id: llmId })
      .then((response) => {
        const str = typeof response === 'string' ? response : '';
        if (str.trim()) {
          const list = str.split(/[,，\n]+/).map((item) => item.trim()).filter(Boolean);
          form.setFieldValue(['processing_configuration', 'graph', 'entity_types'], list.length ? list : ['']);
          messageApi.success(t('knowledgeBase.generateEntityTypesSuccess'));
        } else {
          messageApi.error(t('knowledgeBase.generateEntityTypesFailed'));
        }
      })
      .catch(() => messageApi.error(t('knowledgeBase.generateEntityTypesFailed')))
      .finally(() => setGeneratingEntityTypes(false));
  };

  /** Save or create the knowledge base (invoked after the rebuild confirmation passes). */
  const performSave = (values: CreateKbFormValues) => {
    setLoading(true);
    const source: V1Source = values.source ?? 'space';
    const indexStrategy: V1IndexStrategy = values.content_type === 'qa'
      ? 'wiki'
      : values.wiki_enabled
        ? (values.rag_enabled ? 'rag_wiki' : 'wiki')
        : 'rag';
    const processingConfiguration = finalizeProcessingConfiguration(
      values.processing_configuration ?? {},
      source,
      indexStrategy,
      values.qa_enabled ?? false,
    );

    const request = editing
      ? patchKnowledgeBaseV1(editing.id, {
        name: values.name,
        description: values.description?.trim() || null,
        embedding_id: values.embedding_id ?? null,
        llm_id: values.llm_id ?? null,
        processing_configuration: processingConfiguration,
      })
      : createKnowledgeBaseV1({
        name: values.name!,
        type: 'document',
        source,
        index_strategy: indexStrategy,
        processing_configuration: processingConfiguration,
        parent_id: parentId ?? null,
        description: values.description?.trim() || null,
        embedding_id: values.embedding_id ?? null,
        llm_id: values.llm_id ?? null,
      });

    request
      .then(() => {
        messageApi.success(editing ? t('common.saveSuccess') : t('common.createSuccess'));
        refreshTable?.();
        handleClose();
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  };

  const handleSubmit = () => {
    form
      .validateFields()
      .then((values) => {
        // Rebuild graph: ask for confirmation before submitting.
        const graphEnabled = Boolean(values.processing_configuration?.graph?.enabled);
        if (isRebuildMode && graphEnabled) {
          modal.confirm({
            title: t('knowledgeBase.rebuildConfirmTitle'),
            content: t('knowledgeBase.rebuildConfirmContent'),
            onOk: () => performSave(values),
            onCancel: () => {},
          });
          return;
        }
        performSave(values);
      })
      .catch(({ errorFields }) => {
        const first = errorFields?.[0];
        if (!first?.name?.length) return;
        const section = getFieldSection(first.name as (string | number)[]);
        if (section && section !== activeSection) {
          flushSync(() => setActiveSection(section));
        }
        form.scrollToField(first.name, { behavior: 'smooth', block: 'center', focus: true });
      });
  };

  /** Switch embedding model: confirm when editing an existing KB, revert to the original value on cancel. */
  const handleEmbeddingChange = (value?: string) => {
    if (!editing?.id) {
      form.setFieldValue('embedding_id', value ?? null);
      return;
    }
    const previousValue = editing.embedding_id;
    if (!value || value === previousValue) {
      form.setFieldValue('embedding_id', value ?? null);
      return;
    }
    modal.confirm({
      title: t('common.updateWarning'),
      content: t('knowledgeBase.updateEmbeddingContent'),
      onOk: () => {
        form.setFieldValue('embedding_id', value);
      },
      onCancel: () => {
        form.setFieldValue('embedding_id', previousValue ?? null);
      },
    });
  };

  useImperativeHandle(ref, () => ({ handleOpen }));

  const isEditing = Boolean(editing?.id);

  return (
    <RbModal
      title={isEditing ? t('knowledgeBase.editKnowledgeBase') : t('knowledgeBase.createModal.createTitle')}
      open={visible}
      onCancel={handleClose}
      width={860}
      destroyOnHidden
      footer={[
        <Button key="cancel" onClick={handleClose}>
          {t('common.cancel')}
        </Button>,
        <Button key="submit" type="primary" loading={loading} onClick={handleSubmit}>
          {isEditing ? t('common.save') : t('knowledgeBase.createModal.createButton')}
        </Button>,
      ]}
    >
      <Form
        form={form}
        labelAlign="left"
        labelCol={{span: 12}}
        labelWrap={true}
        className="rb:grid rb:grid-cols-[200px_minmax(0,1fr)] rb:h-[calc(100vh-202px)] rb:min-h-65 rb:overflow-hidden"
        initialValues={{
          content_type: 'document',
          source: 'space',
          rag_enabled: true,
          wiki_enabled: false,
          qa_enabled: false,
        }}
      >
        <Flex vertical gap={4} className="rb:h-full rb:overflow-y-auto rb-border-r rb:pr-3! rb:pt-1! rb:pb-3!" aria-label={t('knowledgeBase.createModal.configCategories')}>
          {sectionList.map((section) => (
            <div
              key={section.key}
              className={clsx(
                'rb:cursor-pointer rb:rounded-lg rb:font-medium rb:py-2.25 rb:px-3 rb:leading-5.5', {
                  'rb:bg-[#F2F4F7] rb:text-primary-600': activeSection === section.key,
                  'rb:hover:bg-[#F7F9FC]': activeSection !== section.key
                },
              )}
              aria-current={activeSection === section.key ? 'page' : undefined}
              onClick={() => setActiveSection(section.key)}
            >
              {t(section.label)}
            </div>
          ))}
        </Flex>

        <div className={clsx('rb:min-w-0 rb:overflow-auto rb:pl-4 rb:pr-2 rb:pb-6')}>
          <BasicSection active={activeSection === 'basic'} editing={isEditing} />
          <ModelsSection active={activeSection === 'models'} customModels={customModels} onEmbeddingChange={handleEmbeddingChange} />
          <ParsingSection active={activeSection === 'parsing'} />
          <ChunkSection active={activeSection === 'chunk'} />
          <MultimodalSection active={activeSection === 'multimodal'} workspaceModels={configurationOptions?.workspace_models} customModels={customModels} />
          <AdvancedSection
            active={activeSection === 'advanced'}
            generatingEntityTypes={generatingEntityTypes}
            showEntityGenerate={showEntityGenerate}
            setShowEntityGenerate={setShowEntityGenerate}
            generateEntityTypes={generateEntityTypes}
          />
        </div>
      </Form>
    </RbModal>
  );
});

export default CreateKnowledgeBaseModal;
export type { CreateKnowledgeBaseModalProps, CreateKnowledgeBaseModalRef } from './types';

