/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:25:53 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:25:53 
 */
/**
 * Type definitions for knowledge base configuration in application settings
 */

import type { KnowledgeBaseListItem } from '@/views/KnowledgeBase/types'

/**
 * Reranker configuration for knowledge retrieval
 */
export interface RerankerConfig {
  /** Whether to enable rerank model */
  rerank_model?: boolean | undefined;
  /** Reranker model ID */
  reranker_id?: string | undefined;
  /** Top K results for reranking */
  reranker_top_k?: number | undefined;
}

/**
 * Knowledge retrieval type
 * - participle: Word segmentation based retrieval
 * - semantic: Semantic similarity based retrieval
 * - hybrid: Combination of both methods
 */
export type RetrieveType = 'participle' | 'semantic' | 'hybrid'

/**
 * Knowledge base configuration form data
 */
export interface KnowledgeConfigForm {
  /** Knowledge base ID */
  kb_id?: string;
  /** Similarity threshold for retrieval (0-1) */
  similarity_threshold?: number;
  /** Weight for vector similarity in hybrid mode (0-1) */
  vector_similarity_weight?: number;
  /** Number of top results to retrieve */
  top_k?: number;
  /** Retrieval strategy type */
  retrieve_type?: RetrieveType;
}

/**
 * Knowledge base with configuration
 */
export interface KnowledgeBase extends KnowledgeBaseListItem, KnowledgeConfigForm {
  /** Additional configuration object */
  config?: KnowledgeConfigForm
}

/**
 * Complete knowledge configuration including reranker settings
 */
export interface KnowledgeConfig extends RerankerConfig {
  /** List of configured knowledge bases */
  knowledge_bases: KnowledgeBase[];
}

/**
 * Modal ref for individual knowledge base configuration
 */
export interface KnowledgeConfigModalRef {
  /** Open modal with knowledge base data */
  handleOpen: (data: KnowledgeBase) => void;
}

/**
 * Modal ref for global knowledge configuration
 */
export interface KnowledgeGlobalConfigModalRef {
  /** Open global configuration modal */
  handleOpen: () => void;
}

/**
 * Modal ref for knowledge base selection
 */
export interface KnowledgeModalRef {
  /** Open modal with optional existing configuration */
  handleOpen: (config?: KnowledgeConfig[]) => void;
}