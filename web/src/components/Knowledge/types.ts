/*
 * @Author: zhaoying
 * @Date: 2026-05-06 15:03:12
 * @Last Modified by: zhaoying
 * @Last Modified time: 
 */
/**
 * Type definitions for Knowledge component
 */

import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'

/**
 * Reranker configuration for knowledge retrieval
 */
export interface RerankerConfig {
  rerank_model?: boolean | undefined;
  reranker_id?: string | undefined;
  reranker_top_k?: number | undefined;
}

/**
 * Knowledge retrieval type
 */
export type RetrieveType = 'participle' | 'semantic' | 'hybrid' | 'graph'

/**
 * Knowledge base configuration form data
 */
export interface KnowledgeConfigForm {
  id?: string;
  kb_id?: string;
  similarity_threshold?: number;
  vector_similarity_weight?: number;
  top_k?: number;
  retrieve_type?: RetrieveType;
  weight?: number;
}

/**
 * Knowledge base with configuration
 */
export interface KnowledgeBase extends Omit<KnowledgeBaseListItem, 'id'>, KnowledgeConfigForm {
  id: string;
  config?: KnowledgeConfigForm
}

/**
 * Complete knowledge configuration
 */
export interface KnowledgeConfig extends RerankerConfig {
  knowledge_bases: KnowledgeBase[];
}

/**
 * Modal refs
 */
export interface KnowledgeConfigModalRef {
  handleOpen: (data: KnowledgeBase) => void;
}
export interface KnowledgeGlobalConfigModalRef {
  handleOpen: () => void;
}
export interface KnowledgeModalRef {
  handleOpen: (config?: KnowledgeConfig[]) => void;
}

/**
 * Style variant for Knowledge component
 * - application: Uses Card wrapper, larger styles for ApplicationConfig
 * - workflow: Compact styles for Workflow panel
 */
export type KnowledgeVariant = 'application' | 'workflow'