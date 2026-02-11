/*
 * @Author: ZhaoYing 
 * @Date: 2026-02-03 17:46:23 
 * @Last Modified by:   ZhaoYing 
 * @Last Modified time: 2026-02-03 17:46:23 
 */
/**
 * Tag form data type
 */
export interface TagFormData {
  tagName: string;
  type: string;
  color: string;
  description?: string;
  applicableScope?: string[];
  semanticExpansion?: string;
  isActive?: boolean;
  /** Distinguish between edit and create operations */
  isEditing?: boolean;
  tagId?: string;
}

/**
 * Memory overview record type
 */
export interface MemoryOverviewRecord {
  id: number;
  memoryID: string,
  contentSummary: string;
  type: string;
  createTime: string;
  lastCallTime: string;
  retentionDegree: string;
  status: string;
}
/**
 * Component exposed methods interface
 */
export interface MemoryOverviewFormRef {
  handleOpen: (memoryOverview?: MemoryOverviewRecord | null) => void;
}

/**
 * Forgetting curve data type
 */
export interface CurveRecord {
  memoryID: string;
  type: string;
  currentRetentionRate: string;
  finallyActivated: string;
  expectedForgettingTime: string;
  reinforcementCount: string;
}

/**
 * Reflection engine configuration
 */
export interface ConfigForm {
  config_id: number | string;
  reflection_enabled: boolean;
  reflection_period_in_hours: string;
  reflexion_range: string;
  baseline: string;
  reflection_model_id: string;
  memory_verify: boolean;
  quality_assessment: boolean;
}

/**
 * Quality assessment result
 */
export interface QualityAssessment {
  score: number;
  summary: string;
}
/**
 * Memory verification result
 */
export interface MemoryVerify {
  has_privacy: boolean;
  privacy_types: string[];
  summary: string;
}
/**
 * Reflection data
 */
export interface ReflexionData {
  reason: string;
  solution: string;
}

/**
 * Reflection engine test result
 */
export interface Result {
  baseline: string;
  source_data: string;
  quality_assessments: QualityAssessment[];
  memory_verifies: MemoryVerify[];
  reflexion_data: ReflexionData[]
}