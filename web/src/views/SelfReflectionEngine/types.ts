// 标签表单数据类型
export interface TagFormData {
  tagName: string;
  type: string;
  color: string;
  description?: string;
  applicableScope?: string[];
  semanticExpansion?: string;
  isActive?: boolean;
  // 扩展字段用于区分编辑和新增操作
  isEditing?: boolean;
  tagId?: string;
}

// 记忆总览数据类型
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
// 定义组件暴露的方法接口
export interface MemoryOverviewFormRef {
  handleOpen: (memoryOverview?: MemoryOverviewRecord | null) => void;
}

// 遗忘曲线数据类型
export interface CurveRecord {
  memoryID: string;
  type: string;
  currentRetentionRate: string;
  finallyActivated: string;
  expectedForgettingTime: string;
  reinforcementCount: string;
}

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

export interface QualityAssessment {
  score: number;
  summary: string;
}
export interface MemoryVerify {
  has_privacy: boolean;
  privacy_types: string[];
  summary: string;
}
export interface ReflexionData {
  reason: string;
  solution: string;
}

export interface Result {
  baseline: string;
  source_data: string;
  quality_assessments: QualityAssessment[];
  memory_verifies: MemoryVerify[];
  reflexion_data: ReflexionData[]
}