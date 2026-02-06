/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:00:08 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:00:08 
 */
/**
 * Type definitions for Forgetting Engine
 */

/**
 * Tag form data (legacy, not used in current implementation)
 */
export interface TagFormData {
  /** Tag name */
  tagName: string;
  /** Tag type */
  type: string;
  /** Tag color */
  color: string;
  /** Tag description */
  description?: string;
  /** Applicable scope */
  applicableScope?: string[];
  /** Semantic expansion */
  semanticExpansion?: string;
  /** Whether tag is active */
  isActive?: boolean;
  /** Whether in editing mode */
  isEditing?: boolean;
  /** Tag ID */
  tagId?: string;
}

/**
 * Memory overview record (legacy, not used in current implementation)
 */
export interface MemoryOverviewRecord {
  /** Record ID */
  id: number;
  /** Memory ID */
  memoryID: string,
  /** Content summary */
  contentSummary: string;
  /** Memory type */
  type: string;
  /** Creation time */
  createTime: string;
  /** Last call time */
  lastCallTime: string;
  /** Retention degree */
  retentionDegree: string;
  /** Status */
  status: string;
}

/**
 * Memory overview form ref interface (legacy)
 */
export interface MemoryOverviewFormRef {
  /** Open form with optional record */
  handleOpen: (memoryOverview?: MemoryOverviewRecord | null) => void;
}

/**
 * Forgetting curve record (legacy, not used in current implementation)
 */
export interface CurveRecord {
  /** Memory ID */
  memoryID: string;
  /** Memory type */
  type: string;
  /** Current retention rate */
  currentRetentionRate: string;
  /** Finally activated time */
  finallyActivated: string;
  /** Expected forgetting time */
  expectedForgettingTime: string;
  /** Reinforcement count */
  reinforcementCount: string;
}

/**
 * Forgetting engine configuration form
 */
export interface ConfigForm {
  /** Configuration ID */
  config_id?: string;
  /** Time decay factor (λ_time) */
  lambda_time: string | number;
  /** Memory strength factor (λ_mem) */
  lambda_mem: string | number;
  /** Minimum retention offset */
  offset: string | number;
  /** Decay constant */
  decay_constant: string | number;
  /** Maximum history length */
  max_history_length: string | number;
  /** Forgetting threshold */
  forgetting_threshold: string | number;
  /** Minimum days since last access */
  min_days_since_access: string | number;
  /** Whether to enable LLM summary */
  enable_llm_summary: boolean;
  /** Maximum merge batch size */
  max_merge_batch_size: string | number;
  /** Forgetting interval in hours */
  forgetting_interval_hours: string | number;
  /** Additional dynamic fields */
  [key: string]: any;
}