import type { ParserConfig } from '@/views/KnowledgeBase/types';
import type {
  V1KnowledgeListItem,
  V1ProcessingConfiguration,
  V1Source,
} from '@/views/KnowledgeBase/typesV1';

/** Additional extension fields of ParserConfig (consistent with the original CreateDataset's buildParserConfig). */
export type ExtendedParserConfig = ParserConfig & { chunk_overlap?: number; qa_prompt?: string };

export interface CreateKnowledgeBaseModalRef {
  handleOpen: (record?: V1KnowledgeListItem | null, initialSection?: SectionKey, parentId?: string, isRebuild?: boolean) => void;
}

export interface CreateKnowledgeBaseModalProps {
  refreshTable?: () => void;
}

export type SectionKey = 'basic' | 'models' | 'parsing' | 'chunk' | 'multimodal' | 'advanced';

/**
 * The form value structure stays isomorphic with the v1 API (V1KnowledgeListItem):
 * - name / description / source / embedding_id / llm_id / processing_configuration map directly to API fields,
 *   written via Form.Item nested names (e.g. ['processing_configuration', 'defaults', 'chunking', 'method']).
 * - Only a few UI helper fields are derived into API fields on submit:
 *   - content_type (document/qa) + rag_enabled + wiki_enabled → index_strategy
 *   - qa_enabled → processing_configuration.defaults.qa.questions_per_chunk (0 means disabled)
 *   - indexing_strategy: a validation-only placeholder field (no UI control)
 */
export interface CreateKbFormValues {
  name?: string;
  description?: string;
  // UI helper fields (mapped to index_strategy on submit)
  content_type?: 'document' | 'qa';
  rag_enabled?: boolean;
  wiki_enabled?: boolean;
  // UI helper fields (mapped to questions_per_chunk on submit)
  qa_enabled?: boolean;
  // Validation-only placeholder field
  indexing_strategy?: boolean;
  // Consistent with the v1 API fields
  source?: V1Source;
  embedding_id?: string | null;
  llm_id?: string | null;
  processing_configuration?: V1ProcessingConfiguration;
}
