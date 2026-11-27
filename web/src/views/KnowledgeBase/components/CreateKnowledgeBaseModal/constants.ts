import type { SectionKey } from './types';

const CM = (key: string) => `knowledgeBase.createModal.${key}`;

export const sectionList: Array<{ key: SectionKey; label: string }> = [
  { key: 'basic', label: CM('basicInfo') },
  { key: 'models', label: CM('modelsConfig') },
  { key: 'parsing', label: CM('parsingConfig') },
  { key: 'chunk', label: CM('chunkingStrategy') },
  { key: 'multimodal', label: CM('multimodalProcessing') },
  { key: 'advanced', label: CM('advancedSettings') },
];

/**
 * Flat field name → its section (used by modals such as UploadFileModalV1 that still use a flat form structure to locate fields that failed validation).
 * The new CreateKnowledgeBaseModal now uses nested names; please use getFieldSection instead.
 */
export const fieldSectionMap: Record<string, SectionKey> = {
  // Basic info
  name: 'basic',
  description: 'basic',
  content_type: 'basic',
  source_type: 'basic',
  entry_url: 'basic',
  max_pages: 'basic',
  delay_seconds: 'basic',
  timeout_seconds: 'basic',
  user_agent: 'basic',
  third_party_platform: 'basic',
  yuque_user_id: 'basic',
  yuque_token: 'basic',
  feishu_app_id: 'basic',
  feishu_app_secret: 'basic',
  feishu_folder_token: 'basic',
  rag_enabled: 'basic',
  wiki_enabled: 'basic',
  indexing_strategy: 'basic',
  wiki_extract_prompt: 'basic',
  // Model config
  llm_id: 'models',
  embedding_id: 'models',
  // Document parsing
  layout_recognize: 'parsing',
  vision_mode: 'parsing',
  // Chunking strategy
  chunk_strategy: 'chunk',
  parent_chunk_mode: 'chunk',
  parent_chunk_token_num: 'chunk',
  parent_chunk_delimiter: 'chunk',
  chunk_token_num: 'chunk',
  chunk_overlap: 'chunk',
  delimiter: 'chunk',
  // Multimodal processing
  multimodal_enabled: 'multimodal',
  image2text_id: 'multimodal',
  audio2text_id: 'multimodal',
  video2text_id: 'multimodal',
  // Advanced settings
  use_graphrag: 'advanced',
  entity_types: 'advanced',
  scene_name: 'advanced',
  qa_enabled: 'advanced',
  auto_questions: 'advanced',
  qa_prompt: 'advanced',
};

/**
 * Locate the section by the nested field path when validation fails.
 * Top-level fields map to basic info / model; the rest fall under processing_configuration.
 */
export const getFieldSection = (namePath: (string | number)[]): SectionKey | undefined => {
  const root = namePath[0];
  if (root === 'processing_configuration') {
    const second = namePath[1];
    if (second === 'multimodal') return 'multimodal';
    if (second === 'graph') return 'advanced';
    if (second === 'wiki') return 'basic';
    if (second === 'sources') return 'basic';
    if (second === 'defaults') {
      const third = namePath[2];
      if (third === 'parsing' || third === 'image') return 'parsing';
      if (third === 'chunking') return 'chunk';
      if (third === 'qa') return 'advanced';
    }
    return undefined;
  }
  switch (root) {
    case 'name':
    case 'description':
    case 'content_type':
    case 'source':
    case 'rag_enabled':
    case 'wiki_enabled':
    case 'indexing_strategy':
      return 'basic';
    case 'llm_id':
    case 'embedding_id':
      return 'models';
    case 'qa_enabled':
      return 'advanced';
    default:
      return undefined;
  }
};

export const modelFields = [
  { name: 'llm', type: 'llm', title: CM('llmModel'), desc: CM('llmModelDesc'), required: true },
  { name: 'embedding', type: 'embedding', title: CM('embeddingModel'), desc: CM('embeddingModelDesc'), required: true },
] as const;

export const multimodalFields = [
  { name: 'image2text', type: 'vision', title: CM('visionModel'), desc: CM('visionModelDesc') },
  { name: 'audio2text', type: 'audio', title: CM('audioModel'), desc: CM('audioModelDesc') },
  { name: 'video2text', type: 'video', title: CM('videoModel'), desc: CM('videoModelDesc') },
] as const;

export const DELIMITER_OPTIONS = [
  { label: CM('delimiterDoubleNewline'), value: '\n\n' },
  { label: CM('delimiterSingleNewline'), value: '\n' },
  { label: CM('delimiterChinesePeriod'), value: '。' },
  { label: CM('delimiterExclamation'), value: '!' },
  { label: CM('delimiterQuestion'), value: '?' },
  { label: CM('delimiterChineseSemicolon'), value: '；' },
  { label: CM('delimiterSemicolon'), value: ';' },
  { label: CM('delimiterSpace'), value: ' ' },
];

export const IndexingStrategy: Array<{ name: 'rag_enabled' | 'wiki_enabled'; title: string; desc: string }> = [
  { name: 'rag_enabled', title: CM('ragSearch'), desc: CM('ragSearchDesc') },
  { name: 'wiki_enabled', title: CM('wikiFaq'), desc: CM('wikiFaqDesc') },
];

export const parentChunkModeOptions = [
  { value: 'full-doc', label: CM('fullDoc'), labelDesc: CM('fullDocDesc') },
  { value: 'paragraph', label: CM('byParagraph'), labelDesc: CM('byParagraphDesc') },
];

export const horizontalClassNames =
  'rb:[&_.ant-form-item-label>label::after]:hidden! rb:[&_.ant-form-item-label>label]:pr-3! rb:[&_.ant-form-item-label>label]:h-auto! rb:mb-6!';

export const splitDelimiters = (value?: string): string[] => {
  if (!value) return ['\n\n'];
  return value.split('\n').filter(Boolean);
};
