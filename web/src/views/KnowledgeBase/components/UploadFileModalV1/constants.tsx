import {
  FileSearchOutlined,
  FileTextOutlined,
  PictureOutlined,
  SlidersOutlined,
} from '@ant-design/icons';

import type { KnowledgeBaseDocumentData } from '@/views/KnowledgeBase/types';
import type {
  DocumentChunkingWrite,
  DocumentLayout,
  DocumentProcessingWrite,
} from '@/views/KnowledgeBase/typesDocumentV1';
import type { ExtendedParserConfig } from '../CreateKnowledgeBaseModal/types';
import { splitDelimiters } from '../CreateKnowledgeBaseModal/constants';
import type { ConfigSection, ConfigSectionKey, UploadFormValues } from './types';

/** Parsing config section definitions (the order is the navigation order in the prototype). */
export const configSections: ConfigSection[] = [
  {
    key: 'parsing',
    labelKey: 'knowledgeBase.createModal.parsingConfig',
    descKey: 'knowledgeBase.uploadModal.parsingDesc',
    detailKey: 'knowledgeBase.uploadModal.parsingDetailDesc',
    icon: <FileSearchOutlined />,
  },
  {
    key: 'chunk',
    labelKey: 'knowledgeBase.createModal.chunkingStrategy',
    descKey: 'knowledgeBase.uploadModal.chunkDesc',
    detailKey: 'knowledgeBase.uploadModal.chunkDetailDesc',
    icon: <FileTextOutlined />,
  },
  {
    key: 'multimodal',
    labelKey: 'knowledgeBase.createModal.multimodalProcessing',
    descKey: 'knowledgeBase.uploadModal.multimodalDesc',
    detailKey: 'knowledgeBase.uploadModal.multimodalDetailDesc',
    icon: <PictureOutlined />,
  },
  {
    key: 'advanced',
    labelKey: 'knowledgeBase.createModal.advancedSettings',
    descKey: 'knowledgeBase.uploadModal.advancedDesc',
    detailKey: 'knowledgeBase.uploadModal.advancedDetailDesc',
    icon: <SlidersOutlined />,
  },
];

const configSectionKeys = configSections.map((section) => section.key);

/** Narrow fieldSectionMap's SectionKey to the four config sections supported by this component. */
export const isConfigSectionKey = (key: string): key is ConfigSectionKey =>
  configSectionKeys.includes(key as ConfigSectionKey);

/** Default form values for each config section, consistent with CreateKnowledgeBaseModal. */
export const defaultFormValues: UploadFormValues = {
  layout_recognize: 'mineru',
  vision_mode: 1,
  chunk_strategy: 'default',
  parent_chunk_mode: 'paragraph',
  parent_chunk_token_num: 1024,
  chunk_token_num: 512,
  chunk_overlap: 52,
  parent_chunk_delimiter: ['\n\n'],
  delimiter: ['\n\n'],
  multimodal_enabled: false,
  qa_enabled: false,
  auto_questions: 3,
};

/** Map an existing document's parser_config back to form values for backfilling when rechunking. */
export const documentToFormValues = (document: KnowledgeBaseDocumentData): UploadFormValues => {
  const pc = (document.parser_config || {}) as ExtendedParserConfig;
  return {
    layout_recognize: pc.layout_recognize ?? 'mineru',
    document_kind: pc.doc_type === 'qa' ? 'qa' : null,
    vision_mode: Number(pc.image?.vision_mode ?? 1),
    chunk_strategy: pc.parent_chunk_mode
      ? 'parent-child'
      : pc.chunk_overlap !== undefined ? 'custom' : 'default',
    parent_chunk_mode: pc.parent_chunk_mode ?? 'paragraph',
    parent_chunk_token_num: pc.parent_chunk_token_num ?? 1024,
    parent_chunk_delimiter: splitDelimiters(pc.parent_chunk_delimiter),
    chunk_token_num: pc.chunk_token_num ?? 512,
    chunk_overlap: pc.chunk_overlap ?? 52,
    delimiter: splitDelimiters(pc.delimiter),
    multimodal_enabled: Boolean(pc.image?.vision_enabled),
    qa_enabled: (pc.auto_questions ?? 0) > 0,
    auto_questions: pc.auto_questions ?? 3,
  };
};

