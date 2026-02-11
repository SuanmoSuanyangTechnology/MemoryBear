/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 16:57:37 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 16:57:37 
 */
/**
 * Type definitions for Emotion Engine
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
 * Emotion engine configuration form
 */
export interface ConfigForm {
  /** Configuration ID */
  config_id: number | string;
  /** Whether emotion engine is enabled */
  emotion_enabled: boolean;
  /** Emotion analysis model ID */
  emotion_model_id: string;
  /** Whether to extract keywords */
  emotion_extract_keywords: boolean;
  /** Minimum emotion intensity threshold (0-1) */
  emotion_min_intensity: number;
  /** Whether to enable subject extraction */
  emotion_enable_subject: boolean;
}