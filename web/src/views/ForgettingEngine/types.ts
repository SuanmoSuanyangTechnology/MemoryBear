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
  config_id?: string;
  lambda_time: string | number;
  lambda_mem: string | number;
  offset: string | number;
  
  decay_constant: string | number;
  max_history_length: string | number;
  forgetting_threshold: string | number;
  min_days_since_access: string | number;
  enable_llm_summary: boolean;
  max_merge_batch_size: string | number;
  forgetting_interval_hours: string | number;

  [key: string]: any;
}