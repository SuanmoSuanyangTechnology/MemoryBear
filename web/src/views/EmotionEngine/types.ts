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
  emotion_enabled: boolean;
  emotion_model_id: string;
  emotion_extract_keywords: boolean;
  emotion_min_intensity: number;
  emotion_enable_subject: boolean;
}