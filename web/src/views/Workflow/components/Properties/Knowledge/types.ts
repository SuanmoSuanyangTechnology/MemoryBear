import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'

export interface RerankerConfig {
  rerank_model?: boolean | undefined;
  reranker_id?: string | undefined;
  reranker_top_k?: number | undefined;
}
export type RetrieveType = 'participle' | 'semantic' | 'hybrid'
export interface KnowledgeConfigForm {
  kb_id?: string;
  similarity_threshold?: number;
  vector_similarity_weight?: number;
  top_k?: number;
  retrieve_type?: RetrieveType;
}
export interface KnowledgeBase extends KnowledgeBaseListItem, KnowledgeConfigForm {
  config?: KnowledgeConfigForm
}
export interface KnowledgeConfig extends RerankerConfig {
  knowledge_bases: KnowledgeBase[];
}

export interface KnowledgeConfigModalRef {
  handleOpen: (data: KnowledgeBase) => void;
}
export interface KnowledgeGlobalConfigModalRef {
  handleOpen: () => void;
}

export interface KnowledgeModalRef {
  handleOpen: (config?: KnowledgeConfig[]) => void;
}