const toLayout = (value?: string): DocumentLayout =>
  value === 'plain' || value === 'mineru' || value === 'textln' ? value : 'mineru';

const toImageMode = (value?: number): 0 | 1 | 2 =>
  value === 0 || value === 1 || value === 2 ? value : 1;

/**
 * Form values → document processing_configuration (v1 API)
 * - The default strategy submits only method, to avoid appearing alongside custom chunking params.
 * - Parent-child chunking and QA are mutually exclusive; the questions-per-chunk count is fixed to 0 for parent-child chunking.
 */
export const formValuesToProcessingConfiguration = (values: UploadFormValues): DocumentProcessingWrite => {
  let chunking: DocumentChunkingWrite;
  if (values.chunk_strategy === 'parent-child') {
    const parentMode = values.parent_chunk_mode ?? 'paragraph';
    chunking = {
      method: 'parent_child',
      max_tokens: values.chunk_token_num,
      delimiters: values.delimiter,
      parent: {
        mode: parentMode,
        ...(parentMode === 'paragraph' ? {
          max_tokens: values.parent_chunk_token_num,
          delimiters: values.parent_chunk_delimiter,
        } : {}),
      },
    };
  } else if (values.chunk_strategy === 'custom') {
    chunking = {
      method: 'custom',
      max_tokens: values.chunk_token_num,
      overlap_tokens: values.chunk_overlap,
      delimiters: values.delimiter,
    };
  } else {
    chunking = { method: 'default' };
  }

  const multimodalEnabled = values.multimodal_enabled ?? false;
  return {
    parsing: {
      layout: toLayout(values.layout_recognize),
      document_kind: values.document_kind ?? null,
    },
    image: { enabled: true, mode: toImageMode(values.vision_mode) },
    chunking,
    qa: {
      questions_per_chunk: values.chunk_strategy !== 'parent-child' && values.qa_enabled
        ? (values.auto_questions ?? 3)
        : 0,
      prompt: null,
    },
    multimodal: {
      enabled: multimodalEnabled,
      vision_model_id: multimodalEnabled ? values.image2text_id ?? null : null,
      audio_model_id: multimodalEnabled ? values.audio2text_id ?? null : null,
      video_model_id: multimodalEnabled ? values.video2text_id ?? null : null,
    },
  };
};

/** Document detail's processing_configuration → form values, for rechunking backfill. */
export const processingConfigurationToFormValues = (
  configuration?: DocumentProcessingWrite | null,
): UploadFormValues => {
  const { parsing, image, chunking, qa, multimodal } = configuration ?? {};
  const method = chunking?.method;

  return {
    ...defaultFormValues,
    layout_recognize: toLayout(parsing?.layout),
    document_kind: parsing?.document_kind ?? null,
    vision_mode: toImageMode(image?.mode),
    chunk_strategy: method === 'parent_child' ? 'parent-child' : method === 'custom' ? 'custom' : 'default',
    chunk_token_num: chunking?.max_tokens ?? defaultFormValues.chunk_token_num,
    chunk_overlap: chunking?.overlap_tokens ?? defaultFormValues.chunk_overlap,
    delimiter: chunking?.delimiters?.length ? chunking.delimiters : defaultFormValues.delimiter,
    parent_chunk_mode: chunking?.parent?.mode ?? 'paragraph',
    parent_chunk_token_num: chunking?.parent?.max_tokens ?? defaultFormValues.parent_chunk_token_num,
    parent_chunk_delimiter: chunking?.parent?.delimiters?.length
      ? chunking.parent.delimiters
      : defaultFormValues.parent_chunk_delimiter,
    multimodal_enabled: multimodal?.enabled ?? false,
    image2text_id: multimodal?.vision_model_id ?? null,
    audio2text_id: multimodal?.audio_model_id ?? null,
    video2text_id: multimodal?.video_model_id ?? null,
    qa_enabled: (qa?.questions_per_chunk ?? 0) > 0,
    auto_questions: qa?.questions_per_chunk || defaultFormValues.auto_questions,
  };
};
